"""
Renders dashboard/template.html into docs/index.html, using only real data
already on disk (data/stocks/*.json signal logs, data/thesis/*.json where
present) -- no hand-authored per-stock text, since this runs unattended.
"""
import json
import re
from datetime import datetime, timezone

from config import ROOT, load_tickers, load_json

TEMPLATE_PATH = ROOT / "dashboard" / "template.html"
OUTPUT_PATH = ROOT / "docs" / "index.html"
THESIS_DIR = ROOT / "data" / "thesis"
STOCKS_DIR = ROOT / "data" / "stocks"
STATE_DIR = ROOT / "data" / "state"

REVISION_LABELS = {
    "up": "Estimates rising",
    "down": "Estimates falling",
    "flat": "Estimates flat",
    "insufficient_history": "Not enough history yet",
}

BULLISH_ENTRY_TYPES = {"golden_cross", "range_breakout", "demand_zone_reaction"}
EXIT_TYPE = "distribution_and_trendline_break"
VALUATION_TYPE = "valuation_override_52w"
TWO_POLE_TYPE = "two_pole_turn"

TYPE_LABELS = {
    "golden_cross": "Golden cross",
    "range_breakout": "Range breakout",
    "demand_zone_reaction": "Demand zone reaction",
    "rsi_divergence_watch": "RSI divergence (legacy)",
    "valuation_override_52w": "Valuation override (52w)",
    "distribution_and_trendline_break": "Distribution / trendline break",
    "eps_revision_proxy": "EPS revision proxy",
    "guidance_rollover": "Guidance rollover",
    "macro_sector_turning": "Macro sector signal",
    "two_pole_turn": "Two-Pole Oscillator",
}

SECTOR_LABELS = {
    "enterprise_tech": "Enterprise Tech",
    "semiconductors": "Semiconductors",
    "ai_data_center_infra": "AI / Data Center Infra",
    "healthcare_medtech": "Healthcare / Medtech",
    "telecom": "Telecom",
    "clean_energy": "Clean Energy",
}

VERDICT_LABELS = {
    "entry": "ENTRY CONFIRMED",
    "exit": "EXIT WATCH",
    "onsale": "WORTH A LOOK",
    "watching": "WATCHING",
    "quiet": "NO SIGNAL",
}

STATUS_LABELS = {
    "entry": "Entry confirmed",
    "exit": "Exit watch",
    "onsale": "On sale — reevaluate",
    "watching": "Watching",
    "quiet": "No signal",
}

NEWS_TAGS = {
    "eps_revision_proxy": ("positive", "Estimate revision proxy"),
    "guidance_rollover": ("negative", "Guidance rollover proxy"),
    "macro_sector_turning": ("neutral", "Macro sector signal"),
}


def _latest_by_type(signal_log):
    latest = {}
    for sig in signal_log:
        latest[sig["type"]] = sig
    return latest


def _priority_signals(latest):
    """Signals worth leading with, most decisive first."""
    ordered = []
    for t in BULLISH_ENTRY_TYPES:
        if t in latest and latest[t]["fires"]:
            ordered.append(latest[t])
    if EXIT_TYPE in latest and latest[EXIT_TYPE]["fires"]:
        ordered.append(latest[EXIT_TYPE])
    if VALUATION_TYPE in latest and latest[VALUATION_TYPE]["fires"]:
        ordered.append(latest[VALUATION_TYPE])
    if TWO_POLE_TYPE in latest:
        ordered.append(latest[TWO_POLE_TYPE])
    for sig in latest.values():
        if sig not in ordered:
            ordered.append(sig)
    return ordered


