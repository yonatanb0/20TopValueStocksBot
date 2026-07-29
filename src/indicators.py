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


def rsi_series(closes, period=14):
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = 100 - (100 / (1 + (avg_gain / avg_loss))) if avg_loss != 0 else 100
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else None
        out[i + 1] = 100 - (100 / (1 + rs)) if rs is not None else 100
    return out


def macd_series(closes, fast=12, slow=26, signal=9):
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    macd_line = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid_start = next((i for i, v in enumerate(macd_line) if v is not None), None)
    signal_line = [None] * len(closes)
    if valid_start is not None:
        trimmed = [v for v in macd_line[valid_start:]]
        sig_trimmed = ema_series(trimmed, signal)
        for i, v in enumerate(sig_trimmed):
            signal_line[valid_start + i] = v
    histogram = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram


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


def rsi_macd_divergence_watch(ohlcv):
    """Watch-only flag, never a standalone trigger per strategy.json."""
    closes = [c["close"] for c in ohlcv]
    if len(closes) < 60:
        return None
    rsi = rsi_series(closes, 14)
    _, _, hist = macd_series(closes, 12, 26, 9)
    swing_highs, swing_lows = _find_swing_points(ohlcv, window=3)

    def check(points, higher_price_lower_indicator):
        if len(points) < 2:
            return False
        (i1, p1), (i2, p2) = points[-2], points[-1]
        r1, r2 = rsi[i1], rsi[i2]
        if r1 is None or r2 is None:
            return False
        if higher_price_lower_indicator:
            return p2 > p1 and r2 < r1
        return p2 < p1 and r2 > r1

    bearish = check(swing_highs, True)
    bullish = check(swing_lows, False)
    if bearish or bullish:
        return {"type": "rsi_divergence_watch", "bearish": bearish, "bullish": bullish, "fires": False}
    return None


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
