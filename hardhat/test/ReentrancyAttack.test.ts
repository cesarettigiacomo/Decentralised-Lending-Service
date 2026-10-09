import { describe, it } from "node:test";
import assert from "node:assert/strict";
import hre from "hardhat";

const ETH = 10n ** 18n;

describe("Reentrancy attack demonstration", async function () {
  // Prepares the scenario for the reentrancy tests
  // - Creates a local Hardhat network
  // - Deploys the VulnerableLendingPool
  // - Deploys ReentrancyAttacker connected to the LendingPool
  async function deployFixture() {
    const { viem } = await hre.network.create();

    const publicClient = await viem.getPublicClient();

    const [maliciousOwner, honestContributor] = await viem.getWalletClients();

    const vulnerablePool = await viem.deployContract("VulnerableLendingPool");

    const attacker = await viem.deployContract("ReentrancyAttacker", [
      vulnerablePool.address,
    ]);

    return {
      publicClient,
      vulnerablePool,
      attacker,
      maliciousOwner,
      honestContributor,
    };
  }

  // Tests the attack
  // - honestContributor deposits 5 ETH
  // - The attacker deposits 1 ETH
  // - The attacker calls attack
  // - The attacker contract re-enters withdraw() multiple times
  // - The attacker receives 4 ETH even though it deposited 1 ETH
  it("drains extra ETH from the intentionally vulnerable pool", async function () {
    const {
      publicClient,
      vulnerablePool,
      attacker,
      maliciousOwner,
      honestContributor,
    } = await deployFixture();

    const honestDeposit = 5n * ETH;
    const attackerDeposit = 1n * ETH;
    const extraWithdrawals = 3n;

    // Honest user deposits liquidity into the vulnerable pool
    await vulnerablePool.write.deposit([], {
      account: honestContributor.account,
      value: honestDeposit,
    });

    let poolBalance = await publicClient.getBalance({
      address: vulnerablePool.address,
    });

    assert.equal(poolBalance, honestDeposit);

    // The attacker deposits 1 ETH and then re-enters withdraw() three times
    // Total received by the attacker contract:
    // first withdraw + 3 re-entrant withdrawals = 4 ETH
    await attacker.write.attack([extraWithdrawals], {
      account: maliciousOwner.account,
      value: attackerDeposit,
    });

    const attackerContractBalance = await publicClient.getBalance({
      address: attacker.address,
    });

    poolBalance = await publicClient.getBalance({
      address: vulnerablePool.address,
    });

    const expectedAttackerBalance = attackerDeposit * (extraWithdrawals + 1n);
    console.log("Expected attacker balance: ", expectedAttackerBalance)
    console.log("Effective attacker balance: ", attackerContractBalance)
    console.log("Pool balance: ", poolBalance)

    assert.equal(attackerContractBalance, expectedAttackerBalance);

    // Initial pool after the attacker's deposit would be 6 ETH
    // It pays out 4 ETH, so 2 ETH remain
    assert.equal(poolBalance, honestDeposit + attackerDeposit - expectedAttackerBalance);

    // The vulnerable pool only records the attacker's deposit as withdrawn once,
    // although the attacker received four withdrawals
    const attackerState = await vulnerablePool.read.contributors([
      attacker.address,
    ]);

    assert.equal(attackerState[0], 0n);
  });

  it("allows the attacker owner to collect the stolen funds", async function () {
    const {
      publicClient,
      vulnerablePool,
      attacker,
      maliciousOwner,
      honestContributor,
    } = await deployFixture();

    await vulnerablePool.write.deposit([], {
      account: honestContributor.account,
      value: 5n * ETH,
    });

    await attacker.write.attack([2n], {
      account: maliciousOwner.account,
      value: 1n * ETH,
    });

    const attackerContractBalanceBefore = await publicClient.getBalance({
      address: attacker.address,
    });

    assert.equal(attackerContractBalanceBefore, 3n * ETH);

    await attacker.write.withdrawStolenFunds([], {
      account: maliciousOwner.account,
    });

    const attackerContractBalanceAfter = await publicClient.getBalance({
      address: attacker.address,
    });

    assert.equal(attackerContractBalanceAfter, 0n);
  });
});
