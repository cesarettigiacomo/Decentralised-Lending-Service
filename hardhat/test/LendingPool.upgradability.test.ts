// Tests the upgradeable proxy used for the main LendingPool contract

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import hre from "hardhat";

describe("LendingPool - upgradability", function () {
  it("initializes LendingPool behind a proxy and allows the proxy admin to upgrade", async function () {
    const { viem } = await hre.network.create();
    const [owner, other] = await viem.getWalletClients(); // owner is the admin; other is an unauthorized user

    const implementationV1 = await viem.deployContract("LendingPool"); // Deployment of the first implementation

    // Deployment of OwnedUpgradeProxy
    // The proxy stores the current logic (V1) and the proxy admin; 0x means empty initData
    const proxy = await viem.deployContract("OwnedUpgradeProxy", [
      implementationV1.address,
      owner.account.address,
      "0x",
    ]);

    // Uses the proxy as LendingPool
    // Creates a TypeScript object that uses the ABI of the LendingPool, but uses the proxy address
    const lendingPoolThroughProxy = await viem.getContractAt(
      "LendingPool",
      proxy.address,
    );

    // Proxy does not have an initialize function, so it enters the fallback and performs delegatecall to implementationV1
    await lendingPoolThroughProxy.write.initialize([owner.account.address], {
      account: owner.account,
    });

    // Reads the owner of the proxy to check if it is the one saved in owner.account.address
    const ownerOnProxy = await lendingPoolThroughProxy.read.owner();
    assert.equal(ownerOnProxy.toLowerCase(), owner.account.address.toLowerCase());

    // Deploys a second implementation
    const implementationV2 = await viem.deployContract("LendingPool");

    // Verifies that a non-admin cannot modify it
    await assert.rejects(
      proxy.write.upgradeTo([implementationV2.address], {
        account: other.account,
      }),
      /Only proxy admin/,
    );

    // Admin does the upgrade
    await proxy.write.upgradeTo([implementationV2.address], {
      account: owner.account,
    });

    // Verifies that the implementation has changed
    const currentImplementation = await proxy.read.implementation();
    assert.equal(
      currentImplementation.toLowerCase(),
      implementationV2.address.toLowerCase(),
    );
  });
});
