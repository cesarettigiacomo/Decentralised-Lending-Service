// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Maintains address and storage
contract OwnedUpgradeProxy {
    bytes32 private constant IMPLEMENTATION_SLOT =
        0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;

    bytes32 private constant ADMIN_SLOT =
        0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103;

    event Upgraded(address indexed implementation);
    event AdminChanged(address indexed oldAdmin, address indexed newAdmin);

    constructor(
        address initialImplementation,
        address initialAdmin,
        // initData contains the ABI-encoded initialization call that the proxy executes on the implementation during deployment
        bytes memory initData
    ) payable {

        // Checks the validity of the addresses
        require(initialImplementation != address(0), "Invalid implementation");
        require(initialAdmin != address(0), "Invalid admin");

        // Writes the admin and implementation to the proxy storage
        _setAdmin(initialAdmin);
        _setImplementation(initialImplementation);

        // Initializes the state of the proxy
        if (initData.length > 0) {
            // Executes the initialization of the implementation contract
            // With delegatecall, the implementation's code is executed, but any state changes are applied to the proxy's storage
            // ok is false if the delegatecall fails, true otherwise
            // reason contains the data returned by the call
            (bool ok, bytes memory reason) = initialImplementation.delegatecall(
                initData
            );

            // If ok is false, it means that the initialization has failed. In that situation:
            // - The constructor has to fail
            // - If the constructor fails, the proxy deployment also fails
            if (!ok) {
                assembly { // assembly is needed to write code at a low level in the EVM

                    // Reverts, returning the error bytes contained in reason
                    // mload(reason) reads the length of reason
                    // add(reason, 32) skips the first 32 bytes of reason and points to the beginning of the data
                    revert(add(reason, 32), mload(reason)) // Returns the error produced by the implementation
                }
            }
        }
    }

    modifier onlyAdmin() {
        require(msg.sender == admin(), "Only proxy admin");
        _;
    }

    // Reads the admin from the storage slot ADMIN_SLOT
    function admin() public view returns (address adm) {
        bytes32 slot = ADMIN_SLOT;
        assembly {
            adm := sload(slot) // Reads the value in the storage slot
        }
    }

    // Reads the implementation address
    function implementation() public view returns (address impl) {
        bytes32 slot = IMPLEMENTATION_SLOT;
        assembly {
            impl := sload(slot)
        }
    }

    // Changes admin
    function changeAdmin(address newAdmin) external onlyAdmin {
        // Checks the validity of the new admin address
        require(newAdmin != address(0), "Invalid admin");

        // Saves the old admin
        address oldAdmin = admin();

        // Updates the slot ADMIN_SLOT
        _setAdmin(newAdmin);
        emit AdminChanged(oldAdmin, newAdmin);
    }

    // Upgrades the implementation
    function upgradeTo(address newImplementation) external onlyAdmin {
        require(newImplementation != address(0), "Invalid implementation");
        _setImplementation(newImplementation);
        emit Upgraded(newImplementation);
    }

    // Saves newAdmin in the storage slot ADMIN_SLOT
    function _setAdmin(address newAdmin) internal {
        bytes32 slot = ADMIN_SLOT;
        assembly {
            sstore(slot, newAdmin)
        }
    }

    // Saves newImplementation in the storage slot IMPLEMENTATION_SLOT
    function _setImplementation(address newImplementation) internal {
        bytes32 slot = IMPLEMENTATION_SLOT;
        assembly {
            sstore(slot, newImplementation)
        }
    }

    // It is executed when there is a call to a function that does not exist in the proxy
    // In this situation, the call data is delegated to the implementation
    // Since delegatecall is used, the executed code is the implementation's code, but the modified storage is the proxy's storage
    fallback() external payable {
        _delegate(implementation());
    }

    // Called when the proxy receives ETH without calldata
    // The call is delegated to the current implementation, which handles the ETH transfer in the proxy's context
    receive() external payable {
        _delegate(implementation());
    }

    // Delegates the current call to the implementation contract
    // The implementation code runs in the proxy context, so storage changes affect the proxy
    function _delegate(address impl) internal {
        assembly {
            // Copies into memory the data received by the original call
            calldatacopy(0, 0, calldatasize()) 

            // Forwards the call to the implementation using delegatecall
            // The implementation code is executed in the proxy context, so storage changes affect the proxy storage
            let result := delegatecall(gas(), impl, 0, calldatasize(), 0, 0) // gas() is needed to determine how much gas can be used to execute the called code

            // Copies into memory the data received from the implementation; this may contain either result values or revert data
            returndatacopy(0, 0, returndatasize())
            switch result
            case 0 {
                // If the implementation fails, the proxy reverts
                revert(0, returndatasize())
            }
            default {
                // Returns the result obtained from the implementation
                return(0, returndatasize())
            }
        }
    }
}
