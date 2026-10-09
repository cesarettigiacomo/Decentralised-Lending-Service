#!/usr/bin/env python3
"""
Automated approval strategy for a contributor

Behaviour:
1. Loads network, account and contract data from deployments/initial_setup.json
2. Uses the autoContributor account created by initial_setup.py
3. Optionally deposits funds into LendingPool if autoContributor is not yet a contributor
4. Polls LendingPool.LoanProposalSubmitted events
5. For each new proposal:
   - checks proposal status
   - checks whether autoContributor already voted
   - votes approve if possible
6. Keeps running, unless --once is used

Requirements:
- Run scripts/initial_setup.py successfully first
- Run from the hardhat directory
- Keep the private chain running
- Install Python dependencies:
  pip install web3 eth-account

Usage:
  python3 scripts/auto_approve_contributor.py

Useful options:
  python3 scripts/auto_approve_contributor.py --once
  python3 scripts/auto_approve_contributor.py --from-block 0 --once
  python3 scripts/auto_approve_contributor.py --poll-interval 5
  python3 scripts/auto_approve_contributor.py --deposit-eth 0.5
  python3 scripts/auto_approve_contributor.py --no-auto-deposit
"""

from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.exceptions import ContractLogicError


DEFAULT_SETUP_FILE = "deployments/initial_setup.json"
DEFAULT_POLL_INTERVAL = 10 # Seconds to wait before another check
DEFAULT_DEPOSIT_ETH = "0.5"

ARTIFACTS = {
    "LendingPool": "artifacts/contracts/LendingPool.sol/LendingPool.json",
}

PROPOSAL_STATUS = {
    0: "Active",
    1: "Approved",
    2: "Rejected",
}

VOTE_CHOICE = {
    0: "None",
    1: "Approve",
    2: "Reject",
}


def install_poa_middleware(w3: Web3) -> None:
    # Geth Clique private chains may use a longer extraData field. This middleware keeps web3.py compatible with PoA-style blocks
    try:
        from web3.middleware import ExtraDataToPOAMiddleware

        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    except Exception:
        try:
            from web3.middleware import geth_poa_middleware

            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        except Exception:
            pass


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_artifact(contract_name: str) -> Dict[str, Any]:
    return load_json(Path(ARTIFACTS[contract_name]))


# Creates the contract object from the artifact
def contract_from_artifact(w3: Web3, contract_name: str, address: str) -> Any:
    artifact = load_artifact(contract_name)

    return w3.eth.contract(
        address=Web3.to_checksum_address(address),
        abi=artifact["abi"],
    )


# Takes the private key from the JSON file and creates a local account
def account_from_setup(setup: Dict[str, Any], name: str) -> LocalAccount:
    private_key = setup["accounts"][name]["private_key"]
    return Account.from_key(private_key)


def raw_signed_transaction(signed: Any) -> bytes:
    # web3.py changed the signed transaction field name across versions. This helper keeps the script compatible with both
    return getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction")


def build_common_tx(
    w3: Web3,
    sender: str,
    nonce: int,
    chain_id: int,
    value: int = 0,
) -> Dict[str, Any]:
    return {
        "from": Web3.to_checksum_address(sender),
        "nonce": nonce,
        "chainId": chain_id,
        "gasPrice": w3.eth.gas_price,
        "value": value,
    }


# Estimates gas
def add_estimated_gas(w3: Web3, tx: Dict[str, Any], multiplier: float = 1.25) -> Dict[str, Any]:
    estimated = w3.eth.estimate_gas(tx)
    tx["gas"] = int(estimated * multiplier)

    return tx


