// Tests the deposits of the contributors in the LendingPool

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { deployFixture } from "./helpers/fixtures";

describe("LendingPool - deposits", function () {

  // Verifies that a contributor can deposit a valid amount
  it("allows a valid deposit", async function () {
    const { lendingPool, contributor1 } = await deployFixture();

    // Checks that the deposit is updated
    await lendingPool.write.deposit([], {
      account: contributor1.account,
      value: 100_001n,
    });

    const c = await lendingPool.read.contributors([
      contributor1.account.address,
    ]);

    assert.equal(c[0], 100_001n);

    const disposable = await lendingPool.read.disposableValue([
      contributor1.account.address,
    ]);

    // Checks that disposableValue corresponds to the deposit
    assert.equal(disposable, 100_001n);
  });

  // Verifies that a deposit equal to MIN_DEPOSIT is rejected
  // Because it must be strictly greater than MIN_DEPOSIT
  it("rejects a deposit equal to MIN_DEPOSIT", async function () {
    const { lendingPool, contributor1 } = await deployFixture();

    await assert.rejects(
      lendingPool.write.deposit([], {
        account: contributor1.account,
        value: 100_000n,
      }),
      /Deposit too small/,
    );
  });
});
