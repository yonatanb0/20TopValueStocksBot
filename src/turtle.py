"""
Turtle Trading (Richard Dennis / William Eckhardt), long-only, adapted onto
this basket per explicit decisions made in conversation (2026-07-31):
 - long-only, no short entries -- doesn't fit a thesis-driven "stocks I
   believe in" basket
 - gated behind the existing macro_sector_turning / valuation_override_52w
   flags rather than running unconditionally -- deliberately blurs Turtle's
   own "ignore fundamentals entirely" philosophy, a conscious tradeoff, not
   an oversight
 - unit sizing needs true account equity, which isn't available yet (see
   compute_unit_size()) -- everything else here works without it

This is READ-ONLY DECISION SUPPORT, same as every other signal in this repo.
It computes what Turtle's rules WOULD do; it never places, modifies, or
cancels an order.

Stateful (unlike indicators.py's pure per-run functions): the System 1
skip-after-winner filter requires knowing whether the last System 1 signal,
once resolved, made money. That state is carried forward in
data/state/{TICKER}.json under "turtle", the same mechanism state_vector.py
already uses for estimate-revision-baseline tracking.
"""
import math

import indicators as ind

RISK_PER_UNIT_PCT = 0.01
MAX_UNITS = 4
STOP_N_MULTIPLE = 2.0
PYRAMID_N_STEP = 0.5

N_PERIOD = 20
S1_LOOKBACK = 20
S1_EXIT_LOOKBACK = 10
S2_LOOKBACK = 55
S2_EXIT_LOOKBACK = 20


def compute_n(ohlcv, period=N_PERIOD):
    """Turtle's 'N' is just an ATR -- reuses indicators.py's existing
    atr_series rather than reimplementing it, just with a 20-day period
    instead of the 14 used elsewhere in this repo."""
    highs = [c["high"] for c in ohlcv]
    lows = [c["low"] for c in ohlcv]
    closes = [c["close"] for c in ohlcv]
    atr = ind.atr_series(highs, lows, closes, period)
    return next((v for v in reversed(atr) if v is not None), None)


def _channel_high(ohlcv, lookback):
    window = ohlcv[-(lookback + 1):-1]
    return max(c["high"] for c in window) if window else None


def _channel_low(ohlcv, lookback):
    window = ohlcv[-(lookback + 1):-1]
    return min(c["low"] for c in window) if window else None


def _breakout_detected(ohlcv, lookback):
    today = ohlcv[-1]
    high = _channel_high(ohlcv, lookback)
    return high is not None and today["close"] > high


def _exit_triggered(ohlcv, lookback):
    """Long-position exit: today's close crosses below the trailing low."""
    today = ohlcv[-1]
    low = _channel_low(ohlcv, lookback)
    return low is not None and today["close"] < low


def _evaluate_system(ohlcv, prior, entry_lookback, exit_lookback, apply_filter, n_value):
    """
    Shared mechanics for System 1 and System 2: resolve a currently-open
    hypothetical trade if its exit channel has been breached, then check for
    a fresh breakout (only meaningful when nothing is currently open --
    while a trade is open, further breakouts are pyramid territory, handled
    separately by compute_position_plan, not a new independent entry).
    """
    today = ohlcv[-1]
    new_state = dict(prior)
    resolution = None

    if prior.get("status") == "open":
        if _exit_triggered(ohlcv, exit_lookback):
            entry_price = prior["entry_price"]
            exit_price = today["close"]
            outcome = "win" if exit_price > entry_price else "loss"
            new_state = {
                "status": "resolved",
                "entry_date": prior["entry_date"],
                "entry_price": entry_price,
                "exit_date": today["date"],
                "exit_price": exit_price,
                "outcome": outcome,
            }
            resolution = {"outcome": outcome, "exit_price": exit_price, "exit_date": today["date"]}

    entry_fired, filtered = False, False
    if new_state.get("status") != "open" and _breakout_detected(ohlcv, entry_lookback):
        if apply_filter and new_state.get("outcome") == "win":
            filtered = True
        else:
            entry_fired = True
            new_state = {
                "status": "open",
                "entry_date": today["date"],
                "entry_price": today["close"],
                "n_at_entry": n_value,
                "exit_date": None,
                "exit_price": None,
                "outcome": None,
            }

    result = None
    if resolution or entry_fired or filtered:
        result = {
            "resolution": resolution,
            "entry_fired": entry_fired,
            "filtered": filtered,
            "entry_price": new_state.get("entry_price") if entry_fired else None,
        }
    return result, new_state


