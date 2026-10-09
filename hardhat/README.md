# P2P Lending Service with Bitcoin Collateral

This project implements a decentralized lending service on a local Ethereum private chain. Contributors deposit ETH into a common `LendingPool`; applicants request loans by providing a Bitcoin address as off-chain liquidity proof; an off-chain oracle reads BTC balances and updates the on-chain `BitcoinOracle`.

The project includes successful and failed-loan demos, oracle fee support, Bitcoin `blk*.dat` parsing with JSON fallback, an upgradable `LendingPool` through a proxy, dedicated `Loan` contracts, partial repayments, repayments after failure, overpayments, contributor compensation, gas analysis, modular Hardhat tests, and a reentrancy attack demonstration on an intentionally vulnerable contract.

---

## Project structure

```text
p2p-lending/
├── project2526genesis.json
├── 0xd278d247A52C550508ea2b2C9321d816238fb523psw.txt
├── chaindata_test
│ 	└── blk00000.dat
├── data/
│   ├── btc_balances.json
│   └── keystore/
│       └── UTC--2026-05-05T14-09-10...
└── hardhat/
    ├── contracts/
    │   ├── BitcoinOracle.sol
    │   ├── LendingPool.sol
    │   ├── Loan.sol
    │   ├── LoanFactory.sol
    │   ├── OwnedUpgradeProxy.sol
    │   ├── attacks/ReentrancyAttacker.sol
    │   └── vulnerable/VulnerableLendingPool.sol
    ├── oracle/oracle_service.py
    ├── scripts/
    │   ├── initial_setup.py
    │   ├── example_operations.py
    │   ├── compensation_demo.py
    │   ├── auto_approve_contributor.py
    │   └── gasAnalysis.ts
    ├── test/
    │   ├── helpers/fixtures.ts
    │   ├── BitcoinOracle.test.ts
    │   ├── Integration.successful-flow.test.ts
    │   ├── LendingPool.access-control.test.ts
    │   ├── LendingPool.collateral.test.ts
    │   ├── LendingPool.deposits.test.ts
    │   ├── LendingPool.proposals.test.ts
    │   ├── LendingPool.upgradability.test.ts
    │   ├── LendingPool.withdrawals.test.ts
    │   ├── Loan.failure-compensation.test.ts
    │   ├── Loan.repayment.test.ts
    │   └── ReentrancyAttack.test.ts
    ├── deployments/
    ├── hardhat.config.ts
    └── package.json
```

---

## Requirements

Install:

```text
Node.js + npm
Python 3
Geth
```

Install Node dependencies from `hardhat/`:

```bash
cd hardhat
npm install
```

Install Python dependencies:

```bash
python3 -m pip install web3 eth-account python-bitcoinlib
```

`python-bitcoinlib` is required for the optional Bitcoin `blk*.dat` oracle mode.

---

## Verify required files

From the project root:

```bash
ls -l project2526genesis.json
ls -l data/keystore/
ls -l 0xd278d247A52C550508ea2b2C9321d816238fb523psw.txt
ls -l data/btc_balances.json
```

`data/btc_balances.json` is used by the oracle fallback mode. Values are expressed in satoshi. Example:

```json
{
  "btc-example-address-1": 100000000,
  "btc-address-rich": 250000000,
  "btc-address-poor": 1
}
```

---

## Start the private chain

From the project root, initialize the chain:

```bash
geth --datadir data init project2526genesis.json
```

Start Geth:

```bash
geth \
  --datadir data \
  --networkid 202526 \
  --http \
  --http.addr 127.0.0.1 \
  --http.port 8545 \
  --http.api eth,net,web3,personal,miner,clique \
  --allow-insecure-unlock \
  --unlock 0xd278d247A52C550508ea2b2C9321d816238fb523 \
  --password 0xd278d247A52C550508ea2b2C9321d816238fb523psw.txt \
  --mine \
  --miner.etherbase 0xd278d247A52C550508ea2b2C9321d816238fb523 \
  --nodiscover
```

Leave this terminal running. The expected RPC endpoint is `http://127.0.0.1:8545`; the expected chain id is `202526`.

---

## Compile contracts

From `hardhat/`:

```bash
npx hardhat clean
npx hardhat compile
```

---

## Initial setup

`initial_setup.py` creates local accounts, funds them using the provided prefunded account, deploys the contracts, links them, and writes deployment data to `deployments/initial_setup.json`.

The prefunded account is used only for ETH transfers. Contract deployment and service operations are executed by newly generated local accounts.

From `hardhat/`:

```bash
KEYSTORE=$(ls ../data/keystore/UTC--2026-05-05T14-09-10* | head -n 1)

python3 scripts/initial_setup.py \
  --keystore "$KEYSTORE" \
  --password-file ../0xd278d247A52C550508ea2b2C9321d816238fb523psw.txt
```

Expected result:

```text
Initial setup completed successfully.
Setup data saved to: deployments/initial_setup.json
```

The setup deploys `BitcoinOracle`, the `LendingPool` implementation, `OwnedUpgradeProxy`, and `LoanFactory`. The official `LendingPool` address stored in `deployments/initial_setup.json` is the proxy address. The implementation address is stored separately as `LendingPoolImplementation`.

`deployments/initial_setup.json` contains local private keys.

---

## Oracle service

The oracle service listens for `BitcoinLiquidityCheckRequested` events and updates `BitcoinOracle` using the `trustedOracle` account.

