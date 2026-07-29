# 20TopValueStocksBot

A private data pool that tracks 20 stocks: per-stock thesis notes plus a running
log of technical / fundamental / macro / news signals, refreshed every 2 hours
by a GitHub Actions cron job. Free-tier data sources: [TwelveData](https://twelvedata.com)
(price/volume) and [Finnhub](https://finnhub.io) (company news + general market news).

## Layout

- `config/tickers.json` — the 20 tracked tickers, with a sector tag used by the macro gate.
- `config/strategy.json` — your buy/sell strategy, encoded as structured rules. Each rule is
  tagged `HARD` (computed directly and reliably from price/volume data) or `SOFT` (a keyword/heuristic
  proxy from news headlines meant to prompt manual review, not to auto-fire a trade).
- `data/stocks/<TICKER>.json` — one file per stock: `thesis` (fill this in yourself, freeform),
  `sector`, and `signal_log` (append-only, deduplicated, populated by each run).
- `src/` — the fetch + indicator + rule-matching + orchestration code.
- `.github/workflows/run_every_2h.yml` — the cron job.

## Known free-tier limitations

- **Finnhub free tier**: 60 API calls/minute, no hard daily cap, and company-news is
  scoped per-symbol directly (no text-matching needed to attribute an article to a
  ticker). One call per ticker per run (20 calls) plus one general-news call comfortably
  fits the per-minute limit with a small throttle between calls. Unlike NewsAPI's free
  plan, there's no ~24h article lag, so news-derived signals (macro gate, EPS-revision
  proxy, guidance rollover) can actually reflect same-day news — still treat them as
  "worth checking" (`SOFT` confidence), not confirmed facts.
- **TwelveData free plan**: capped at 8 API *credits* per minute (confirmed directly from the
  API's error response), and each symbol in a batch call costs 1 credit regardless of batching --
  so one call for all 20 tickers instantly blows the per-minute budget. The client chunks into
  groups of 8 symbols and pauses ~61s between chunks, adding ~2 minutes to each run (harmless on
  a 2-hour cadence). Don't add per-symbol indicator API calls on top of this (indicators are
  computed locally in `indicators.py` from the one daily-history pull, specifically to avoid
  burning more credits than necessary).
- **EPS revisions & true valuation-vs-history**: real analyst estimate-revision feeds and
  historical P/E percentile data generally aren't available for free. The bot substitutes:
  a 52-week price-range position as a "cheap vs. own history" proxy (always computed), and a
  news-headline keyword scan as an EPS-revision proxy (flagged `SOFT`, always says "verify" in
  its description).
- **Macro gate** ("sector has actually turned") is inherently a judgment call your strategy
  explicitly wants to make deliberately, not something to auto-confirm. The bot only ever
  raises a `macro_sector_turning` flag for you to evaluate — it never marks a sector as
  confirmed-open on its own.

## Local setup

1. Copy `.env.example` to `.env` and fill in your two free API keys.
2. `pip install -r requirements.txt`
3. `python src/main.py`

Output: console log of what ran, plus updated files under `data/stocks/`.

## Deploying the scheduled job

1. Push this repo to GitHub (already done if you're reading this from the repo).
2. In the repo's Settings → Secrets and variables → Actions, add two repository secrets:
   - `TWELVEDATA_API_KEY`
   - `FINNHUB_API_KEY`
3. The workflow runs automatically every 2 hours (`0 */2 * * *`), and can also be triggered
   manually from the Actions tab ("Run workflow") to test it immediately rather than waiting
   for the next scheduled tick.
4. Each run commits any new signals straight back into `data/stocks/*.json` on the default
   branch, so your git history becomes a free audit trail of what fired and when.

## Editing your thesis per stock

`data/stocks/<TICKER>.json` has a `thesis` field that starts empty. Edit it directly (or ask me
to draft one from a conversation) — it's never overwritten by the bot, only `signal_log` and
`last_updated` are.
