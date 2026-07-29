import json
from datetime import datetime, timezone
from config import DATA_DIR


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
        "signal_log": [],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_stock(ticker):
    with open(_path(ticker), "r", encoding="utf-8") as f:
        return json.load(f)


def append_signals(ticker, signals):
    """
    signals: list of dicts, each must include a 'dedupe_key' string.
    Only signals whose dedupe_key isn't already present in the log get appended,
    so re-running within the same 2-hour cycle (or same trading day) doesn't spam duplicates.
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
    with open(_path(ticker), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return added
