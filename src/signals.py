import hashlib

import indicators as ind


def _key(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


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
        signals.append(
            {
                "category": "technical",
                "type": "golden_cross",
                "fires": gc["fires"],
                "description": (
                    f"50/200 golden cross on {gc['cross_date']}"
                    + (" with volume expansion (entry trigger confirmed)" if gc["fires"] else " but WITHOUT volume expansion (not yet confirmed)")
                ),
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
        signals.append(
            {
                "category": "technical",
                "type": "range_breakout",
                "fires": bo["fires"],
                "description": (
                    f"Closed above {entry['range_breakout']['lookback_days']}-day range high "
                    f"({bo['range_high']:.2f}) on {bo['date']}"
                    + (f" with {bo['volume_multiple']:.1f}x avg volume (confirmed)" if bo["fires"] else " but volume didn't confirm")
                ),
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
        signals.append(
            {
                "category": "technical",
                "type": "demand_zone_reaction",
                "fires": zone["fires"],
                "description": (
                    f"Price touched demand zone ~{zone['zone_level']:.2f} "
                    f"({zone['zone_touches']} prior touches)"
                    + (" and closed back above it (reaction confirmed)" if zone["fires"] else " -- watch for a close back above to confirm")
                ),
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


def _article_matches(article, terms):
    text = f"{article.get('title') or ''} {article.get('description') or ''}".lower()
    return [t for t in terms if t.lower() in text]


def build_news_signals(tickers_meta, company_articles, macro_articles, strategy):
    """
    Returns dict[ticker] -> list of signal dicts (macro, fundamental/candidacy, exit-guidance).
    """
    by_ticker = {t["ticker"]: [] for t in tickers_meta}
    name_to_ticker = {t["name"]: t["ticker"] for t in tickers_meta}
    ticker_to_sector = {t["ticker"]: t["sector"] for t in tickers_meta}

    candidacy_kw = strategy["candidacy_filter"]["keywords_positive"]
    rollover_kw = strategy["exit_triggers"]["guidance_rollover"]["keywords"]

    for article in company_articles:
        matched_names = _article_matches(article, list(name_to_ticker.keys()))
        for name in matched_names:
            ticker = name_to_ticker[name]
            url = article.get("url", "")

            hits = _article_matches(article, candidacy_kw)
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

            hits = _article_matches(article, rollover_kw)
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
