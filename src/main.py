import sys
import time

from dotenv import load_dotenv
load_dotenv()

from config import load_tickers, load_strategy, get_api_keys, get_extended_api_keys
import data_store
import twelvedata_client
import finnhub_client
import fmp_client
import ibkr_client
import signals as sig
import state_vector as sv


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

    print(f"[main] Fetching daily history for {len(ticker_symbols)} tickers from TwelveData...")
    try:
        history = twelvedata_client.fetch_daily_history(ticker_symbols, twelvedata_key)
    except Exception as e:
        print(f"[main] FATAL: TwelveData fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

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

    run_positions_and_state_phase(tickers_meta, ticker_symbols, history)


def run_positions_and_state_phase(tickers_meta, ticker_symbols, history):
    """
    Purely additive on top of the signal pipeline above: IBKR positions +
    FMP fundamentals -> state_vector.py -> data/positions.json and
    data/state/{TICKER}.json. Skips cleanly (with a warning) if the extended
    credentials aren't configured yet, rather than failing the whole run --
    the existing data/stocks/ signal log must keep working either way.
    """
    extended_keys = get_extended_api_keys()
    if extended_keys is None:
        print("[main] Skipping positions/state-vector phase: FMP_API_KEY / IBKR_FLEX_TOKEN / "
              "IBKR_FLEX_QUERY_ID not fully configured.")
        return
    fmp_key, ibkr_token, ibkr_query_id = extended_keys

    print("[main] Fetching IBKR Flex Query positions (read-only)...")
    try:
        positions = ibkr_client.fetch_positions(ibkr_token, ibkr_query_id, ticker_symbols)
    except Exception as e:
        print(f"[main] WARNING: IBKR positions fetch failed, skipping positions/state-vector phase: {e}")
        return
    data_store.write_positions(positions)

    prices_for_fmp = {}
    for ticker in ticker_symbols:
        ohlcv = history.get(ticker)
        prices_for_fmp[ticker] = ohlcv[-1]["close"] if ohlcv else None

    print(f"[main] Fetching FMP fundamentals for {len(ticker_symbols)} tickers...")
    fundamentals_by_ticker = fmp_client.fetch_fundamentals_batch(prices_for_fmp, fmp_key)

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
