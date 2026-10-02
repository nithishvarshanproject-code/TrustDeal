"""Information tools the language model may choose (function calling). They never decide:
alternatives are priced and approved by MeTTa; market prices are seller-only evidence."""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Product, ToolCache
from engine import bridge, log_redaction

MAX_CHEAPER_MODELS = 2
ENV_FILE = __import__("pathlib").Path(__file__).resolve().parents[2] / "omega" / "omega.env"
TAVILY_URL = os.getenv("TAVILY_URL", "https://api.tavily.com/search")
TAVILY_TIMEOUT_S = float(os.getenv("TAVILY_TIMEOUT_S", "20"))
CACHE_TTL = timedelta(hours=24)
MAX_PRICES = 5
MAX_TITLE = 120


class ToolUnavailable(Exception):
    """The tool could not run (no provider, network error, rate limit); the agent continues."""


# ---------- suggest_alternatives ----------

def suggest_alternatives(db: Session, product: Product, original_input: dict,
                         input_for) -> list[dict]:
    """Up to 3 options from the store's own catalog that meet the customer's budget.
    Candidates: cheaper models in the same category (closest price first); MeTTa adds a
    bigger-quantity option when it applies, and keeps only options its rules APPROVE.
    `input_for(product)` builds the MeTTa deal input for another product (same customer)."""
    cheaper = db.scalars(
        select(Product).where(Product.category == product.category, Product.id != product.id,
                              Product.list_price < product.list_price)
        .order_by(Product.list_price.desc())).all()[:MAX_CHEAPER_MODELS]
    candidates = [("cheaper-model", p.id, input_for(p)) for p in cheaper]
    options = bridge.alternatives(original_input, product.id, candidates)
    names = {p.id: p for p in [product, *cheaper]}
    for option in options:
        p = names[option["product_id"]]
        option.update(product_name=p.name, list_price=p.list_price, category=p.category)
    return options


# ---------- market_price_lookup (Tavily web search; seller-only evidence) ----------
# Web pages are untrusted data. Nothing from them is followed or passed to a model with tools:
# only a price (regex), the result's URL (as returned by the search API) and a sanitized title
# are kept. No source URL -> not kept. Results are cached 24 h and never mark anything verified.

_PRICE = re.compile(r"(?:\u20b9|\bRs\.?|\bINR)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def web_search_key() -> str | None:
    """TAVILY_API_KEY from the environment, else omega/omega.env. DEALDESK_WEB_SEARCH=off disables it."""
    if os.getenv("DEALDESK_WEB_SEARCH", "tavily").lower() == "off":
        return None
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key and ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("TAVILY_API_KEY="):
                key = line.split("=", 1)[1].strip()
    log_redaction.register_secret(key)
    return key or None


def _tavily_post(body: dict, key: str) -> dict:
    """The only network call of this tool (tests replace it)."""
    try:
        resp = httpx.post(TAVILY_URL, json=body, timeout=TAVILY_TIMEOUT_S,
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        raise ToolUnavailable(f"network error ({exc.__class__.__name__})") from exc
    if resp.status_code == 429:
        raise ToolUnavailable("rate limited (HTTP 429)")
    if resp.status_code != 200:
        raise ToolUnavailable(f"search provider returned HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ToolUnavailable("invalid JSON from the search provider") from exc


def _clean_title(text) -> str:
    title = " ".join(_CONTROL.sub(" ", str(text or "")).split())
    return title[:MAX_TITLE]


def _safe_url(url) -> str | None:
    if not isinstance(url, str) or len(url) > 500 or _CONTROL.search(url):
        return None
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc else None


def extract_prices(results: list, retrieved_at: str) -> list[dict]:
    """Structured fields only: the first rupee price in each result, with that result's URL."""
    prices = []
    for r in results if isinstance(results, list) else []:
        if not isinstance(r, dict):
            continue
        url = _safe_url(r.get("url"))
        if url is None:
            continue                                    # no source -> not shown
        match = _PRICE.search(str(r.get("content") or ""))
        if not match:
            continue
        try:
            amount = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if not 1 <= amount <= 10_000_000:
            continue
        prices.append({"amount": amount, "currency": "INR", "source_url": url,
                       "source_title": _clean_title(r.get("title")) or urlparse(url).netloc,
                       "retrieved_at": retrieved_at})
        if len(prices) >= MAX_PRICES:
            break
    return prices


def market_price_lookup(db: Session, product_name: str) -> dict:
    """Current market prices with source URLs for the seller (never for the customer).
    Returns {"prices": [{amount, currency, source_url, source_title, retrieved_at}], "cached": bool}."""
    cache_key = " ".join(product_name.lower().split())
    since = datetime.now(timezone.utc) - CACHE_TTL
    for row in db.scalars(select(ToolCache).where(ToolCache.tool == "market_price_lookup",
                                                  ToolCache.key == cache_key).order_by(ToolCache.id.desc())):
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        if created >= since:
            return {**json.loads(row.result_json), "cached": True}
        break
    key = web_search_key()
    if not key:
        raise ToolUnavailable("no web search provider configured (TAVILY_API_KEY)")
    data = _tavily_post({"query": f"{product_name} price in India", "search_depth": "basic",
                         "max_results": 8, "include_answer": False}, key)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = {"query": f"{product_name} price in India", "prices": extract_prices(data.get("results"), now)}
    db.add(ToolCache(tool="market_price_lookup", key=cache_key, result_json=json.dumps(result)))
    db.flush()
    return {**result, "cached": False}
