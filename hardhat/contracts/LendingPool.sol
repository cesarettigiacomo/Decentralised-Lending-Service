// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./BitcoinOracle.sol";

interface ILoanFactory {
    function createLoan(
        address applicant,
        uint256 proposalId,
        uint256 principal,
        uint256 interestRate,
        uint256 duration
    ) external returns (address);
}

contract LendingPool {
    uint256 public constant MIN_DEPOSIT = 100_000 wei;
    uint256 public constant VOTING_PERIOD = 12;

    uint256 public constant INITIAL_COLLATERAL_PERCENTAGE = 50;
    uint256 public constant MIN_COLLATERAL_PERCENTAGE = 1;
    uint256 public constant MAX_COLLATERAL_PERCENTAGE = 100;
    uint256 public constant COLLATERAL_STEP = 5;

    enum ProposalStatus {
        Active,
        Approved,
        Rejected
    }

    enum VoteChoice {
        None,
        Approve,
        Reject
    }

    struct LoanProposal {
        address applicant;
        uint256 amount;
        uint256 interestRate;
        uint256 duration;
        string btcAddress;
        uint256 creationBlock;
        ProposalStatus status;
        uint256 loanedAmount;
        uint256 collateralPercentage;
    }

    struct Contributor {
        uint256 deposited;
        uint256 locked;
        uint256 pendingGains;
        bool exists;
    }

    LoanProposal[] public proposals;

    mapping(uint256 => mapping(address => VoteChoice)) public proposalVotes;

    // proposalId => contributor => current amount still locked for that proposal
    mapping(uint256 => mapping(address => uint256)) public lockedByProposal;

    // proposalId => contributor => initial amount locked when the loan was created
    // This is kept for repayment ordering and interest distribution
    mapping(uint256 => mapping(address => uint256)) public initialLockedByProposal;

    // proposalId => contributor => amount already paid from the compensation pool
    // This enables multiple compensation requests until the contributor has recovered all still-unrecovered locked value or the compensation pool is empty
    mapping(uint256 => mapping(address => uint256)) public compensatedByProposal;

    mapping(address => Contributor) public contributors;
    address[] public contributorList;

    address[] public loans;
    mapping(uint256 => address) public loanByProposal;

    mapping(uint256 => bool) public proposalRepaid;
    mapping(uint256 => bool) public proposalFailed;

    // Total value repaid by the applicant for a proposal
    mapping(uint256 => uint256) public repaidByProposal;

    // Split accounting used to support partial repayments and overpayments
    mapping(uint256 => uint256) public principalRepaidByProposal;
    mapping(uint256 => uint256) public interestRepaidByProposal;

    uint256 public compensationPool;

    address public owner;
    bool private initialized;

    BitcoinOracle public bitcoinOracle;
    ILoanFactory public loanFactory;
    mapping(address => uint256) public applicantCollateralPercentage;

    event Deposited(address indexed contributor, uint256 amount);
    event Withdrawn(address indexed contributor, uint256 amount);

    event LoanProposalSubmitted(
        uint256 indexed proposalId,
        address indexed applicant,
        uint256 amount,
        uint256 interestRate,
        uint256 duration,
        string btcAddress
    );

    event Voted(
        uint256 indexed proposalId,
        address indexed contributor,
        bool approve
    );

    event ProposalResolved(
        uint256 indexed proposalId,
        bool approved,
        uint256 approveWeight,
        uint256 rejectWeight
    );

    event ContributorFundsLocked(
        uint256 indexed proposalId,
        address indexed contributor,
        uint256 amount
    );

    event FundsLockedForProposal(
        uint256 indexed proposalId,
        uint256 requestedAmount,
        uint256 loanedAmount
    );

    event LoanTransferred(
        uint256 indexed proposalId,
        address indexed applicant,
        uint256 amount
    );

    event LoanCreated(
        uint256 indexed proposalId,
        address indexed loanAddress,
        address indexed applicant,
        uint256 principal
    );

    event LoanRepaymentReceived(
        uint256 indexed proposalId,
        address indexed loanAddress,
        uint256 amount,
        uint256 totalRepaid
    );

    event ContributorPrincipalUnlocked(
        uint256 indexed proposalId,
        address indexed contributor,
        uint256 unlockedAmount
    );

    event ContributorInterestCredited(
        uint256 indexed proposalId,
        address indexed contributor,
        uint256 interestGain
    );

    event GainsWithdrawn(address indexed contributor, uint256 amount);

    event LoanFailed(
        uint256 indexed proposalId,
        address indexed loanAddress,
        uint256 repaidAmount,
        uint256 unpaidAmount
    );

    event CompensationPoolFunded(uint256 indexed proposalId, uint256 amount);

    event CompensationClaimed(
        uint256 indexed proposalId,
        address indexed contributor,
        uint256 owedBeforeClaim,
        uint256 compensationPaid,
        uint256 owedAfterClaim
    );

    event ProposalRejectedByInsufficientBitcoinCollateral(
        uint256 indexed proposalId,
        address indexed applicant,
        string btcAddress,
        uint256 requiredCollateralWei,
        uint256 availableCollateralWei,
        uint256 collateralPercentage
    );

    event CollateralPercentageUpdated(
        address indexed applicant,
        uint256 oldPercentage,
        uint256 newPercentage
    );

    event BitcoinLiquidityCheckRequested(
        address indexed applicant,
        string btcAddress
    );

    event LoanFactorySet(address indexed loanFactoryAddress);
    event BitcoinOracleSet(address indexed bitcoinOracleAddress);
    event Initialized(address indexed owner);

    modifier onlyContributor() {
        require(contributors[msg.sender].deposited > 0, "Not a contributor");
        _;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    bool private locked;

    modifier nonReentrant() {
        require(!locked, "ReentrancyGuard: reentrant call");
        locked = true;
        _;
        locked = false;
    }

    constructor() {
        owner = msg.sender;
        initialized = true;
    }

    // Initializer used when the contract is deployed behind OwnedUpgradeProxy
    // Direct deployments keep using the constructor above
    function initialize(address initialOwner) external {
        require(!initialized, "Already initialized");
        require(initialOwner != address(0), "Invalid owner");

        owner = initialOwner;
        initialized = true;

        emit Initialized(initialOwner);
    }

    // Allows a user to deposit in the pool
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

    // Allows a contributor to withdraw non-locked funds
    function withdraw(uint256 amount) external nonReentrant {
        require(amount > 0, "Invalid amount");
        require(
            amount <= disposableValue(msg.sender),
            "Not enough disposable value"
        );

        contributors[msg.sender].deposited -= amount;

        emit Withdrawn(msg.sender, amount);

        (bool success, ) = payable(msg.sender).call{value: amount}("");
        require(success, "Withdraw transfer failed");
    }

    // An applicant can create a loan proposal
    function submitLoanProposal(
        uint256 amount,
        uint256 interestRate,
        uint256 duration,
        string calldata btcAddress
    ) external returns (uint256) {
        require(amount > 0, "Invalid amount");
        require(
            interestRate >= 1 && interestRate <= 100,
            "Invalid interest rate"
        );
        require(duration > 0, "Invalid duration");
        require(bytes(btcAddress).length > 0, "Invalid BTC address");

        proposals.push(
            LoanProposal({
                applicant: msg.sender,
                amount: amount,
                interestRate: interestRate,
                duration: duration,
                btcAddress: btcAddress,
                creationBlock: block.number,
                status: ProposalStatus.Active,
                loanedAmount: 0,
                collateralPercentage: 0
            })
        );

        uint256 proposalId = proposals.length - 1;

        emit LoanProposalSubmitted(
            proposalId,
            msg.sender,
            amount,
            interestRate,
            duration,
            btcAddress
        );

        return proposalId;
    }

    // Contributors vote for a proposal
    function vote(uint256 proposalId, bool approve) external onlyContributor {
        require(proposalId < proposals.length, "Invalid proposal id");

        LoanProposal storage proposal = proposals[proposalId];

        require(
            proposal.status == ProposalStatus.Active,
            "Proposal not active"
        );
        require(
            proposalVotes[proposalId][msg.sender] == VoteChoice.None,
            "Already voted"
        );

        // If approve is true, saves Approve; otherwise, saves Reject
        proposalVotes[proposalId][msg.sender] = approve
            ? VoteChoice.Approve
            : VoteChoice.Reject;

        emit Voted(proposalId, msg.sender, approve);
    }

    // Sums the disposable value of all contributors to calculate the total liquidity of the pool
    function cumulativeDisposableValue() public view returns (uint256 total) {
        for (uint256 i = 0; i < contributorList.length; i++) {
            total += disposableValue(contributorList[i]);
        }
    }

    function resolveProposal(uint256 proposalId) external nonReentrant {
        require(proposalId < proposals.length, "Invalid proposal id");

        LoanProposal storage proposal = proposals[proposalId];

        require(
            proposal.status == ProposalStatus.Active,
            "Proposal not active"
        );

        // Only the applicant can call this function after the end of the voting period to resolve the proposal
        require(proposal.applicant == msg.sender, "Only applicant can resolve");
        require(
            block.number > proposal.creationBlock + VOTING_PERIOD,
            "Voting period not ended"
        );

        // Calculates the total disposable value and, if it is not sufficient, rejects the proposal
        uint256 totalDisposable = cumulativeDisposableValue();

        if (totalDisposable < proposal.amount) {
            proposal.status = ProposalStatus.Rejected;
            emit ProposalResolved(proposalId, false, 0, totalDisposable);
            return;
        }

        // Checks with the oracle whether the BTC balance is sufficient
        if (!_hasEnoughBitcoinCollateral(proposal)) {
            proposal.status = ProposalStatus.Rejected;

            uint256 collateralPercentage = collateralPercentageOf(
                proposal.applicant
            );
            uint256 requiredCollateral = requiredCollateralWei(
                proposal.applicant,
                proposal.amount
            );
            uint256 availableCollateral = bitcoinOracle.btcValueInWei(
                proposal.btcAddress
            );

            emit ProposalRejectedByInsufficientBitcoinCollateral(
                proposalId,
                proposal.applicant,
                proposal.btcAddress,
                requiredCollateral,
                availableCollateral,
                collateralPercentage
            );

            emit ProposalResolved(proposalId, false, 0, totalDisposable);
            return;
        }

         // Calculates the weight of the Approve votes
        uint256 approveWeight = 0;

        for (uint256 i = 0; i < contributorList.length; i++) {
            address contributor = contributorList[i];

            if (proposalVotes[proposalId][contributor] == VoteChoice.Approve) {
                approveWeight += disposableValue(contributor);
            }
        }

        uint256 rejectWeight = totalDisposable - approveWeight;

        // Approves the proposal
        if (approveWeight > rejectWeight) {

            // Locks the funds
            uint256 loanedAmount = _lockFundsForApprovedProposal(
                proposalId,
                totalDisposable
            );

            if (loanedAmount == 0) {
                proposal.status = ProposalStatus.Rejected;
                emit ProposalResolved(
                    proposalId,
                    false,
                    approveWeight,
                    rejectWeight
                );
                return;
            }

            proposal.status = ProposalStatus.Approved;
            proposal.collateralPercentage = collateralPercentageOf(
                proposal.applicant
            );

            // Creates the Loan for the approved proposal
            _createLoanForApprovedProposal(proposalId, loanedAmount);

            emit ProposalResolved(
                proposalId,
                true,
                approveWeight,
                rejectWeight
            );

            // Transfers ETH to the applicant
            _transferLoanToApplicant(
                proposalId,
                proposal.applicant,
                loanedAmount
            );
        } else {
            // Rejects the proposal
            proposal.status = ProposalStatus.Rejected;
            emit ProposalResolved(
                proposalId,
                false,
                approveWeight,
                rejectWeight
            );
        }
    }

    // Locks the contributors' funds for the approved proposal proportionally to their disposable value
    function _lockFundsForApprovedProposal(
        uint256 proposalId,
        uint256 totalDisposable
    ) internal returns (uint256 loanedAmount) {
        LoanProposal storage proposal = proposals[proposalId];

        for (uint256 i = 0; i < contributorList.length; i++) {
            address contributor = contributorList[i];
            uint256 contributorDisposable = disposableValue(contributor);

            if (contributorDisposable == 0) {
                continue;
            }

            uint256 amountToLock = (proposal.amount * contributorDisposable) /
                totalDisposable;

            if (amountToLock == 0) {
                continue;
            }

            contributors[contributor].locked += amountToLock;
            lockedByProposal[proposalId][contributor] += amountToLock;
            initialLockedByProposal[proposalId][contributor] += amountToLock;
            loanedAmount += amountToLock;

            emit ContributorFundsLocked(proposalId, contributor, amountToLock);
        }

        proposal.loanedAmount = loanedAmount;
        emit FundsLockedForProposal(proposalId, proposal.amount, loanedAmount);
    }

    // Transfers ETH to the applicant
    function _transferLoanToApplicant(
        uint256 proposalId,
        address applicant,
        uint256 amount
    ) internal {
        (bool success, ) = payable(applicant).call{value: amount}("");
        require(success, "Loan transfer failed");

        emit LoanTransferred(proposalId, applicant, amount);
    }

    // Calls LoanFactory to create a Loan contract
    function _createLoanForApprovedProposal(
        uint256 proposalId,
        uint256 loanedAmount
    ) internal returns (address loanAddress) {
        require(address(loanFactory) != address(0), "Loan factory not set");

        LoanProposal storage proposal = proposals[proposalId];

        loanAddress = loanFactory.createLoan(
            proposal.applicant,
            proposalId,
            loanedAmount,
            proposal.interestRate,
            proposal.duration
        );

        // Saves the address of the new Loan in loans
        loans.push(loanAddress);
        loanByProposal[proposalId] = loanAddress;

        emit LoanCreated(
            proposalId,
            loanAddress,
            proposal.applicant,
            loanedAmount
        );
    }

    // Receives ETH from the dedicated Loan contract and accounts for partial repayments, failed-loan repayments and overpayments
    function receiveLoanRepayment(uint256 proposalId) external payable {
        require(proposalId < proposals.length, "Invalid proposal id");
        // Only the associated Loan contract
        require(msg.sender == loanByProposal[proposalId], "Only loan contract");
        // Checks the validity of the repaid amount for this call
        require(msg.value > 0, "Invalid repayment");
        require(!proposalRepaid[proposalId], "Proposal already repaid");

        LoanProposal storage proposal = proposals[proposalId];

        require(
            proposal.status == ProposalStatus.Approved,
            "Proposal not approved"
        );
        require(proposal.loanedAmount > 0, "Invalid loaned amount");

        uint256 principal = proposal.loanedAmount;
        uint256 interestDue = (principal * proposal.interestRate) / 100;

        // Updates the repaid amount for this proposal
        repaidByProposal[proposalId] += msg.value;

        // remaining is the current part of the repayment that has to be distributed
        uint256 remaining = msg.value;

        // First, repays the principal
        uint256 principalRemaining = principal - principalRepaidByProposal[proposalId];
        uint256 principalPortion = remaining <= principalRemaining
            ? remaining
            : principalRemaining;

        if (principalPortion > 0) {
            principalRepaidByProposal[proposalId] += principalPortion;

            // Unlocks the locked funds of the contributor
            uint256 principalForfeited = _unlockPrincipalRepayment(
                proposalId,
                principalPortion
            );

            // If the call returns a forfeited amount, this means that the contributor has already been repaid, for example through
            // claimedCompensation. If the borrower pays late, this amount is not given to the contributor but goes to the compensation pool
            if (principalForfeited > 0) {
                compensationPool += principalForfeited;
                emit CompensationPoolFunded(proposalId, principalForfeited);
            }

            remaining -= principalPortion;
        }

        // Second, repays the interest
        uint256 interestRemaining = interestDue - interestRepaidByProposal[proposalId];
        uint256 interestPortion = remaining <= interestRemaining
            ? remaining
            : interestRemaining;

        if (interestPortion > 0) {
            interestRepaidByProposal[proposalId] += interestPortion;

            uint256 collateralPercentage = proposal.collateralPercentage;
            if (collateralPercentage == 0) {
                collateralPercentage = collateralPercentageOf(proposal.applicant);
            }

            // Interest is divided into two parts:
            // compensationShare goes into the compensation pool
            // contributorsInterest goes to the contributors
            uint256 compensationShare =
                (interestPortion * collateralPercentage) /
                100;
            uint256 contributorsInterest = interestPortion - compensationShare;

            // Updates the compensation pool
            if (compensationShare > 0) {
                compensationPool += compensationShare;
                emit CompensationPoolFunded(proposalId, compensationShare);
            }

            // Distributes the contributors' interest, updating the pending gains
            _creditContributorInterest(proposalId, contributorsInterest);
            remaining -= interestPortion;
        }

        // Any value beyond principal + expected interest goes to the compensation pool
        if (remaining > 0) {
            compensationPool += remaining;
            emit CompensationPoolFunded(proposalId, remaining);
        }

        emit LoanRepaymentReceived(
            proposalId,
            msg.sender,
            msg.value,
            repaidByProposal[proposalId]
        );

        if (
            !proposalFailed[proposalId] &&
            principalRepaidByProposal[proposalId] >= principal &&
            interestRepaidByProposal[proposalId] >= interestDue
        ) {
            proposalRepaid[proposalId] = true;
            _decreaseCollateralPercentage(proposal.applicant);
        }
    }

    // Refunds principal following the required order: highest initial locked value first; in case of equality, lower contributor address first
    // Returns any amount that can no longer be credited to contributors because it was already compensated/forfeited; that amount is credited to the compensation pool
    function _unlockPrincipalRepayment(
        uint256 proposalId,
        uint256 amount
    ) internal returns (uint256 forfeited) {
        uint256 remaining = amount;

        // While there is capital, the function chooses the next contributor to repay
        while (remaining > 0) {
            address contributor = _nextContributorForPrincipalRepayment(
                proposalId
            );

            if (contributor == address(0)) {
                forfeited = remaining;
                break;
            }

            // If remaining <= lockedAmount, it unlocks remaining; otherwise, it unlocks all of lockedAmount
            uint256 lockedAmount = lockedByProposal[proposalId][contributor];
            uint256 unlocked = remaining <= lockedAmount
                ? remaining
                : lockedAmount;

            // Updates the locked amount for the contributor
            contributors[contributor].locked -= unlocked;

            // Updates the locked amount for this proposal
            lockedByProposal[proposalId][contributor] -= unlocked;

            remaining -= unlocked;

            emit ContributorPrincipalUnlocked(proposalId, contributor, unlocked);
        }
    }

    // Determines the next contributor to unlock after a repayment, starting with the one who has the highest locked value;
    // in case of equality, the contributor with the lowest address is selected
    function _nextContributorForPrincipalRepayment(
        uint256 proposalId
    ) internal view returns (address selected) {

        // Initial locked value of the selected contributor
        uint256 selectedInitialLocked = 0;

        for (uint256 i = 0; i < contributorList.length; i++) {

            // For each contributor, checks the locked amount for this proposal
            address contributor = contributorList[i];
            uint256 currentLocked = lockedByProposal[proposalId][contributor];

            if (currentLocked == 0) {
                continue;
            }

            // Saves the INITIAL locked amount for the contributor
            uint256 initialLocked = initialLockedByProposal[proposalId][
                contributor
            ];

            // Chooses the selected contributor if:
            // - No contributors have been selected yet
            // - OR has a higher initialValue
            // - OR has a lower address
            if (
                selected == address(0) ||
                initialLocked > selectedInitialLocked ||
                (initialLocked == selectedInitialLocked &&
                    contributor < selected)
            ) {
                selected = contributor;
                selectedInitialLocked = initialLocked;
            }
        }
    }

    // Distributes interest to the contributors and updates the pending gains; it does not transfer ETH
    function _creditContributorInterest(
        uint256 proposalId,
        uint256 contributorsInterest
    ) internal {
        if (contributorsInterest == 0) {
            return;
        }

        LoanProposal storage proposal = proposals[proposalId];
        uint256 principal = proposal.loanedAmount; // principal is needed to calculate the part of interest
        uint256 remainingInterest = contributorsInterest; // remainingInterest is needed to manage rounding
        address lastContributorWithInitialLock = address(0); // Contains the last contributor in contributorList who locked something for this proposal

        for (uint256 i = 0; i < contributorList.length; i++) {
            address contributor = contributorList[i];
            if (initialLockedByProposal[proposalId][contributor] > 0) {
                lastContributorWithInitialLock = contributor;
            }
        }

        require(
            lastContributorWithInitialLock != address(0),
            "No contributor locked funds"
        );

        for (uint256 i = 0; i < contributorList.length; i++) {
            address contributor = contributorList[i];
            uint256 initialLocked = initialLockedByProposal[proposalId][
                contributor
            ];

            // If the contributor did not contribute to this proposal, skips it
            if (initialLocked == 0) {
                continue;
            }

            uint256 interestGain;

            // If the contributor is the last one, assigns the remaining interest to them
            if (contributor == lastContributorWithInitialLock) {
                interestGain = remainingInterest;
            } else {
                // Otherwise, applies the formula proportionally to the initial locked amount of the contributor for the proposal
                interestGain =
                    (contributorsInterest * initialLocked) /
                    principal;
                remainingInterest -= interestGain;
            }

            // Adds the gains
            contributors[contributor].pendingGains += interestGain;
            emit ContributorInterestCredited(
                proposalId,
                contributor,
                interestGain
            );
        }
    }

     // Used by the contributor to withdraw interest
    function withdrawGains() external nonReentrant {
        uint256 amount = contributors[msg.sender].pendingGains;

        require(amount > 0, "No gains to withdraw");

        contributors[msg.sender].pendingGains = 0;

        emit GainsWithdrawn(msg.sender, amount);

        (bool success, ) = payable(msg.sender).call{value: amount}("");
        require(success, "Gains transfer failed");
    }

    // Can be called only by the Loan associated with the proposal. It is used to notify that the loan has not been fully repaid
    function notifyLoanFailed(uint256 proposalId) external {
        require(proposalId < proposals.length, "Invalid proposal id");
        require(msg.sender == loanByProposal[proposalId], "Only loan contract");

        // Verifies that the loan is neither fully repaid nor already failed
        require(!proposalRepaid[proposalId], "Proposal already repaid");
        require(!proposalFailed[proposalId], "Proposal already failed");

        LoanProposal storage proposal = proposals[proposalId];

        require(
            proposal.status == ProposalStatus.Approved,
            "Proposal not approved"
        );
        require(proposal.loanedAmount > 0, "Invalid loaned amount");

         // Gets the repaid amount
        uint256 principal = proposal.loanedAmount;
        uint256 interest = (principal * proposal.interestRate) / 100;
        uint256 totalDue = principal + interest;

        uint256 repaidAmount = repaidByProposal[proposalId];

        require(repaidAmount < totalDue, "Loan fully repaid");

        proposalFailed[proposalId] = true;

         // Increases the collateral percentage
        _increaseCollateralPercentage(proposal.applicant);

        uint256 unpaidAmount = totalDue - repaidAmount;
        emit LoanFailed(proposalId, msg.sender, repaidAmount, unpaidAmount);
    }

    // Allows a contributor to recover what can be recovered after an unpaid loan
    function claimCompensation(
        uint256 proposalId
    ) external onlyContributor nonReentrant {
        // Verifies that the proposal has failed and that compensation has not yet been requested by the contributor
        require(proposalId < proposals.length, "Invalid proposal id");
        require(proposalFailed[proposalId], "Proposal not failed");

        uint256 owedBeforeClaim = lockedByProposal[proposalId][msg.sender];

        require(owedBeforeClaim > 0, "No compensation owed");
        require(compensationPool > 0, "Compensation pool empty");

        uint256 compensationPaid = owedBeforeClaim <= compensationPool
            ? owedBeforeClaim
            : compensationPool;

        compensationPool -= compensationPaid;
        compensatedByProposal[proposalId][msg.sender] += compensationPaid;

        // The compensated part is no longer claimable from future repayments by this contributor
        lockedByProposal[proposalId][msg.sender] -= compensationPaid;
        contributors[msg.sender].locked -= compensationPaid;

        // The amount is paid out of the compensation pool; therefore, it is no longer part of the contributor's deposited position
        contributors[msg.sender].deposited -= compensationPaid;

        (bool success, ) = payable(msg.sender).call{value: compensationPaid}("");
        require(success, "Compensation transfer failed");

        uint256 owedAfterClaim = lockedByProposal[proposalId][msg.sender];

        emit CompensationClaimed(
            proposalId,
            msg.sender,
            owedBeforeClaim,
            compensationPaid,
            owedAfterClaim
        );
    }

    // Sets the Bitcoin oracle. Callable only by the owner
    function setBitcoinOracle(address bitcoinOracleAddress) external onlyOwner {
        require(bitcoinOracleAddress != address(0), "Invalid oracle");

        bitcoinOracle = BitcoinOracle(bitcoinOracleAddress);

        emit BitcoinOracleSet(bitcoinOracleAddress);
    }

    // Sets the Loan Factory. Callable only by the owner
    function setLoanFactory(address loanFactoryAddress) external onlyOwner {
        require(loanFactoryAddress != address(0), "Invalid loan factory");

        loanFactory = ILoanFactory(loanFactoryAddress);

        emit LoanFactorySet(loanFactoryAddress);
    }

    // Applicants call this payable function to request the off-chain BTC liquidity check
    // The fee is forwarded to BitcoinOracle.requestUpdate(), while the LendingPool event
    // is kept for backward compatibility with the existing oracle_service.py listener
    function requestBitcoinLiquidityCheck(
        string calldata btcAddress
    ) external payable {
        require(address(bitcoinOracle) != address(0), "Oracle not set");
        require(bytes(btcAddress).length > 0, "Invalid BTC address");

        bitcoinOracle.requestUpdate{value: msg.value}(btcAddress);

        emit BitcoinLiquidityCheckRequested(msg.sender, btcAddress);
    }

     // Returns the collateral percentage required from an applicant; if it is not set, returns the initial value
    function collateralPercentageOf(
        address applicant
    ) public view returns (uint256) {
        uint256 percentage = applicantCollateralPercentage[applicant];

        if (percentage == 0) {
            return INITIAL_COLLATERAL_PERCENTAGE;
        }

        return percentage;
    }

    // Calculates the required collateral, in wei
    function requiredCollateralWei(
        address applicant,
        uint256 loanAmount
    ) public view returns (uint256) {
        return (loanAmount * collateralPercentageOf(applicant)) / 100;
    }

     // Asks the oracle for the value of the BTC collateral associated with the proposal, in wei
    function bitcoinCollateralValueWei(
        uint256 proposalId
    ) public view returns (uint256) {
        require(proposalId < proposals.length, "Invalid proposal id");

        LoanProposal storage proposal = proposals[proposalId];
        return bitcoinOracle.btcValueInWei(proposal.btcAddress);
    }

    // Checks whether a Loan is completely closed or can be terminated
    function canTerminateLoan(uint256 proposalId) external view returns (bool) {
        require(proposalId < proposals.length, "Invalid proposal id");

        // A Loan can be considered closed if it has been fully repaid or if it has been marked as Failed
        if (!proposalRepaid[proposalId] && !proposalFailed[proposalId]) {
            return false;
        }

        // Checks whether any contributor still has locked funds for that Loan; if so, the Loan cannot be terminated
        for (uint256 i = 0; i < contributorList.length; i++) {
            if (lockedByProposal[proposalId][contributorList[i]] > 0) {
                return false;
            }
        }

        return true;
    }
    
    // Verifies whether the address has enough BTC collateral
    function _hasEnoughBitcoinCollateral(
        LoanProposal storage proposal
    ) internal view returns (bool) {
        require(address(bitcoinOracle) != address(0), "Oracle not set");

        uint256 collateralPercentage = collateralPercentageOf(
            proposal.applicant
        );

        return
            bitcoinOracle.hasEnoughCollateral(
                proposal.btcAddress,
                proposal.amount,
                collateralPercentage
            );
    }

    // Increases the collateral percentage for an applicant when a Loan fails
    function _increaseCollateralPercentage(address applicant) internal {
        uint256 oldPercentage = collateralPercentageOf(applicant);
        uint256 newPercentage = oldPercentage + COLLATERAL_STEP;

        if (newPercentage > MAX_COLLATERAL_PERCENTAGE) {
            newPercentage = MAX_COLLATERAL_PERCENTAGE;
        }

        applicantCollateralPercentage[applicant] = newPercentage;

        emit CollateralPercentageUpdated(
            applicant,
            oldPercentage,
            newPercentage
        );
    }

    // Decreases the collateral percentage for an applicant after the repayment of a loan
    function _decreaseCollateralPercentage(address applicant) internal {
        uint256 oldPercentage = collateralPercentageOf(applicant);
        uint256 newPercentage;

        if (oldPercentage <= MIN_COLLATERAL_PERCENTAGE + COLLATERAL_STEP) {
            newPercentage = MIN_COLLATERAL_PERCENTAGE;
        } else {
            newPercentage = oldPercentage - COLLATERAL_STEP;
        }

        applicantCollateralPercentage[applicant] = newPercentage;

        emit CollateralPercentageUpdated(
            applicant,
            oldPercentage,
            newPercentage
        );
    }
}