def _status_and_lean(latest):
    bullish_entries = [t for t in BULLISH_ENTRY_TYPES if t in latest and latest[t]["fires"]]
    exit_sig = latest.get(EXIT_TYPE)
    confirmed_exit = bool(exit_sig and exit_sig["fires"])
    valuation_sig = latest.get(VALUATION_TYPE)
    onsale = bool(valuation_sig and valuation_sig["fires"])
    two_pole_sig = latest.get(TWO_POLE_TYPE)

    if bullish_entries:
        status = "entry"
    elif confirmed_exit:
        status = "exit"
    elif onsale:
        status = "onsale"
    elif latest:
        status = "watching"
    else:
        status = "quiet"

    if bullish_entries:
        lean = "surge"
    elif confirmed_exit:
        lean = "fall"
    elif two_pole_sig and two_pole_sig["details"].get("direction") == "buy":
        lean = "surge"
    elif two_pole_sig and two_pole_sig["details"].get("direction") == "sell":
        lean = "fall"
    else:
        lean = "neutral"

    return status, lean, bullish_entries


def _truncate(text, limit=220):
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _position_view(position):
    """position: the `position` block from data/state/{TICKER}.json (itself
    sourced from the IBKR Flex Query in data/positions.json). None if IBKR
    wasn't configured for this run or the ticker isn't currently held."""
    if not position or not position.get("held"):
        return None
    shares = position.get("shares")
    avg_cost = position.get("avg_cost")
    cost_basis = (shares * avg_cost) if (shares and avg_cost) else None
    pnl = position.get("unrealized_pnl")
    pnl_pct = round(pnl / cost_basis * 100, 2) if (cost_basis and pnl is not None) else None
    return {
        "shares": shares,
        "avgCost": avg_cost,
        "marketValue": position.get("market_value"),
        "unrealizedPnl": pnl,
        "unrealizedPnlPct": pnl_pct,
    }


def _state_vector_view(state):
    """Distilled view of data/state/{TICKER}.json for the dashboard -- the
    decision-relevant fields (price vs. plan, trend, vol, fundamentals,
    catalyst timing), not every raw field the state vector computes
    (volume ratio, consolidation range, etc. stay data-only, same "distilled
    not exhaustive" approach as the thesis schema)."""
    if not state:
        return None
    pvp = state.get("price_vs_plan") or {}
    trend = state.get("trend") or {}
    vol = state.get("volatility") or {}
    fund = state.get("fundamentals") or {}
    cat = state.get("catalysts_and_review") or {}

    has_any = any([pvp.get("stop_price"), trend, vol, fund.get("forward_pe"), fund.get("ev_ebitda_ttm")])
    if not has_any:
        return None

    return {
        "stopPrice": pvp.get("stop_price"),
        "targetPrice": pvp.get("target_price"),
        "pctFromStop": pvp.get("pct_from_stop"),
        "pctFromTarget": pvp.get("pct_from_target"),
        "atrUnitsFromStop": pvp.get("atr_units_from_stop"),
        "priceVsSma20": trend.get("price_vs_sma20"),
        "priceVsSma50": trend.get("price_vs_sma50"),
        "priceVsSma200": trend.get("price_vs_sma200"),
        "atr14": vol.get("atr14"),
        "realizedVol20d": vol.get("realized_vol_20d_annualized_pct"),
        "forwardPe": fund.get("forward_pe"),
        "evEbitdaTtm": fund.get("ev_ebitda_ttm"),
        "revisionDirection": REVISION_LABELS.get(fund.get("estimate_revision_direction"), fund.get("estimate_revision_direction")),
        "revisionPct": fund.get("estimate_revision_pct"),
        "daysToNextEarnings": cat.get("days_to_next_earnings"),
        "daysSinceReviewDue": cat.get("days_since_review_due"),
        "lastUpdated": state.get("last_updated"),
    }


