import os
from dotenv import load_dotenv

load_dotenv()

# Ensure ape-geth uses the same RPC configured for Base.
BASE_RPC_URL = os.getenv("BASE_RPC_URL")
if BASE_RPC_URL:
    os.environ["WEB3_HTTP_PROVIDER_URI"] = BASE_RPC_URL

from ape import accounts, project
from ape.logging import logger


APE_ALIAS = os.getenv("APE_ACCOUNT_ALIAS")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
ADMIN_WALLET = os.getenv("ADMIN_WALLET")
MANAGER_WALLET = os.getenv("MANAGER_WALLET") or ADMIN_WALLET
USDC_ADDRESS = os.getenv("USDC_ADDRESS", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")  # Base mainnet USDC


def load_account():
    if APE_ALIAS:
        return accounts.load(APE_ALIAS)
    if not PRIVATE_KEY:
        raise RuntimeError("Set PRIVATE_KEY in .env or provide APE_ACCOUNT_ALIAS")
    return accounts.private_key_to_account(PRIVATE_KEY)


def main():
    acct = load_account()
    if not ADMIN_WALLET:
        raise RuntimeError("Set ADMIN_WALLET in .env")
    if not MANAGER_WALLET:
        raise RuntimeError("Set MANAGER_WALLET (or ADMIN_WALLET) in .env")

    logger.info("Deploying NKVault")
    logger.info("Deployer: %s", acct.address)
    logger.info("Admin   : %s", ADMIN_WALLET)
    logger.info("Manager : %s", MANAGER_WALLET)
    logger.info("USDC    : %s", USDC_ADDRESS)

    vault = acct.deploy(project.NKVault, USDC_ADDRESS, ADMIN_WALLET, MANAGER_WALLET)
    logger.info("NKVault deployed")

    print("\n=== Deployment summary ===")
    print(f"Deployer: {acct.address}")
    print(f"Admin   : {ADMIN_WALLET}")
    print(f"Manager : {MANAGER_WALLET}")
    print(f"USDC    : {USDC_ADDRESS}")
    print(f"Vault   : {vault.address}")
