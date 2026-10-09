import hardhatToolboxViemPlugin from "@nomicfoundation/hardhat-toolbox-viem";
import { defineConfig } from "hardhat/config";

const soliditySettings = {
  evmVersion: "berlin",
  optimizer: {
    enabled: true,
    runs: 200,
  },
  metadata: {
    bytecodeHash: "none",
  },
};

export default defineConfig({
  plugins: [hardhatToolboxViemPlugin],
  solidity: {
    profiles: {
      default: {
        version: "0.8.28",
        settings: soliditySettings,
      },
      production: {
        version: "0.8.28",
        settings: soliditySettings,
      },
    },
  },
  networks: {
    hardhatMainnet: {
      type: "edr-simulated",
      chainType: "l1",
    },
    hardhatOp: {
      type: "edr-simulated",
      chainType: "op",
    },
    localgeth: {
      type: "http",
      chainType: "l1",
      url: "http://127.0.0.1:8545",
      accounts: "remote",
      chainId: 202526,
    },
  },
});