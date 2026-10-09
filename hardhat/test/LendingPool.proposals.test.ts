// Tests the management of the loan proposals
// Creation
// Vote
// Resolution
// Approval
// Rejection
// Locking of the funds

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_BTC_ADDRESS,
  ZERO_ADDRESS,
  createApprovedLoan,
  deployFixture,
  depositDefaultContributors,
  recordBtcBalance,
} from "./helpers/fixtures";

describe("LendingPool - proposal lifecycle and voting", function () {
  
  // Checks that a contributor who has deposited is allowed to vote for a proposal
  it("allows a contributor to vote", async function () {
    const { lendingPool, contributor1, applicant } = await deployFixture();

    await lendingPool.write.deposit([], {
      account: contributor1.account,
      value: 1_000_000n,
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

    const vote = await lendingPool.read.proposalVotes([
      0n,
      contributor1.account.address,
    ]);

    // Checks that the vote is saved as Approve
    assert.equal(vote, 1); // VoteChoice.Approve
  });

  // Checks that a proposal is approved when the weight of the Approve votes is greater than the weight of the Reject votes
  it("approves a proposal if approve weight is greater than reject weight", async function () {
    const fixture = await deployFixture();
    const {
      lendingPool,
      contributor1,
      contributor2,
      applicant,
      networkHelpers,
    } = fixture;

    await recordBtcBalance(fixture);

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

    assert.equal(proposal[6], 1); // ProposalStatus.Approved
  });

  // Checks that the proposal is rejected if the Approve weight equals the Reject weight
  // To be approved, it must be approveWeight > rejectWeight
  it("rejects a proposal on tie", async function () {
    const fixture = await deployFixture();
    const {
      lendingPool,
      contributor1,
      contributor2,
      applicant,
      networkHelpers,
    } = fixture;

    await recordBtcBalance(fixture);

    await lendingPool.write.deposit([], {
      account: contributor1.account,
      value: 1_000_000n,
    });

    await lendingPool.write.deposit([], {
      account: contributor2.account,
      value: 1_000_000n,
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
  });

  // Verifies that an account that did not deposit is not allowed to vote
  it("rejects vote from non contributor", async function () {
    const { lendingPool, contributor1, applicant } = await deployFixture();

    await lendingPool.write.submitLoanProposal(
      [500_000n, 10n, 20n, DEFAULT_BTC_ADDRESS],
      {
        account: applicant.account,
      },
    );

    await assert.rejects(
      lendingPool.write.vote([0n, true], {
        account: contributor1.account,
      }),
      /Not a contributor/,
    );
  });

  // Verifies that an account is not allowed to vote twice for the same proposal
  it("rejects double vote from same contributor", async function () {
    const { lendingPool, contributor1, applicant } = await deployFixture();

    await lendingPool.write.deposit([], {
      account: contributor1.account,
      value: 1_000_000n,
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

    await assert.rejects(
      lendingPool.write.vote([0n, false], {
        account: contributor1.account,
      }),
      /Already voted/,
    );
  });

  // Verifies that only the applicant of the proposal can call resolveProposal
  it("rejects resolution from non applicant", async function () {
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

    await lendingPool.write.submitLoanProposal(
      [500_000n, 10n, 20n, DEFAULT_BTC_ADDRESS],
      {
        account: applicant.account,
      },
    );

    await lendingPool.write.vote([0n, true], {
      account: contributor1.account,
    });

    await networkHelpers.mine(13);

    await assert.rejects(
      lendingPool.write.resolveProposal([0n], {
        account: contributor2.account,
      }),
      /Only applicant can resolve/,
    );
  });

  // Verifies that the proposal cannot be resolved before the end of the voting period
  it("rejects resolution before voting period ends", async function () {
    const { lendingPool, contributor1, applicant } = await deployFixture();

    await lendingPool.write.deposit([], {
      account: contributor1.account,
      value: 1_000_000n,
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

    await assert.rejects(
      lendingPool.write.resolveProposal([0n], {
        account: applicant.account,
      }),
      /Voting period not ended/,
    );
  });

  // Verifies that the proposal is rejected if the pool does not have enough liquidity to cover the requested amount
  // Even if the votes are in favour, the loan is not created without sufficient funds
  it("rejects proposal if cumulative disposable value is lower than requested amount", async function () {
    const { lendingPool, contributor1, applicant, networkHelpers } =
      await deployFixture();

    await lendingPool.write.deposit([], {
      account: contributor1.account,
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

    await networkHelpers.mine(13);

    await lendingPool.write.resolveProposal([0n], {
      account: applicant.account,
    });

    const proposal = await lendingPool.read.proposals([0n]);

    assert.equal(proposal[6], 2); // ProposalStatus.Rejected
  });

  // Verifies that when a proposal is approved, the Loan contract is created
  it("creates a Loan contract when a proposal is approved", async function () {
    const fixture = await deployFixture();
    const { lendingPool } = fixture;

    await depositDefaultContributors(fixture);
    await createApprovedLoan(fixture, 0n);

    const loanAddress = await lendingPool.read.loanByProposal([0n]);

    assert.notEqual(loanAddress, ZERO_ADDRESS);
  });

  // Verifies that the contributors' funds are locked proportionally to their weight in the pool
  it("locks contributor funds proportionally when a proposal is approved", async function () {
    const fixture = await deployFixture();
    const { lendingPool, contributor1, contributor2 } = fixture;

    await depositDefaultContributors(fixture);
    await createApprovedLoan(fixture, 0n);

    const c1 = await lendingPool.read.contributors([
      contributor1.account.address,
    ]);

    const c2 = await lendingPool.read.contributors([
      contributor2.account.address,
    ]);

    const proposal = await lendingPool.read.proposals([0n]);

    assert.equal(c1[1], 416_666n); // locked
    assert.equal(c2[1], 83_333n); // locked
    assert.equal(proposal[7], 499_999n); // loanedAmount
  });
});
