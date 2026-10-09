#!/usr/bin/env python3
"""
Initial setup script

What it does:
1. Connects to the local private chain
2. Decrypts the prefunded account from the provided keystore
3. Creates fresh accounts for:
   - deployer
   - trustedOracle
   - contributor1
   - contributor2
   - applicant
   - autoContributor
4. Uses the prefunded account ONLY to transfer value to those new accounts
5. Deploys:
   - BitcoinOracle
   - LendingPool
   - LoanFactory
6. Links the deployed contracts
7. Saves account/contract addresses and private keys to deployments/initial_setup.json

Requirements:
- Run `npx hardhat compile` first
- Install Python dependencies:
  pip install web3 eth-account
- Start the private chain based on the provided genesis file before running this script
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3


DEFAULT_RPC_URL = "http://127.0.0.1:8545"
DEFAULT_CHAIN_ID = 202526

DEFAULT_KEYSTORE = (
    "UTC--2026-05-05T14-09-10.723312492Z--"
    "d278d247a52c550508ea2b2c9321d816238fb523"
)
DEFAULT_PASSWORD_FILE = "0xd278d247A52C550508ea2b2C9321d816238fb523psw.txt"

ARTIFACTS = {
    "BitcoinOracle": "artifacts/contracts/BitcoinOracle.sol/BitcoinOracle.json",
    "LendingPool": "artifacts/contracts/LendingPool.sol/LendingPool.json",
    "LoanFactory": "artifacts/contracts/LoanFactory.sol/LoanFactory.json",
    "OwnedUpgradeProxy": "artifacts/contracts/OwnedUpgradeProxy.sol/OwnedUpgradeProxy.json",
}

OUTPUT_FILE = "deployments/initial_setup.json"


def to_wei(eth_amount: str) -> int:
    return Web3.to_wei(eth_amount, "ether")


FUNDING_PLAN = {
    "deployer": to_wei("20"),
    "trustedOracle": to_wei("5"),
    "contributor1": to_wei("5"),
    "contributor2": to_wei("5"),
    "applicant": to_wei("5"),
    "autoContributor": to_wei("5"),
}


def load_artifact(contract_name: str) -> Dict[str, Any]:
    artifact_path = Path(ARTIFACTS[contract_name])

    if not artifact_path.exists():
        raise FileNotFoundError(
            f"Missing artifact for {contract_name}: {artifact_path}\n"
            "Run `npx hardhat compile` before executing this script."
        )

    with artifact_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_password(password_file: Path) -> str:
    if not password_file.exists():
        raise FileNotFoundError(f"Password file not found: {password_file}")

    return password_file.read_text(encoding="utf-8").strip()


# Reads the keystore and password, decrypts the private key, and returns an Ethereum account that can be used to sign transactions
def decrypt_prefunded_account(keystore_path: Path, password_file: Path) -> LocalAccount:
    if not keystore_path.exists():
        raise FileNotFoundError(f"Keystore file not found: {keystore_path}")

    password = read_password(password_file)

    with keystore_path.open("r", encoding="utf-8") as f:
        encrypted_key = json.load(f)

    private_key = Account.decrypt(encrypted_key, password)
    return Account.from_key(private_key)


def create_named_accounts() -> Dict[str, LocalAccount]:
    return {
        "deployer": Account.create("p2p-deployer"),
        "trustedOracle": Account.create("p2p-trusted-oracle"),
        "contributor1": Account.create("p2p-contributor-1"),
        "contributor2": Account.create("p2p-contributor-2"),
        "applicant": Account.create("p2p-applicant"),
        "autoContributor": Account.create("p2p-auto-contributor"),
    }


def raw_signed_transaction(signed: Any) -> bytes:
    # web3.py changed the signed transaction field name across versions. This helper keeps the script compatible with both
    return getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction")


def sign_send_wait(
    w3: Web3,
    private_key: bytes | str,
    tx: Dict[str, Any],
    label: str,
) -> Any:
    signed = Account.sign_transaction(tx, private_key) # Signs the transaction locally in the Python script, not in the Ethereum node, which does not have to know the private key
    tx_hash = w3.eth.send_raw_transaction(raw_signed_transaction(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    # receipt.status != 1 means that the transaction failed
    if receipt.status != 1:
        raise RuntimeError(f"Transaction failed: {label}, tx={tx_hash.hex()}")

    print(f"{label:<42} tx={tx_hash.hex()} gasUsed={receipt.gasUsed}")
    return receipt


def add_estimated_gas(w3: Web3, tx: Dict[str, Any], multiplier: float = 1.25) -> Dict[str, Any]:
    estimated = w3.eth.estimate_gas(tx)
    tx["gas"] = int(estimated * multiplier)
    return tx


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


def print_balance(w3: Web3, label: str, address: str) -> None:
    balance_wei = w3.eth.get_balance(Web3.to_checksum_address(address))
    balance_eth = Web3.from_wei(balance_wei, "ether")
    print(f"{label:<18} {address} balance={balance_eth} ETH")


# Funds created accounts
def fund_accounts(
    w3: Web3,
    prefunded: LocalAccount,
    new_accounts: Dict[str, LocalAccount],
    chain_id: int,
) -> None:
    print("\nFunding newly created accounts...")
    print("The prefunded account is used ONLY for value transfers.")

    nonce = w3.eth.get_transaction_count(prefunded.address)

    for name, account in new_accounts.items():
        # Takes the amount from the funding plan
        amount = FUNDING_PLAN[name]

        # Creates a transaction
        tx = {
            "to": Web3.to_checksum_address(account.address),
            **build_common_tx(
                w3=w3,
                sender=prefunded.address,
                nonce=nonce,
                chain_id=chain_id,
                value=amount,
            ),
        }

        # Estimates gas
        tx = add_estimated_gas(w3, tx, multiplier=1.10)

        # Signs the transaction with the key of the prefunded account
        sign_send_wait(
            w3=w3,
            private_key=prefunded.key,
            tx=tx,
            label=f"fund {name}",
        )

        nonce += 1
        print_balance(w3, name, account.address)


def deploy_contract(
    w3: Web3,
    contract_name: str,
    deployer: LocalAccount,
    chain_id: int,
    constructor_args: Optional[list[Any]] = None,
) -> tuple[str, Any, int]:
    constructor_args = constructor_args or []

    artifact = load_artifact(contract_name)

    # Creates a contract object
    contract = w3.eth.contract(
        abi=artifact["abi"],
        bytecode=artifact["bytecode"],
    )

    nonce = w3.eth.get_transaction_count(deployer.address)

    # Creates the deploy transaction
    tx = contract.constructor(*constructor_args).build_transaction(
        build_common_tx(
            w3=w3,
            sender=deployer.address,
            nonce=nonce,
            chain_id=chain_id,
        )
    )

    # Estimates gas
    tx = add_estimated_gas(w3, tx)

    # Signs and sends the transaction
    receipt = sign_send_wait(
        w3=w3,
        private_key=deployer.key,
        tx=tx,
        label=f"deploy {contract_name}",
    )

    deployed_address = receipt.contractAddress
    instance = w3.eth.contract(
        address=Web3.to_checksum_address(deployed_address),
        abi=artifact["abi"],
    )

    print(f"{contract_name} deployed at: {deployed_address}")

    # Returns the deployed address, an instance of the contract, and the gas used
    return deployed_address, instance, receipt.gasUsed


# Calls functions of the already deployed contracts
# Takes a contract function as input and creates a transaction to execute it on the blockchain
def call_contract_function(
    w3: Web3,
    sender: LocalAccount,
    chain_id: int,
    function_call: Any,
    label: str,
) -> Any:
    nonce = w3.eth.get_transaction_count(sender.address)

    tx = function_call.build_transaction(
        build_common_tx(
            w3=w3,
            sender=sender.address,
            nonce=nonce,
            chain_id=chain_id,
        )
    )

    tx = add_estimated_gas(w3, tx)

    return sign_send_wait(
        w3=w3,
        private_key=sender.key,
        tx=tx,
        label=label,
    )


def account_to_json(account: LocalAccount) -> Dict[str, str]:
    return {
        "address": account.address,
        "private_key": account.key.hex(),
    }


def save_setup(
    path: Path,
    rpc_url: str,
    chain_id: int,
    prefunded_address: str,
    accounts: Dict[str, LocalAccount],
    contracts: Dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True) # Creates the folder to contain the file

    data = {
        "network": {
            "rpc_url": rpc_url,
            "chain_id": chain_id,
        },
        "prefunded_account": {
            "address": prefunded_address,
            "note": "Used only to transfer value to newly created accounts.",
        },
        "accounts": {
            name: account_to_json(account)
            for name, account in accounts.items()
        },
        "contracts": contracts,
    }

    path.write_text(json.dumps(data, indent=2), encoding="utf-8") # Writes data in JSON format

    print(f"\nSetup data saved to: {path}")
    print("WARNING: this file contains private keys. Do not commit it.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initial setup for the P2P lending service private chain."
    )

    parser.add_argument(
        "--rpc-url",
        default=DEFAULT_RPC_URL,
        help=f"Private chain RPC URL. Default: {DEFAULT_RPC_URL}",
    )

    parser.add_argument(
        "--chain-id",
        type=int,
        default=DEFAULT_CHAIN_ID,
        help=f"Expected chain id. Default: {DEFAULT_CHAIN_ID}",
    )

    parser.add_argument(
        "--keystore",
        default=DEFAULT_KEYSTORE,
        help=f"Path to the prefunded account keystore. Default: {DEFAULT_KEYSTORE}",
    )

    parser.add_argument(
        "--password-file",
        default=DEFAULT_PASSWORD_FILE,
        help=f"Path to the prefunded account password file. Default: {DEFAULT_PASSWORD_FILE}",
    )

    parser.add_argument(
        "--output",
        default=OUTPUT_FILE,
        help=f"Where to save generated accounts and deployed addresses. Default: {OUTPUT_FILE}",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Connects to the private blockchain via RPC
    w3 = Web3(Web3.HTTPProvider(args.rpc_url))

    if not w3.is_connected():
        raise ConnectionError(f"Could not connect to RPC URL: {args.rpc_url}")

    actual_chain_id = w3.eth.chain_id

    # Checks if it's the right chain
    if actual_chain_id != args.chain_id:
        raise RuntimeError(
            f"Unexpected chain id: {actual_chain_id}. "
            f"Expected: {args.chain_id}. "
            "Are you connected to the private chain based on the provided genesis file?"
        )

    print("Connected to private chain")
    print(f"RPC URL:  {args.rpc_url}")
    print(f"Chain ID: {actual_chain_id}")

    # Decrypts prefunded account
    prefunded = decrypt_prefunded_account(
        keystore_path=Path(args.keystore),
        password_file=Path(args.password_file),
    )

    print("\nPrefunded account loaded:")
    print_balance(w3, "prefunded", prefunded.address)

    # Creates new accounts
    accounts = create_named_accounts()

    print("\nCreated new accounts:")
    for name, account in accounts.items():
        print(f"{name:<18} {account.address}")

    # Funds accounts
    fund_accounts(
        w3=w3,
        prefunded=prefunded,
        new_accounts=accounts,
        chain_id=args.chain_id,
    )

    # Stores deployer and trusted_oracle in variables
    deployer = accounts["deployer"]
    trusted_oracle = accounts["trustedOracle"]

    print("\nDeploying contracts with the newly created deployer account...")
    print("The prefunded account is NOT used for deployment or contract calls.")

    # Deploys 3 contracts
    bitcoin_oracle_address, bitcoin_oracle, _ = deploy_contract(
        w3=w3,
        contract_name="BitcoinOracle",
        deployer=deployer,
        chain_id=args.chain_id,
        constructor_args=[trusted_oracle.address],
    )

    # Deploys the LendingPool implementation and an upgradeable proxy
    # The rest of the system interacts with the proxy address through the LendingPool ABI
    lending_pool_implementation_address, lending_pool_implementation, _ = deploy_contract(
        w3=w3,
        contract_name="LendingPool",
        deployer=deployer,
        chain_id=args.chain_id,
    )

    initialize_data = lending_pool_implementation.functions.initialize(
        deployer.address
    )._encode_transaction_data()

    lending_pool_proxy_address, _, _ = deploy_contract(
        w3=w3,
        contract_name="OwnedUpgradeProxy",
        deployer=deployer,
        chain_id=args.chain_id,
        constructor_args=[
            lending_pool_implementation_address,
            deployer.address,
            initialize_data,
        ],
    )

    lending_pool_address = lending_pool_proxy_address
    lending_pool = w3.eth.contract(
        address=Web3.to_checksum_address(lending_pool_address),
        abi=load_artifact("LendingPool")["abi"],
    )

    loan_factory_address, loan_factory, _ = deploy_contract(
        w3=w3,
        contract_name="LoanFactory",
        deployer=deployer,
        chain_id=args.chain_id,
    )

    print("\nLinking contracts...")

    # Links the contracts
    call_contract_function(
        w3=w3,
        sender=deployer,
        chain_id=args.chain_id,
        function_call=lending_pool.functions.setBitcoinOracle(bitcoin_oracle_address),
        label="LendingPool.setBitcoinOracle",
    )

    call_contract_function(
        w3=w3,
        sender=deployer,
        chain_id=args.chain_id,
        function_call=loan_factory.functions.setLendingPool(lending_pool_address),
        label="LoanFactory.setLendingPool",
    )

    call_contract_function(
        w3=w3,
        sender=deployer,
        chain_id=args.chain_id,
        function_call=lending_pool.functions.setLoanFactory(loan_factory_address),
        label="LendingPool.setLoanFactory",
    )

    contracts = {
        "BitcoinOracle": bitcoin_oracle_address,
        "LendingPoolImplementation": lending_pool_implementation_address,
        "LendingPool": lending_pool_address,
        "OwnedUpgradeProxy": lending_pool_proxy_address,
        "LoanFactory": loan_factory_address,
    }

    print("\nFinal account balances:")
    print_balance(w3, "prefunded", prefunded.address)
    for name, account in accounts.items():
        print_balance(w3, name, account.address)

    print("\nDeployed contracts:")
    for name, address in contracts.items():
        print(f"{name:<18} {address}")

    # Saves setup in a JSON file
    save_setup(
        path=Path(args.output),
        rpc_url=args.rpc_url,
        chain_id=args.chain_id,
        prefunded_address=prefunded.address,
        accounts=accounts,
        contracts=contracts,
    )

    print("\nInitial setup completed successfully.")


if __name__ == "__main__":
    main()
