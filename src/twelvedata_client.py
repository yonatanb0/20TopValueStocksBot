"""
TwelveData client. Fetches daily OHLCV history for all tickers in ONE batched
HTTP request (comma-separated symbols) to stay within the free-tier 8
requests/minute limit. Credit usage is still 1 credit per symbol per call
(free tier cap: 800 credits/day), so keep this to a single call per run.
"""
import time
import requests

BASE_URL = "https://api.twelvedata.com/time_series"


def _parse_values(raw_values):
    """TwelveData returns newest-first; we want ascending chronological order."""
    parsed = []
    for row in raw_values:
        parsed.append(
            {
                "date": row["datetime"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(float(row.get("volume", 0) or 0)),
            }
        )
    parsed.reverse()
    return parsed


def fetch_daily_history(tickers, api_key, outputsize=260, timeout=30):
    """
    tickers: list of ticker symbol strings
    Returns: dict[ticker] -> list of {date, open, high, low, close, volume} ascending by date
    Raises RuntimeError on API-level errors; caller decides how to handle partial failures.
    """
    symbols_param = ",".join(tickers)
    params = {
        "symbol": symbols_param,
        "interval": "1day",
        "outputsize": outputsize,
        "apikey": api_key,
    }

    resp = requests.get(BASE_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()

    result = {}
    errors = {}

    if len(tickers) == 1:
        # Flat response shape when only one symbol is requested.
        ticker = tickers[0]
        if payload.get("status") == "error":
            errors[ticker] = payload.get("message", "unknown error")
        else:
            result[ticker] = _parse_values(payload["values"])
        if errors:
            raise RuntimeError(f"TwelveData errors: {errors}")
        return result

    # Batched response shape: keyed by symbol.
    for ticker in tickers:
        entry = payload.get(ticker)
        if entry is None:
            errors[ticker] = "missing from response"
            continue
        if entry.get("status") == "error":
            errors[ticker] = entry.get("message", "unknown error")
            continue
        result[ticker] = _parse_values(entry["values"])

    if errors and not result:
        raise RuntimeError(f"TwelveData errors, no data returned at all: {errors}")
    if errors:
        print(f"[twelvedata_client] Warning: partial failures: {errors}")

    return result
