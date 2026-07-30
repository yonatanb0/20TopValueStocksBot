import json
from datetime import datetime, timezone
from config import DATA_DIR, ROOT

THESIS_DIR = ROOT / "data" / "thesis"
STATE_DIR = ROOT / "data" / "state"
POSITIONS_PATH = ROOT / "data" / "positions.json"
BASKET_PATH = ROOT / "data" / "basket.json"


def _path(ticker):
    return DATA_DIR / f"{ticker}.json"


def init_stock_file(ticker, name, sector):
    path = _path(ticker)
    if path.exists():
        return
    data = {
        "ticker": ticker,
        "name": name,
        "sector": sector,
        "last_updated": None,
        "last_price": None,
        "last_price_date": None,
        "signal_log": [],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_stock(ticker):
    with open(_path(ticker), "r", encoding="utf-8") as f:
        return json.load(f)


def append_signals(ticker, signals, last_price=None, last_price_date=None):
    """
    signals: list of dicts, each must include a 'dedupe_key' string.
    Only signals whose dedupe_key isn't already present in the log get appended,
    so re-running within the same 2-hour cycle (or same trading day) doesn't spam duplicates.

    last_price / last_price_date: the latest close from this run's price fetch,
    recorded unconditionally regardless of whether any signal fired -- previously
    a price only ended up in the file as an incidental field on certain signal
    types (range_breakout, valuation_override_52w always had one; demand_zone_reaction
    only when it fired; rsi_divergence and distribution_and_trendline_break never did),
    so tickers with no matching signal had no price recorded anywhere at all.
    """
    data = load_stock(ticker)
    existing_keys = {s.get("dedupe_key") for s in data["signal_log"]}
    now = datetime.now(timezone.utc).isoformat()

    added = 0
    for sig in signals:
        if sig["dedupe_key"] in existing_keys:
            continue
        sig["timestamp"] = now
        data["signal_log"].append(sig)
        existing_keys.add(sig["dedupe_key"])
        added += 1

    data["last_updated"] = now
    if last_price is not None:
        data["last_price"] = last_price
        data["last_price_date"] = last_price_date
    with open(_path(ticker), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return added


def load_thesis(ticker):
    """Read-only: never written to by this bot. Returns None if the user
    hasn't uploaded a research thesis for this ticker yet."""
    path = THESIS_DIR / f"{ticker}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prior_state(ticker):
    path = STATE_DIR / f"{ticker}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_state(ticker, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / f"{ticker}.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def write_positions(positions_by_ticker):
    payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "positions": positions_by_ticker,
    }
    POSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(POSITIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def write_basket_analytics(basket):
    BASKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BASKET_PATH, "w", encoding="utf-8") as f:
        json.dump(basket, f, indent=2)
