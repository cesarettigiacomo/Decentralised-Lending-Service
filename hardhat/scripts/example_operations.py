#!/usr/bin/env python3
"""
Example operations script

This script showcases a complete successful loan flow on the private chain:
1. Loads accounts and deployed contract addresses from deployments/initial_setup.json
2. Prints balances and relevant contract state
3. Contributors deposit funds into LendingPool
4. Applicant requests a Bitcoin liquidity check and submits a loan proposal
5. The script waits for oracle/oracle_service.py to update BitcoinOracle
6. Applicant submits a loan proposal
7. Contributors vote with different behaviours
8. The script advances enough blocks for the voting period to end
9. Applicant resolves the proposal
10. LoanFactory creates a Loan contract and LendingPool transfers funds to applicant
11. Applicant fully repays the loan
12. Contributors withdraw their pending gains

Requirements:
- Run scripts/initial_setup.py successfully first
- Run from the hardhat directory
- Keep the private chain running
- Install Python dependencies:
  pip install web3 eth-account

Usage:
  # Terminal 1:
  # python3 oracle/oracle_service.py --fallback-balances ../data/btc_balances.json --from-block latest
  
  # Terminal 2:
  # python3 scripts/example_operations.py

Optional:
  python3 scripts/example_operations.py --setup deployments/initial_setup.json
  python3 scripts/example_operations.py --btc-address btc-address-rich
"""

from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3


DEFAULT_SETUP_FILE = "deployments/initial_setup.json"

ARTIFACTS = {
    "BitcoinOracle": "artifacts/contracts/BitcoinOracle.sol/BitcoinOracle.json",
    "LendingPool": "artifacts/contracts/LendingPool.sol/LendingPool.json",
    "Loan": "artifacts/contracts/Loan.sol/Loan.json",
    "LoanFactory": "artifacts/contracts/LoanFactory.sol/LoanFactory.json",
}

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

BTC_ADDRESS = "btc-example-address-1"
ONE_BTC_IN_SATOSHI = 100_000_000

# Values for the demo
DEPOSIT_CONTRIBUTOR_1 = Web3.to_wei(1, "ether")
DEPOSIT_CONTRIBUTOR_2 = Web3.to_wei(0.5, "ether")
DEPOSIT_AUTO_CONTRIBUTOR = Web3.to_wei(0.5, "ether")

LOAN_AMOUNT = Web3.to_wei(1, "ether")
INTEREST_RATE = 10
LOAN_DURATION_BLOCKS = 20

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

