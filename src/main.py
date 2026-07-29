import sys
import time

from dotenv import load_dotenv
load_dotenv()

from config import load_tickers, load_strategy, get_api_keys
import data_store
import twelvedata_client
import finnhub_client
import signals as sig


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

        all_signals = technical_signals + news_signals
        added = data_store.append_signals(ticker, all_signals)
        total_added += added
        if added:
            print(f"[main] {ticker}: +{added} new signal(s)")

    print(f"[main] Done. {total_added} new signal(s) written across {len(tickers_meta)} tickers.")


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
