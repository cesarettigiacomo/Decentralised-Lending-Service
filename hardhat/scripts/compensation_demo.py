#!/usr/bin/env python3
"""
Failed-loan and compensation demo

This script demonstrates the non-happy-path flow:
1. contributors deposit ETH
2. applicant has a BTC balance recorded by BitcoinOracle
3. applicant submits a loan proposal
4. contributors vote
5. proposal is resolved and a Loan is created
6. applicant does NOT repay
7. blocks are advanced until the loan expires
8. Loan.markFailed() is called
9. contributors try to claim compensation from LendingPool

Run from hardhat/.

Recommended terminal layout:

Terminal 1:
  python3 oracle/oracle_service.py \
    --fallback-balances ../data/btc_balances.json \
    --from-block latest

Terminal 2:
  python3 scripts/compensation_demo.py

Notes:
- If the compensation pool is empty, this script automatically runs a small
  successful warm-up loan first, so that the compensation pool receives part
  of the interest
- If BitcoinOracle already has a record for the selected BTC address, the
  script continues without waiting for oracle_service.py
"""

from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3


DEFAULT_SETUP_FILE = "deployments/initial_setup.json"

ARTIFACTS = {
    "BitcoinOracle": "artifacts/contracts/BitcoinOracle.sol/BitcoinOracle.json",
    "LendingPool": "artifacts/contracts/LendingPool.sol/LendingPool.json",
    "Loan": "artifacts/contracts/Loan.sol/Loan.json",
}

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

DEFAULT_BTC_ADDRESS = "btc-address-rich"

DEPOSIT_CONTRIBUTOR_1 = Web3.to_wei(1, "ether")
DEPOSIT_CONTRIBUTOR_2 = Web3.to_wei(0.5, "ether")
DEPOSIT_AUTO_CONTRIBUTOR = Web3.to_wei(0.5, "ether")

LOAN_AMOUNT = Web3.to_wei(1, "ether")
INTEREST_RATE = 10
LOAN_DURATION_BLOCKS = 20

MIN_COMPENSATION_POOL = Web3.to_wei(0.01, "ether")

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


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

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

    return json.loads(path.read_text(encoding="utf-8"))


def contract_from_artifact(w3: Web3, contract_name: str, address: str) -> Any:
    artifact = load_json(Path(ARTIFACTS[contract_name]))

    return w3.eth.contract(
        address=Web3.to_checksum_address(address),
        abi=artifact["abi"],
    )


def account_from_setup(setup: Dict[str, Any], name: str) -> LocalAccount:
    return Account.from_key(setup["accounts"][name]["private_key"])


def raw_signed_transaction(signed: Any) -> bytes:
    return getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction")


def build_tx_base(
    w3: Web3,
    account: LocalAccount,
    chain_id: int,
    value: int = 0,
) -> Dict[str, Any]:
    return {
        "from": Web3.to_checksum_address(account.address),
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": chain_id,
        "gasPrice": w3.eth.gas_price,
        "value": value,
    }


