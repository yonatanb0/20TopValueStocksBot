import sys

from dotenv import load_dotenv
load_dotenv()

from config import load_tickers, load_strategy, get_api_keys, get_ibkr_credentials
import data_store
import twelvedata_client
import finnhub_client
import ibkr_client
import signals as sig
import state_vector as sv
import basket_analytics as ba

BENCHMARK_SYMBOL = "SPY"


def main():
    tickers_meta = load_tickers()
    strategy = load_strategy()
    try:
        twelvedata_key, finnhub_key = get_api_keys()
    except RuntimeError as e:
        print(f"[main] FATAL: {e}", file=sys.stderr)
        sys.exit(1)

    for t in tickers_meta:
        data_store.init_stock_file(t["ticker"], t["name"], t["sector"])

    ticker_symbols = [t["ticker"] for t in tickers_meta]

    print(f"[main] Fetching daily history for {len(ticker_symbols)} tickers + benchmark from TwelveData...")
    try:
        history = twelvedata_client.fetch_daily_history(ticker_symbols + [BENCHMARK_SYMBOL], twelvedata_key)
    except Exception as e:
        print(f"[main] FATAL: TwelveData fetch failed: {e}", file=sys.stderr)
        sys.exit(1)
    benchmark_ohlcv = history.pop(BENCHMARK_SYMBOL, None)
    if benchmark_ohlcv is None:
        print(f"[main] WARNING: {BENCHMARK_SYMBOL} benchmark history missing this run -- beta will be skipped.")

    print(f"[main] Fetching company news from Finnhub for {len(ticker_symbols)} tickers...")
    company_news_by_ticker = finnhub_client.fetch_company_news_batch(ticker_symbols, finnhub_key)

    print("[main] Fetching general market news from Finnhub...")
    try:
        macro_articles = finnhub_client.fetch_general_news(finnhub_key)
    except Exception as e:
        print(f"[main] WARNING: general news fetch failed: {e}")
        macro_articles = []

    news_signals_by_ticker = sig.build_news_signals(
        tickers_meta, company_news_by_ticker, macro_articles, strategy
    )

    total_added = 0
    for t in tickers_meta:
        ticker = t["ticker"]
        ohlcv = history.get(ticker)
        technical_signals = build_technical_signals_safe(ticker, ohlcv, strategy)
        news_signals = news_signals_by_ticker.get(ticker, [])

        last_price, last_price_date = (None, None)
        if ohlcv:
            last_price, last_price_date = ohlcv[-1]["close"], ohlcv[-1]["date"]

        all_signals = technical_signals + news_signals
        added = data_store.append_signals(ticker, all_signals, last_price, last_price_date)
        total_added += added
        if added:
            print(f"[main] {ticker}: +{added} new signal(s)")

    print(f"[main] Done. {total_added} new signal(s) written across {len(tickers_meta)} tickers.")

    positions = run_positions_and_state_phase(tickers_meta, ticker_symbols, history, finnhub_key)
    run_basket_analytics_phase(tickers_meta, history, benchmark_ohlcv, positions)


def run_positions_and_state_phase(tickers_meta, ticker_symbols, history, finnhub_key):
    """
    Purely additive on top of the signal pipeline above: IBKR positions
    (needs its own credentials, independently optional) + Finnhub
    fundamentals (reuses the finnhub_key already required for news -- no
    extra credential needed) -> state_vector.py -> data/positions.json and
    data/state/{TICKER}.json. IBKR being unconfigured/expired only skips the
    positions fetch, not the whole phase -- the state vector is still useful
    from fundamentals + thesis + technicals alone.
    """
    positions = {}
    ibkr_creds = get_ibkr_credentials()
    if ibkr_creds is None:
        print("[main] Skipping IBKR positions fetch: IBKR_FLEX_TOKEN / IBKR_FLEX_QUERY_ID not configured.")
    else:
        ibkr_token, ibkr_query_id = ibkr_creds
        print("[main] Fetching IBKR Flex Query positions (read-only)...")
        try:
            positions = ibkr_client.fetch_positions(ibkr_token, ibkr_query_id, ticker_symbols)
            data_store.write_positions(positions)
        except Exception as e:
            print(f"[main] WARNING: IBKR positions fetch failed: {e}")
            positions = {}

    print(f"[main] Fetching Finnhub fundamentals for {len(ticker_symbols)} tickers...")
    fundamentals_by_ticker = finnhub_client.fetch_fundamentals_batch(ticker_symbols, finnhub_key)

    for t in tickers_meta:
        ticker = t["ticker"]
        ohlcv = history.get(ticker)
        thesis = data_store.load_thesis(ticker)
        position = positions.get(ticker)
        fundamentals = fundamentals_by_ticker.get(ticker)
        prior_state = data_store.load_prior_state(ticker)

        try:
            state = sv.compute_state_vector(ticker, ohlcv, thesis, position, fundamentals, prior_state)
            data_store.write_state(ticker, state)
        except Exception as e:
            print(f"[main] WARNING: state vector computation failed for {ticker}: {e}")

    print(f"[main] Positions/state-vector phase done for {len(tickers_meta)} tickers.")
    return positions


def run_basket_analytics_phase(tickers_meta, history, benchmark_ohlcv, positions):
    """
    Layer 2: basket-level analytics (60d correlation matrix, beta vs. the
    benchmark, sector concentration) -- pure Python math (basket_analytics.py),
    reuses the OHLCV history already fetched this run. Written to
    data/basket.json, one file for the whole basket (not per-ticker).
    """
    print("[main] Computing basket analytics (correlation, beta, sector concentration)...")
    try:
        basket = ba.compute_basket_analytics(tickers_meta, history, benchmark_ohlcv, positions)
        data_store.write_basket_analytics(basket)
        print("[main] Basket analytics written to data/basket.json.")
    except Exception as e:
        print(f"[main] WARNING: basket analytics computation failed: {e}")


def build_technical_signals_safe(ticker, ohlcv, strategy):
    if not ohlcv:
        print(f"[main] WARNING: no price history for {ticker}, skipping technical signals")
        return []
    try:
        return sig.build_technical_signals(ticker, ohlcv, strategy)
    except Exception as e:
        print(f"[main] WARNING: technical signal computation failed for {ticker}: {e}")
        return []


if __name__ == "__main__":
    main()