# Signs the transaction with the private key, sends it with send_raw_transaction, and waits for the receipt to check whether it was executed correctly
def sign_send_wait(
    w3: Web3,
    account: LocalAccount,
    tx: Dict[str, Any],
    label: str,
) -> Any:
    signed = Account.sign_transaction(tx, account.key)
    tx_hash = w3.eth.send_raw_transaction(raw_signed_transaction(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status != 1:
        raise RuntimeError(f"Transaction failed: {label}, tx={tx_hash.hex()}")

    print(f"{label:<36} tx={tx_hash.hex()} gasUsed={receipt.gasUsed}")

    return receipt


# Transact:
# Takes the nonce
# Builds the transaction starting from the contract function
# Estimates gas
# Signs
# Sends
# Waits for the result
def transact(
    w3: Web3,
    account: LocalAccount,
    chain_id: int,
    function_call: Any,
    label: str,
    value: int = 0,
) -> Any:
    nonce = w3.eth.get_transaction_count(account.address)

    tx = function_call.build_transaction(
        build_common_tx(
            w3=w3,
            sender=account.address,
            nonce=nonce,
            chain_id=chain_id,
            value=value,
        )
    )

    tx = add_estimated_gas(w3, tx)

    return sign_send_wait(
        w3=w3,
        account=account,
        tx=tx,
        label=label,
    )


def wei_to_eth(value: int) -> Decimal:
    return Web3.from_wei(value, "ether")


def format_wei(value: int) -> str:
    return f"{value} wei ({wei_to_eth(value)} ETH)"


def print_account_balance(w3: Web3, label: str, address: str) -> None:
    balance = w3.eth.get_balance(Web3.to_checksum_address(address))
    print(f"{label:<18} {address} balance={wei_to_eth(balance)} ETH")


# Reads the state of a contributor from the LendingPool
def read_contributor_state(lending_pool: Any, contributor: str) -> Dict[str, Any]:

    # Reads deposited, locked, pending_gains, and whether the contributor exists
    deposited, locked, pending_gains, exists = lending_pool.functions.contributors(
        Web3.to_checksum_address(contributor)
    ).call()

    # Reads the disposable value for the contributor
    disposable = lending_pool.functions.disposableValue(
        Web3.to_checksum_address(contributor)
    ).call()

    return {
        "deposited": deposited,
        "locked": locked,
        "pending_gains": pending_gains,
        "exists": exists,
        "disposable": disposable,
    }


def print_contributor_state(lending_pool: Any, contributor: str) -> None:
    state = read_contributor_state(lending_pool, contributor)

    print("autoContributor state:")
    print(f"  exists:       {state['exists']}")
    print(f"  deposited:    {format_wei(state['deposited'])}")
    print(f"  locked:       {format_wei(state['locked'])}")
    print(f"  disposable:   {format_wei(state['disposable'])}")
    print(f"  pendingGains: {format_wei(state['pending_gains'])}")


def ensure_auto_contributor_deposited(
    w3: Web3,
    chain_id: int,
    lending_pool: Any,
    auto_contributor: LocalAccount,
    deposit_amount_wei: int,
    auto_deposit: bool,
) -> None:
    state = read_contributor_state(lending_pool, auto_contributor.address)

    # Checks if the contributor has already deposited funds in the LendingPool
    if state["deposited"] > 0:
        print("autoContributor is already a contributor.")
        print_contributor_state(lending_pool, auto_contributor.address)
        return

    if not auto_deposit:
        print("autoContributor has not deposited funds.")
        print("Auto-deposit disabled. The strategy can notice proposals but cannot vote.")
        return

    # Checks the minimum deposit if auto deposit is active
    min_deposit = lending_pool.functions.MIN_DEPOSIT().call()

    if deposit_amount_wei <= min_deposit:
        raise ValueError(
            f"Deposit too small: {deposit_amount_wei}. "
            f"MIN_DEPOSIT is {min_deposit}; deposit must be strictly greater."
        )

    print("\nautoContributor is not yet a contributor.")
    print(f"Depositing {format_wei(deposit_amount_wei)} into LendingPool...")

    transact(
        w3=w3,
        account=auto_contributor,
        chain_id=chain_id,
        function_call=lending_pool.functions.deposit(),
        label="autoContributor.deposit",
        value=deposit_amount_wei,
    )

    print_contributor_state(lending_pool, auto_contributor.address)


# Selects the starting block using --from-block
def normalize_from_block(w3: Web3, from_block: str) -> int:
    if from_block == "latest":
        return w3.eth.block_number

    if from_block == "current":
        return w3.eth.block_number

    if from_block == "0":
        return 0

    try:
        value = int(from_block)
    except ValueError as exc:
        raise ValueError(
            "--from-block must be 'latest', 'current', '0', or an integer"
        ) from exc

    if value < 0:
        raise ValueError("--from-block cannot be negative")

    return value


# Searches for LoanProposalSubmitted events emitted by the LendingPool contract between two blocks
def get_loan_proposal_logs(
    lending_pool: Any,
    from_block: int,
    to_block: int,
) -> List[Any]:
    """
    web3.py has slightly different APIs across versions. Try event.get_logs first,
    then fall back to a manual eth_getLogs + process_log approach.
    """
    event = lending_pool.events.LoanProposalSubmitted()

    try:
        return event.get_logs(from_block=from_block, to_block=to_block)
    except TypeError:
        try:
            return event.get_logs(fromBlock=from_block, toBlock=to_block)
        except Exception:
            pass
    except Exception:
        pass

    event_topic = Web3.keccak(
        text="LoanProposalSubmitted(uint256,address,uint256,uint256,uint256,string)"
    ).hex()

    raw_logs = lending_pool.w3.eth.get_logs(
        {
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": lending_pool.address,
            "topics": [event_topic],
        }
    )

    return [event.process_log(raw_log) for raw_log in raw_logs]


# Reads from the contract the proposal with proposal_id
# Useful to check if a proposal is still active
def proposal_summary(lending_pool: Any, proposal_id: int) -> Dict[str, Any]:
    proposal = lending_pool.functions.proposals(proposal_id).call()

    return {
        "applicant": proposal[0],
        "amount": proposal[1],
        "interest_rate": proposal[2],
        "duration": proposal[3],
        "btc_address": proposal[4],
        "creation_block": proposal[5],
        "status": proposal[6],
        "loaned_amount": proposal[7],
    }


# Prints information about the event
def print_proposal_event(event: Any) -> None:
    args = event["args"]

    print("\nNew loan proposal noticed:")
    print(f"  proposalId:   {args['proposalId']}")
    print(f"  applicant:    {args['applicant']}")
    print(f"  amount:       {format_wei(args['amount'])}")
    print(f"  interestRate: {args['interestRate']}%")
    print(f"  duration:     {args['duration']} blocks")
    print(f"  btcAddress:   {args['btcAddress']}")
    print(f"  blockNumber:  {event['blockNumber']}")


# Checks if the proposal is active, whether the contributor has already voted, and whether the account is a contributor. If all conditions are met, votes Approve
def try_vote_approve(
    w3: Web3,
    chain_id: int,
    lending_pool: Any,
    auto_contributor: LocalAccount,
    proposal_id: int,
) -> bool:
    try:
        # Reads the proposal
        proposal = proposal_summary(lending_pool, proposal_id)
    except Exception as exc:
        print(f"Could not read proposal {proposal_id}: {exc}")
        return False

    # If it is not Active, it does not vote
    status = int(proposal["status"])

    if status != 0:
        print(
            f"Skipping proposal {proposal_id}: "
            f"status={PROPOSAL_STATUS.get(status, str(status))}"
        )
        return False

    vote = lending_pool.functions.proposalVotes(
        proposal_id,
        Web3.to_checksum_address(auto_contributor.address),
    ).call()

    # If vote is 0, it means the contributor has already voted
    if int(vote) != 0:
        print(
            f"Skipping proposal {proposal_id}: "
            f"already voted {VOTE_CHOICE.get(int(vote), str(vote))}"
        )
        return False

    contributor_state = read_contributor_state(
        lending_pool,
        auto_contributor.address,
    )

    # Checks whether the account is a contributor. If it has not deposited funds, it can't vote
    if contributor_state["deposited"] == 0:
        print(
            f"Cannot vote proposal {proposal_id}: "
            "autoContributor is not a contributor."
        )
        return False

    if contributor_state["disposable"] == 0:
        print(
            "autoContributor has no disposable value. "
            "The vote may have zero weight, but it is still submitted."
        )

    try:
        transact(
            w3=w3,
            account=auto_contributor,
            chain_id=chain_id,
            function_call=lending_pool.functions.vote(proposal_id, True), # Votes true: Approve
            label=f"autoContributor.voteApprove({proposal_id})",
        )

        # Reads the vote again, then prints it
        vote_after = lending_pool.functions.proposalVotes(
            proposal_id,
            Web3.to_checksum_address(auto_contributor.address),
        ).call()

        print(
            f"Vote recorded for proposal {proposal_id}: "
            f"{VOTE_CHOICE.get(int(vote_after), str(vote_after))}"
        )

        return True

    except ContractLogicError as exc:
        print(f"Vote reverted for proposal {proposal_id}: {exc}")
        return False
    except Exception as exc:
        print(f"Could not vote proposal {proposal_id}: {exc}")
        return False


# Selects an interval of blocks, searches for proposals in that interval, and tries to vote Approve
def scan_and_vote(
    w3: Web3,
    chain_id: int,
    lending_pool: Any,
    auto_contributor: LocalAccount,
    from_block: int,
    to_block: int,
    processed_proposals: set[int],
) -> int:
    if to_block < from_block:
        return from_block

    logs = get_loan_proposal_logs(
        lending_pool=lending_pool,
        from_block=from_block,
        to_block=to_block,
    )

    if not logs:
        print(f"No new proposals from block {from_block} to {to_block}.")
        return to_block + 1

    print(f"Found {len(logs)} proposal event(s) from block {from_block} to {to_block}.")

    # Searches for events LoanProposalSubmitted in the interval from_block - to_block
    for event in logs:
        proposal_id = int(event["args"]["proposalId"])

        # Avoids processing the same proposal more than once
        if proposal_id in processed_proposals:
            continue

        processed_proposals.add(proposal_id)

        print_proposal_event(event)

        try_vote_approve(
            w3=w3,
            chain_id=chain_id,
            lending_pool=lending_pool,
            auto_contributor=auto_contributor,
            proposal_id=proposal_id,
        )

        print_contributor_state(lending_pool, auto_contributor.address)

    return to_block + 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automatically approve every new LendingPool loan proposal."
    )

    parser.add_argument(
        "--setup",
        default=DEFAULT_SETUP_FILE,
        help=f"Path to initial setup JSON. Default: {DEFAULT_SETUP_FILE}",
    )

    parser.add_argument(
        "--rpc-url",
        default=None,
        help="Override RPC URL from setup file.",
    )

    parser.add_argument(
        "--chain-id",
        type=int,
        default=None,
        help="Override chain id from setup file.",
    )

    parser.add_argument(
        "--from-block",
        default="latest",
        help=(
            "Block to start scanning from. Use 'latest' for only new proposals, "
            "'0' to scan all past events, or an integer. Default: latest."
        ),
    )

    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Polling interval in seconds. Default: {DEFAULT_POLL_INTERVAL}.",
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Scan once and exit instead of continuously monitoring.",
    )

    parser.add_argument(
        "--deposit-eth",
        default=DEFAULT_DEPOSIT_ETH,
        help=f"ETH amount to deposit if autoContributor is not a contributor. Default: {DEFAULT_DEPOSIT_ETH}.",
    )

    parser.add_argument(
        "--no-auto-deposit",
        action="store_true",
        help="Do not automatically deposit funds if autoContributor is not a contributor.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup = load_json(Path(args.setup))

    rpc_url = args.rpc_url or setup["network"]["rpc_url"]
    chain_id = args.chain_id or int(setup["network"]["chain_id"])

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    install_poa_middleware(w3)

    if not w3.is_connected():
        raise ConnectionError(f"Could not connect to RPC URL: {rpc_url}")

    # Checks if it is the right chain
    actual_chain_id = w3.eth.chain_id

    if actual_chain_id != chain_id:
        raise RuntimeError(
            f"Unexpected chain id: {actual_chain_id}. Expected: {chain_id}."
        )

    # Creates the contract object
    lending_pool = contract_from_artifact(
        w3,
        "LendingPool",
        setup["contracts"]["LendingPool"],
    )

    # Loads the account
    auto_contributor = account_from_setup(setup, "autoContributor")

    print("Connected to private chain")
    print(f"RPC URL:      {rpc_url}")
    print(f"Chain ID:     {actual_chain_id}")
    print(f"LendingPool:  {lending_pool.address}")
    print(f"Strategy:     vote APPROVE for every new LoanProposalSubmitted event")
    print(f"Account:      autoContributor {auto_contributor.address}")

    print_account_balance(w3, "autoContributor", auto_contributor.address)

    deposit_amount_wei = int(Web3.to_wei(Decimal(args.deposit_eth), "ether"))

    # Optionally deposits
    ensure_auto_contributor_deposited(
        w3=w3,
        chain_id=chain_id,
        lending_pool=lending_pool,
        auto_contributor=auto_contributor,
        deposit_amount_wei=deposit_amount_wei,
        auto_deposit=not args.no_auto_deposit,
    )

    # Determines the block from which to start searching for events
    next_from_block = normalize_from_block(w3, args.from_block) # normalize_from_block transforms the block number into a concrete value
    processed_proposals: set[int] = set()

    print("\nMonitoring started.")
    print(f"Starting from block: {next_from_block}")
    print("Press Ctrl+C to stop.")

    # Checks for new events every poll_interval seconds
    try:
        while True:
            latest_block = w3.eth.block_number

            next_from_block = scan_and_vote(
                w3=w3,
                chain_id=chain_id,
                lending_pool=lending_pool,
                auto_contributor=auto_contributor,
                from_block=next_from_block,
                to_block=latest_block,
                processed_proposals=processed_proposals,
            )

            if args.once:
                print("\nOne-shot scan completed.")
                break

            time.sleep(args.poll_interval)

    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")


if __name__ == "__main__":
    main()
