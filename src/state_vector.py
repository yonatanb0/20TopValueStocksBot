"""
Layer-1 state vector -- pure Python, no LLM (like signals.py, this module
does no file I/O; main.py reads the inputs and writes the output). Combines
this run's OHLCV, IBKR position, FMP fundamentals, and -- where a thesis
exists -- the ticker's entry_exit_plan/risk_management/next_review into one
deterministic snapshot per ticker.

The thesis JSON is never written to (read-only input, same rule as the rest
of the pipeline); everything computed here is meant for data/state/{TICKER}.json,
which is otherwise the only place that carries state forward run-to-run
(currently just for estimate-revision-direction tracking and last_verdict,
below).
"""
import re
from datetime import date, datetime, timezone

import indicators as ind

MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
REVISION_WINDOW_DAYS = 75  # ~3 months, matches the spec's "3-month" framing


def _parse_approx_month(text):
    """
    Best-effort parse of loose date strings ('~October 2026', '~Oct 2026',
    '2026-10') into a date on the 1st of that month. Thesis files write
    these by hand, so exact day-of-month is never meaningful here anyway.
    Returns None if nothing recognizable is found.
    """
    if not text:
        return None
    match = re.search(r"(20\d{2})-(\d{2})", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), 1)
    match = re.search(r"([A-Za-z]{3,9})\.?\s+(20\d{2})", text)
    if match:
        month = MONTH_NAMES.get(match.group(1).lower()[:3])
        if month:
            return date(int(match.group(2)), month, 1)
    return None


def _pct(a, b):
    """% change of a relative to b, or None if either is missing/b is 0."""
    if a is None or b in (None, 0):
        return None
    return round((a - b) / b * 100, 2)


def _trend_block(ohlcv):
    closes = [c["close"] for c in ohlcv]
    price = closes[-1]
    out = {}
    for period in (20, 50, 200):
        series = ind.sma_series(closes, period)
        current = series[-1]
        prior = series[-6] if len(series) > 5 else None
        out[f"sma{period}"] = round(current, 2) if current is not None else None
        out[f"sma{period}_slope_5d_pct"] = _pct(current, prior)
        out[f"price_vs_sma{period}"] = (
            None if current is None else "above" if price > current else "below"
        )
    return out


def _volatility_block(ohlcv):
    closes = [c["close"] for c in ohlcv]
    highs = [c["high"] for c in ohlcv]
    lows = [c["low"] for c in ohlcv]
    price = closes[-1]

    atr = ind.atr_series(highs, lows, closes, 14)
    last_atr = next((v for v in reversed(atr) if v is not None), None)

    window = closes[-21:] if len(closes) >= 21 else closes
    daily_returns = [window[i] / window[i - 1] - 1 for i in range(1, len(window)) if window[i - 1]]
    realized_vol = None
    if len(daily_returns) >= 2:
        mean = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        realized_vol = round((variance ** 0.5) * (252 ** 0.5) * 100, 2)  # annualized %

    return {
        "atr14": round(last_atr, 2) if last_atr is not None else None,
        "atr14_pct_of_price": round(last_atr / price * 100, 2) if last_atr and price else None,
        "realized_vol_20d_annualized_pct": realized_vol,
    }


def _range_block(ohlcv):
    out = {}
    for period in (20, 60):
        window = ohlcv[-period:] if len(ohlcv) >= period else ohlcv
        out[f"high_{period}d"] = round(max(c["high"] for c in window), 2)
        out[f"low_{period}d"] = round(min(c["low"] for c in window), 2)
    return out


def _momentum_block(ohlcv):
    closes = [c["close"] for c in ohlcv]
    rsi = ind.rsi_series(closes, 14)
    last_rsi = next((v for v in reversed(rsi) if v is not None), None)
    return {"rsi14": round(last_rsi, 2) if last_rsi is not None else None}


def _volume_block(ohlcv, period=20):
    window = ohlcv[-period:] if len(ohlcv) >= period else ohlcv
    up_volume = sum(c["volume"] for c in window if c["close"] >= c["open"])
    down_volume = sum(c["volume"] for c in window if c["close"] < c["open"])
    return {"up_down_volume_ratio_20d": round(up_volume / down_volume, 2) if down_volume else None}


def _consolidation_block(ohlcv, period=20):
    window = ohlcv[-period:] if len(ohlcv) >= period else ohlcv
    high = max(c["high"] for c in window)
    low = min(c["low"] for c in window)
    price = ohlcv[-1]["close"]
    return {
        "range_width_pct": round((high - low) / low * 100, 2) if low else None,
        "distance_from_high_pct": _pct(price, high),
        "distance_from_low_pct": _pct(price, low),
    }


