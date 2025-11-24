# Narok NK (Base)

USDC-backed NK vault on Base mainnet by Narok. Transparent, on-chain NAV with a manager role for external strategies.

**Badges:** MIT • Solidity 0.8.25 • Base mainnet (`chainId 8453`) • Public code, open for review  
**Quick links:** [Site](https://narok.finance) · [Contact](mailto:hello@narok.finance) · Verified vault address announced on the site

> This repository is for transparency and community review. It is **not** a guide to clone or deploy your own vault. Interact only with the official addresses published by Narok.

## What the vault does
- **USDC in / NK out:** deposits mint NK shares at the live NAV; redemptions burn NK for the corresponding USDC held by the vault (subject to liquidity).
- **External exposure tracking:** `externalNavUsd` stores the USDC-equivalent value of off-chain or non-USDC positions. NAV adjustments use `setExternalAssetValue`, `managerSetExternalAssetValue`, `invest`, and `divest`.
- **Manager-controlled capital:** the manager can move USDC out (`managerWithdrawUSDC`) to trade and return it later (`managerReturnUSDC`) while keeping NAV accurate.
- **Admin fee:** 2% yearly streamed to `adminWallet`, accrued per interaction via `_accrueFee()`.
- **Safety:** OpenZeppelin ERC20 + Ownable + ReentrancyGuard + SafeERC20; address validation on writes; USDC balance checks on withdrawals.

## Network and identifiers
- **Chain:** Base mainnet (`chainId 8453`)
- **Token:** `NK` (decimals: 6, matching USDC)
- **USDC on Base:** `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`
- **Vault address:** published on https://narok.finance once deployed/updated. Always verify against official announcements before interacting.

## For NK holders
- Deposit USDC via the official Narok interface or call `deposit(uint256 assets, address receiver)`.
- Redeem with `redeem(uint256 shares, address receiver, address owner)` or request a USDC amount via `withdraw(uint256 assets, address receiver, address owner)`. Withdrawals revert if the vault lacks USDC liquidity.
- Monitor price and NAV through `totalAssets()`, `totalSupply()`, and `externalNavUsd`.
- Events to watch: `Deposit`, `Withdraw`, `ExternalAssetUpdated`, `FeeAccrued`, `ManagerWithdrawUSDC`, `ManagerReturnUSDC`, `ManagerInvest`, `ManagerDivest`.

## Integrations (other protocols)
Any integrator can:
- Call `deposit` to mint NK for a beneficiary using USDC.
- Hold NK in their own contracts or treasuries as a yield-bearing, NAV-tracked position.
- Redeem programmatically with `withdraw`/`redeem` when liquidity is available.
- Observe NAV updates via events, or read `externalNavUsd` + `totalAssets()` for pricing.

## Roles and controls
- **Owner:** sets `adminWallet` and `manager`; can set external NAV entries.
- **Manager:** can move USDC out/in for trading and report NAV changes.
- **Admin:** receives the streaming fee.
- Liquidity protection: withdrawals revert if USDC in the vault is insufficient; this is deliberate to protect remaining holders.

## Transparency notes
- Solidity source: `contracts/NKVault.sol`. Scripts in `scripts/` show how Narok operates (deployment and NAV updates) for auditability, not for public redeploys.
- Secrets are never stored here; `.env` is git-ignored. `.env.example` only lists variable names.
- No formal audit is claimed. Independent review and on-chain verification are encouraged.

## Verifying the contract
- Compare the vault address published on https://narok.finance with the verified contract on Basescan (Base mainnet).
- The compiled artifact should match this repository’s `NKVault.sol`. Any source change requires re-verification before trusting a new address.

## Contact
- Site and dashboard: https://narok.finance
- Security or bug reports: hello@narok.finance

If you plan to integrate with NK or review the code, feel free to reach out. Public scrutiny and responsible disclosure are welcome.
