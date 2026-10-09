// Tests the access controls on LendingPool and on LoanFactory

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { deployFixture } from "./helpers/fixtures";

describe("LendingPool and LoanFactory - access control", function () {
  
  // Verifies that only the owner of the LendingPool is allowed to set the address of the BitcoinOracle
  it("rejects setBitcoinOracle from non owner", async function () {
    const { lendingPool, bitcoinOracle, contributor1 } = await deployFixture();

    await assert.rejects(
      lendingPool.write.setBitcoinOracle([bitcoinOracle.address], {
        account: contributor1.account,
      }),
      /Only owner/,
    );
  });

  // Verifies that only the owner of the LendingPool is allowed to set the LoanFactory
  it("rejects setLoanFactory from non owner", async function () {
    const { lendingPool, loanFactory, contributor1 } = await deployFixture();

    await assert.rejects(
      lendingPool.write.setLoanFactory([loanFactory.address], {
        account: contributor1.account,
      }),
      /Only owner/,
    );
  });

  // Verifies that LoanFactory.createLoan can be called only by the LendingPool
  it("rejects direct LoanFactory createLoan calls from non LendingPool", async function () {
    const { loanFactory, applicant, contributor1 } = await deployFixture();

    await assert.rejects(
      loanFactory.write.createLoan(
        [
          applicant.account.address,
          0n,
          500_000n,
          10n,
          20n,
        ],
        {
          account: contributor1.account,
        },
      ),
      /Only lending pool/,
    );
  });
});
