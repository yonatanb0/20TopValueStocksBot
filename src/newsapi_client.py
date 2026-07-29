"""
NewsAPI client. Free "Developer" tier limits: 100 requests/day, and articles
lag live news by up to ~24 hours. We batch company names into query chunks
(NewsAPI's q parameter caps around 500 chars) to keep total requests per
2-hour run low -- with 20 tickers this is ~3-4 requests/run, well under
the daily cap even at 12 runs/day.
"""
import requests

BASE_URL = "https://newsapi.org/v2/everything"
MAX_Q_LEN = 450  # stay safely under NewsAPI's ~500 char cap on q


def chunk_names(names, max_len=MAX_Q_LEN):
    """Group company names into OR-query chunks that fit under max_len."""
    chunks = []
    current = []
    current_len = 0
    for name in names:
        token = f'"{name}"'
        added_len = len(token) + (4 if current else 0)  # + ' OR '
        if current and current_len + added_len > max_len:
            chunks.append(current)
            current = [name]
            current_len = len(token)
        else:
            current.append(name)
            current_len += added_len
    if current:
        chunks.append(current)
    return chunks


def _request(query, api_key, page_size=30, timeout=30):
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": page_size,
        "apiKey": api_key,
    }
    resp = requests.get(BASE_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {payload.get('message')}")
    return payload.get("articles", [])


def fetch_company_news(company_names, api_key):
    """
    Returns a flat list of article dicts (title, description, url, publishedAt, source).
    Each article is later matched locally against company names / keyword lists.
    """
    articles = []
    for chunk in chunk_names(company_names):
        query = " OR ".join(f'"{name}"' for name in chunk)
        articles.extend(_request(query, api_key))
    return articles


def fetch_macro_news(macro_keywords, api_key):
    query = " OR ".join(f'"{kw}"' for kw in macro_keywords)
    return _request(query, api_key, page_size=50)
