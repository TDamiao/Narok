import argparse
import json
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, getcontext
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests
from ape import accounts, networks, project
from ape.logging import logger
from dotenv import load_dotenv
from web3 import Web3

getcontext().prec = 64

ERC20_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]

CHAINLINK_AGGREGATOR_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"


@dataclass
class TokenEntry:
    symbol: str
    token_address: str
    decimals: int
    balance_of: Optional[str]
    price_source: Dict[str, str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update external NAV entries for the Narok vault.")
    parser.add_argument(
        "--only",
        nargs="*",
        help="List of symbols (e.g., cbBTC cbETH) to update. Default: every token in the config file.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Send transactions to setExternalAssetValue. Without this flag the script only logs calculated values.",
    )
    parser.add_argument(
        "--min-diff",
        default="1",
        help="Minimum USD difference (e.g., 1 or 0.5) before sending a transaction. Default = 1 USDC.",
    )
    return parser.parse_args()


def load_config(path: Path) -> List[TokenEntry]:
    if not path.exists():
        raise FileNotFoundError(
            f"Token config '{path}' not found. Create the file and list each token you want to track."
        )

    with path.open() as handle:
        raw = json.load(handle)

    entries = []
    for item in raw.get("tokens", []):
        symbol = item.get("symbol")
        token_address = item.get("token_address")
        price_source = item.get("price_source") or {}
        if not symbol or not token_address or not price_source:
            raise ValueError(f"Invalid token entry in {path}: {item}")

        entry = TokenEntry(
            symbol=symbol,
            token_address=token_address,
            decimals=int(item.get("decimals") or 0),
            balance_of=item.get("balance_of"),
            price_source=price_source,
        )
        entries.append(entry)
    if not entries:
        raise ValueError(f"No tokens found in {path}")
    return entries


def ensure_checksum(address: str) -> str:
    if not address.startswith("0x"):
        raise ValueError(f"Invalid address: {address}")
    return Web3.to_checksum_address(address)


def fetch_coingecko_prices(ids: Iterable[str]) -> Dict[str, Decimal]:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    params = {"ids": ",".join(ids), "vs_currencies": "usd"}
    response = requests.get(COINGECKO_URL, params=params, timeout=15)
    response.raise_for_status()
    payload = response.json()
    prices: Dict[str, Decimal] = {}
    for identifier in ids:
        entry = payload.get(identifier)
        if not entry or "usd" not in entry:
            raise RuntimeError(f"Price not found for {identifier} via CoinGecko")
        prices[identifier] = Decimal(str(entry["usd"]))
    return prices


def resolve_price(
    symbol: str,
    source: Dict[str, str],
    provider,
    coingecko_prices: Dict[str, Decimal],
) -> Decimal:
    source_type = (source.get("type") or "").lower()
    if source_type == "chainlink":
        feed_address = ensure_checksum(source["feed_address"])
        feed = provider.web3.eth.contract(address=feed_address, abi=CHAINLINK_AGGREGATOR_ABI)
        answer = feed.functions.latestRoundData().call()[1]
        if answer <= 0:
            raise RuntimeError(f"Chainlink returned an invalid price for {symbol} ({answer})")
        feed_decimals = source.get("feed_decimals")
        if feed_decimals is None:
            feed_decimals = feed.functions.decimals().call()
        feed_decimals = int(feed_decimals)
        return Decimal(answer) / Decimal(10**feed_decimals)
    if source_type == "fixed":
        if "price_usd" not in source:
            raise RuntimeError(f"Define price_usd for token {symbol}")
        return Decimal(str(source["price_usd"]))
    if source_type == "coingecko":
        cg_id = source.get("id")
        if not cg_id:
            raise RuntimeError(f"Define the CoinGecko 'id' for {symbol}")
        try:
            return coingecko_prices[cg_id]
        except KeyError as err:
            raise RuntimeError(f"Missing CoinGecko price for {cg_id}") from err
    raise RuntimeError(f"Unsupported price_source type in {symbol}: {source_type}")


def fetch_token_balance(token_address: str, owner: str, provider, decimals_hint: int) -> (int, int):
    erc20 = provider.web3.eth.contract(address=token_address, abi=ERC20_ABI)
    balance = erc20.functions.balanceOf(owner).call()
    decimals = decimals_hint or erc20.functions.decimals().call()
    return balance, int(decimals)


def to_usdc_scaled(balance: int, token_decimals: int, price: Decimal) -> int:
    if balance == 0:
        return 0
    token_amount = Decimal(balance) / Decimal(10**token_decimals)
    usd_value = token_amount * price
    scaled = (usd_value * Decimal(10**6)).to_integral_value(rounding=ROUND_HALF_UP)
    return int(scaled)


def build_min_diff(raw: str) -> int:
    diff = Decimal(str(raw))
    if diff < 0:
        raise ValueError("min-diff must be zero or positive")
    scaled = (diff * Decimal(10**6)).to_integral_value(rounding=ROUND_HALF_UP)
    return int(scaled)


def main():
    load_dotenv()
    args = parse_args()

    private_key = os.getenv("PRIVATE_KEY")
    if args.apply and not private_key:
        raise RuntimeError("Set PRIVATE_KEY in .env to send transactions.")

    vault_address = os.getenv("VAULT_ADDRESS")
    if not vault_address:
        raise RuntimeError("Set VAULT_ADDRESS in .env with the deployed NKVault address.")
    portfolio_wallet = os.getenv("PORTFOLIO_WALLET") or os.getenv("ADMIN_WALLET")
    if not portfolio_wallet:
        raise RuntimeError("Set PORTFOLIO_WALLET (or ADMIN_WALLET) in .env.")

    config_path = Path(os.getenv("PORTFOLIO_CONFIG", "portfolio.tokens.json"))
    tokens = load_config(config_path)

    provider = networks.provider
    if provider is None:
        raise RuntimeError("No active network. Run with `ape run scripts/update_external_nav.py --network base:mainnet -- ...`")

    coingecko_ids = [
        entry.price_source.get("id")
        for entry in tokens
        if (entry.price_source.get("type") or "").lower() == "coingecko"
    ]
    cg_prices = fetch_coingecko_prices(coingecko_ids)

    acct = accounts.from_key(private_key) if args.apply else None
    vault = project.NKVault.at(ensure_checksum(vault_address))
    default_holder = ensure_checksum(portfolio_wallet)
    min_diff = build_min_diff(args.min_diff)

    logger.info("Updating external NAV using %s", config_path)
    filtered = {sym.lower() for sym in (args.only or [])}

    updates = []
    for entry in tokens:
        if filtered and entry.symbol.lower() not in filtered:
            continue
        token_address = ensure_checksum(entry.token_address)
        holder_raw = entry.balance_of or portfolio_wallet
        holder = ensure_checksum(holder_raw) if holder_raw else default_holder
        balance, decimals = fetch_token_balance(token_address, holder, provider, entry.decimals)
        price = resolve_price(entry.symbol, entry.price_source, provider, cg_prices)
        new_value = to_usdc_scaled(balance, decimals, price)
        current_value = vault.externalAssetValue(token_address)
        delta = abs(int(new_value) - int(current_value))
        amount_in_token = Decimal(balance) / Decimal(10**decimals) if decimals else Decimal(0)
        logger.info(
            "%s | balance %.6f | price %s USD | new NAV %s USDC | current %s USDC",
            entry.symbol,
            amount_in_token,
            price,
            Decimal(new_value) / Decimal(10**6),
            Decimal(current_value) / Decimal(10**6),
        )
        if delta < min_diff:
            continue
        updates.append(
            {
                "symbol": entry.symbol,
                "token": token_address,
                "new": new_value,
                "current": current_value,
                "holder": holder,
            }
        )

    if not updates:
        logger.info("No token exceeded the threshold of %s USDC.", Decimal(min_diff) / Decimal(10**6))
        return

    logger.info("Will update %d token entries.", len(updates))
    if not args.apply:
        logger.info("Dry-run mode. Add --apply to broadcast transactions.")
        return

    for entry in updates:
        logger.info("Calling setExternalAssetValue(%s, %s)", entry["symbol"], entry["new"])
        tx = vault.setExternalAssetValue(entry["token"], entry["new"], sender=acct)
        if hasattr(tx, "await_confirmations"):
            tx.await_confirmations()
        logger.info("Tx %s confirmed for %s", tx.txn_hash, entry["symbol"])


if __name__ == "__main__":
    main()
