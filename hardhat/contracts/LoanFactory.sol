// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./Loan.sol";

contract LoanFactory {
    address public owner;
    address public lendingPool;

    event LendingPoolSet(address indexed lendingPool);

    event LoanCreated(
        uint256 indexed proposalId,
        address indexed loanAddress,
        address indexed applicant,
        uint256 principal
    );

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    modifier onlyLendingPool() {
        require(msg.sender == lendingPool, "Only lending pool");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    // Sets the LendingPool authorized to use this factory
    function setLendingPool(address _lendingPool) external onlyOwner {
        require(_lendingPool != address(0), "Invalid lending pool");

        lendingPool = _lendingPool;

        emit LendingPoolSet(_lendingPool);
    }

    // Creates a Loan. The function is external but callable only by the LendingPool
    function createLoan(
        address applicant,
        uint256 proposalId,
        uint256 principal,
        uint256 interestRate,
        uint256 duration
    ) external onlyLendingPool returns (address loanAddress) {
        Loan loan = new Loan(
            msg.sender,
            applicant,
            proposalId,
            principal,
            interestRate,
            duration
        );

        loanAddress = address(loan);

        emit LoanCreated(
            proposalId,
            loanAddress,
            applicant,
            principal
        );
    }
}