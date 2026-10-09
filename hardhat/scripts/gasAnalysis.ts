import hre from "hardhat";
import { writeFileSync } from "node:fs";
import { encodeFunctionData } from "viem";

const DEFAULT_BTC_ADDRESS = "btc-address-1";
const ONE_BTC_IN_SATOSHI = 100_000_000n;

// Prepares the contributor deposits. Contributor1 deposits 1_100_000 and withdraws 100_000, leaving 1_000_000
const CONTRIBUTOR_1_INITIAL_DEPOSIT = 1_100_000n;
const CONTRIBUTOR_1_WITHDRAW = 100_000n;

// Used to measure the cheaper deposit path for an already registered contributor
// It must be strictly greater than MIN_DEPOSIT, so it cannot be exactly 100_000 wei
const CONTRIBUTOR_1_EXISTING_DEPOSIT = 100_001n;
const CONTRIBUTOR_2_DEPOSIT = 200_000n;

// Parameters of the measured loans
const LIQUIDITY_REJECTED_LOAN_AMOUNT = 2_000_000n;
const VOTE_REJECTED_LOAN_AMOUNT = 100_000n;
const FIRST_LOAN_AMOUNT = 500_000n;
const FIRST_LOAN_INTEREST_RATE = 10n;
const FIRST_LOAN_DURATION = 20n;

const SECOND_LOAN_AMOUNT = 120_000n;
const SECOND_LOAN_INTEREST_RATE = 10n;
const SECOND_LOAN_DURATION = 20n;

const OVERPAYMENT_LOAN_AMOUNT = 500_000n;
const OVERPAYMENT_LOAN_INTEREST_RATE = 100n;
const OVERPAYMENT_LOAN_DURATION = 20n;
const PARTIAL_REPAYMENT_AMOUNT = 100_000n;
const OVERPAYMENT_EXTRA = 200_000n;

// Defines a type for the transaction, meaning that the string has to start with "0x"
type TxHash = `0x${string}`;

// Row for the gas table
type GasRow = {
  operation: string;
  gasUsed: bigint;
  effectiveGasPriceWei: bigint;
  txFeeWei: bigint;
  txHash: TxHash;
  contractAddress?: string;
  note?: string;
};

// Function to write values correctly into the CSV file
function csvEscape(value: string): string {
  // If one of the following characters is contained in the string, it must be enclosed in double quotes
  if (value.includes(",") || value.includes("\n") || value.includes('"')) {
    return `"${value.replaceAll('"', '""')}"`;
  }

  return value;
}

