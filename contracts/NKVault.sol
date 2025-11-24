// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title NKVault
 * @notice USDC-backed NK with a manager role that can deploy capital off-chain while keeping NAV tracking
 *         on-chain. Fees stream to the admin wallet and external positions are reported via `externalNavUsd`.
 */
contract NKVault is ERC20, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    uint256 public constant FEE_BPS = 200; // 2% yearly
    uint256 public constant BPS_DENOMINATOR = 10_000;
    uint256 public constant YEAR = 365 days;
    string private constant LIQUIDITY_LIMIT_MSG = "Liquidity capped until manager tops up USDC";

    IERC20 public immutable usdc;
    uint8 private immutable shareDecimals;
    address public adminWallet;
    address public manager;
    uint256 public lastAccrual;
    uint256 public externalNavUsd;

    mapping(address => uint256) public externalAssetValue;

    event Deposit(address indexed sender, address indexed receiver, uint256 assets, uint256 shares);
    event Withdraw(address indexed caller, address indexed receiver, address indexed owner, uint256 assets, uint256 shares);
    event FeeAccrued(uint256 assets);
    event AdminWalletUpdated(address newAdmin);
    event ExternalAssetUpdated(address indexed asset, uint256 usdValue, uint256 newTotalNav);
    event ManagerUpdated(address newManager);
    event ManagerWithdrawUSDC(address indexed manager, address indexed to, uint256 amount);
    event ManagerReturnUSDC(address indexed manager, address indexed from, uint256 amount);
    event ManagerInvest(address indexed manager, address indexed asset, uint256 usdValue, uint256 newExternalNav);
    event ManagerDivest(address indexed manager, address indexed asset, uint256 usdValue, uint256 newExternalNav);

    error InvalidAddress();
    error ZeroAmount();

    modifier onlyManager() {
        require(msg.sender == manager, "Not manager");
        _;
    }

    constructor(address usdcToken, address admin, address manager_) ERC20("Narok NK", "NK") Ownable(msg.sender) {
        if (usdcToken == address(0) || admin == address(0)) revert InvalidAddress();
        usdc = IERC20(usdcToken);
        shareDecimals = IERC20Metadata(usdcToken).decimals();
        adminWallet = admin;
        manager = manager_ == address(0) ? admin : manager_;
        lastAccrual = block.timestamp;
    }

    function decimals() public view override returns (uint8) {
        return shareDecimals;
    }

    /**
     * @notice Configure the governance-managed manager wallet that can deploy USDC off-chain.
     * @param newManager The address that will manage external capital deployments.
     */
    function setManager(address newManager) external onlyOwner {
        if (newManager == address(0)) revert InvalidAddress();
        manager = newManager;
        emit ManagerUpdated(newManager);
    }

    function setAdminWallet(address newAdmin) external onlyOwner {
        if (newAdmin == address(0)) revert InvalidAddress();
        adminWallet = newAdmin;
        emit AdminWalletUpdated(newAdmin);
    }

    function totalAssets() public view returns (uint256) {
        return usdc.balanceOf(address(this)) + externalNavUsd;
    }

    function previewDeposit(uint256 assets) public view returns (uint256) {
        uint256 supply = totalSupply();
        uint256 total = totalAssets();
        if (supply == 0 || total == 0) return assets;
        return (assets * supply) / total;
    }

    function previewRedeem(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply();
        uint256 total = totalAssets();
        if (supply == 0) return 0;
        return (shares * total) / supply;
    }

    function deposit(uint256 assets, address receiver) external nonReentrant returns (uint256 shares) {
        _accrueFee();
        if (assets == 0) revert ZeroAmount();
        shares = previewDeposit(assets);
        if (shares == 0) revert ZeroAmount();
        usdc.safeTransferFrom(msg.sender, address(this), assets);
        _mint(receiver, shares);
        emit Deposit(msg.sender, receiver, assets, shares);
    }

    function withdraw(uint256 assets, address receiver, address owner_) external nonReentrant returns (uint256 shares) {
        _accrueFee();
        if (assets == 0) revert ZeroAmount();
        uint256 supply = totalSupply();
        uint256 total = totalAssets();
        if (supply == 0 || total == 0) revert ZeroAmount();
        shares = (assets * supply) / total;
        _redeem(shares, receiver, owner_);
        return shares;
    }

    function redeem(uint256 shares, address receiver, address owner_) external nonReentrant returns (uint256 assets) {
        _accrueFee();
        if (shares == 0) revert ZeroAmount();
        assets = previewRedeem(shares);
        _redeem(shares, receiver, owner_);
        return assets;
    }

    function accrueFee() external nonReentrant {
        _accrueFee();
    }

    function setExternalAssetValue(address asset, uint256 usdValue) external onlyOwner {
        _updateExternalAssetValue(asset, usdValue);
    }

    /**
     * @notice Manager-only mirror of `setExternalAssetValue` so NAV tracking can be updated alongside trades.
     */
    function managerSetExternalAssetValue(address asset, uint256 usdValue) external onlyManager {
        _updateExternalAssetValue(asset, usdValue);
    }

    /**
     * @notice Withdraw USDC from the vault for the manager to deploy in external trading strategies.
     * @param amount USDC amount to withdraw.
     * @param to Target address (e.g., trading wallet) receiving the USDC.
     */
    function managerWithdrawUSDC(uint256 amount, address to) external onlyManager nonReentrant {
        require(to != address(0), "Invalid target");
        if (amount == 0) revert ZeroAmount();
        _accrueFee();

        uint256 usdcBalance = usdc.balanceOf(address(this));
        require(amount <= usdcBalance, "Insufficient vault USDC");
        usdc.safeTransfer(to, amount);
        emit ManagerWithdrawUSDC(msg.sender, to, amount);
    }

    /**
     * @notice Returns USDC back into the vault after trades complete.
     * @param amount USDC amount being returned; caller must have approved the vault.
     */
    function managerReturnUSDC(uint256 amount) external onlyManager nonReentrant {
        if (amount == 0) revert ZeroAmount();
        usdc.safeTransferFrom(msg.sender, address(this), amount);
        emit ManagerReturnUSDC(manager, msg.sender, amount);
    }

    /**
     * @notice Increase the NAV allocated to a specific external asset when deploying capital.
     * @param asset External asset identifier (e.g., cbBTC, cbETH).
     * @param usdValue USD value to add to the tracked NAV for that asset.
     */
    function invest(address asset, uint256 usdValue) external onlyManager {
        if (asset == address(0)) revert InvalidAddress();
        uint256 previous = externalAssetValue[asset];
        uint256 next = previous + usdValue;
        externalAssetValue[asset] = next;
        externalNavUsd += usdValue;
        emit ManagerInvest(msg.sender, asset, next, externalNavUsd);
    }

    /**
     * @notice Decrease the NAV allocated to an external asset when capital is being withdrawn.
     * @param asset External asset identifier.
     * @param usdValue USD value to remove from the tracked NAV for that asset.
     */
    function divest(address asset, uint256 usdValue) external onlyManager {
        if (asset == address(0)) revert InvalidAddress();
        uint256 previous = externalAssetValue[asset];
        require(usdValue <= previous, "Amount exceeds tracked NAV");
        uint256 next = previous - usdValue;
        externalAssetValue[asset] = next;
        externalNavUsd -= usdValue;
        emit ManagerDivest(msg.sender, asset, next, externalNavUsd);
    }

    function _redeem(uint256 shares, address receiver, address owner_) internal {
        uint256 assets = previewRedeem(shares);
        if (assets == 0) revert ZeroAmount();
        require(assets <= usdc.balanceOf(address(this)), LIQUIDITY_LIMIT_MSG);
        if (msg.sender != owner_) _spendAllowance(owner_, msg.sender, shares);
        _burn(owner_, shares);
        usdc.safeTransfer(receiver, assets);
        emit Withdraw(msg.sender, receiver, owner_, assets, shares);
    }

    function _accrueFee() internal {
        uint256 dt = block.timestamp - lastAccrual;
        if (dt == 0) return;
        lastAccrual = block.timestamp;

        uint256 usdcBalance = usdc.balanceOf(address(this));
        uint256 total = usdcBalance + externalNavUsd;
        if (total == 0) return;

        uint256 fee = (total * FEE_BPS * dt) / (BPS_DENOMINATOR * YEAR);
        if (fee > usdcBalance) fee = usdcBalance;
        if (fee == 0) return;

        usdc.safeTransfer(adminWallet, fee);
        emit FeeAccrued(fee);
    }

    function _updateExternalAssetValue(address asset, uint256 usdValue) internal {
        if (asset == address(0)) revert InvalidAddress();
        uint256 previous = externalAssetValue[asset];
        externalAssetValue[asset] = usdValue;
        externalNavUsd = externalNavUsd - previous + usdValue;
        emit ExternalAssetUpdated(asset, usdValue, externalNavUsd);
    }
}