LOAN_STATUS = {
    0: "Active",
    1: "Successful",
    2: "Failed",
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


def wei_to_eth(value: int) -> Decimal:
    return Web3.from_wei(value, "ether")


def format_wei(value: int) -> str:
    return f"{value} wei ({wei_to_eth(value)} ETH)"


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_artifact(contract_name: str) -> Dict[str, Any]:
    return load_json(Path(ARTIFACTS[contract_name]))


# Creates the Python object associated with a deployed contract
def contract_from_artifact(w3: Web3, contract_name: str, address: str) -> Any:
    artifact = load_artifact(contract_name)

    return w3.eth.contract(
        address=Web3.to_checksum_address(address),
        abi=artifact["abi"],
    )


# Takes the private key from initial_setup.json and recreates the Web3 account
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


def add_estimated_gas(w3: Web3, tx: Dict[str, Any], multiplier: float = 1.25) -> Dict[str, Any]:
    estimated = w3.eth.estimate_gas(tx)
    tx["gas"] = int(estimated * multiplier)

    return tx


def sign_send_wait(
    w3: Web3,
    account: LocalAccount,
    tx: Dict[str, Any],
    label: str,
) -> Any:
    
    # Signs the transaction locally with the private key
    signed = Account.sign_transaction(tx, account.key)

    # Sends the signed transaction to the blockchain
    tx_hash = w3.eth.send_raw_transaction(raw_signed_transaction(signed))
    
    # Waits for the transaction to be included in a block
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status != 1:
        raise RuntimeError(f"Transaction failed: {label}, tx={tx_hash.hex()}")

    print(f"{label:<42} tx={tx_hash.hex()} gasUsed={receipt.gasUsed}")

    return receipt


# Takes a smart contract function and transforms it into a signed transaction
def transact(
    w3: Web3,
    account: LocalAccount,
    chain_id: int,
    function_call: Any,
    label: str,
    value: int = 0,
) -> Any:
    nonce = w3.eth.get_transaction_count(account.address)

    # Builds the transaction to call that function
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


# Sends a zero-value transaction from the deployer to itself
# This is useful on private chains where we want to deterministically advance blocks
# The prefunded account is not used
def send_noop_transaction(
    w3: Web3,
    account: LocalAccount,
    chain_id: int,
    label: str = "advance one block",
) -> Any:
    nonce = w3.eth.get_transaction_count(account.address)

    tx = {
        "to": Web3.to_checksum_address(account.address),
        **build_common_tx(
            w3=w3,
            sender=account.address,
            nonce=nonce,
            chain_id=chain_id,
            value=0,
        ),
        "gas": 21_000,
    }

    return sign_send_wait(
        w3=w3,
        account=account,
        tx=tx,
        label=label,
    )


def print_balances(w3: Web3, title: str, addresses: Dict[str, str]) -> None:
    print(f"\n=== Balances: {title} ===")

    for name, address in addresses.items():
        balance = w3.eth.get_balance(Web3.to_checksum_address(address))
        print(f"{name:<18} {address}  {wei_to_eth(balance)} ETH")


def print_contributor_state(lending_pool: Any, label: str, address: str) -> None:
    deposited, locked, pending_gains, exists = lending_pool.functions.contributors(
        Web3.to_checksum_address(address)
    ).call()

    disposable = lending_pool.functions.disposableValue(
        Web3.to_checksum_address(address)
    ).call()

    print(f"{label:<18} exists={exists}")
    print(f"{'':<18} deposited={format_wei(deposited)}")
    print(f"{'':<18} locked={format_wei(locked)}")
    print(f"{'':<18} disposable={format_wei(disposable)}")
    print(f"{'':<18} pendingGains={format_wei(pending_gains)}")


def print_proposal_state(
    lending_pool: Any,
    proposal_id: int,
    title: str,
    contributor_addresses: Optional[Dict[str, str]] = None,
) -> None:
    print(f"\n=== Proposal state: {title} ===")

    # Reads a proposal from the contract
    proposal = lending_pool.functions.proposals(proposal_id).call()

    # Takes the fields
    applicant = proposal[0]
    amount = proposal[1]
    interest_rate = proposal[2]
    duration = proposal[3]
    btc_address = proposal[4]
    creation_block = proposal[5]
    status = proposal[6]
    loaned_amount = proposal[7]

    print(f"proposalId:     {proposal_id}")
    print(f"applicant:      {applicant}")
    print(f"amount:         {format_wei(amount)}")
    print(f"interestRate:   {interest_rate}%")
    print(f"duration:       {duration} blocks")
    print(f"btcAddress:     {btc_address}")
    print(f"creationBlock:  {creation_block}")
    print(f"status:         {PROPOSAL_STATUS.get(status, str(status))}")
    print(f"loanedAmount:   {format_wei(loaned_amount)}")

    # Also tries to read:
    try:
        proposal_repaid = lending_pool.functions.proposalRepaid(proposal_id).call()
        proposal_failed = lending_pool.functions.proposalFailed(proposal_id).call()
        repaid = lending_pool.functions.repaidByProposal(proposal_id).call()
        print(f"proposalRepaid: {proposal_repaid}")
        print(f"proposalFailed: {proposal_failed}")
        print(f"repaidAmount:   {format_wei(repaid)}")
    except Exception:
        pass

    try:
        loan_address = lending_pool.functions.loanByProposal(proposal_id).call()
        print(f"loanAddress:    {loan_address}")
    except Exception:
        pass

    if contributor_addresses:
        print("\nLocked by proposal:")
        for name, address in contributor_addresses.items():

            # Reads from the LendingPool contract the amount of that contributor's capital that has been locked for that proposal
            locked = lending_pool.functions.lockedByProposal(
                proposal_id,
                Web3.to_checksum_address(address),
            ).call()

            # Reads the vote given by that contributor for that proposal
            vote = lending_pool.functions.proposalVotes(
                proposal_id,
                Web3.to_checksum_address(address),
            ).call()

            # Prints the result (contributor name, locked funds, vote for the proposal)
            print(
                f"{name:<18} locked={format_wei(locked)} "
                f"vote={VOTE_CHOICE.get(vote, str(vote))}"
            )


def print_pool_state(
    w3: Web3,
    lending_pool: Any,
    applicant_address: str,
    contributor_addresses: Dict[str, str],
    title: str,
) -> None:
    print(f"\n=== LendingPool state: {title} ===")

    current_block = w3.eth.block_number
    pool_balance = w3.eth.get_balance(lending_pool.address)
    compensation_pool = lending_pool.functions.compensationPool().call()
    cumulative_disposable = lending_pool.functions.cumulativeDisposableValue().call()
    collateral_percentage = lending_pool.functions.collateralPercentageOf(
        Web3.to_checksum_address(applicant_address)
    ).call()

    print(f"currentBlock:          {current_block}")
    print(f"LendingPool balance:   {format_wei(pool_balance)}")
    print(f"compensationPool:      {format_wei(compensation_pool)}")
    print(f"cumulativeDisposable:  {format_wei(cumulative_disposable)}")
    print(f"applicant collateral:  {collateral_percentage}%")

    print("\nContributors:")
    for name, address in contributor_addresses.items():
        print_contributor_state(lending_pool, name, address)


def print_loan_state(loan: Any, title: str) -> None:
    print(f"\n=== Loan state: {title} ===")

    principal = loan.functions.principal().call()
    interest_rate = loan.functions.interestRate().call()
    total_due = loan.functions.totalDue().call()
    remaining_due = loan.functions.remainingDue().call()
    repaid_amount = loan.functions.repaidAmount().call()
    status = loan.functions.status().call()
    expiration_block = loan.functions.expirationBlock().call()

    print(f"loanAddress:      {loan.address}")
    print(f"principal:        {format_wei(principal)}")
    print(f"interestRate:     {interest_rate}%")
    print(f"totalDue:         {format_wei(total_due)}")
    print(f"remainingDue:     {format_wei(remaining_due)}")
    print(f"repaidAmount:     {format_wei(repaid_amount)}")
    print(f"status:           {LOAN_STATUS.get(status, str(status))}")
    print(f"expirationBlock:  {expiration_block}")


def extract_proposal_id(lending_pool: Any, receipt: Any, fallback: int = 0) -> int:
    try:
        # Tries to get the transaction receipt, which contains gas used, transaction status, and logs or events emitted during the transaction
        logs = lending_pool.events.LoanProposalSubmitted().process_receipt(receipt)

        # If there is an event of type LoanProposalSubmitted, takes the first one and reads proposalId
        if logs:
            return int(logs[0]["args"]["proposalId"])
    except Exception:
        pass # pass prevents error messages

    print(f"Could not decode proposalId from event. Falling back to {fallback}.")
    return fallback


# Advances until the end of the voting period
def advance_until_voting_period_ended(
    w3: Web3,
    chain_id: int,
    deployer: LocalAccount,
    lending_pool: Any,
    proposal_id: int,
) -> None:
    proposal = lending_pool.functions.proposals(proposal_id).call()
    creation_block = int(proposal[5])
    voting_period = int(lending_pool.functions.VOTING_PERIOD().call())
    target_block = creation_block + voting_period + 1

    print("\nAdvancing blocks until voting period ends...")
    print(f"creationBlock={creation_block}")
    print(f"VOTING_PERIOD={voting_period}")
    print(f"Need block.number > {creation_block + voting_period}")
    print(f"Target block: {target_block}")

    while w3.eth.block_number < target_block:
        send_noop_transaction(
            w3=w3,
            account=deployer,
            chain_id=chain_id,
            label="advance block",
        )

    print(f"Current block: {w3.eth.block_number}")


# Checks if there are pending gains and, if so, calls withdrawGains() from the LendingPool
def withdraw_gains_if_any(
    w3: Web3,
    chain_id: int,
    lending_pool: Any,
    account: LocalAccount,
    label: str,
) -> None:
    contributor = lending_pool.functions.contributors(account.address).call()
    pending_gains = contributor[2]

    # Case without pending gains
    if pending_gains == 0:
        print(f"{label}: no pending gains to withdraw.")
        return

    # Otherwise, calls lending_pool.functions.withdrawGains()
    transact(
        w3=w3,
        account=account,
        chain_id=chain_id,
        function_call=lending_pool.functions.withdrawGains(),
        label=f"{label}.withdrawGains",
    )



def print_oracle_state(
    bitcoin_oracle: Any,
    lending_pool: Any,
    applicant_address: str,
    btc_address: str,
) -> None:
    has_record = bitcoin_oracle.functions.hasBtcBalanceRecord(btc_address).call()

    print(f"BTC address:          {btc_address}")
    print(f"Oracle has record:    {has_record}")

    if not has_record:
        return

    balance = bitcoin_oracle.functions.btcBalanceInSatoshi(btc_address).call()
    btc_value = bitcoin_oracle.functions.btcValueInWei(btc_address).call()
    required_collateral = lending_pool.functions.requiredCollateralWei(
        Web3.to_checksum_address(applicant_address),
        LOAN_AMOUNT,
    ).call()

    print(f"BTC balance recorded: {balance} satoshi")
    print(f"BTC collateral value: {format_wei(btc_value)}")
    print(f"Required collateral:  {format_wei(required_collateral)}")


def wait_for_oracle_record(
    bitcoin_oracle: Any,
    lending_pool: Any,
    applicant_address: str,
    btc_address: str,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> None:
    print("\nWaiting for oracle_service.py to update BitcoinOracle...")
    print(f"Timeout: {timeout_seconds}s, poll interval: {poll_interval_seconds}s")

    start = time.time()

    while True:
        has_record = bitcoin_oracle.functions.hasBtcBalanceRecord(btc_address).call()

        if has_record:
            print("Oracle record found.")
            print_oracle_state(
                bitcoin_oracle=bitcoin_oracle,
                lending_pool=lending_pool,
                applicant_address=applicant_address,
                btc_address=btc_address,
            )
            return

        elapsed = time.time() - start

        if elapsed >= timeout_seconds:
            raise TimeoutError(
                "BitcoinOracle was not updated in time.\n"
                "Make sure oracle_service.py is running in another terminal, for example:\n"
                "  python3 oracle/oracle_service.py "
                "--fallback-balances ../data/btc_balances.json "
                "--from-block latest\n"
                "If you started oracle_service.py after this request was emitted, "
                "restart it with --from-block 0 or emit a new request."
            )

        print("Oracle record not available yet. Waiting...")
        time.sleep(poll_interval_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an example operations flow for the P2P lending service."
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
        "--btc-address",
        default=BTC_ADDRESS,
        help=f"BTC address used by the applicant. Default: {BTC_ADDRESS}",
    )

    parser.add_argument(
        "--oracle-timeout",
        type=int,
        default=120,
        help="Seconds to wait for oracle_service.py to update BitcoinOracle. Default: 120.",
    )

    parser.add_argument(
        "--oracle-poll-interval",
        type=int,
        default=2,
        help="Seconds between BitcoinOracle polling attempts. Default: 2.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup = load_json(Path(args.setup))
    btc_address = args.btc_address

    rpc_url = args.rpc_url or setup["network"]["rpc_url"]
    chain_id = args.chain_id or int(setup["network"]["chain_id"])

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    install_poa_middleware(w3)

    if not w3.is_connected():
        raise ConnectionError(f"Could not connect to RPC URL: {rpc_url}")

    actual_chain_id = w3.eth.chain_id

    if actual_chain_id != chain_id:
        raise RuntimeError(
            f"Unexpected chain id: {actual_chain_id}. Expected: {chain_id}."
        )

    print("Connected to private chain")
    print(f"RPC URL:  {rpc_url}")
    print(f"Chain ID: {actual_chain_id}")

    deployer = account_from_setup(setup, "deployer")
    trusted_oracle = account_from_setup(setup, "trustedOracle")
    contributor1 = account_from_setup(setup, "contributor1")
    contributor2 = account_from_setup(setup, "contributor2")
    applicant = account_from_setup(setup, "applicant")
    auto_contributor = account_from_setup(setup, "autoContributor")

    bitcoin_oracle = contract_from_artifact(
        w3,
        "BitcoinOracle",
        setup["contracts"]["BitcoinOracle"],
    )

    lending_pool = contract_from_artifact(
        w3,
        "LendingPool",
        setup["contracts"]["LendingPool"],
    )

    addresses = {
        "deployer": deployer.address,
        "trustedOracle": trusted_oracle.address,
        "contributor1": contributor1.address,
        "contributor2": contributor2.address,
        "applicant": applicant.address,
        "autoContributor": auto_contributor.address,
        "LendingPool": lending_pool.address,
    }

    contributor_addresses = {
        "contributor1": contributor1.address,
        "contributor2": contributor2.address,
        "autoContributor": auto_contributor.address,
    }

    print_balances(w3, "initial", addresses)
    print_pool_state(
        w3=w3,
        lending_pool=lending_pool,
        applicant_address=applicant.address,
        contributor_addresses=contributor_addresses,
        title="initial",
    )

    # Deposits of the contributors
    # contributor1: 1 ETH
    # contributor2: 0.5 ETH
    # autoContributor: 0.5 ETH
    # These ETH amounts go into the LendingPool
    print("\nStep 1 - Contributors deposit funds")

    transact(
        w3=w3,
        account=contributor1,
        chain_id=chain_id,
        function_call=lending_pool.functions.deposit(),
        value=DEPOSIT_CONTRIBUTOR_1,
        label="contributor1.deposit",
    )

    transact(
        w3=w3,
        account=contributor2,
        chain_id=chain_id,
        function_call=lending_pool.functions.deposit(),
        value=DEPOSIT_CONTRIBUTOR_2,
        label="contributor2.deposit",
    )

    transact(
        w3=w3,
        account=auto_contributor,
        chain_id=chain_id,
        function_call=lending_pool.functions.deposit(),
        value=DEPOSIT_AUTO_CONTRIBUTOR,
        label="autoContributor.deposit",
    )

    print_balances(w3, "after deposits", addresses)
    print_pool_state(
        w3=w3,
        lending_pool=lending_pool,
        applicant_address=applicant.address,
        contributor_addresses=contributor_addresses,
        title="after deposits",
    )

    # Applicant requests a BTC liquidity check
    print("\nStep 2 - Applicant requests BTC liquidity check")

    print("Oracle state before request:")
    print_oracle_state(
        bitcoin_oracle=bitcoin_oracle,
        lending_pool=lending_pool,
        applicant_address=applicant.address,
        btc_address=btc_address,
    )

    oracle_fee = bitcoin_oracle.functions.MIN_ORACLE_FEE().call()

    transact(
        w3=w3,
        account=applicant,
        chain_id=chain_id,
        function_call=lending_pool.functions.requestBitcoinLiquidityCheck(btc_address),
        value=oracle_fee,
        label="applicant.requestBitcoinLiquidityCheck",
    )

    print_balances(w3, "after BTC liquidity check request", addresses)

    # Waits for the update of the BitcoinOracle contract
    print("\nStep 3 - Wait for off-chain oracle service")

    wait_for_oracle_record(
        bitcoin_oracle=bitcoin_oracle,
        lending_pool=lending_pool,
        applicant_address=applicant.address,
        btc_address=btc_address,
        timeout_seconds=args.oracle_timeout,
        poll_interval_seconds=args.oracle_poll_interval,
    )

    print_balances(w3, "after oracle service update", addresses)

    print("\nStep 4 - Applicant submits loan proposal")

    # Loan proposal:
    # Loan amount: 1 ETH
    # Interest rate: 10%
    # Duration: 20 blocks
    # Collateral: BTC address
    submit_receipt = transact(
        w3=w3,
        account=applicant,
        chain_id=chain_id,
        function_call=lending_pool.functions.submitLoanProposal(
            LOAN_AMOUNT,
            INTEREST_RATE,
            LOAN_DURATION_BLOCKS,
            btc_address,
        ),
        label="applicant.submitLoanProposal",
    )

    # Extracts the proposalId from the event LoanProposalSubmitted
    # In the function, if it cannot extract it, it uses fallback=0
    proposal_id = extract_proposal_id(lending_pool, submit_receipt)

    print_balances(w3, "after proposal submission", addresses)
    print_proposal_state(
        lending_pool=lending_pool,
        proposal_id=proposal_id,
        title="after submission",
        contributor_addresses=contributor_addresses,
    )

    # Contributors vote
    # contributor1: Approve
    # contributor2: Reject
    # autoContributor: Approve
    print("\nStep 5 - Contributors vote with different behaviours")

    transact(
        w3=w3,
        account=contributor1,
        chain_id=chain_id,
        function_call=lending_pool.functions.vote(proposal_id, True),
        label="contributor1.voteApprove",
    )

    transact(
        w3=w3,
        account=contributor2,
        chain_id=chain_id,
        function_call=lending_pool.functions.vote(proposal_id, False),
        label="contributor2.voteReject",
    )

    transact(
        w3=w3,
        account=auto_contributor,
        chain_id=chain_id,
        function_call=lending_pool.functions.vote(proposal_id, True),
        label="autoContributor.voteApprove",
    )

    print_balances(w3, "after votes", addresses)
    print_proposal_state(
        lending_pool=lending_pool,
        proposal_id=proposal_id,
        title="after votes",
        contributor_addresses=contributor_addresses,
    )

    # Advances until the end of the voting period
    print("\nStep 6 - End voting period")

    advance_until_voting_period_ended(
        w3=w3,
        chain_id=chain_id,
        deployer=deployer,
        lending_pool=lending_pool,
        proposal_id=proposal_id,
    )

    print_balances(w3, "after block advancement", addresses)

    # Resolves the proposal
    print("\nStep 7 - Applicant resolves proposal")

    transact(
        w3=w3,
        account=applicant,
        chain_id=chain_id,
        function_call=lending_pool.functions.resolveProposal(proposal_id),
        label="applicant.resolveProposal",
    )

    # Reads the address; if it is not equal to 0, that means that a contract has been created
    loan_address = lending_pool.functions.loanByProposal(proposal_id).call()

    if loan_address == ZERO_ADDRESS:
        raise RuntimeError("Proposal was not approved or Loan contract was not created.")

    # If the Loan exists, the script loads it
    loan = contract_from_artifact(w3, "Loan", loan_address)

    addresses["Loan"] = loan.address

    print_balances(w3, "after proposal resolution and loan transfer", addresses)
    print_pool_state(
        w3=w3,
        lending_pool=lending_pool,
        applicant_address=applicant.address,
        contributor_addresses=contributor_addresses,
        title="after proposal resolution",
    )
    print_proposal_state(
        lending_pool=lending_pool,
        proposal_id=proposal_id,
        title="after proposal resolution",
        contributor_addresses=contributor_addresses,
    )
    print_loan_state(loan, "after creation")

    # Applicant fully repays the loan
    print("\nStep 8 - Applicant fully repays loan")

    # Amount to repay
    total_due = loan.functions.totalDue().call()

    transact(
        w3=w3,
        account=applicant,
        chain_id=chain_id,
        function_call=loan.functions.repay(),
        value=total_due,
        label="applicant.repayFullLoan",
    )

    print_balances(w3, "after full repayment", addresses)
    print_pool_state(
        w3=w3,
        lending_pool=lending_pool,
        applicant_address=applicant.address,
        contributor_addresses=contributor_addresses,
        title="after full repayment",
    )
    print_proposal_state(
        lending_pool=lending_pool,
        proposal_id=proposal_id,
        title="after full repayment",
        contributor_addresses=contributor_addresses,
    )
    print_loan_state(loan, "after full repayment")

    # Contributors withdraw pending gains
    print("\nStep 9 - Contributors withdraw pending gains")

    withdraw_gains_if_any(
        w3=w3,
        chain_id=chain_id,
        lending_pool=lending_pool,
        account=contributor1,
        label="contributor1",
    )

    withdraw_gains_if_any(
        w3=w3,
        chain_id=chain_id,
        lending_pool=lending_pool,
        account=contributor2,
        label="contributor2",
    )

    withdraw_gains_if_any(
        w3=w3,
        chain_id=chain_id,
        lending_pool=lending_pool,
        account=auto_contributor,
        label="autoContributor",
    )

    print_balances(w3, "after withdrawing gains", addresses)
    print_pool_state(
        w3=w3,
        lending_pool=lending_pool,
        applicant_address=applicant.address,
        contributor_addresses=contributor_addresses,
        title="after withdrawing gains",
    )
    print_proposal_state(
        lending_pool=lending_pool,
        proposal_id=proposal_id,
        title="final",
        contributor_addresses=contributor_addresses,
    )
    print_loan_state(loan, "final")

    print("\nExample operations completed successfully.")


if __name__ == "__main__":
    main()
