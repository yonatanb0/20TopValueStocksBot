"""
Finnhub client. Free tier: 60 API calls/minute, no hard daily cap, and
company-news is scoped per-symbol directly (no OR-query text matching needed
to attribute articles to a ticker). Also isn't subject to NewsAPI's ~24h lag.
"""
import time
from datetime import datetime, timedelta, timezone

import requests

BASE_URL = "https://finnhub.io/api/v1"


def _normalize(article):
    dt = article.get("datetime")
    published = (
        datetime.fromtimestamp(dt, tz=timezone.utc).isoformat() if dt else None
    )
    return {
        "title": article.get("headline"),
        "description": article.get("summary"),
        "url": article.get("url"),
        "publishedAt": published,
        "source": (article.get("source") or ""),
    }


def fetch_company_news(ticker, api_key, days_back=3, timeout=20):
    to_date = datetime.now(timezone.utc).date()
    from_date = to_date - timedelta(days=days_back)
    params = {
        "symbol": ticker,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "token": api_key,
    }
    resp = requests.get(f"{BASE_URL}/company-news", params=params, timeout=timeout)
    resp.raise_for_status()
    return [_normalize(a) for a in resp.json()]


def fetch_company_news_batch(tickers, api_key, days_back=3, throttle_seconds=0.3):
    """
    One call per ticker (Finnhub has no OR-batch endpoint for company news).
    Free tier allows 60 calls/min, so 20 tickers comfortably fit with a small
    throttle to stay clear of any burst limit.
    """
    result = {}
    for ticker in tickers:
        try:
            result[ticker] = fetch_company_news(ticker, api_key, days_back=days_back)
        except Exception as e:
            print(f"[finnhub_client] WARNING: company news fetch failed for {ticker}: {e}")
            result[ticker] = []
        time.sleep(throttle_seconds)
    return result


def fetch_general_news(api_key, category="general", timeout=20):
    params = {"category": category, "token": api_key}
    resp = requests.get(f"{BASE_URL}/news", params=params, timeout=timeout)
    resp.raise_for_status()
    return [_normalize(a) for a in resp.json()]
