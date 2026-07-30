"""
Layer 2: basket-level analytics across the tracked 20-name basket -- pure
Python math, no LLM, no I/O (main.py reads/writes; this module only
computes), matching the state_vector.py pattern.

60-day correlation matrix and beta vs. a benchmark are computed from the
same OHLCV history already pulled every run (correlation needs zero extra
data; beta needs one extra TwelveData symbol, the benchmark). Sector
concentration is computed from data/positions.json + config/tickers.json.

Deliberately NOT included: cash / "dry powder". The configured IBKR Flex
Query only reports OpenPositions -- no cash/NAV section -- so there is no
account cash balance available today. Adding a "Cash Report" (or NAV)
section to the same Flex Query in Client Portal would enable this.
"""
from datetime import datetime, timezone

MIN_SAMPLES = 10


def _daily_returns(closes):
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]


def _aligned_returns(ohlcv_a, ohlcv_b, lookback_days):
    """Aligns two OHLCV series by date (both are daily bars from the same
    market, so dates should already match, but this guards against gaps --
    e.g. one symbol missing a bar TwelveData has for the other) and returns
    each series' trailing daily returns over the lookback window."""
    by_date_a = {c["date"]: c["close"] for c in ohlcv_a}
    by_date_b = {c["date"]: c["close"] for c in ohlcv_b}
    common_dates = sorted(set(by_date_a) & set(by_date_b))
    recent_dates = common_dates[-(lookback_days + 1):]
    closes_a = [by_date_a[d] for d in recent_dates]
    closes_b = [by_date_b[d] for d in recent_dates]
    return _daily_returns(closes_a), _daily_returns(closes_b)


def _mean(xs):
    return sum(xs) / len(xs)


def _covariance(xs, ys):
    mx, my = _mean(xs), _mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)


def _variance(xs):
    mx = _mean(xs)
    return sum((x - mx) ** 2 for x in xs) / (len(xs) - 1)


def _stdev(xs):
    return _variance(xs) ** 0.5


def correlation(ohlcv_a, ohlcv_b, lookback_days=60):
    ra, rb = _aligned_returns(ohlcv_a, ohlcv_b, lookback_days)
    if len(ra) < MIN_SAMPLES or len(rb) < MIN_SAMPLES:
        return None
    sa, sb = _stdev(ra), _stdev(rb)
    if sa == 0 or sb == 0:
        return None
    return round(_covariance(ra, rb) / (sa * sb), 3)


def beta(stock_ohlcv, benchmark_ohlcv, lookback_days=60):
    rs, rb = _aligned_returns(stock_ohlcv, benchmark_ohlcv, lookback_days)
    if len(rs) < MIN_SAMPLES or len(rb) < MIN_SAMPLES:
        return None
    var_b = _variance(rb)
    if var_b == 0:
        return None
    return round(_covariance(rs, rb) / var_b, 3)


def compute_correlation_matrix(tickers, history, lookback_days=60):
    matrix = {}
    for a in tickers:
        matrix[a] = {}
        for b in tickers:
            if a == b:
                matrix[a][b] = 1.0
                continue
            if b in matrix and a in matrix[b]:
                matrix[a][b] = matrix[b][a]
                continue
            ohlcv_a, ohlcv_b = history.get(a), history.get(b)
            matrix[a][b] = correlation(ohlcv_a, ohlcv_b, lookback_days) if (ohlcv_a and ohlcv_b) else None
    return matrix


def compute_betas(tickers, history, benchmark_ohlcv, lookback_days=60):
    result = {}
    for t in tickers:
        ohlcv = history.get(t)
        result[t] = beta(ohlcv, benchmark_ohlcv, lookback_days) if (ohlcv and benchmark_ohlcv) else None
    return result


def compute_sector_concentration(positions_by_ticker, tickers_meta):
    """Concentration within the TRACKED basket only -- IBKR positions outside
    the 20 tracked tickers (if any) aren't fetched at all, so this can't and
    doesn't represent the whole account, only how the tracked holdings split
    by sector."""
    ticker_to_sector = {t["ticker"]: t["sector"] for t in tickers_meta}
    by_sector = {}
    total = 0.0
    for ticker, pos in (positions_by_ticker or {}).items():
        if not pos or not pos.get("held"):
            continue
        mv = pos.get("market_value") or 0
        sector = ticker_to_sector.get(ticker, "unknown")
        by_sector[sector] = by_sector.get(sector, 0.0) + mv
        total += mv

    return {
        "by_sector": {
            sector: {
                "market_value": round(mv, 2),
                "pct_of_tracked_holdings": round(mv / total * 100, 2) if total else None,
            }
            for sector, mv in by_sector.items()
        },
        "total_tracked_market_value": round(total, 2),
    }


def compute_basket_analytics(tickers_meta, history, benchmark_ohlcv, positions_by_ticker, lookback_days=60):
    tickers = [t["ticker"] for t in tickers_meta]
    return {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "correlation": compute_correlation_matrix(tickers, history, lookback_days),
        "beta_vs_spy": compute_betas(tickers, history, benchmark_ohlcv, lookback_days),
        "sector_concentration": compute_sector_concentration(positions_by_ticker, tickers_meta),
        "dry_powder": {
            "available": False,
            "note": "The configured IBKR Flex Query only reports OpenPositions, no cash/NAV "
                    "section -- add a 'Cash Report' (or NAV) section to the same Flex Query in "
                    "Client Portal to enable this.",
        },
    }
