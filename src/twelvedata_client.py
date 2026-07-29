"""
TwelveData client. Free tier caps at 8 API CREDITS per minute (confirmed via
the API's own error message -- this is a per-minute credit budget, not a
per-request one), and each symbol in a batched call costs 1 credit regardless
of batching. So a single call with all 20 symbols burns through the whole
minute's budget instantly and gets rate-limited. We chunk into groups of
<= MAX_SYMBOLS_PER_CALL and pace the chunks a minute apart.
"""
import time
import requests

BASE_URL = "https://api.twelvedata.com/time_series"
MAX_SYMBOLS_PER_CALL = 8
SECONDS_BETWEEN_CHUNKS = 61


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


def _fetch_chunk(tickers, api_key, outputsize, timeout):
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


def _chunk_list(items, size):
    return [items[i : i + size] for i in range(0, len(items), size)]


def fetch_daily_history(tickers, api_key, outputsize=260, timeout=30):
    """
    tickers: list of ticker symbol strings
    Returns: dict[ticker] -> list of {date, open, high, low, close, volume} ascending by date
    Chunks into groups of MAX_SYMBOLS_PER_CALL to respect the free-tier
    8-credits/minute cap, pacing chunks SECONDS_BETWEEN_CHUNKS apart.
    """
    chunks = _chunk_list(tickers, MAX_SYMBOLS_PER_CALL)
    result = {}
    for i, chunk in enumerate(chunks):
        if i > 0:
            print(f"[twelvedata_client] Pausing {SECONDS_BETWEEN_CHUNKS}s to stay under the per-minute credit cap...")
            time.sleep(SECONDS_BETWEEN_CHUNKS)
        print(f"[twelvedata_client] Fetching chunk {i + 1}/{len(chunks)}: {chunk}")
        result.update(_fetch_chunk(chunk, api_key, outputsize, timeout))
    return result
