// Tests the withdrawal of available funds and accrued income

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { deployFixture, setupApprovedLoan } from "./helpers/fixtures";

describe("LendingPool - withdrawals", function () {
  
  // Verifies that a contributor can withdraw the disposable funds
  it("allows a contributor to withdraw disposable funds", async function () {
    const { lendingPool, contributor1 } = await deployFixture();

    await lendingPool.write.deposit([], {
      account: contributor1.account,
      value: 1_000_000n,
    });

    await lendingPool.write.withdraw([300_000n], {
      account: contributor1.account,
    });

    const c1 = await lendingPool.read.contributors([
      contributor1.account.address,
    ]);

    assert.equal(c1[0], 700_000n); // deposited
    assert.equal(c1[1], 0n); // locked

    const disposable = await lendingPool.read.disposableValue([
      contributor1.account.address,
    ]);

    assert.equal(disposable, 700_000n);
  });

  // Verifies that a contributor cannot withdraw funds that are locked in a proposal or in an active loan
  it("rejects withdrawing locked funds", async function () {
    const { lendingPool, contributor1 } = await setupApprovedLoan();

    const disposable = await lendingPool.read.disposableValue([
      contributor1.account.address,
    ]);

    assert.equal(disposable, 583_334n);

    await assert.rejects(
      lendingPool.write.withdraw([600_000n], {
        account: contributor1.account,
      }),
      /Not enough disposable value/,
    );
  });

  // Verifies that, after a loan is repaid successfully, a contributor can withdraw their pendingGains
  it("allows a contributor to withdraw gains after successful repayment", async function () {
    const { lendingPool, loan, contributor1, applicant } =
      await setupApprovedLoan();

    const totalDue = await loan.read.totalDue();

    await loan.write.repay([], {
      account: applicant.account,
      value: totalDue,
    });

    const c1Before = await lendingPool.read.contributors([
      contributor1.account.address,
    ]);

    assert.equal(c1Before[2] > 0n, true); // pendingGains

    await lendingPool.write.withdrawGains([], {
      account: contributor1.account,
    });

    const c1After = await lendingPool.read.contributors([
      contributor1.account.address,
    ]);

    // After the withdrawal, pendingGains must be 0
    assert.equal(c1After[2], 0n); // pendingGains
  });

  // Verifies that a contributor cannot withdraw the same gains twice
  it("rejects withdrawing gains twice", async function () {
    const { lendingPool, loan, contributor1, applicant } =
      await setupApprovedLoan();

    const totalDue = await loan.read.totalDue();

    await loan.write.repay([], {
      account: applicant.account,
      value: totalDue,
    });

    await lendingPool.write.withdrawGains([], {
      account: contributor1.account,
    });

    await assert.rejects(
      lendingPool.write.withdrawGains([], {
        account: contributor1.account,
      }),
      /No gains to withdraw/,
    );
  });
});