async function main() {
  // Connects the script to the Hardhat network using viem
  // networkHelpers is useful for controlling the test blockchain, for example, to mine blocks manually
  const { viem, networkHelpers } = await hre.network.connect();

  const publicClient = await viem.getPublicClient();
  const [deployer, trustedOracle, contributor1, contributor2, applicant] =
    await viem.getWalletClients();

  const gasRows: GasRow[] = [];

  // Takes the receipt of a transaction and creates a gas analysis row
  function pushGasRow(
    operation: string,
    receipt: Awaited<ReturnType<typeof publicClient.waitForTransactionReceipt>>,
    note?: string,
  ) {
    const effectiveGasPriceWei = receipt.effectiveGasPrice ?? 0n; // If receipt.effectiveGasPrice is null or undefined, uses 0n
    const txFeeWei = receipt.gasUsed * effectiveGasPriceWei; // Cost of the transaction

    // Saves the values into gasRows
    gasRows.push({
      operation,
      gasUsed: receipt.gasUsed,
      effectiveGasPriceWei,
      txFeeWei,
      txHash: receipt.transactionHash,
      contractAddress: receipt.contractAddress ?? undefined,
      note,
    });
  }

  // Measures a transaction
  async function measureTx(
    operation: string,
    run: () => Promise<TxHash>,
    note?: string,
  ) {
    // Executes the transaction
    // Takes the hash
    // Waits for the receipt
    const hash = await run();
    const receipt = await publicClient.waitForTransactionReceipt({ hash });

    // Registers gasUsed, gasPrice and txFee
    pushGasRow(operation, receipt, note);

    return receipt;
  }

  // Measures the deployment of a contract
  async function deployMeasured(
    contractName: string,
    args: readonly unknown[] = [],
    operationName?: string,
    note?: string,
  ) {
    // Reads the artifact, which contains the ABI and the bytecode of the compiled contract
    const artifact = await hre.artifacts.readArtifact(contractName);

    // Deployment of the contract
    // Uses the deployer to send the deployment transaction
    const hash = await deployer.deployContract({
      abi: artifact.abi,
      bytecode: artifact.bytecode as TxHash,
      args,
      account: deployer.account,
    });

    const receipt = await publicClient.waitForTransactionReceipt({ hash });

    // Checks that the receipt contains a contract address
    if (!receipt.contractAddress) {
      throw new Error(`${contractName} deployment did not return an address`);
    }

    // Saves the gas row
    pushGasRow(operationName ?? `Deploy ${contractName}`, receipt, note ?? receipt.contractAddress);

    // Returns an instance of a deployed contract
    return viem.getContractAt(contractName, receipt.contractAddress);
  }

  // Helper that submits a proposal and keeps the gas measurement readable
  async function submitProposalMeasured(
    proposalId: bigint,
    amount: bigint,
    interestRate: bigint,
    duration: bigint,
    note?: string,
  ) {
    await measureTx(
      `LendingPool.submitLoanProposal #${proposalId}`,
      () =>
        lendingPool.write.submitLoanProposal(
          [amount, interestRate, duration, DEFAULT_BTC_ADDRESS],
          {
            account: applicant.account,
          },
        ),
      note,
    );
  }

  // Helper to approve a proposal with contributor1 and reject it with contributor2
  // Since contributor1 has more disposable value, the proposal is approved
  async function voteApproveWithContributor1(proposalId: bigint) {
    await measureTx(`LendingPool.vote #${proposalId} contributor1 approve`, () =>
      lendingPool.write.vote([proposalId, true], {
        account: contributor1.account,
      }),
    );

    await measureTx(`LendingPool.vote #${proposalId} contributor2 reject`, () =>
      lendingPool.write.vote([proposalId, false], {
        account: contributor2.account,
      }),
    );
  }

  // Helper to mine the proposal voting period
  async function mineVotingPeriod() {
    // Mines 13 blocks for the voting period
    await networkHelpers.mine(13);
  }

  console.log("Starting gas analysis...");
  console.log("--------------------------------");
  console.log("Deployer:", deployer.account.address);
  console.log("Trusted oracle:", trustedOracle.account.address);
  console.log("Contributor 1:", contributor1.account.address);
  console.log("Contributor 2:", contributor2.account.address);
  console.log("Applicant:", applicant.account.address);

  // Deployment of the oracle, LendingPool implementation, proxy, and LoanFactory
  // Each deployment is saved in gasRows. The proxy deployment also runs initialize() through delegatecall
  const bitcoinOracle = await deployMeasured("BitcoinOracle", [
    trustedOracle.account.address,
  ]);

  const lendingPoolImplementation = await deployMeasured(
    "LendingPool",
    [],
    "Deploy LendingPool implementation",
    "Implementation contract used by OwnedUpgradeProxy.",
  );

  const lendingPoolArtifact = await hre.artifacts.readArtifact("LendingPool");
  const initData = encodeFunctionData({
    abi: lendingPoolArtifact.abi,
    functionName: "initialize",
    args: [deployer.account.address],
  });

  const ownedUpgradeProxy = await deployMeasured(
    "OwnedUpgradeProxy",
    [lendingPoolImplementation.address, deployer.account.address, initData],
    "Deploy OwnedUpgradeProxy",
    "Proxy deployment; constructor stores admin/implementation and initializes LendingPool storage with delegatecall.",
  );

  const lendingPoolUpgradeImplementation = await deployMeasured(
    "LendingPool",
    [],
    "Deploy LendingPool upgrade implementation",
    "Second implementation used to measure the upgrade path.",
  );

  await measureTx(
    "OwnedUpgradeProxy.upgradeTo",
    () =>
      ownedUpgradeProxy.write.upgradeTo([lendingPoolUpgradeImplementation.address], {
        account: deployer.account,
      }),
    "Measures the cost of upgrading the LendingPool logic while keeping the proxy address and storage.",
  );

  const lendingPool = await viem.getContractAt(
    "LendingPool",
    ownedUpgradeProxy.address,
  );

  const loanFactory = await deployMeasured("LoanFactory");

  // Connects BitcoinOracle to the LendingPool
  await measureTx("LendingPool.setBitcoinOracle", () =>
    lendingPool.write.setBitcoinOracle([bitcoinOracle.address], {
      account: deployer.account,
    }),
  );

  // Sets the authorized LendingPool in the LoanFactory
  await measureTx("LoanFactory.setLendingPool", () =>
    loanFactory.write.setLendingPool([lendingPool.address], {
      account: deployer.account,
    }),
  );

  // Communicates to the LendingPool which LoanFactory to use to create Loans
  await measureTx("LendingPool.setLoanFactory", () =>
    lendingPool.write.setLoanFactory([loanFactory.address], {
      account: deployer.account,
    }),
  );

  const oracleFee = await bitcoinOracle.read.MIN_ORACLE_FEE();

  // Measures the paid oracle request through the LendingPool
  // This is not the off-chain update; it is the on-chain request that emits the event served by oracle_service.py
  await measureTx(
    "LendingPool.requestBitcoinLiquidityCheck",
    () =>
      lendingPool.write.requestBitcoinLiquidityCheck([DEFAULT_BTC_ADDRESS], {
        account: applicant.account,
        value: oracleFee,
      }),
    "Applicant pays BitcoinOracle.MIN_ORACLE_FEE through the LendingPool.",
  );

  // contributor1 deposits 1_100_000 wei
  await measureTx(
    "LendingPool.deposit contributor1 new contributor",
    () =>
      lendingPool.write.deposit([], {
        account: contributor1.account,
        value: CONTRIBUTOR_1_INITIAL_DEPOSIT,
      }),
    "Initial deposit is 1_100_000 wei so that 100_000 wei can be withdrawn while leaving 1_000_000 wei deposited.",
  );

  // contributor1 withdraws 100_000 wei
  await measureTx(
    "LendingPool.withdraw contributor1",
    () =>
      lendingPool.write.withdraw([CONTRIBUTOR_1_WITHDRAW], {
        account: contributor1.account,
      }),
    "Leaves contributor1 with 1_000_000 wei deposited, matching the test scenario.",
  );

  // contributor1 deposits again; this is cheaper than the first deposit because the contributor already exists
  await measureTx(
    "LendingPool.deposit contributor1 existing contributor",
    () =>
      lendingPool.write.deposit([], {
        account: contributor1.account,
        value: CONTRIBUTOR_1_EXISTING_DEPOSIT,
      }),
    "Best-case deposit path for an already registered contributor; it uses MIN_DEPOSIT + 1 wei because deposits must be strictly greater than MIN_DEPOSIT.",
  );

  // contributor2 deposits
  await measureTx("LendingPool.deposit contributor2 new contributor", () =>
    lendingPool.write.deposit([], {
      account: contributor2.account,
      value: CONTRIBUTOR_2_DEPOSIT,
    }),
  );

  // trustedOracle sets the BTC balance of DEFAULT_BTC_ADDRESS to 1 BTC
  await measureTx(
    "BitcoinOracle.setBtcBalance",
    () =>
      bitcoinOracle.write.setBtcBalance(
        [DEFAULT_BTC_ADDRESS, ONE_BTC_IN_SATOSHI],
        {
          account: trustedOracle.account,
        },
      ),
    "Records 1 BTC as collateral for the applicant BTC address.",
  );

  // Proposal #0: best-case resolution. It is rejected before vote counting because the requested liquidity is too high
  await submitProposalMeasured(
    0n,
    LIQUIDITY_REJECTED_LOAN_AMOUNT,
    10n,
    20n,
    "Used to measure a cheap rejected resolution due to insufficient disposable liquidity.",
  );

  await mineVotingPeriod();

  await measureTx(
    "LendingPool.resolveProposal #0 rejected - insufficient liquidity (best case)",
    () =>
      lendingPool.write.resolveProposal([0n], {
        account: applicant.account,
      }),
    "Best-case rejected resolution: the pool has less disposable value than the requested amount, so no Loan is deployed.",
  );

  // Proposal #1: rejected by the voting result. This measures the vote-counting branch without Loan deployment
  await submitProposalMeasured(
    1n,
    VOTE_REJECTED_LOAN_AMOUNT,
    10n,
    20n,
    "Used to measure a rejected proposal after vote accounting.",
  );

  await measureTx("LendingPool.vote #1 contributor1 reject", () =>
    lendingPool.write.vote([1n, false], {
      account: contributor1.account,
    }),
  );

  await measureTx("LendingPool.vote #1 contributor2 reject", () =>
    lendingPool.write.vote([1n, false], {
      account: contributor2.account,
    }),
  );

  await mineVotingPeriod();

  await measureTx(
    "LendingPool.resolveProposal #1 rejected - vote majority",
    () =>
      lendingPool.write.resolveProposal([1n], {
        account: applicant.account,
      }),
    "Rejected after vote accounting; no LoanFactory call and no Loan deployment.",
  );

  // Creates proposal #2, the expensive approved case
  await submitProposalMeasured(2n, FIRST_LOAN_AMOUNT, FIRST_LOAN_INTEREST_RATE, FIRST_LOAN_DURATION);

  // contributor1 approves and contributor2 rejects
  await voteApproveWithContributor1(2n);

  await mineVotingPeriod();

  // Resolves the proposal
  await measureTx(
    "LendingPool.resolveProposal #2 approved with Loan deployment (worst case)",
    () =>
      lendingPool.write.resolveProposal([2n], {
        account: applicant.account,
      }),
    "Worst-case resolution: vote accounting, proportional locking, LoanFactory call, Loan deployment, and loan transfer to applicant.",
  );

  // Recovers the created Loan
  const firstLoanAddress = await lendingPool.read.loanByProposal([2n]);
  const firstLoan = await viem.getContractAt("Loan", firstLoanAddress);

  // The applicant repays part of the loan; this measures partial repayment
  await measureTx(
    "Loan.repay #2 partial",
    () =>
      firstLoan.write.repay([], {
        account: applicant.account,
        value: PARTIAL_REPAYMENT_AMOUNT,
      }),
    "Partial repayment; progressively updates repaid amount and unlocks contributor positions.",
  );

  const firstLoanRemainingDue = await firstLoan.read.remainingDue();

  // The applicant repays the remaining amount; this measures the gas of the full closing repayment
  await measureTx(
    "Loan.repay #2 remaining full close",
    () =>
      firstLoan.write.repay([], {
        account: applicant.account,
        value: firstLoanRemainingDue,
      }),
    "Final repayment after a previous partial repayment; closes the loan as successful.",
  );

  // contributor1 withdraws gains
  await measureTx("LendingPool.withdrawGains contributor1", () =>
    lendingPool.write.withdrawGains([], {
      account: contributor1.account,
    }),
  );

  await measureTx(
    "Loan.terminate #2 successful",
    () => firstLoan.write.terminate([], { account: applicant.account }),
    "Terminates a successful loan contract after full repayment.",
  );

  // Creates another proposal #3, used to measure failed loan and compensation
  await submitProposalMeasured(3n, SECOND_LOAN_AMOUNT, SECOND_LOAN_INTEREST_RATE, SECOND_LOAN_DURATION);

  // Contributors vote
  await voteApproveWithContributor1(3n);

  await mineVotingPeriod();

  // Resolves the proposal
  await measureTx(
    "LendingPool.resolveProposal #3 approved for failed-loan flow",
    () =>
      lendingPool.write.resolveProposal([3n], {
        account: applicant.account,
      }),
    "Approved proposal used to measure markFailed and compensation paths.",
  );

  // Recovers the second loan
  const secondLoanAddress = await lendingPool.read.loanByProposal([3n]);
  const secondLoan = await viem.getContractAt("Loan", secondLoanAddress);

  // Expires the loan
  await networkHelpers.mine(Number(SECOND_LOAN_DURATION + 1n));

  // Measures the cost of markFailed
  await measureTx(
    "Loan.markFailed #3",
    () =>
      secondLoan.write.markFailed([], {
        account: contributor1.account,
      }),
    "Marks the second measured loan as failed and updates applicant collateral percentage.",
  );

  // contributor1 claims compensation
  // Measures the gas of claimCompensation when the pool does not contain enough funds to cover the full loss
  await measureTx(
    "LendingPool.claimCompensation contributor1 #3 partial - pool insufficient",
    () =>
      lendingPool.write.claimCompensation([3n], {
        account: contributor1.account,
      }),
    "Partial compensation: compensation pool is not enough to cover the whole locked loss.",
  );

  // Creates another approved loan #4, used to measure overpayment and to refill the compensation pool
  await submitProposalMeasured(
    4n,
    OVERPAYMENT_LOAN_AMOUNT,
    OVERPAYMENT_LOAN_INTEREST_RATE,
    OVERPAYMENT_LOAN_DURATION,
    "Used to measure overpayment and to refill the compensation pool.",
  );

  await voteApproveWithContributor1(4n);
  await mineVotingPeriod();

  await measureTx(
    "LendingPool.resolveProposal #4 approved for overpayment flow",
    () =>
      lendingPool.write.resolveProposal([4n], {
        account: applicant.account,
      }),
    "Approved proposal used to measure overpayment and compensation-pool refill.",
  );

  const overpaymentLoanAddress = await lendingPool.read.loanByProposal([4n]);
  const overpaymentLoan = await viem.getContractAt("Loan", overpaymentLoanAddress);
  const overpaymentLoanTotalDue = await overpaymentLoan.read.totalDue();

  await measureTx(
    "Loan.repay #4 overpayment",
    () =>
      overpaymentLoan.write.repay([], {
        account: applicant.account,
        value: overpaymentLoanTotalDue + OVERPAYMENT_EXTRA,
      }),
    "Overpayment path: value exceeding the remaining due is credited to the compensation pool.",
  );

  // After the overpayment, the compensation pool has been refilled
  // contributor1 can claim the remaining compensation for proposal #3
  await measureTx(
    "LendingPool.claimCompensation contributor1 #3 remaining after refill",
    () =>
      lendingPool.write.claimCompensation([3n], {
        account: contributor1.account,
      }),
    "Repeated compensation claim after the compensation pool has been refilled by another successful loan.",
  );

  await measureTx(
    "LendingPool.claimCompensation contributor2 #3 full - pool sufficient",
    () =>
      lendingPool.write.claimCompensation([3n], {
        account: contributor2.account,
      }),
    "Full compensation claim when the compensation pool is sufficient to cover this contributor's remaining loss.",
  );

  await measureTx(
    "Loan.terminate #3 failed after closed positions",
    () => secondLoan.write.terminate([], { account: applicant.account }),
    "Terminates a failed loan after compensation closed all contributor positions.",
  );

  // Reads compensationPool and applicantCollateralPercentage
  // With these values, it is possible to evaluate the final state
  const compensationPool = await lendingPool.read.compensationPool();
  const applicantCollateralPercentage =
    await lendingPool.read.collateralPercentageOf([applicant.account.address]);

  console.log("\nFinal state checks");
  console.log("--------------------------------");
  console.log("Compensation pool:", compensationPool.toString(), "wei");
  console.log(
    "Applicant collateral percentage:",
    applicantCollateralPercentage.toString(),
    "%",
  );

  // Creates the table
  // Starts from gasRows and converts it into a readable table for console.table
  const table = gasRows.map((row) => ({
    Operation: row.operation,
    "Gas used": row.gasUsed.toString(),
    "Effective gas price (wei)": row.effectiveGasPriceWei.toString(),
    "Tx fee (wei)": row.txFeeWei.toString(),
    "Contract address / note": row.contractAddress ?? row.note ?? "", // If it's null or undefined, uses ""
  }));

  // Prints the table
  console.log("\nGas analysis");
  console.log("--------------------------------");
  console.table(table);

  // Builds the CSV
  // CSV header
  const csvHeader = [
    "Operation",
    "GasUsed",
    "EffectiveGasPriceWei",
    "TxFeeWei",
    "TxHash",
    "ContractAddress",
    "Note",
  ];

  // Creates the row
  const csvRows = gasRows.map((row) =>
    [
      row.operation,
      row.gasUsed.toString(),
      row.effectiveGasPriceWei.toString(),
      row.txFeeWei.toString(),
      row.txHash,
      row.contractAddress ?? "", // If it's null or undefined, uses ""
      row.note ?? "", // If it's null or undefined, uses ""
    ]
      .map(csvEscape)
      .join(","),
  );

  // Writes the file
  const csv = [csvHeader.join(","), ...csvRows].join("\n");
  writeFileSync("gas-analysis.csv", csv);

  console.log("\nCSV written to: gas-analysis.csv");
  console.log("Gas analysis completed successfully.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
