"""
Pure-Python technical indicator + pattern detection helpers.
Input `ohlcv` is a list of {date, open, high, low, close, volume} ascending by date
(as returned by twelvedata_client.fetch_daily_history).

These are pragmatic approximations of the trigger definitions in
config/strategy.json -- good enough to flag "go look at this", not
execution-grade signal precision.
"""

NONE = None


def sma_series(values, period):
    out = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = sum(values[i - period + 1 : i + 1]) / period
    return out


def ema_series(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2 / (period + 1)
    for i in range(period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def atr_series(highs, lows, closes, period=14):
    out = [None] * len(closes)
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return out
    avg = sum(trs[:period]) / period
    out[period - 1] = avg
    for i in range(period, len(trs)):
        avg = (avg * (period - 1) + trs[i]) / period
        out[i] = avg
    return out


def golden_cross_signal(ohlcv, fast=50, slow=200, lookback_days=5, vol_avg_period=20):
    closes = [c["close"] for c in ohlcv]
    volumes = [c["volume"] for c in ohlcv]
    if len(closes) < slow + lookback_days:
        return None
    sma_fast = sma_series(closes, fast)
    sma_slow = sma_series(closes, slow)
    vol_avg = sma_series(volumes, vol_avg_period)

    n = len(closes)
    for i in range(n - lookback_days, n):
        if i < 1:
            continue
        prev_f, prev_s = sma_fast[i - 1], sma_slow[i - 1]
        cur_f, cur_s = sma_fast[i], sma_slow[i]
        if None in (prev_f, prev_s, cur_f, cur_s):
            continue
        crossed = prev_f <= prev_s and cur_f > cur_s
        if crossed:
            expansion_window = ohlcv[i : min(i + 3, n)]
            expanded = any(
                vol_avg[j] is not None and volumes[j] > vol_avg[j]
                for j in range(i, min(i + 3, n))
            )
            return {
                "type": "golden_cross",
                "cross_date": ohlcv[i]["date"],
                "cross_index": i,
                "volume_expansion": expanded,
                "fires": expanded,
            }
    return None


def range_breakout_signal(ohlcv, lookback_days=20, min_volume_multiple=2.0):
    if len(ohlcv) < lookback_days + 1:
        return None
    today = ohlcv[-1]
    window = ohlcv[-(lookback_days + 1) : -1]
    range_high = max(c["high"] for c in window)
    avg_volume = sum(c["volume"] for c in window) / len(window)
    closed_above = today["close"] > range_high
    volume_ok = avg_volume > 0 and today["volume"] >= min_volume_multiple * avg_volume
    if closed_above:
        return {
            "type": "range_breakout",
            "date": today["date"],
            "range_high": range_high,
            "close": today["close"],
            "volume_multiple": (today["volume"] / avg_volume) if avg_volume else None,
            "fires": volume_ok,
        }
    return None


def _find_swing_points(ohlcv, window=3):
    highs = [c["high"] for c in ohlcv]
    lows = [c["low"] for c in ohlcv]
    swing_highs, swing_lows = [], []
    for i in range(window, len(ohlcv) - window):
        local_highs = highs[i - window : i + window + 1]
        local_lows = lows[i - window : i + window + 1]
        if highs[i] == max(local_highs):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(local_lows):
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


def _cluster_zones(points, tolerance_pct):
    """points: list of (index, price). Groups nearby prices into zones."""
    if not points:
        return []
    sorted_points = sorted(points, key=lambda p: p[1])
    zones = []
    current = [sorted_points[0]]
    for p in sorted_points[1:]:
        ref = current[-1][1]
        if abs(p[1] - ref) / ref * 100 <= tolerance_pct:
            current.append(p)
        else:
            zones.append(current)
            current = [p]
    zones.append(current)
    return [
        {
            "level": sum(p[1] for p in z) / len(z),
            "touches": len(z),
            "last_touch_index": max(p[0] for p in z),
        }
        for z in zones
        if len(z) >= 2
    ]


def demand_zone_reaction_signal(ohlcv, lookback_days=90, tolerance_pct=1.5, react_window=3):
    if len(ohlcv) < lookback_days:
        return None
    recent = ohlcv[-lookback_days:]
    _, swing_lows = _find_swing_points(recent, window=3)
    demand_zones = _cluster_zones(swing_lows, tolerance_pct)
    if not demand_zones:
        return None

    today = ohlcv[-1]
    check_window = ohlcv[-react_window:]
    for zone in demand_zones:
        touched = any(
            abs(day["low"] - zone["level"]) / zone["level"] * 100 <= tolerance_pct
            for day in check_window
        )
        reacted = touched and today["close"] > zone["level"]
        if touched:
            return {
                "type": "demand_zone_reaction",
                "zone_level": zone["level"],
                "zone_touches": zone["touches"],
                "date": today["date"],
                "fires": reacted,
            }
    return None


def _sma_safe(values, period):
    """Like sma_series, but skips (returns None for) any window containing a None,
    needed because the deviation series below has a leading run of Nones."""
    out = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        if any(v is None for v in window):
            continue
        out[i] = sum(window) / period
    return out


def _stdev_safe(values, period):
    """Population stdev (divide by N), matching Pine's ta.stdev default."""
    out = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        if any(v is None for v in window):
            continue
        mean = sum(window) / period
        variance = sum((v - mean) ** 2 for v in window) / period
        out[i] = variance ** 0.5
    return out


def _ema_immediate(values, length):
    """
    EMA seeded immediately with the first value, as in BigBeluga's Two-Pole
    Oscillator (smooth1/smooth2) -- unlike ema_series, which seeds with an SMA
    of the first `length` values and returns None until then.
    """
    alpha = 2.0 / (length + 1)
    out = [None] * len(values)
    prev = None
    for i, v in enumerate(values):
        if v is None:
            continue
        prev = v if prev is None else (1 - alpha) * prev + alpha * v
        out[i] = prev
    return out


def two_pole_filter(values, length):
    """Two cascaded immediate-seed EMA passes -- a proper 2-pole IIR low-pass filter."""
    smooth1 = _ema_immediate(values, length)
    return _ema_immediate(smooth1, length)


def two_pole_oscillator_signal(ohlcv, length=15, deviation_period=25, area_period=100, lag=4):
    """
    BigBeluga's Two-Pole Oscillator [https://www.tradingview.com/script/2Ssn4yDZ],
    verbatim formula verified against the published Pine source. Watch-only flag,
    same role as the RSI/MACD divergence check it replaces -- never a standalone
    trigger per strategy.json.

    Input: a z-score of price deviation from its own 25-SMA, detrended again by
    its own SMA and normalized by its stdev. Two-pole filtered, then compared to
    itself `lag` bars back (the oscillator's own "signal line"). A buy turn fires
    when it crosses above that lagged value while still negative; a sell turn is
    the mirror. Each carries an invalidation level (a volatility-sized buffer
    below/above the signal bar) -- included as context, not used as a stop here.
    """
    closes = [c["close"] for c in ohlcv]
    highs = [c["high"] for c in ohlcv]
    lows = [c["low"] for c in ohlcv]

    sma1 = _sma_safe(closes, deviation_period)
    deviation = [c - s if s is not None else None for c, s in zip(closes, sma1)]
    dev_sma = _sma_safe(deviation, deviation_period)
    dev_stdev = _stdev_safe(deviation, deviation_period)

    sma_n1 = [
        (d - m) / s if (d is not None and m is not None and s not in (None, 0)) else None
        for d, m, s in zip(deviation, dev_sma, dev_stdev)
    ]

    first_valid = next((i for i, v in enumerate(sma_n1) if v is not None), None)
    if first_valid is None or len(ohlcv) < first_valid + lag + 2:
        return None

    two_p = [None] * first_valid + two_pole_filter(sma_n1[first_valid:], length)

    i, j = len(ohlcv) - 1, len(ohlcv) - 1 - lag
    prev_i, prev_j = i - 1, j - 1
    if prev_j < 0 or None in (two_p[i], two_p[j], two_p[prev_i], two_p[prev_j]):
        return None

    turned_up = two_p[prev_i] <= two_p[prev_j] and two_p[i] > two_p[j]
    turned_down = two_p[prev_i] >= two_p[prev_j] and two_p[i] < two_p[j]
    buy = turned_up and two_p[i] < 0
    sell = turned_down and two_p[i] > 0
    if not (buy or sell):
        return None

    avg_range = _sma_safe([h - l for h, l in zip(highs, lows)], area_period)
    area = avg_range[i]
    level = None
    if area is not None:
        level = round(lows[i] - area, 2) if buy else round(highs[i] + area, 2)

    return {
        "type": "two_pole_turn",
        "direction": "buy" if buy else "sell",
        "value": round(two_p[i], 3),
        "invalidation_level": level,
        "date": ohlcv[i]["date"],
        "fires": False,
    }


def position_in_52w_range(ohlcv, lookback_days=252):
    window = ohlcv[-lookback_days:] if len(ohlcv) >= lookback_days else ohlcv
    highs = [c["high"] for c in window]
    lows = [c["low"] for c in window]
    high_52w, low_52w = max(highs), min(lows)
    close = ohlcv[-1]["close"]
    if high_52w == low_52w:
        return None
    pct = (close - low_52w) / (high_52w - low_52w) * 100
    return {"pct_of_52w_range": round(pct, 1), "close": close, "low_52w": low_52w, "high_52w": high_52w}


def distribution_and_trendline_break(ohlcv, lookback_days=60):
    if len(ohlcv) < lookback_days:
        return None
    recent = ohlcv[-lookback_days:]
    volumes = [c["volume"] for c in recent]
    avg_volume = sum(volumes[:-1]) / (len(volumes) - 1)
    today = recent[-1]
    is_down_day = today["close"] < recent[-2]["close"]
    distribution = is_down_day and avg_volume > 0 and today["volume"] > 1.5 * avg_volume

    _, swing_lows = _find_swing_points(recent, window=3)
    trendline_break = False
    if len(swing_lows) >= 2:
        (i1, p1), (i2, p2) = swing_lows[-2], swing_lows[-1]
        if i2 > i1:
            slope = (p2 - p1) / (i2 - i1)
            projected = p2 + slope * (len(recent) - 1 - i2)
            trendline_break = today["close"] < projected

    if distribution or trendline_break:
        return {
            "type": "distribution_and_trendline_break",
            "distribution_volume": distribution,
            "trendline_break": trendline_break,
            "date": today["date"],
            "fires": distribution and trendline_break,
        }
    return None


def stop_loss_level(ohlcv, basis_price, atr_multiple=1.0, atr_period=14):
    highs = [c["high"] for c in ohlcv]
    lows = [c["low"] for c in ohlcv]
    closes = [c["close"] for c in ohlcv]
    atr = atr_series(highs, lows, closes, atr_period)
    last_atr = next((v for v in reversed(atr) if v is not None), None)
    if last_atr is None:
        return None
    return round(basis_price - atr_multiple * last_atr, 2)


def last_swing_low_before(ohlcv, index, window=3):
    """Most recent confirmed swing low at or before `index` (a pivot needs
    `window` bars on both sides to confirm, so this can't see the last
    `window` bars before `index`)."""
    sub = ohlcv[: index + 1]
    _, swing_lows = _find_swing_points(sub, window=window)
    if not swing_lows:
        return None
    return swing_lows[-1][1]


def take_profit_level(entry_price, stop_price, reward_to_risk_ratio=3.0):
    if stop_price is None or entry_price <= stop_price:
        return None
    risk = entry_price - stop_price
    return round(entry_price + reward_to_risk_ratio * risk, 2)
