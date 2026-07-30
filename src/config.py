import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data" / "stocks"


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_tickers():
    return load_json(CONFIG_DIR / "tickers.json")["stocks"]


def load_strategy():
    return load_json(CONFIG_DIR / "strategy.json")


def get_api_keys():
    twelvedata_key = os.environ.get("TWELVEDATA_API_KEY")
    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    missing = [
        name
        for name, val in (("TWELVEDATA_API_KEY", twelvedata_key), ("FINNHUB_API_KEY", finnhub_key))
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set them locally before running, or as GitHub Actions secrets for the scheduled workflow."
        )
    return twelvedata_key, finnhub_key


def get_ibkr_credentials():
    """
    IBKR Flex Query credentials for the read-only positions fetch. Optional --
    returns None if not configured (or if the token has expired -- it has a
    configurable expiry in Client Portal, default 6h), so main.py can skip
    just the positions fetch. Fundamentals + state vector still compute
    either way, from Finnhub (already required for news) + thesis +
    technicals alone -- positions are the only thing that needs this.
    """
    ibkr_token = os.environ.get("IBKR_FLEX_TOKEN")
    ibkr_query_id = os.environ.get("IBKR_FLEX_QUERY_ID")
    if not (ibkr_token and ibkr_query_id):
        return None
    return ibkr_token, ibkr_query_id


def get_ibkr_cash_credentials():
    """
    IBKR Flex Query credentials for the read-only cash-balance ("dry
    powder") fetch -- a SEPARATE Flex Query (its own query ID) from
    get_ibkr_credentials() above, same account token. Optional -- returns
    None if not configured, so main.py can skip just this fetch.
    """
    ibkr_token = os.environ.get("IBKR_FLEX_TOKEN")
    cash_query_id = os.environ.get("IBKR_FLEX_CASH_QUERY_ID")
    if not (ibkr_token and cash_query_id):
        return None
    return ibkr_token, cash_query_id