def build_stock_entry(ticker_meta):
    ticker = ticker_meta["ticker"]
    name = ticker_meta["name"]
    sector = SECTOR_LABELS.get(ticker_meta["sector"], ticker_meta["sector"])

    stock_data = load_json(STOCKS_DIR / f"{ticker}.json")
    signal_log = stock_data.get("signal_log", [])
    price = stock_data.get("last_price")

    thesis_path = THESIS_DIR / f"{ticker}.json"
    thesis = load_json(thesis_path) if thesis_path.exists() else None

    state_path = STATE_DIR / f"{ticker}.json"
    state = load_json(state_path) if state_path.exists() else None

    latest = _latest_by_type(signal_log)
    status, lean, bullish_entries = _status_and_lean(latest)
    priority_sigs = _priority_signals(latest)

    if thesis:
        pitch = thesis["thesis_summary"]["elevator_pitch"][0]
        verdict = thesis["verdict"]["call"]
        verdict_paragraph = " ".join(thesis["thesis_summary"]["elevator_pitch"])
        eep = thesis.get("entry_exit_plan", {})
        levels = []
        if "stop_loss" in eep:
            levels.append({"k": "Stop", "v": f"${eep['stop_loss']['price']}"})
        if "take_profit" in eep and "tp1" in eep["take_profit"]:
            levels.append({"k": "Target", "v": f"${eep['take_profit']['tp1']}"})
        if "risk_reward_to_tp1" in eep:
            levels.append({"k": "R:R", "v": eep["risk_reward_to_tp1"]})
        review = thesis.get("next_review", {}).get("estimated_date")
    else:
        verdict = VERDICT_LABELS[status]
        if priority_sigs:
            pitch = _truncate(priority_sigs[0]["description"])
            verdict_paragraph = " ".join(_truncate(s["description"], 260) for s in priority_sigs[:2])
        else:
            pitch = f"No technical or news signals this cycle."
            verdict_paragraph = f"Nothing technical or news-based has fired for {name} in the latest run."
        levels = []
        review = None
        if bullish_entries:
            d = latest[bullish_entries[0]]["details"]
            if d.get("stop_loss") is not None and d.get("take_profit") is not None:
                levels = [
                    {"k": "Stop", "v": f"${d['stop_loss']}"},
                    {"k": "Target", "v": f"${d['take_profit']}"},
                ]

    headlines = []
    for t in ("eps_revision_proxy", "guidance_rollover", "macro_sector_turning"):
        sig = latest.get(t)
        if not sig:
            continue
        details = sig["details"]
        tag, tag_label = NEWS_TAGS[t]
        # Older log entries (before `headline` was added to details) only have
        # the title embedded in the quoted description text -- fall back to
        # extracting it from there rather than showing a blank title.
        quoted = re.search(r'"([^"]+)"', sig.get("description", ""))
        title = details.get("headline") or details.get("title") or (quoted.group(1) if quoted else "(untitled)")
        published = (details.get("published") or "")[:10]
        matched = details.get("matched_keywords") or details.get("matched") or []
        headlines.append(
            {
                "title": title,
                "date": published,
                "url": details.get("url"),
                "tag": tag,
                "tagLabel": tag_label,
                "note": ("Matched: " + ", ".join(matched)) if matched else "",
            }
        )

    signals = [
        {"type": TYPE_LABELS.get(sig["type"], sig["type"]), "on": sig["fires"], "note": sig["description"]}
        for sig in latest.values()
    ]

    return {
        "ticker": ticker,
        "name": name,
        "sector": sector,
        "price": price,
        "status": status,
        "statusLabel": STATUS_LABELS[status],
        "hasThesis": thesis is not None,
        "lean": lean,
        "pitch": pitch,
        "verdict": verdict,
        "verdictParagraph": verdict_paragraph,
        "headlines": headlines,
        "signals": signals,
        "levels": levels,
        "review": review,
        "position": _position_view(state.get("position") if state else None),
        "stateVector": _state_vector_view(state),
    }


def main():
    tickers_meta = load_tickers()
    stocks = [build_stock_entry(t) for t in tickers_meta]

    now = datetime.now(timezone.utc)
    from zoneinfo import ZoneInfo

    jerusalem_time = now.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%Y-%m-%d %H:%M")

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered = template.replace("__STOCKS_JSON__", json.dumps(stocks)).replace(
        "__GENERATED_AT_JERUSALEM__", jerusalem_time
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"[build_dashboard] Wrote {OUTPUT_PATH} ({len(stocks)} stocks)")


if __name__ == "__main__":
    main()