def evaluate_system1(ohlcv, prior_turtle_state, gate_open, n_value):
    """20-day breakout, skip-after-winner filter applied. Inert (no
    evaluation, no state change) while the macro/valuation gate is closed --
    treated as if Turtle doesn't exist for this ticker until it opens,
    rather than silently tracking a hypothetical trade the user can't see."""
    prior = (prior_turtle_state or {}).get("system1") or {}
    if not gate_open:
        return None, prior
    result, new_state = _evaluate_system(ohlcv, prior, S1_LOOKBACK, S1_EXIT_LOOKBACK, True, n_value)
    if result:
        result["system"] = "system1"
    return result, new_state


def evaluate_system2(ohlcv, prior_turtle_state, gate_open, n_value):
    """55-day breakout, unconditional -- no skip filter."""
    prior = (prior_turtle_state or {}).get("system2") or {}
    if not gate_open:
        return None, prior
    result, new_state = _evaluate_system(ohlcv, prior, S2_LOOKBACK, S2_EXIT_LOOKBACK, False, n_value)
    if result:
        result["system"] = "system2"
    return result, new_state


def compute_position_plan(open_trade_state, current_price):
    """
    Given an OPEN trade's state (entry price Pe, N as of that entry -- locked
    in at entry time, not recomputed daily, matching real Turtle behavior),
    returns the 4-unit pyramid ladder, how many units current price has
    actually reached, and the resulting unified stop: 2N behind the newest
    unit reached, since Turtle's rule is that the stop for the WHOLE position
    ratchets forward with every unit added, not just the first.
    """
    pe = open_trade_state.get("entry_price")
    n = open_trade_state.get("n_at_entry")
    if not pe or not n:
        return None

    unit_prices = [round(pe + PYRAMID_N_STEP * n * i, 2) for i in range(MAX_UNITS)]
    units_reached = 1
    if current_price is not None:
        for i in range(1, MAX_UNITS):
            if current_price >= unit_prices[i]:
                units_reached = i + 1

    newest_unit_price = unit_prices[units_reached - 1]
    stop_price = round(newest_unit_price - STOP_N_MULTIPLE * n, 2)

    return {
        "unit_prices": unit_prices,
        "units_reached": units_reached,
        "stop_price": stop_price,
        "n_at_entry": round(n, 3),
    }


def compute_unit_size(account_equity, n_value):
    """
    Unit Size = floor(0.01 * equity / N). Returns None if either input is
    missing -- account_equity isn't wired in yet (basket_analytics.py's
    dry_powder is a documented lower bound on true equity, not the real
    number Turtle sizing needs; pending a NAV Flex Query section).
    """
    if not account_equity or not n_value:
        return None
    return math.floor(RISK_PER_UNIT_PCT * account_equity / n_value)


def compute_turtle_state(ohlcv, prior_turtle_state, gate_open, account_equity=None):
    if not ohlcv or len(ohlcv) < S2_LOOKBACK + 1:
        return None

    n_value = compute_n(ohlcv)
    s1_result, s1_state = evaluate_system1(ohlcv, prior_turtle_state, gate_open, n_value)
    s2_result, s2_state = evaluate_system2(ohlcv, prior_turtle_state, gate_open, n_value)

    current_price = ohlcv[-1]["close"]
    open_trade = s1_state if s1_state.get("status") == "open" else (
        s2_state if s2_state.get("status") == "open" else None
    )
    position_plan = compute_position_plan(open_trade, current_price) if open_trade else None
    unit_size = compute_unit_size(account_equity, n_value) if position_plan else None

    return {
        "gate_open": gate_open,
        "n": round(n_value, 3) if n_value else None,
        "system1": {"state": s1_state, "signal": s1_result},
        "system2": {"state": s2_state, "signal": s2_result},
        "position_plan": position_plan,
        "unit_size": unit_size,
    }
