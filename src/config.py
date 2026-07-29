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
    newsapi_key = os.environ.get("NEWSAPI_API_KEY")
    missing = [
        name
        for name, val in (("TWELVEDATA_API_KEY", twelvedata_key), ("NEWSAPI_API_KEY", newsapi_key))
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set them locally before running, or as GitHub Actions secrets for the scheduled workflow."
        )
    return twelvedata_key, newsapi_key
