// Tests BitcoinOracle access control, request fee, and BTC balance recording.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_BTC_ADDRESS,
  ONE_BTC_IN_SATOSHI,
  deployFixture,
} from "./helpers/fixtures";

describe("BitcoinOracle", function () {
  it("allows only the trusted oracle to record BTC balances", async function () {
    const { bitcoinOracle, oracle, contributor1 } = await deployFixture();

    // Verifies that an unauthorized account is rejected
    await assert.rejects(
      bitcoinOracle.write.setBtcBalance(
        [DEFAULT_BTC_ADDRESS, ONE_BTC_IN_SATOSHI],
        {
          account: contributor1.account,
        },
      ),
      /Only trusted oracle/,
    );

// The regex /Only trusted oracle/ checks that the error contains that message

    // Verifies that the trusted oracle can register the BTC balance
    await bitcoinOracle.write.setBtcBalance(
      [DEFAULT_BTC_ADDRESS, ONE_BTC_IN_SATOSHI],
      {
        account: oracle.account,
      },
    );

    const recorded = await bitcoinOracle.read.hasBtcBalanceRecord([
      DEFAULT_BTC_ADDRESS,
    ]);
    assert.equal(recorded, true);

    const balance = await bitcoinOracle.read.btcBalanceInSatoshi([
      DEFAULT_BTC_ADDRESS,
    ]);
    assert.equal(balance, ONE_BTC_IN_SATOSHI);
  });

  // To request a BTC balance update, it is necessary to pay at least the minimum fee
  it("requires the minimum oracle fee for BTC balance update requests", async function () {
    const { bitcoinOracle, applicant } = await deployFixture();

    const minFee = await bitcoinOracle.read.MIN_ORACLE_FEE();

    await assert.rejects(
      bitcoinOracle.write.requestUpdate([DEFAULT_BTC_ADDRESS], {
        account: applicant.account,
        value: minFee - 1n,
      }),
      /Oracle fee too low/,
    );

    // This call succeeds because the minFee is provided
    await bitcoinOracle.write.requestUpdate([DEFAULT_BTC_ADDRESS], {
      account: applicant.account,
      value: minFee,
    });
  });
});
