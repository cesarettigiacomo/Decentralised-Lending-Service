// Tests failed loans, repayment after failure, repeated compensation claims and termination

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  createApprovedLoan,
  setupApprovedLoan,
  setupWithFundedCompensationPool,
} from "./helpers/fixtures";

describe("Loan - failure and compensation", function () {
  // Verifies that, after the loan expires, markFailed can be called
  it("marks an expired loan as failed and records proposalFailed", async function () {
    const { lendingPool, loan, contributor1, networkHelpers } =
      await setupApprovedLoan();

    await networkHelpers.mine(21);

    await loan.write.markFailed([], {
      account: contributor1.account,
    });

    // Checks that LoanStatus is Failed and that proposalFailed is true
    const status = await loan.read.status();
    assert.equal(status, 2); // LoanStatus.Failed

    const proposalFailed = await lendingPool.read.proposalFailed([0n]);
    assert.equal(proposalFailed, true);
  });

  // Verifies that it is not possible to mark a loan as failed before expiration
  it("rejects marking a loan failed before expiration", async function () {
    const { loan, contributor1 } = await setupApprovedLoan();

    await assert.rejects(
      loan.write.markFailed([], {
        account: contributor1.account,
      }),
      /Loan not expired/,
    );
  });

  // It is possible to repay after the failure, but the loan remains Failed
  it("allows repayment after failure while keeping the loan failed", async function () {
    const { lendingPool, loan, contributor1, applicant, networkHelpers } =
      await setupApprovedLoan();

    await networkHelpers.mine(21); // Mines 21 blocks

    await loan.write.markFailed([], {
      account: contributor1.account,
    });

    // Applicant sends 100,000 wei after the failure
    await loan.write.repay([], {
      account: applicant.account,
      value: 100_000n,
    });

    const status = await loan.read.status();
    assert.equal(status, 2); // Remains Failed

    const proposalFailed = await lendingPool.read.proposalFailed([0n]);
    assert.equal(proposalFailed, true);

    const repaid = await lendingPool.read.repaidByProposal([0n]);
    assert.equal(repaid, 100_000n);
  });

  // Allows the contributor to request compensation multiple times
  it("allows repeated compensation claims when the pool is refilled by another loan", async function () {
    const fixture = await setupWithFundedCompensationPool();
    const { lendingPool, contributor1, applicant, networkHelpers } = fixture;

    // Creates a Loan that will fail
    const failedLoanData = await createApprovedLoan(
      fixture,
      1n,
      120_000n,
      10n,
      20n,
    );

    await networkHelpers.mine(21); // Mines 21 blocks

    // Marks the Loan as failed
    await failedLoanData.loan.write.markFailed([], {
      account: contributor1.account,
    });

    // First compensation request
    await lendingPool.write.claimCompensation([1n], {
      account: contributor1.account,
    });

    const remainingAfterFirstClaim = await lendingPool.read.lockedByProposal([
      1n,
      contributor1.account.address,
    ]);

    // After the first compensation, the contributor still has locked funds
    assert.equal(remainingAfterFirstClaim > 0n, true);

    // Refills the compensation pool with a different successful loan
    // Replaying the same failed loan would progressively unlock its positions; therefore, the second compensation claim would correctly have no owed value
    const refillingLoanData = await createApprovedLoan(
      // Creates another Loan, which succeeds
      fixture,
      2n,
      500_000n,
      100n,
      20n,
    );

    const totalDueForRefillingLoan =
      await refillingLoanData.loan.read.totalDue();

    await refillingLoanData.loan.write.repay([], {
      account: applicant.account,
      value: totalDueForRefillingLoan,
    });

    // contributor1 requests compensation again for the previous failed loan
    await lendingPool.write.claimCompensation([1n], {
      account: contributor1.account,
    });

    // After the second compensation, contributor1 has no more locked funds for proposal 1
    const remainingAfterSecondClaim = await lendingPool.read.lockedByProposal([
      1n,
      contributor1.account.address,
    ]);

    assert.equal(remainingAfterSecondClaim, 0n);
  });

  // It is not possible to make a compensation claim for a non-failed loan
  it("rejects compensation claim for a non-failed loan", async function () {
    const { lendingPool, contributor1 } = await setupApprovedLoan();

    await assert.rejects(
      lendingPool.write.claimCompensation([0n], {
        account: contributor1.account,
      }),
      /Proposal not failed/,
    );
  });

  // A failed loan can be terminated only after all locked positions are closed; being Failed alone is not enough.
  // There must be no contributor funds that are still locked
  it("terminates a failed loan only after all locked positions are closed", async function () {
    // Prepares:
    // - An approved loan proposal
    // - A deployed Loan contract
    // - Contributors with locked funds
    // - An applicant who has received the loan
    const fixture = await setupApprovedLoan();
    const { lendingPool, loan, contributor1, applicant, networkHelpers } =
      fixture;

    await networkHelpers.mine(21); // Mines 21 blocks

    // Marks the loan as failed
    await loan.write.markFailed([], {
      account: contributor1.account,
    });

    // Rejects the termination of the Loan
    await assert.rejects(
      loan.write.terminate([]),
      /Loan has pending positions/,
    );

    // Fully repays after failure. The loan remains failed, but positions are closed
    await loan.write.repay([], {
      account: applicant.account,
      value: 1_000_000n,
    });

    // Returns true only if the loan is repaid or failed, and there are no contributors with locked funds for that proposal
    const canTerminate = await lendingPool.read.canTerminateLoan([0n]);
    assert.equal(canTerminate, true);

    // Terminates the loan
    await loan.write.terminate([]);
    assert.equal(await loan.read.terminated(), true);
  });
});
