import hashlib

import indicators as ind


def _key(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _stop_and_target(ohlcv, basis_price, entry_price, risk_cfg):
    stop_cfg = risk_cfg["stop_loss"]
    tp_cfg = risk_cfg["take_profit"]
    stop = ind.stop_loss_level(ohlcv, basis_price, atr_multiple=stop_cfg["buffer_atr_multiple"])
    target = ind.take_profit_level(entry_price, stop, tp_cfg["reward_to_risk_ratio"]) if stop is not None else None
    return stop, target


def _risk_suffix(stop, target, ratio):
    if stop is None or target is None:
        return ""
    return f" Stop ~{stop}, target ~{target} ({ratio:.0f}:1 reward:risk)."


def build_technical_signals(ticker, ohlcv, strategy):
    signals = []
    if len(ohlcv) < 30:
        return signals

    entry = strategy["entry_triggers"]
    risk = strategy["risk_management"]
    exits = strategy["exit_triggers"]
    valuation = strategy["valuation_override"]

    gc = ind.golden_cross_signal(
        ohlcv,
        fast=entry["golden_cross"]["fast_ma"],
        slow=entry["golden_cross"]["slow_ma"],
    )
    if gc:
        description = (
            f"50/200 golden cross on {gc['cross_date']}"
            + (" with volume expansion (entry trigger confirmed)." if gc["fires"] else " but WITHOUT volume expansion (not yet confirmed).")
        )
        if gc["fires"]:
            basis = ind.last_swing_low_before(ohlcv, gc["cross_index"]) or ohlcv[gc["cross_index"]]["low"]
            entry_price = ohlcv[-1]["close"]
            stop, target = _stop_and_target(ohlcv, basis, entry_price, risk)
            gc["stop_loss"] = stop
            gc["take_profit"] = target
            gc["entry_reference_price"] = entry_price
            description += _risk_suffix(stop, target, risk["take_profit"]["reward_to_risk_ratio"])
        signals.append(
            {
                "category": "technical",
                "type": "golden_cross",
                "fires": gc["fires"],
                "description": description,
                "details": gc,
                "dedupe_key": _key(ticker, "golden_cross", gc["cross_date"]),
            }
        )

    bo = ind.range_breakout_signal(
        ohlcv,
        lookback_days=entry["range_breakout"]["lookback_days"],
        min_volume_multiple=entry["range_breakout"]["min_volume_multiple"],
    )
    if bo:
        description = (
            f"Closed above {entry['range_breakout']['lookback_days']}-day range high "
            f"({bo['range_high']:.2f}) on {bo['date']}"
            + (f" with {bo['volume_multiple']:.1f}x avg volume (confirmed)." if bo["fires"] else " but volume didn't confirm.")
        )
        if bo["fires"]:
            stop, target = _stop_and_target(ohlcv, bo["range_high"], bo["close"], risk)
            bo["stop_loss"] = stop
            bo["take_profit"] = target
            description += _risk_suffix(stop, target, risk["take_profit"]["reward_to_risk_ratio"])
        signals.append(
            {
                "category": "technical",
                "type": "range_breakout",
                "fires": bo["fires"],
                "description": description,
                "details": bo,
                "dedupe_key": _key(ticker, "range_breakout", bo["date"]),
            }
        )

    zone = ind.demand_zone_reaction_signal(
        ohlcv,
        lookback_days=entry["supply_demand_zone_reaction"]["zone_definition"] and 90,
        tolerance_pct=entry["supply_demand_zone_reaction"]["zone_touch_tolerance_pct"],
    )
    if zone:
        description = (
            f"Price touched demand zone ~{zone['zone_level']:.2f} "
            f"({zone['zone_touches']} prior touches)"
            + (" and closed back above it (reaction confirmed)." if zone["fires"] else " -- watch for a close back above to confirm.")
        )
        if zone["fires"]:
            entry_price = ohlcv[-1]["close"]
            stop, target = _stop_and_target(ohlcv, zone["zone_level"], entry_price, risk)
            zone["stop_loss"] = stop
            zone["take_profit"] = target
            zone["entry_reference_price"] = entry_price
            description += _risk_suffix(stop, target, risk["take_profit"]["reward_to_risk_ratio"])
        signals.append(
            {
                "category": "technical",
                "type": "demand_zone_reaction",
                "fires": zone["fires"],
                "description": description,
                "details": zone,
                "dedupe_key": _key(ticker, "demand_zone_reaction", zone["date"], round(zone["zone_level"], 1)),
            }
        )

    div = ind.rsi_macd_divergence_watch(ohlcv)
    if div:
        kind = "bearish" if div["bearish"] else "bullish"
        signals.append(
            {
                "category": "technical",
                "type": "rsi_divergence_watch",
                "fires": False,
                "description": f"{kind.capitalize()} RSI divergence detected -- watch closer, not a standalone trigger.",
                "details": div,
                "dedupe_key": _key(ticker, "rsi_divergence_watch", ohlcv[-1]["date"], kind),
            }
        )

    range_pos = ind.position_in_52w_range(ohlcv)
    if range_pos and range_pos["pct_of_52w_range"] <= valuation["primary_method"]["threshold_pct"]:
        signals.append(
            {
                "category": "fundamental",
                "type": "valuation_override_52w",
                "fires": True,
                "description": (
                    f"Trading in bottom {valuation['primary_method']['threshold_pct']}% of its 52-week range "
                    f"({range_pos['pct_of_52w_range']}%) -- 'on sale' proxy, reevaluate regardless of macro gate."
                ),
                "details": range_pos,
                "dedupe_key": _key(ticker, "valuation_override_52w", ohlcv[-1]["date"]),
            }
        )

    dist = ind.distribution_and_trendline_break(ohlcv)
    if dist:
        signals.append(
            {
                "category": "technical",
                "type": "distribution_and_trendline_break",
                "fires": dist["fires"],
                "description": (
                    "EXIT WATCH: "
                    + ("Distribution volume detected. " if dist["distribution_volume"] else "")
                    + ("Uptrend trendline broken. " if dist["trendline_break"] else "")
                    + ("Both confirmed -- thesis likely invalidated." if dist["fires"] else "One signal present -- watch closely.")
                ),
                "details": dist,
                "dedupe_key": _key(ticker, "distribution_and_trendline_break", dist["date"]),
            }
        )

    return signals


def _text_matches(text, terms):
    text = (text or "").lower()
    return [t for t in terms if t.lower() in text]


def _article_matches(article, terms):
    """Broad match across title + description -- used for the macro/sector scan,
    which isn't tied to a single company's identity so body text is fair game."""
    combined = f"{article.get('title') or ''} {article.get('description') or ''}"
    return _text_matches(combined, terms)


def _company_headline_matches(article, terms, company_name, ticker):
    """
    Stricter match for per-company signals (EPS-revision proxy, guidance rollover).
    Two guards against false positives like an "Applied Digital beats estimates"
    article surfacing under Cisco's news feed and matching "misses estimates"
    from unrelated body text:
      1. Keywords must appear in the headline title itself, not the body --
         body text often describes a different company in the same roundup piece.
      2. The company's own name or ticker must appear in the title -- a sanity
         check that the headline is actually about this company at all.
    """
    title = article.get("title") or ""
    title_lower = title.lower()
    if company_name.lower() not in title_lower and ticker.lower() not in title_lower:
        return []
    return _text_matches(title, terms)


def build_news_signals(tickers_meta, company_news_by_ticker, macro_articles, strategy):
    """
    company_news_by_ticker: dict[ticker] -> list of articles already scoped to that
    ticker by the news source (Finnhub's company-news is per-symbol), though Finnhub
    can still return loosely-related roundup articles under a company's feed -- see
    _company_headline_matches for the guards against that.
    Returns dict[ticker] -> list of signal dicts (macro, fundamental/candidacy, exit-guidance).
    """
    by_ticker = {t["ticker"]: [] for t in tickers_meta}
    ticker_to_name = {t["ticker"]: t["name"] for t in tickers_meta}

    candidacy_kw = strategy["candidacy_filter"]["keywords_positive"]
    rollover_kw = strategy["exit_triggers"]["guidance_rollover"]["keywords"]

    for ticker, articles in company_news_by_ticker.items():
        company_name = ticker_to_name.get(ticker, ticker)
        for article in articles:
            url = article.get("url", "")

            hits = _company_headline_matches(article, candidacy_kw, company_name, ticker)
            if hits:
                by_ticker[ticker].append(
                    {
                        "category": "fundamental",
                        "type": "eps_revision_proxy",
                        "fires": False,
                        "description": (
                            f"Headline suggests positive estimate revision ({', '.join(hits)}): "
                            f"\"{article.get('title')}\" -- verify against actual analyst EPS revisions."
                        ),
                        "details": {"url": url, "matched_keywords": hits, "published": article.get("publishedAt")},
                        "dedupe_key": _key(ticker, "eps_revision_proxy", url),
                    }
                )

            hits = _company_headline_matches(article, rollover_kw, company_name, ticker)
            if hits:
                by_ticker[ticker].append(
                    {
                        "category": "fundamental",
                        "type": "guidance_rollover",
                        "fires": False,
                        "description": (
                            f"EXIT WATCH -- headline suggests guidance rollover ({', '.join(hits)}): "
                            f"\"{article.get('title')}\""
                        ),
                        "details": {"url": url, "matched_keywords": hits, "published": article.get("publishedAt")},
                        "dedupe_key": _key(ticker, "guidance_rollover", url),
                    }
                )

    sector_keywords = strategy["macro_gate"]["per_sector_keywords"]
    general_keywords = strategy["macro_gate"]["macro_keywords_general"]
    sectors_hit = {}
    for article in macro_articles:
        hits_general = _article_matches(article, general_keywords)
        for sector, kws in sector_keywords.items():
            hits_sector = _article_matches(article, kws)
            if hits_sector or hits_general:
                sectors_hit.setdefault(sector, []).append(
                    {
                        "url": article.get("url"),
                        "title": article.get("title"),
                        "matched": hits_sector + hits_general,
                        "published": article.get("publishedAt"),
                    }
                )

    for sector, hits in sectors_hit.items():
        tickers_in_sector = [t["ticker"] for t in tickers_meta if t["sector"] == sector]
        for ticker in tickers_in_sector:
            for hit in hits:
                by_ticker[ticker].append(
                    {
                        "category": "macro",
                        "type": "macro_sector_turning",
                        "fires": False,
                        "description": (
                            f"Sector '{sector}' macro signal ({', '.join(hit['matched'])}): "
                            f"\"{hit['title']}\" -- confirm before treating the macro gate as open."
                        ),
                        "details": hit,
                        "dedupe_key": _key(ticker, "macro_sector_turning", hit["url"]),
                    }
                )

    return by_ticker
