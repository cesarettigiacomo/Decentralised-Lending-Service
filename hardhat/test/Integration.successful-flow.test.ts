// Tests the complete flow: not a single component, but the interaction among BitcoinOracle, LendingPool, LoanFactory, and Loan
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_BTC_ADDRESS,
  ZERO_ADDRESS,
  deployFixture,
  depositDefaultContributors,
  recordBtcBalance,
} from "./helpers/fixtures";

describe("Integration - successful lending flow", function () {
  // Runs the complete successful loan flow
  it("runs the complete successful loan flow", async function () {
    // Contract deployment
    const fixture = await deployFixture();
    const {
      viem,
      networkHelpers,
      lendingPool,
      contributor1,
      contributor2,
      applicant,
    } = fixture;

    // Contributors deposit
    await depositDefaultContributors(fixture);

    // Records BTC balance
    await recordBtcBalance(fixture, DEFAULT_BTC_ADDRESS);

    // Submits loan proposal
    await lendingPool.write.submitLoanProposal(
      [500_000n, 10n, 20n, DEFAULT_BTC_ADDRESS],
      {
        account: applicant.account,
      },
    );

    // Vote
    await lendingPool.write.vote([0n, true], {
      account: contributor1.account,
    });

    await lendingPool.write.vote([0n, false], {
      account: contributor2.account,
    });

    // Advances blocks
    await networkHelpers.mine(13);

    // Resolves proposal
    await lendingPool.write.resolveProposal([0n], {
      account: applicant.account,
    });

    const proposal = await lendingPool.read.proposals([0n]);
    assert.equal(proposal[6], 1); // ProposalStatus.Approved

    // Creates loan
    const loanAddress = await lendingPool.read.loanByProposal([0n]);
    assert.notEqual(loanAddress, ZERO_ADDRESS);

    const loan = await viem.getContractAt("Loan", loanAddress);
    const totalDue = await loan.read.totalDue();

    // Repays the full loan
    await loan.write.repay([], {
      account: applicant.account,
      value: totalDue,
    });

    const proposalRepaid = await lendingPool.read.proposalRepaid([0n]);
    assert.equal(proposalRepaid, true);

    // Marks the Loan as successful
    const status = await loan.read.status();
    assert.equal(status, 1); // LoanStatus.Successful

    const c1BeforeWithdraw = await lendingPool.read.contributors([
      contributor1.account.address,
    ]);
    assert.equal(c1BeforeWithdraw[2] > 0n, true);

    // Withdraws contributor gains
    await lendingPool.write.withdrawGains([], {
      account: contributor1.account,
    });

    const c1AfterWithdraw = await lendingPool.read.contributors([
      contributor1.account.address,
    ]);
    assert.equal(c1AfterWithdraw[2], 0n);
  });
});
