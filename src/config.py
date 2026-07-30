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


def get_extended_api_keys():
    """
    FMP + IBKR Flex credentials for the positions/fundamentals/state-vector
    phase. Unlike get_api_keys() above (TwelveData/Finnhub, which the whole
    run depends on), missing extended keys don't fail the run -- returns
    None so main.py can skip just this phase, since it's purely additive on
    top of the existing signal pipeline.
    """
    fmp_key = os.environ.get("FMP_API_KEY")
    ibkr_token = os.environ.get("IBKR_FLEX_TOKEN")
    ibkr_query_id = os.environ.get("IBKR_FLEX_QUERY_ID")
    if not (fmp_key and ibkr_token and ibkr_query_id):
        return None
    return fmp_key, ibkr_token, ibkr_query_id
