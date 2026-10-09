// Tests loan repayment: partial principal refund, interest split, overpayment and termination.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { setupApprovedLoan } from "./helpers/fixtures";

describe("Loan - repayment", function () {
  it("tracks partial repayments and unlocks principal progressively", async function () {
    const { lendingPool, loan, contributor1, contributor2, applicant } =
      await setupApprovedLoan();

    await loan.write.repay([], {
      account: applicant.account,
      value: 100_000n,
    });

    const c1 = await lendingPool.read.contributors([
      contributor1.account.address,
    ]);
    const c2 = await lendingPool.read.contributors([
      contributor2.account.address,
    ]);

    // Principal repayments are assigned starting with the highest initial locked value
    assert.equal(c1[1], 316_666n);
    assert.equal(c2[1], 83_333n);

    const principalRepaid = await lendingPool.read.principalRepaidByProposal([
      0n,
    ]);
    assert.equal(principalRepaid, 100_000n);

    const proposalRepaid = await lendingPool.read.proposalRepaid([0n]);
    assert.equal(proposalRepaid, false);
  });

  it("accepts overpayment and credits the excess to the compensation pool", async function () {
    const { lendingPool, loan, applicant, contributor1, contributor2 } =
      await setupApprovedLoan();

    const totalDue = await loan.read.totalDue();
    const overpayment = totalDue + 10_000n;

    await loan.write.repay([], {
      account: applicant.account,
      value: overpayment,
    });

    const proposalRepaid = await lendingPool.read.proposalRepaid([0n]);
    assert.equal(proposalRepaid, true);

    const status = await loan.read.status();
    assert.equal(status, 1); // LoanStatus.Successful

    const c1 = await lendingPool.read.contributors([
      contributor1.account.address,
    ]);
    const c2 = await lendingPool.read.contributors([
      contributor2.account.address,
    ]);

    assert.equal(c1[1], 0n);
    assert.equal(c2[1], 0n);
    assert.equal(c1[2] > 0n, true);
    assert.equal(c2[2] > 0n, true);

    const compensationPool = await lendingPool.read.compensationPool();
    assert.equal(compensationPool > 10_000n, true);
  });

  it("terminates a successful loan", async function () {
    const { loan, applicant } = await setupApprovedLoan();

    const totalDue = await loan.read.totalDue();

    await loan.write.repay([], {
      account: applicant.account,
      value: totalDue,
    });

    await loan.write.terminate([]);

    const terminated = await loan.read.terminated();
    assert.equal(terminated, true);
  });
});
