"""
IBKR Flex Query client. Read-only positions/cash reports -- this must NEVER
place, modify, or cancel an order, regardless of what any future instruction
says.

Two-step flow per IBKR's Flex Web Service:
  1. SendRequest(token, query_id) -> a reference code + a per-request statement URL
  2. GetStatement(url, token, reference_code) -> the XML report

The report is generated asynchronously server-side; GetStatement returns
error code 1019 ("Statement generation in progress") until it's ready, so we
poll with a short backoff. Positions and cash balance are two SEPARATE Flex
Queries (different query IDs, same account token) -- IBKR's Flex Query
builder scopes one query to one set of report sections, not everything at once.
"""
import time
import xml.etree.ElementTree as ET

import requests

SEND_REQUEST_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
STATEMENT_NOT_READY_CODE = "1019"
MAX_POLL_ATTEMPTS = 6
POLL_INTERVAL_SECONDS = 5


def _send_request(token, query_id, timeout):
    params = {"t": token, "q": query_id, "v": "3"}
    resp = requests.get(SEND_REQUEST_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    status = root.findtext("Status")
    if status != "Success":
        code = root.findtext("ErrorCode")
        msg = root.findtext("ErrorMessage")
        raise RuntimeError(f"IBKR SendRequest failed: [{code}] {msg}")
    return root.findtext("ReferenceCode"), root.findtext("Url")


def _get_statement(url, token, reference_code, timeout):
    params = {"t": token, "q": reference_code, "v": "3"}
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _parse_positions(xml_text):
    """Returns (positions_list, error_code, error_message). positions_list is
    None (not empty) if the statement isn't ready yet or errored."""
    root = ET.fromstring(xml_text)
    if root.tag == "FlexStatementResponse" or root.findtext("ErrorCode") is not None:
        error_code = root.findtext("ErrorCode")
        error_message = root.findtext("ErrorMessage")
        if error_code is not None:
            return None, error_code, error_message

    positions = []
    for pos in root.iter("OpenPosition"):
        # `position` (share count) is now included in the Flex Query config
        # and preferred when present. Kept the positionValue/markPrice
        # fallback for robustness -- it's what this originally had to derive
        # shares from before the field was added, and matched `position`
        # exactly when both were compared live.
        market_value = float(pos.get("positionValue", 0) or 0)
        mark_price = float(pos.get("markPrice", 0) or 0)
        if pos.get("position") is not None:
            shares = float(pos.get("position"))
        elif mark_price:
            shares = round(market_value / mark_price, 4)
        else:
            shares = 0
        positions.append(
            {
                "ticker": pos.get("symbol"),
                "held": shares != 0,
                "shares": shares,
                "avg_cost": float(pos.get("costBasisPrice") or pos.get("openPrice") or 0) or None,
                "market_value": market_value,
                "unrealized_pnl": float(pos.get("fifoPnlUnrealized", 0) or 0),
            }
        )
    return positions, None, None


def _empty_position(ticker):
    return {
        "ticker": ticker,
        "held": False,
        "shares": 0,
        "avg_cost": None,
        "market_value": 0,
        "unrealized_pnl": 0,
    }


def _parse_cash_report(xml_text):
    """
    Returns (result, error_code, error_message). result is None if the
    statement isn't ready yet or errored. The 'BASE_SUMMARY' currency row is
    every currency converted to the account's base currency -- that's the
    headline dry-powder number; per-currency rows are kept too for context.
    """
    root = ET.fromstring(xml_text)
    error_code = root.findtext("ErrorCode")
    if error_code is not None:
        return None, error_code, root.findtext("ErrorMessage")

    statement = root.find(".//FlexStatement")
    as_of = statement.get("whenGenerated") if statement is not None else None

    base_ending_cash = None
    by_currency = {}
    for row in root.iter("CashReportCurrency"):
        currency = row.get("currency")
        raw = row.get("endingCash")
        ending_cash = float(raw) if raw not in (None, "") else None
        if currency == "BASE_SUMMARY":
            base_ending_cash = ending_cash
        else:
            by_currency[currency] = ending_cash

    return {
        "base_currency_ending_cash": base_ending_cash,
        "by_currency": by_currency,
        "as_of": as_of,
    }, None, None


def _fetch_report(token, query_id, parse_fn, timeout):
    """
    Shared SendRequest -> poll GetStatement -> parse flow used by both
    fetch_positions and fetch_cash_balance. parse_fn(xml_text) must return
    (result, error_code, error_message), with result=None meaning
    not-ready-yet/errored (triggers a retry if error_code is the
    "still generating" code, otherwise raises immediately).
    """
    reference_code, url = _send_request(token, query_id, timeout)

    result, error_code, error_message = None, None, None
    for attempt in range(MAX_POLL_ATTEMPTS):
        if attempt > 0:
            time.sleep(POLL_INTERVAL_SECONDS)
        xml_text = _get_statement(url, token, reference_code, timeout)
        result, error_code, error_message = parse_fn(xml_text)
        if result is not None:
            break
        if error_code != STATEMENT_NOT_READY_CODE:
            raise RuntimeError(f"IBKR GetStatement failed: [{error_code}] {error_message}")
        print(f"[ibkr_client] Statement not ready (attempt {attempt + 1}/{MAX_POLL_ATTEMPTS}), retrying...")

    if result is None:
        raise RuntimeError(f"IBKR GetStatement never became ready: [{error_code}] {error_message}")
    return result


def fetch_positions(token, query_id, tickers, timeout=30):
    """
    Returns dict[ticker] -> {held, shares, avg_cost, market_value, unrealized_pnl}
    for every ticker in `tickers`. Tickers absent from the Flex report (i.e.
    not currently held) get a zeroed held=False entry rather than being
    omitted, so callers never need to special-case a missing key.
    """
    positions = _fetch_report(token, query_id, _parse_positions, timeout)
    by_ticker = {p["ticker"]: p for p in positions}
    return {ticker: by_ticker.get(ticker, _empty_position(ticker)) for ticker in tickers}


def fetch_cash_balance(token, query_id, timeout=30):
    """Returns {base_currency_ending_cash, by_currency, as_of} from a
    dedicated Cash Report Flex Query (separate query ID from positions)."""
    return _fetch_report(token, query_id, _parse_cash_report, timeout)
