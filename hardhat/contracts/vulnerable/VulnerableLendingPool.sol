// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// This contract is an intentionally vulnerable variant of the LendingPool deposit/withdraw logic

 /*
 *      The vulnerability is in withdraw():
 *      - the external call is executed before updating the contributor state
 *      - during that external call, a malicious contract can re-enter withdraw()
 *      - each re-entrant call still sees the old deposited value
 *
 *      This file is only used by test/ReentrancyAttack.test.ts to demonstrate
 *      how the safe LendingPool would become vulnerable if the checks-effects-
 *      interactions pattern / nonReentrant protection were removed
 */
contract VulnerableLendingPool {
    uint256 public constant MIN_DEPOSIT = 100_000 wei;

    struct Contributor {
        uint256 deposited;
        uint256 locked;
        uint256 pendingGains;
        bool exists;
    }

    mapping(address => Contributor) public contributors;
    address[] public contributorList;

    event Deposited(address indexed contributor, uint256 amount);
    event Withdrawn(address indexed contributor, uint256 amount);

    function deposit() external payable {
        require(msg.value > MIN_DEPOSIT, "Deposit too small");

        Contributor storage c = contributors[msg.sender];

        if (!c.exists) {
            c.exists = true;
            contributorList.push(msg.sender);
        }

        c.deposited += msg.value;

        emit Deposited(msg.sender, msg.value);
    }

    function disposableValue(address contributor) public view returns (uint256) {
        Contributor memory c = contributors[contributor];
        return c.deposited - c.locked;
    }

    /**
     * Intentionally vulnerable withdraw
     *
     * Secure logic should update the internal state before the external
     * call and should usually be protected by a nonReentrant modifier:
     *
     * contributors[msg.sender].deposited -= amount;
     * (bool success, ) = payable(msg.sender).call{value: amount}("");
     * require(success, "Withdraw transfer failed");
     *
     * Here, the opposite is done on purpose
     */
    function withdraw(uint256 amount) external {
        require(amount > 0, "Invalid amount");
        require(
            amount <= disposableValue(msg.sender),
            "Not enough disposable value"
        );

        uint256 oldDeposited = contributors[msg.sender].deposited;

        // VULNERABILITY:
        // External call before committing the state update
        (bool success, ) = payable(msg.sender).call{value: amount}("");
        require(success, "Withdraw transfer failed");

        // Still wrong:
        // Re-entrant calls have already happened while deposited was unchanged
        contributors[msg.sender].deposited = oldDeposited - amount;

        emit Withdrawn(msg.sender, amount);
    }

    receive() external payable {}
}