def send_tx(
    w3: Web3,
    chain_id: int,
    account: LocalAccount,
    function_call: Any,
    label: str,
    value: int = 0,
) -> Any:
    tx = function_call.build_transaction(
        build_tx_base(
            w3=w3,
            account=account,
            chain_id=chain_id,
            value=value,
        )
    )

    # Estimates gas
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.25)

    signed = Account.sign_transaction(tx, account.key)
    tx_hash = w3.eth.send_raw_transaction(raw_signed_transaction(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status != 1:
        raise RuntimeError(f"{label} failed: tx={tx_hash.hex()}")

    # Prints the hash and gas used
    print(f"{label:<42} tx={tx_hash.hex()} gasUsed={receipt.gasUsed}")
    return receipt


# Sends a transaction to itself only to advance blocks
def send_noop(
    w3: Web3,
    chain_id: int,
    account: LocalAccount,
    label: str = "advance block",
) -> Any:
    tx = {
        **build_tx_base(
            w3=w3,
            account=account,
            chain_id=chain_id,
        ),
        "to": Web3.to_checksum_address(account.address),
        "gas": 21_000,
    }

    signed = Account.sign_transaction(tx, account.key)
    tx_hash = w3.eth.send_raw_transaction(raw_signed_transaction(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status != 1:
        raise RuntimeError(f"{label} failed: tx={tx_hash.hex()}")

    print(f"{label:<42} tx={tx_hash.hex()} gasUsed={receipt.gasUsed}")
    return receipt


# ---------------------------------------------------------------------------
# Printing helpers
# ---------------------------------------------------------------------------

def print_balances(w3: Web3, title: str, addresses: Dict[str, str]) -> None:
    print(f"\n=== Balances: {title} ===")

    for name, address in addresses.items():
        balance = w3.eth.get_balance(Web3.to_checksum_address(address))
        print(f"{name:<18} {address}  {wei_to_eth(balance)} ETH")


def print_contributor(lending_pool: Any, name: str, address: str) -> None:
    deposited, locked, pending_gains, exists = lending_pool.functions.contributors(
        Web3.to_checksum_address(address)
    ).call()

    disposable = lending_pool.functions.disposableValue(
        Web3.to_checksum_address(address)
    ).call()

    print(f"{name:<18} exists={exists}")
    print(f"{'':<18} deposited={format_wei(deposited)}")
    print(f"{'':<18} locked={format_wei(locked)}")
    print(f"{'':<18} disposable={format_wei(disposable)}")
    print(f"{'':<18} pendingGains={format_wei(pending_gains)}")


def print_pool_state(
    w3: Web3,
    lending_pool: Any,
    applicant_address: str,
    contributor_addresses: Dict[str, str],
    title: str,
) -> None:
    print(f"\n=== LendingPool state: {title} ===")

    compensation_pool = lending_pool.functions.compensationPool().call()
    cumulative_disposable = lending_pool.functions.cumulativeDisposableValue().call()
    collateral_percentage = lending_pool.functions.collateralPercentageOf(
        Web3.to_checksum_address(applicant_address)
    ).call()

    print(f"currentBlock:          {w3.eth.block_number}")
    print(f"LendingPool balance:   {format_wei(w3.eth.get_balance(lending_pool.address))}")
    print(f"compensationPool:      {format_wei(compensation_pool)}")
    print(f"cumulativeDisposable:  {format_wei(cumulative_disposable)}")
    print(f"applicant collateral:  {collateral_percentage}%")

    print("\nContributors:")
    for name, address in contributor_addresses.items():
        print_contributor(lending_pool, name, address)


def print_proposal_state(
    lending_pool: Any,
    proposal_id: int,
    title: str,
    contributor_addresses: Optional[Dict[str, str]] = None,
) -> None:
    print(f"\n=== Proposal state: {title} ===")

    proposal = lending_pool.functions.proposals(proposal_id).call()

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

    try:
        print(f"proposalRepaid: {lending_pool.functions.proposalRepaid(proposal_id).call()}")
        print(f"proposalFailed: {lending_pool.functions.proposalFailed(proposal_id).call()}")
        print(f"repaidAmount:   {format_wei(lending_pool.functions.repaidByProposal(proposal_id).call())}")
        print(f"loanAddress:    {lending_pool.functions.loanByProposal(proposal_id).call()}")
    except Exception:
        pass

    if contributor_addresses:
        print("\nLocked by proposal:")
        for name, address in contributor_addresses.items():
            locked = lending_pool.functions.lockedByProposal(
                proposal_id,
                Web3.to_checksum_address(address),
            ).call()

            vote = lending_pool.functions.proposalVotes(
                proposal_id,
                Web3.to_checksum_address(address),
            ).call()

            print(
                f"{name:<18} locked={format_wei(locked)} "
                f"vote={VOTE_CHOICE.get(vote, str(vote))}"
            )


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


# ---------------------------------------------------------------------------
# Demo logic
# ---------------------------------------------------------------------------

# Tries to extract the proposalId from the event
def extract_proposal_id(lending_pool: Any, receipt: Any, fallback: int = 0) -> int:
    try:
        logs = lending_pool.events.LoanProposalSubmitted().process_receipt(receipt)

        if logs:
            return int(logs[0]["args"]["proposalId"])
    except Exception:
        pass

    print(f"Could not decode proposalId from event. Falling back to {fallback}.")
    return fallback


# Makes the contributors deposit (even if they have already done so, if it is called more than once, deposits will increase)
def ensure_deposits(
    w3: Web3,
    chain_id: int,
    lending_pool: Any,
    contributor1: LocalAccount,
    contributor2: LocalAccount,
    auto_contributor: LocalAccount,
) -> None:
    print("\nDepositing contributor funds")

    send_tx(
        w3,
        chain_id,
        contributor1,
        lending_pool.functions.deposit(),
        "contributor1.deposit",
        value=DEPOSIT_CONTRIBUTOR_1,
    )

    send_tx(
        w3,
        chain_id,
        contributor2,
        lending_pool.functions.deposit(),
        "contributor2.deposit",
        value=DEPOSIT_CONTRIBUTOR_2,
    )

    send_tx(
        w3,
        chain_id,
        auto_contributor,
        lending_pool.functions.deposit(),
        "autoContributor.deposit",
        value=DEPOSIT_AUTO_CONTRIBUTOR,
    )


# Checks if the oracle knows the BTC balance used by the applicant
def ensure_oracle_record(
    w3: Web3,
    chain_id: int,
    lending_pool: Any,
    bitcoin_oracle: Any,
    applicant: LocalAccount,
    btc_address: str,
    loan_amount: int,
    timeout: int,
    poll_interval: int,
) -> None:
    has_record = bitcoin_oracle.functions.hasBtcBalanceRecord(btc_address).call()

    if has_record:
        balance = bitcoin_oracle.functions.btcBalanceInSatoshi(btc_address).call()
        value = bitcoin_oracle.functions.btcValueInWei(btc_address).call()
        required = lending_pool.functions.requiredCollateralWei(
            Web3.to_checksum_address(applicant.address),
            loan_amount,
        ).call()

        print("\nOracle record already available")
        print(f"BTC address:          {btc_address}")
        print(f"BTC balance recorded: {balance} satoshi")
        print(f"BTC collateral value: {format_wei(value)}")
        print(f"Required collateral:  {format_wei(required)}")
        return

    print("\nRequesting BTC liquidity check")
    oracle_fee = bitcoin_oracle.functions.MIN_ORACLE_FEE().call()

    send_tx(
        w3,
        chain_id,
        applicant,
        lending_pool.functions.requestBitcoinLiquidityCheck(btc_address),
        "applicant.requestBitcoinLiquidityCheck",
        value=oracle_fee,
    )

    print("Waiting for oracle_service.py to update BitcoinOracle...")

    started_at = time.time()

    while True:
        has_record = bitcoin_oracle.functions.hasBtcBalanceRecord(btc_address).call()

        if has_record:
            balance = bitcoin_oracle.functions.btcBalanceInSatoshi(btc_address).call()
            print(f"Oracle record found: {btc_address} -> {balance} satoshi")
            return

        if time.time() - started_at > timeout:
            raise TimeoutError(
                "BitcoinOracle was not updated in time. "
                "Make sure oracle/oracle_service.py is running."
            )

        print("Oracle record not available yet. Waiting...")
        time.sleep(poll_interval)


# Waits for the end of the voting period. It uses noop transactions
def advance_until_voting_ended(
    w3: Web3,
    chain_id: int,
    deployer: LocalAccount,
    lending_pool: Any,
    proposal_id: int,
) -> None:
    proposal = lending_pool.functions.proposals(proposal_id).call()
    creation_block = int(proposal[5])
    voting_period = int(lending_pool.functions.VOTING_PERIOD().call())
    target = creation_block + voting_period + 1

    print("\nAdvancing blocks until voting period ends")
    print(f"creationBlock={creation_block}")
    print(f"VOTING_PERIOD={voting_period}")
    print(f"target block={target}")

    while w3.eth.block_number < target:
        send_noop(w3, chain_id, deployer)

    print(f"Current block: {w3.eth.block_number}")


# Waits until the loan expires
def advance_until_loan_expired(
    w3: Web3,
    chain_id: int,
    deployer: LocalAccount,
    loan: Any,
) -> None:
    expiration_block = int(loan.functions.expirationBlock().call())
    target = expiration_block + 1

    print("\nAdvancing blocks until loan expires")
    print(f"expirationBlock={expiration_block}")
    print(f"target block={target}")

    while w3.eth.block_number < target:
        send_noop(w3, chain_id, deployer)

    print(f"Current block: {w3.eth.block_number}")


def create_approved_loan(
    w3: Web3,
    chain_id: int,
    lending_pool: Any,
    bitcoin_oracle: Any,
    deployer: LocalAccount,
    contributor1: LocalAccount,
    contributor2: LocalAccount,
    auto_contributor: LocalAccount,
    applicant: LocalAccount,
    btc_address: str,
    oracle_timeout: int,
    oracle_poll_interval: int,
    title: str,
) -> Tuple[int, Any]:
    print(f"\n========== {title} ==========")

    ensure_deposits(
        w3=w3,
        chain_id=chain_id,
        lending_pool=lending_pool,
        contributor1=contributor1,
        contributor2=contributor2,
        auto_contributor=auto_contributor,
    )

    ensure_oracle_record(
        w3=w3,
        chain_id=chain_id,
        lending_pool=lending_pool,
        bitcoin_oracle=bitcoin_oracle,
        applicant=applicant,
        btc_address=btc_address,
        loan_amount=LOAN_AMOUNT,
        timeout=oracle_timeout,
        poll_interval=oracle_poll_interval,
    )

    print("\nSubmitting loan proposal")

    receipt = send_tx(
        w3,
        chain_id,
        applicant,
        lending_pool.functions.submitLoanProposal(
            LOAN_AMOUNT,
            INTEREST_RATE,
            LOAN_DURATION_BLOCKS,
            btc_address,
        ),
        "applicant.submitLoanProposal",
    )

    # Extracts proposal id
    proposal_id = extract_proposal_id(lending_pool, receipt)

    print("\nVoting on proposal")

    # Contributors vote
    # contributor 1: approve
    # contributor 2: reject
    # autoContributor: approve
    send_tx(
        w3,
        chain_id,
        contributor1,
        lending_pool.functions.vote(proposal_id, True),
        "contributor1.voteApprove",
    )

    send_tx(
        w3,
        chain_id,
        contributor2,
        lending_pool.functions.vote(proposal_id, False),
        "contributor2.voteReject",
    )

    send_tx(
        w3,
        chain_id,
        auto_contributor,
        lending_pool.functions.vote(proposal_id, True),
        "autoContributor.voteApprove",
    )

    advance_until_voting_ended(w3, chain_id, deployer, lending_pool, proposal_id)

    print("\nResolving proposal")

    send_tx(
        w3,
        chain_id,
        applicant,
        lending_pool.functions.resolveProposal(proposal_id),
        "applicant.resolveProposal",
    )

    # Reads the address of the Loan contract
    loan_address = lending_pool.functions.loanByProposal(proposal_id).call()

    # If the Loan has not been created, raises an error
    if loan_address == ZERO_ADDRESS:
        raise RuntimeError("Loan was not created. Proposal was probably rejected.")

    loan = contract_from_artifact(w3, "Loan", loan_address)

    return proposal_id, loan


# Creates an approved loan, then makes the applicant repay it to finance the compensation pool
def run_warmup_successful_loan(
    w3: Web3,
    chain_id: int,
    lending_pool: Any,
    bitcoin_oracle: Any,
    deployer: LocalAccount,
    contributor1: LocalAccount,
    contributor2: LocalAccount,
    auto_contributor: LocalAccount,
    applicant: LocalAccount,
    btc_address: str,
    oracle_timeout: int,
    oracle_poll_interval: int,
) -> None:
    proposal_id, loan = create_approved_loan(
        w3=w3,
        chain_id=chain_id,
        lending_pool=lending_pool,
        bitcoin_oracle=bitcoin_oracle,
        deployer=deployer,
        contributor1=contributor1,
        contributor2=contributor2,
        auto_contributor=auto_contributor,
        applicant=applicant,
        btc_address=btc_address,
        oracle_timeout=oracle_timeout,
        oracle_poll_interval=oracle_poll_interval,
        title="Warm-up successful loan to fund compensationPool",
    )

    print("\nRepaying warm-up loan")

    total_due = loan.functions.totalDue().call()

    send_tx(
        w3,
        chain_id,
        applicant,
        loan.functions.repay(),
        "applicant.repayWarmupLoan",
        value=total_due,
    )

    print_proposal_state(lending_pool, proposal_id, "warm-up final")
    print_loan_state(loan, "warm-up final")


# Called for each contributor after the loan has failed
def try_claim_compensation(
    w3: Web3,
    chain_id: int,
    lending_pool: Any,
    proposal_id: int,
    account: LocalAccount,
    label: str,
) -> None:
    compensation_pool = lending_pool.functions.compensationPool().call()

    # If the pool is empty, skips the claim
    if compensation_pool == 0:
        print(f"{label}: compensationPool is empty, skipping claim.")
        return

    # Reads information from the LendingPool about the contributor identified by account.address
    deposited_before, locked_before, _, _ = lending_pool.functions.contributors(
        account.address
    ).call()

    print(f"\n{label} claims compensation")
    print(f"Before: deposited={format_wei(deposited_before)}, locked={format_wei(locked_before)}")
    print(f"Compensation pool before: {format_wei(compensation_pool)}")

    try:
        send_tx(
            w3,
            chain_id,
            account,
            lending_pool.functions.claimCompensation(proposal_id),
            f"{label}.claimCompensation",
        )
    except Exception as exc:
        print(f"{label}: compensation claim failed/skipped: {exc}")
        return

    deposited_after, locked_after, _, _ = lending_pool.functions.contributors(
        account.address
    ).call()

    compensation_pool_after = lending_pool.functions.compensationPool().call()

    print(f"After:  deposited={format_wei(deposited_after)}, locked={format_wei(locked_after)}")
    print(f"Compensation pool after: {format_wei(compensation_pool_after)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a failed-loan and compensation demo."
    )

    parser.add_argument(
        "--setup",
        default=DEFAULT_SETUP_FILE,
        help=f"Path to setup file. Default: {DEFAULT_SETUP_FILE}",
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
        default=DEFAULT_BTC_ADDRESS,
        help=f"BTC address used by applicant. Default: {DEFAULT_BTC_ADDRESS}",
    )

    parser.add_argument(
        "--oracle-timeout",
        type=int,
        default=120,
        help="Seconds to wait for oracle_service.py. Default: 120.",
    )

    parser.add_argument(
        "--oracle-poll-interval",
        type=int,
        default=2,
        help="Seconds between oracle polling attempts. Default: 2.",
    )

    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="Do not create a successful loan first, even if compensationPool is low.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Reads the configuration and connects to the chain
    setup = load_json(Path(args.setup))

    rpc_url = args.rpc_url or setup["network"]["rpc_url"]
    chain_id = args.chain_id or int(setup["network"]["chain_id"])

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    install_poa_middleware(w3)

    if not w3.is_connected():
        raise ConnectionError(f"Could not connect to RPC URL: {rpc_url}")

    if w3.eth.chain_id != chain_id:
        raise RuntimeError(f"Unexpected chain id: {w3.eth.chain_id}. Expected: {chain_id}")

    print("Connected to private chain")
    print(f"RPC URL:  {rpc_url}")
    print(f"Chain ID: {chain_id}")

    # Loads the accounts
    deployer = account_from_setup(setup, "deployer")
    trusted_oracle = account_from_setup(setup, "trustedOracle")
    contributor1 = account_from_setup(setup, "contributor1")
    contributor2 = account_from_setup(setup, "contributor2")
    auto_contributor = account_from_setup(setup, "autoContributor")
    applicant = account_from_setup(setup, "applicant")

    # Loads the contracts
    lending_pool = contract_from_artifact(
        w3,
        "LendingPool",
        setup["contracts"]["LendingPool"],
    )

    bitcoin_oracle = contract_from_artifact(
        w3,
        "BitcoinOracle",
        setup["contracts"]["BitcoinOracle"],
    )

    addresses = {
        "deployer": deployer.address,
        "trustedOracle": trusted_oracle.address,
        "contributor1": contributor1.address,
        "contributor2": contributor2.address,
        "autoContributor": auto_contributor.address,
        "applicant": applicant.address,
        "LendingPool": lending_pool.address,
    }

    contributor_addresses = {
        "contributor1": contributor1.address,
        "contributor2": contributor2.address,
        "autoContributor": auto_contributor.address,
    }

    # Prints ETH balances, LendingPool state, and contributors' state
    print_balances(w3, "initial", addresses)
    print_pool_state(w3, lending_pool, applicant.address, contributor_addresses, "initial")

    compensation_pool = lending_pool.functions.compensationPool().call()

    # Checks if the compensation pool has enough funds
    if compensation_pool < MIN_COMPENSATION_POOL and not args.no_warmup:
        print(
            "\nCompensation pool is low. "
            "Running a successful warm-up loan to fund it."
        )

        run_warmup_successful_loan(
            w3=w3,
            chain_id=chain_id,
            lending_pool=lending_pool,
            bitcoin_oracle=bitcoin_oracle,
            deployer=deployer,
            contributor1=contributor1,
            contributor2=contributor2,
            auto_contributor=auto_contributor,
            applicant=applicant,
            btc_address=args.btc_address,
            oracle_timeout=args.oracle_timeout,
            oracle_poll_interval=args.oracle_poll_interval,
        )

        print_pool_state(w3, lending_pool, applicant.address, contributor_addresses, "after warm-up")
    else:
        print("\nCompensation pool already funded enough for the demo.")

    collateral_before = lending_pool.functions.collateralPercentageOf(applicant.address).call()

    # Creates a loan that will fail
    proposal_id, failed_loan = create_approved_loan(
        w3=w3,
        chain_id=chain_id,
        lending_pool=lending_pool,
        bitcoin_oracle=bitcoin_oracle,
        deployer=deployer,
        contributor1=contributor1,
        contributor2=contributor2,
        auto_contributor=auto_contributor,
        applicant=applicant,
        btc_address=args.btc_address,
        oracle_timeout=args.oracle_timeout,
        oracle_poll_interval=args.oracle_poll_interval,
        title="Failed loan scenario",
    )

    addresses["FailedLoan"] = failed_loan.address

    print_balances(w3, "after failed-loan creation", addresses)
    print_pool_state(w3, lending_pool, applicant.address, contributor_addresses, "after failed-loan creation")
    print_proposal_state(lending_pool, proposal_id, "after failed-loan creation", contributor_addresses)
    print_loan_state(failed_loan, "after creation")

    # repay() is not called
    print("\nApplicant intentionally does NOT repay the loan.")

    # Advances until the loan expires
    advance_until_loan_expired(w3, chain_id, deployer, failed_loan)

    print("\nMarking loan as failed")

    send_tx(
        w3,
        chain_id,
        contributor1,
        failed_loan.functions.markFailed(),
        "loan.markFailed",
    )

    # Checks if the collateral required from the applicant has changed
    collateral_after_failure = lending_pool.functions.collateralPercentageOf(applicant.address).call()

    print("\nCollateral percentage change")
    print(f"Before failure: {collateral_before}%")
    print(f"After failure:  {collateral_after_failure}%")

    print_balances(w3, "after markFailed", addresses)
    print_pool_state(w3, lending_pool, applicant.address, contributor_addresses, "after markFailed")
    print_proposal_state(lending_pool, proposal_id, "after markFailed", contributor_addresses)
    print_loan_state(failed_loan, "after markFailed")

    print("\nClaiming compensation")

    # Tries to claim compensation for contributors
    try_claim_compensation(
        w3,
        chain_id,
        lending_pool,
        proposal_id,
        contributor1,
        "contributor1",
    )

    try_claim_compensation(
        w3,
        chain_id,
        lending_pool,
        proposal_id,
        contributor2,
        "contributor2",
    )

    try_claim_compensation(
        w3,
        chain_id,
        lending_pool,
        proposal_id,
        auto_contributor,
        "autoContributor",
    )

    print_balances(w3, "final", addresses)
    print_pool_state(w3, lending_pool, applicant.address, contributor_addresses, "final")
    print_proposal_state(lending_pool, proposal_id, "final", contributor_addresses)
    print_loan_state(failed_loan, "final")

    print("\nCompensation demo completed successfully.")


if __name__ == "__main__":
    main()
