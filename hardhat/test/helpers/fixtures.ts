import hre from "hardhat";

export const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";
export const DEFAULT_BTC_ADDRESS = "btc-address-1";
export const ONE_BTC_IN_SATOSHI = 100_000_000n;

/*
deployFixture()

Deploys:
- BitcoinOracle
- LendingPool
- LoanFactory

and then connects them to each other.
*/
export async function deployFixture() {
  // Creates a local Hardhat environment
  const { viem, networkHelpers } = await hre.network.create();

  // Takes some accounts for tests
  const [owner, oracle, contributor1, contributor2, applicant] =
    await viem.getWalletClients();

  // Deploys and connects all the contracts
  const bitcoinOracle = await viem.deployContract("BitcoinOracle", [
    oracle.account.address,
  ]);

  const lendingPool = await viem.deployContract("LendingPool");
  const loanFactory = await viem.deployContract("LoanFactory");

  await lendingPool.write.setBitcoinOracle([bitcoinOracle.address], {
    account: owner.account,
  });

  await loanFactory.write.setLendingPool([lendingPool.address], {
    account: owner.account,
  });

  await lendingPool.write.setLoanFactory([loanFactory.address], {
    account: owner.account,
  });

  return {
    viem,
    networkHelpers,
    bitcoinOracle,
    loanFactory,
    lendingPool,
    owner,
    oracle,
    contributor1,
    contributor2,
    applicant,
  };
}

/*
recordBtcBalance:

Records a BTC balance in the oracle.
*/
export async function recordBtcBalance(
  fixture: any,
  btcAddress: string = DEFAULT_BTC_ADDRESS,
  balanceInSatoshi: bigint = ONE_BTC_IN_SATOSHI,
) {
  await fixture.bitcoinOracle.write.setBtcBalance(
    [btcAddress, balanceInSatoshi],
    {
      account: fixture.oracle.account, // Only the oracle account is authorized to update the BTC balance
    },
  );
}

/*
depositDefaultContributors:

Makes the contributors deposit funds.
*/
export async function depositDefaultContributors(fixture: any) {
  const { lendingPool, contributor1, contributor2 } = fixture;

  await lendingPool.write.deposit([], {
    account: contributor1.account,
    value: 1_000_000n,
  });

  await lendingPool.write.deposit([], {
    account: contributor2.account,
    value: 200_000n,
  });
}

/*
createApprovedLoan:

Creates an approved proposal and its Loan
*/
export async function createApprovedLoan(
  fixture: any,
  proposalId: bigint,
  amount: bigint = 500_000n,
  interestRate: bigint = 10n,
  duration: bigint = 20n,
  btcAddress: string = DEFAULT_BTC_ADDRESS,
) {
  const {
    viem,
    lendingPool,
    contributor1,
    contributor2,
    applicant,
    networkHelpers,
  } = fixture;

  // Before asking for a loan, the oracle is updated in order to check if the applicant has sufficient BTC collateral
  await recordBtcBalance(fixture, btcAddress);

  // The applicant requests a loan
  await lendingPool.write.submitLoanProposal(
    [amount, interestRate, duration, btcAddress],
    {
      account: applicant.account,
    },
  );

  // Contributors vote (contributor1 votes true, contributor2 votes false)
  await lendingPool.write.vote([proposalId, true], {
    account: contributor1.account,
  });

  await lendingPool.write.vote([proposalId, false], {
    account: contributor2.account,
  });

  // Mines 13 blocks in the local blockchain in order to wait before resolving the proposal
  await networkHelpers.mine(13);

  // Resolves the proposal and creates a Loan
  await lendingPool.write.resolveProposal([proposalId], {
    account: applicant.account,
  });

  const loanAddress = await lendingPool.read.loanByProposal([proposalId]);
  const loan = await viem.getContractAt("Loan", loanAddress);

  return { loanAddress, loan };
}

/*
setupApprovedLoan:

Creates a ready scenario with:
- deposited contributors
- updated oracle
- approved proposal
- created Loan
*/
export async function setupApprovedLoan(
  amount: bigint = 500_000n,
  interestRate: bigint = 10n,
  duration: bigint = 20n,
) {

  // Reads the address of the Loan contract associated with the proposal from LendingPool.
  const fixture = await deployFixture();
  await depositDefaultContributors(fixture);

  const loanData = await createApprovedLoan(
    fixture,
    0n,
    amount,
    interestRate,
    duration,
  );

  return {
    ...fixture,
    ...loanData,
  };
}

/*
setupWithFundedCompensationPool:

Creates a scenario such that a funded loan already exists.
 */
export async function setupWithFundedCompensationPool() {
  // Contracts are deployed
  const fixture = await deployFixture();

  // Contributors deposit
  await depositDefaultContributors(fixture);

  // Creates a first approved loan
  const firstLoanData = await createApprovedLoan(
    fixture,
    0n,
    500_000n,
    10n,
    20n,
  );

  // Total to repay
  const totalDue = await firstLoanData.loan.read.totalDue();

  // Total repayment by the applicant
  await firstLoanData.loan.write.repay([], {
    account: fixture.applicant.account,
    value: totalDue,
  });

  return fixture;
}