def _price_vs_plan_block(price, atr, position, thesis):
    entry_price = None
    if position and position.get("held") and position.get("avg_cost"):
        entry_price = position["avg_cost"]

    stop_price = None
    target_price = None
    if thesis:
        plan = thesis.get("entry_exit_plan", {})
        stop_price = plan.get("stop_loss", {}).get("price")
        target_price = plan.get("take_profit", {}).get("tp1")

    block = {
        "current_price": price,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "pct_from_entry": _pct(price, entry_price),
        "pct_from_stop": _pct(price, stop_price),
        "pct_from_target": _pct(price, target_price),
        "atr_units_from_stop": None,
    }
    if atr and stop_price is not None and price is not None:
        block["atr_units_from_stop"] = round((price - stop_price) / atr, 2)
    return block


def _catalyst_review_block(thesis, today):
    if not thesis:
        return {
            "next_earnings_date_approx": None,
            "days_to_next_earnings": None,
            "next_review_date_approx": None,
            "days_since_review_due": None,
        }
    plan = thesis.get("entry_exit_plan", {})
    review = thesis.get("next_review", {})

    earnings_text = plan.get("next_earnings_date")
    earnings_date = _parse_approx_month(earnings_text)
    review_date = _parse_approx_month(review.get("estimated_date"))

    return {
        "next_earnings_date_approx": earnings_text,
        "days_to_next_earnings": (earnings_date - today).days if earnings_date else None,
        "next_review_date_approx": review.get("estimated_date"),
        "days_since_review_due": (today - review_date).days if review_date else None,
    }


def _fundamentals_block(fundamentals, price, prior_state, today):
    """
    Finnhub's free tier (like FMP's) doesn't expose a raw forward-EPS-estimate
    number -- only the derived forwardPE. We back out an implied consensus
    forward EPS as price / forwardPE (approximately recovers the same number
    Finnhub used to compute forwardPE) and track *that* for revision
    direction: compare this run's implied EPS against a baseline snapshot
    carried in the prior state file, only advancing the baseline once
    REVISION_WINDOW_DAYS have actually elapsed, so the comparison stays a
    real ~3-month delta instead of drifting shorter every run.
    """
    fundamentals = fundamentals or {}
    forward_pe = fundamentals.get("forward_pe")
    ev_ebitda_ttm = fundamentals.get("ev_ebitda_ttm")
    implied_forward_eps = round(price / forward_pe, 4) if (forward_pe and price) else None

    result = {
        "forward_pe": forward_pe,
        "ev_ebitda_ttm": ev_ebitda_ttm,
        "implied_forward_eps": implied_forward_eps,
        "estimate_revision_direction": "insufficient_history",
        "estimate_revision_pct": None,
        "estimate_baseline_date": today.isoformat(),
        "estimate_baseline_implied_eps": implied_forward_eps,
    }
    if implied_forward_eps is None:
        return result

    prior_fundamentals = (prior_state or {}).get("fundamentals") or {}
    baseline_date_str = prior_fundamentals.get("estimate_baseline_date")
    baseline_eps = prior_fundamentals.get("estimate_baseline_implied_eps")

    if baseline_date_str and baseline_eps is not None:
        elapsed = (today - date.fromisoformat(baseline_date_str)).days
        if elapsed >= REVISION_WINDOW_DAYS:
            pct = _pct(implied_forward_eps, baseline_eps)
            result["estimate_revision_pct"] = pct
            if pct is not None:
                result["estimate_revision_direction"] = "up" if pct > 1 else "down" if pct < -1 else "flat"
            # window closes -- fresh baseline starts today (already the default above)
        else:
            # window still open -- keep carrying the same baseline forward
            result["estimate_baseline_date"] = baseline_date_str
            result["estimate_baseline_implied_eps"] = baseline_eps

    return result


def compute_state_vector(ticker, ohlcv, thesis, position, fundamentals, prior_state, today=None):
    today = today or datetime.now(timezone.utc).date()
    has_technicals = bool(ohlcv) and len(ohlcv) >= 20
    price = ohlcv[-1]["close"] if ohlcv else None

    volatility = _volatility_block(ohlcv) if has_technicals else None
    atr = volatility["atr14"] if volatility else None

    return {
        "ticker": ticker,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "has_thesis": thesis is not None,
        "position": position,
        "price_vs_plan": _price_vs_plan_block(price, atr, position, thesis),
        "trend": _trend_block(ohlcv) if has_technicals else None,
        "volatility": volatility,
        "range": _range_block(ohlcv) if has_technicals else None,
        "momentum": _momentum_block(ohlcv) if has_technicals else None,
        "volume": _volume_block(ohlcv) if has_technicals else None,
        "consolidation": _consolidation_block(ohlcv) if has_technicals else None,
        "catalysts_and_review": _catalyst_review_block(thesis, today),
        "fundamentals": _fundamentals_block(fundamentals, price, prior_state, today),
        "last_verdict": (prior_state or {}).get("last_verdict"),
    }