A BTC liquidity request requires the applicant to pay the minimum oracle fee. The demo scripts read `BitcoinOracle.MIN_ORACLE_FEE()` and pay it automatically.

### Fallback JSON mode

Use this mode for reproducible demos when real Bitcoin `blk*.dat` files are not available.

From `hardhat/`:

```bash
python3 oracle/oracle_service.py \
  --fallback-balances ../data/btc_balances.json \
  --from-block latest
```

If the request was emitted before starting the oracle:

```bash
python3 oracle/oracle_service.py \
  --fallback-balances ../data/btc_balances.json \
  --from-block 0
```

One-shot scan:

```bash
python3 oracle/oracle_service.py \
  --fallback-balances ../data/btc_balances.json \
  --from-block 0 \
  --once
```

### Bitcoin `blk*.dat` mode

Place Bitcoin block files in a root-level `chaindata/` directory:

```text
p2p-lending/
├── chaindata/
│   ├── blk00000.dat
│   ├── blk00001.dat
│   └── ...
└── hardhat/
```

Run:

```bash
python3 oracle/oracle_service.py \
  --chaindata ../chaindata \
  --cache ../data/btc_balances_from_blk_cache.json \
  --bitcoin-network mainnet \
  --max-blocks 131000 \
  --from-block latest
```

Force cache rebuild:

```bash
python3 oracle/oracle_service.py \
  --chaindata ../chaindata \
  --cache ../data/btc_balances_from_blk_cache.json \
  --bitcoin-network mainnet \
  --max-blocks 131000 \
  --rebuild-cache \
  --from-block latest
```

---

## Successful loan demo

Use two terminals.

Terminal 1, from `hardhat/`:

```bash
python3 oracle/oracle_service.py \
  --fallback-balances ../data/btc_balances.json \
  --from-block latest
```

Terminal 2, from `hardhat/`:

```bash
python3 scripts/example_operations.py --btc-address btc-address-rich
```

Expected final output:

```text
Example operations completed successfully.
```

The demo covers contributor deposits, paid BTC liquidity request, oracle update, loan proposal submission, voting, proposal resolution, dedicated `Loan` creation, full repayment, compensation pool funding, collateral percentage decrease, and contributor gain withdrawal.

---

## Failed loan and compensation demo

Use two terminals.

Terminal 1, from `hardhat/`:

```bash
python3 oracle/oracle_service.py \
  --fallback-balances ../data/btc_balances.json \
  --from-block latest
```

Terminal 2, from `hardhat/`:

```bash
python3 scripts/compensation_demo.py
```

Expected final output:

```text
Compensation demo completed successfully.
```

The demo covers approved loan creation, applicant non-repayment before expiration, `Loan.markFailed()`, collateral percentage increase, partial compensation from the compensation pool, and remaining locked value tracking for future compensation claims.

---

## Auto-approve contributor

`auto_approve_contributor.py` implements an automated contributor strategy that votes `approve` on new proposals.

From `hardhat/`:

```bash
python3 scripts/auto_approve_contributor.py --from-block latest
```

One-shot scan:

```bash
python3 scripts/auto_approve_contributor.py --from-block 0 --once
```

Auto-deposit before monitoring:

```bash
python3 scripts/auto_approve_contributor.py --deposit-eth 0.5
```

---

## Tests

Run all tests from `hardhat/`:

```bash
npx hardhat test
```

Run selected modules:

```bash
npx hardhat test test/BitcoinOracle.test.ts
npx hardhat test test/LendingPool.upgradability.test.ts
npx hardhat test test/LendingPool.proposals.test.ts
npx hardhat test test/Loan.repayment.test.ts
npx hardhat test test/Loan.failure-compensation.test.ts
npx hardhat test test/ReentrancyAttack.test.ts
```

The test suite covers:


- BitcoinOracle access control and oracle fee
- Deposits and withdrawals
- Proposal lifecycle and weighted voting
- BTC collateral policy
- Upgradability through OwnedUpgradeProxy
- Partial repayments and overpayments
- Repayment after failure
- Repeated compensation claims
- Successful and failed loan termination
- Successful end-to-end integration flow
- Reentrancy attack on the intentionally vulnerable pool


Expected result:

```text
40 passing
```

---

## Gas analysis

Gas analysis is implemented as a Hardhat TypeScript script.

From `hardhat/`:

```bash
npx hardhat run scripts/gasAnalysis.ts
```

Expected output:

```text
Gas analysis completed successfully.
CSV written to: gas-analysis.csv
```

Generated file:

```text
hardhat/gas-analysis.csv
```

This script is intended for the simulated Hardhat network. Run it without `--network localgeth`.

---

## Generated files

Main generated files:

```text
hardhat/deployments/initial_setup.json
hardhat/gas-analysis.csv
```

`deployments/initial_setup.json` contains local private keys.

---

## Final checklist

From `hardhat/`:

```bash
npx hardhat clean
npx hardhat compile
npx hardhat test
npx hardhat run scripts/gasAnalysis.ts
```

With Geth running:

```bash
python3 scripts/initial_setup.py \
  --keystore "$(ls ../data/keystore/UTC--2026-05-05T14-09-10* | head -n 1)" \
  --password-file ../0xd278d247A52C550508ea2b2C9321d816238fb523psw.txt
```

In separate terminals:

```bash
python3 oracle/oracle_service.py \
  --fallback-balances ../data/btc_balances.json \
  --from-block latest
```

```bash
python3 scripts/example_operations.py --btc-address btc-address-rich
```

```bash
python3 scripts/compensation_demo.py
```

