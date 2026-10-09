// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract BitcoinOracle {
    uint256 public constant SATOSHI_PER_BTC = 100_000_000;
    uint256 public constant ETH_PER_BTC = 30;

    uint256 public constant ORACLE_FEE_GAS_PRICE = 0.1 gwei;

    // Estimated gas cost for the trusted oracle update operation
    // This value can be adjusted after gas measurement
    uint256 public constant ORACLE_UPDATE_GAS_ESTIMATE = 72_270;

    // Minimum fee required to request a BTC balance update
    uint256 public constant MIN_ORACLE_FEE =
        ORACLE_UPDATE_GAS_ESTIMATE * ORACLE_FEE_GAS_PRICE;

    // Address of the trusted oracle, the only address authorized to write BTC balances in the contract
    address public trustedOracle;

    mapping(string => uint256) private btcBalancesInSatoshi;
    mapping(string => bool) private btcBalanceRecorded;

    event BtcBalanceUpdateRequested(
        address indexed requester,
        string btcAddress,
        uint256 feePaid
    );

    event BtcBalanceRecorded(
        string btcAddress,
        uint256 balanceInSatoshi,
        uint256 valueInWei
    );

    event TrustedOracleChanged(
        address indexed oldOracle,
        address indexed newOracle
    );

    modifier onlyTrustedOracle() {
        require(msg.sender == trustedOracle, "Only trusted oracle");
        _;
    }

    constructor(address _trustedOracle) {
        require(_trustedOracle != address(0), "Invalid trusted oracle");
        trustedOracle = _trustedOracle;
    }

    // Payable request endpoint used by applicants to ask the off-chain oracle to refresh the BTC balance of a given Bitcoin address
    function requestUpdate(string calldata btcAddress) external payable {
        require(bytes(btcAddress).length > 0, "Invalid BTC address");
        require(msg.value >= MIN_ORACLE_FEE, "Oracle fee too low");

        emit BtcBalanceUpdateRequested(msg.sender, btcAddress, msg.value);
    }

    function minimumOracleFee() external pure returns (uint256) {
        return MIN_ORACLE_FEE;
    }

    // Registers on-chain the BTC balance of an address
    function setBtcBalance(
        string calldata btcAddress,
        uint256 balanceInSatoshi
    ) external onlyTrustedOracle {
        require(bytes(btcAddress).length > 0, "Invalid BTC address");

        // Records the balance
        btcBalancesInSatoshi[btcAddress] = balanceInSatoshi;
        btcBalanceRecorded[btcAddress] = true;

        emit BtcBalanceRecorded(
            btcAddress,
            balanceInSatoshi,
            _btcBalanceToWei(balanceInSatoshi)
        );
    }

    // Changes the trusted oracle
    function transferTrustedOracle(
        address newTrustedOracle
    ) external onlyTrustedOracle {
        require(newTrustedOracle != address(0), "Invalid trusted oracle");

        address oldOracle = trustedOracle;
        trustedOracle = newTrustedOracle;

        emit TrustedOracleChanged(oldOracle, newTrustedOracle);
    }

    // Checks whether a balance is recorded for an address
    function hasBtcBalanceRecord(
        string calldata btcAddress
    ) external view returns (bool) {
        return btcBalanceRecorded[btcAddress];
    }

    // Returns the recorded balance, in satoshi
    function btcBalanceInSatoshi(
        string calldata btcAddress
    ) external view returns (uint256) {
        return btcBalancesInSatoshi[btcAddress];
    }

    // Returns the value of the recorded balance, in wei
    function btcValueInWei(
        string calldata btcAddress
    ) public view returns (uint256) {
        if (!btcBalanceRecorded[btcAddress]) {
            return 0;
        }

        return _btcBalanceToWei(btcBalancesInSatoshi[btcAddress]);
    }

     // Calculates the requested collateral for a loan
    function requiredCollateralWei(
        uint256 loanAmountWei,
        uint256 collateralPercentage
    ) public pure returns (uint256) {
        return (loanAmountWei * collateralPercentage) / 100;
    }

    // Checks if a BTC address has enough collateral for a loan
    function hasEnoughCollateral(
        string calldata btcAddress,
        uint256 loanAmountWei,
        uint256 collateralPercentage
    ) external view returns (bool) {
        if (!btcBalanceRecorded[btcAddress]) {
            return false;
        }

        return
            btcValueInWei(btcAddress) >=
            requiredCollateralWei(loanAmountWei, collateralPercentage);
    }

    // Converts from satoshi to wei
    function _btcBalanceToWei(
        uint256 balanceInSatoshi
    ) internal pure returns (uint256) {
        return (balanceInSatoshi * ETH_PER_BTC * 1 ether) / SATOSHI_PER_BTC;
    }
}
