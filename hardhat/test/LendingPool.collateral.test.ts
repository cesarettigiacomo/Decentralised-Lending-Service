// Tests the logic of the BTC collateral and the update of the collateral percentage of the applicant

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_BTC_ADDRESS,
  ZERO_ADDRESS,
  createApprovedLoan,
  deployFixture,
  depositDefaultContributors,
  recordBtcBalance,
  setupApprovedLoan,
} from "./helpers/fixtures";

describe("LendingPool - BTC collateral policy", function () {
  
  // Verifies that a proposal is rejected if the BitcoinOracle has no records for the BTC address
  it("rejects a proposal if no BTC balance has been recorded", async function () {
    const {
      lendingPool,
      contributor1,
      contributor2,
      applicant,
      networkHelpers,
    } = await deployFixture();

    await lendingPool.write.deposit([], {
      account: contributor1.account,
      value: 1_000_000n,
    });

    await lendingPool.write.deposit([], {
      account: contributor2.account,
      value: 200_000n,
    });

    await lendingPool.write.submitLoanProposal(
      [500_000n, 10n, 20n, DEFAULT_BTC_ADDRESS],
      {
        account: applicant.account,
      },
    );

    await lendingPool.write.vote([0n, true], {
      account: contributor1.account,
    });

    await lendingPool.write.vote([0n, false], {
      account: contributor2.account,
    });

    await networkHelpers.mine(13);

    await lendingPool.write.resolveProposal([0n], {
      account: applicant.account,
    });

    const proposal = await lendingPool.read.proposals([0n]);
    assert.equal(proposal[6], 2); // ProposalStatus.Rejected

    const loanAddress = await lendingPool.read.loanByProposal([0n]);
    assert.equal(loanAddress, ZERO_ADDRESS);
  });

  // Verifies that a proposal is rejected if the registered BTC value is too low for the requested loan
  it("rejects a proposal if the BTC collateral is insufficient", async function () {
    const fixture = await deployFixture();
    const { lendingPool, contributor1, applicant, networkHelpers } = fixture;

    await recordBtcBalance(fixture, DEFAULT_BTC_ADDRESS, 1n);

    const depositAmount = 2_000_000_000_000_000n;
    const loanAmount = 1_000_000_000_000_000n;

    await lendingPool.write.deposit([], {
      account: contributor1.account,
      value: depositAmount,
    });

    await lendingPool.write.submitLoanProposal(
      [loanAmount, 10n, 20n, DEFAULT_BTC_ADDRESS],
      {
        account: applicant.account,
      },
    );

    await lendingPool.write.vote([0n, true], {
      account: contributor1.account,
    });

    await networkHelpers.mine(13);

    await lendingPool.write.resolveProposal([0n], {
      account: applicant.account,
    });

    const proposal = await lendingPool.read.proposals([0n]);
    assert.equal(proposal[6], 2); // ProposalStatus.Rejected

    const loanAddress = await lendingPool.read.loanByProposal([0n]);
    assert.equal(loanAddress, ZERO_ADDRESS);
  });

  // Verifies that, if the balance is sufficient, the proposal can be approved and the loan can be created
  it("approves a proposal if the BTC collateral is sufficient", async function () {
    const fixture = await deployFixture();
    const { lendingPool } = fixture;

    await depositDefaultContributors(fixture);
    await createApprovedLoan(fixture, 0n);

    const proposal = await lendingPool.read.proposals([0n]);
    assert.equal(proposal[6], 1); // ProposalStatus.Approved

    const loanAddress = await lendingPool.read.loanByProposal([0n]);
    assert.notEqual(loanAddress, ZERO_ADDRESS);
  });

  // Verifies that, after a loan is successfully repaid, the collateral percentage of the applicant decreases
  it("decreases the applicant collateral percentage after a successful loan", async function () {
    const { lendingPool, loan, applicant } = await setupApprovedLoan();

    const before = await lendingPool.read.collateralPercentageOf([
      applicant.account.address,
    ]);
    assert.equal(before, 50n);

    const totalDue = await loan.read.totalDue();

    await loan.write.repay([], {
      account: applicant.account,
      value: totalDue,
    });

    const after = await lendingPool.read.collateralPercentageOf([
      applicant.account.address,
    ]);
    assert.equal(after, 45n);
  });

  // Verifies that, after a failed loan, the collateral percentage of the applicant increases
  it("increases the applicant collateral percentage after a failed loan", async function () {
    const { lendingPool, loan, contributor1, applicant, networkHelpers } =
      await setupApprovedLoan();

    const before = await lendingPool.read.collateralPercentageOf([
      applicant.account.address,
    ]);
    assert.equal(before, 50n);

    await networkHelpers.mine(21);

    await loan.write.markFailed([], {
      account: contributor1.account,
    });

    const after = await lendingPool.read.collateralPercentageOf([
      applicant.account.address,
    ]);
    assert.equal(after, 55n);
  });
});
