"""
Financial Modeling Prep client (fundamentals). Free tier is the "stable" API
(the older /api/v3/* endpoints are fully retired -- confirmed live, they now
return a "Legacy Endpoint" error regardless of key). Budgeted at 2 calls per
ticker per run (key-metrics-ttm + analyst-estimates), ~40/day at 20 tickers,
well under the free plan's daily cap. Quote isn't fetched here -- callers pass
in the current price they already have from TwelveData.

No true "3-month consensus estimate revision" endpoint exists on the free
tier (that needs a point-in-time estimate history FMP doesn't expose). We
approximate it in state_vector.py instead, by comparing this run's epsAvg
against a snapshot stored in data/state/{TICKER}.json from ~3 months back --
this client just returns the current raw estimate.
"""
from datetime import date

import requests

BASE_URL = "https://financialmodelingprep.com/stable"


def _get(endpoint, params, timeout):
    resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=timeout)
    if resp.status_code == 402:
        # FMP's free tier gates several "stable" endpoints to a small allowed-symbol
        # list (confirmed live: AAPL/NVDA/INTC/CSCO/VZ work, most of our other 15
        # tickers don't) -- this is a real per-symbol plan restriction, not a rate
        # limit or a transient error, so it gets its own clear message.
        raise RuntimeError(
            f"FMP free-tier restriction on {endpoint} for symbol={params.get('symbol')}: {resp.text.strip()}"
        )
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict) and "Error Message" in payload:
        raise RuntimeError(f"FMP error on {endpoint}: {payload['Error Message']}")
    return payload


def fetch_ev_ebitda_ttm(ticker, api_key, timeout=20):
    payload = _get("key-metrics-ttm", {"symbol": ticker, "apikey": api_key}, timeout)
    if not payload:
        return None
    return payload[0].get("evToEBITDATTM")


def fetch_forward_estimate(ticker, api_key, timeout=20):
    """
    Returns the analyst consensus estimate for the nearest future fiscal
    year: {fiscal_year_end, eps_avg, num_analysts_eps, revenue_avg}, or None
    if no future-dated estimate is available. Used both for forward P/E
    (price / eps_avg, computed by the caller) and as this run's snapshot for
    revision-direction tracking.
    """
    payload = _get(
        "analyst-estimates",
        {"symbol": ticker, "period": "annual", "limit": 10, "apikey": api_key},
        timeout,
    )
    today = date.today().isoformat()
    future = [row for row in payload if row.get("date", "") > today]
    if not future:
        return None
    nearest = min(future, key=lambda row: row["date"])
    eps_avg = nearest.get("epsAvg")
    if eps_avg is None:
        return None
    return {
        "fiscal_year_end": nearest["date"],
        "eps_avg": eps_avg,
        "num_analysts_eps": nearest.get("numAnalystsEps"),
        "revenue_avg": nearest.get("revenueAvg"),
    }


def fetch_fundamentals(ticker, api_key, current_price, timeout=20):
    """
    Returns {forward_pe, ev_ebitda_ttm, forward_estimate} for one ticker.
    Never raises -- fundamentals are a nice-to-have slice of the state
    vector, not something that should take down a whole run.
    """
    result = {"forward_pe": None, "ev_ebitda_ttm": None, "forward_estimate": None}
    try:
        result["ev_ebitda_ttm"] = fetch_ev_ebitda_ttm(ticker, api_key, timeout)
    except Exception as e:
        print(f"[fmp_client] WARNING: EV/EBITDA fetch failed for {ticker}: {e}")

    try:
        estimate = fetch_forward_estimate(ticker, api_key, timeout)
        result["forward_estimate"] = estimate
        if estimate and current_price is not None and estimate["eps_avg"]:
            result["forward_pe"] = round(current_price / estimate["eps_avg"], 2)
    except Exception as e:
        print(f"[fmp_client] WARNING: forward estimate fetch failed for {ticker}: {e}")

    return result


def fetch_fundamentals_batch(tickers_to_prices, api_key, timeout=20):
    """tickers_to_prices: dict[ticker] -> current_price (or None)."""
    result = {}
    for ticker, price in tickers_to_prices.items():
        result[ticker] = fetch_fundamentals(ticker, api_key, price, timeout)
    return result
