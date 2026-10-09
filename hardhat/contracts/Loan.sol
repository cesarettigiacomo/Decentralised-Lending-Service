// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface ILendingPool {
    function receiveLoanRepayment(uint256 proposalId) external payable;

    function notifyLoanFailed(uint256 proposalId) external;

    function canTerminateLoan(uint256 proposalId) external view returns (bool);
}

contract Loan {
    enum LoanStatus {
        Active,
        Successful,
        Failed
    }

    address public lendingPool;
    address public applicant;

    uint256 public proposalId;
    uint256 public principal;
    uint256 public interestRate;
    uint256 public startBlock;
    uint256 public duration;
    uint256 public expirationBlock;

    uint256 public repaidAmount;

    LoanStatus public status;
    bool public terminated;

    event Repaid(address indexed applicant, uint256 amount);
    event MarkedSuccessful();
    event MarkedFailed();
    event LoanTerminated();

    modifier onlyLendingPool() {
        require(msg.sender == lendingPool, "Only lending pool");
        _;
    }

    modifier onlyApplicant() {
        require(msg.sender == applicant, "Only applicant");
        _;
    }

    modifier notTerminated() {
        require(!terminated, "Loan terminated");
        _;
    }

    constructor(
        address _lendingPool,
        address _applicant,
        uint256 _proposalId,
        uint256 _principal,
        uint256 _interestRate,
        uint256 _duration
    ) {
        require(_lendingPool != address(0), "Invalid lending pool");
        require(_applicant != address(0), "Invalid applicant");
        require(_principal > 0, "Invalid principal");
        require(
            _interestRate >= 1 && _interestRate <= 100,
            "Invalid interest rate"
        );
        require(_duration > 0, "Invalid duration");

        lendingPool = _lendingPool;
        applicant = _applicant;
        proposalId = _proposalId;
        principal = _principal;
        interestRate = _interestRate;
        duration = _duration;

        startBlock = block.number;
        expirationBlock = block.number + _duration;

        status = LoanStatus.Active;
    }

    // Calculates the amount that has to be repaid
    function totalDue() public view returns (uint256) {
        return principal + ((principal * interestRate) / 100);
    }

    // Calculates the remaining amount that has to be repaid
    function remainingDue() public view returns (uint256) {
        uint256 due = totalDue();

        if (repaidAmount >= due) {
            return 0;
        }

        return due - repaidAmount;
    }

    // Checks whether the loan is expired
    function isExpired() public view returns (bool) {
        return block.number > expirationBlock;
    }

    // The applicant may partially repay both active and failed loans
    // Overpayment is accepted; the LendingPool credits the excess to the compensation pool
    // A failed loan remains failed even if later repaid in full
    function repay() external payable onlyApplicant notTerminated {

        // Checks whether the loan is active
        require(
            status == LoanStatus.Active || status == LoanStatus.Failed,
            "Loan closed"
        );

        // Checks if ETH is sent
        require(msg.value > 0, "Invalid repayment"); // msg.value is the amount of ETH sent with the call

        // Updates the repaid amount
        repaidAmount += msg.value;

        emit Repaid(msg.sender, msg.value);

        if (status == LoanStatus.Active && repaidAmount >= totalDue()) {
            status = LoanStatus.Successful;
            emit MarkedSuccessful();
        }

        ILendingPool(lendingPool).receiveLoanRepayment{value: msg.value}(
            proposalId
        );
    }

    // Use cases:
    // loan is still Active
    // loan is expired
    // loan is not completely repaid
    function markFailed() external notTerminated {
        require(status == LoanStatus.Active, "Loan not active");
        require(isExpired(), "Loan not expired");
        require(repaidAmount < totalDue(), "Loan fully repaid");

        status = LoanStatus.Failed;

        emit MarkedFailed();

        ILendingPool(lendingPool).notifyLoanFailed(proposalId);
    }

    // Terminates the dedicated loan contract after all relevant accounting has been closed
    function terminate() external notTerminated {
        if (status == LoanStatus.Successful) {
            terminated = true;
            emit LoanTerminated();
            return;
        }

        require(status == LoanStatus.Failed, "Loan not closed");
        require(
            ILendingPool(lendingPool).canTerminateLoan(proposalId),
            "Loan has pending positions"
        );

        terminated = true;
        emit LoanTerminated();
    }
}
