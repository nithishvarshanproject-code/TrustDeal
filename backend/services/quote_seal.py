"""Tamper-proof quotes: every quote carries a short verification code, an HMAC-SHA256 over its
canonical fields (quote ref, customer, product, quantity, list price, discount, unit price, total,
savings, valid-until) keyed with a server secret.

The secret is 32 random bytes in a git-ignored file (backend/secrets/quote_hmac.key, or
DEALDESK_QUOTE_SECRET_FILE), handled by secret_file.py: created on first use, never overwritten, a corrupt
file is a clear error, registered with the log redaction, never returned, printed or logged.

The code is 12 consonants (BCDFGHJKLMNPQRSTVWXZ, about 52 bits), shown as KXMB-PQRT-HNDZ: no digits
(it can never look like a price) and no vowels (it can never spell a word).
"""
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote as urlquote

from backend.services.secret_file import SecretFileError, load_or_create

DEFAULT_SECRET_FILE = Path(__file__).resolve().parents[1] / "secrets" / "quote_hmac.key"
ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
CODE_LEN = 12
VERSION = "v1"
DEFAULT_PUBLIC_URL = "http://localhost:5173"
_CODE_RE = re.compile(f"[{ALPHABET}]{{{CODE_LEN}}}")

_cache: tuple[str, bytes] | None = None     # (secret file path, key)

QuoteSecretError = SecretFileError          # the secret file exists but cannot be used


def secret_file() -> Path:
    return Path(os.environ.get("DEALDESK_QUOTE_SECRET_FILE") or DEFAULT_SECRET_FILE)


def reset_cache() -> None:
    """Forget the loaded key (as after a restart)."""
    global _cache
    _cache = None


def _key() -> bytes:
    global _cache
    path = secret_file()
    if _cache is not None and _cache[0] == str(path):
        return _cache[1]
    key = load_or_create(path, "quote signing secret")
    _cache = (str(path), key)
    return key


def _money(value: float) -> str:
    return f"{round(float(value), 2):.2f}"


def _utc(value: datetime) -> str:
    if value.tzinfo is None:          # SQLite returns naive datetimes; they are stored in UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(ref: str, quote, deal) -> bytes:
    """The signed fields of a quote (AgentQuote) and its request (AgentDeal), as canonical JSON."""
    payload = {"v": VERSION, "quote_ref": ref, "customer_id": int(deal.customer_id),
               "product_id": int(deal.product_id), "quantity": int(quote.quantity),
               "list_price": _money(quote.list_price), "discount_pct": _money(quote.discount),
               "unit_price": _money(quote.unit_price), "total": _money(quote.total),
               "savings": _money(quote.savings), "valid_until": _utc(quote.valid_until)}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def code_for(ref: str, quote, deal) -> str:
    """The 12-letter code for this quote's current fields (compact, no dashes)."""
    digest = hmac.new(_key(), canonical(ref, quote, deal), hashlib.sha256).digest()
    n = int.from_bytes(digest[:8], "big")
    letters = []
    for _ in range(CODE_LEN):
        n, r = divmod(n, len(ALPHABET))
        letters.append(ALPHABET[r])
    return "".join(letters)


def normalize_code(text: str) -> str | None:
    """'kxmb pqrt-hndz' -> 'KXMBPQRTHNDZ'; anything that cannot be a code -> None."""
    compact = re.sub(r"[\s-]", "", str(text)).upper()
    return compact if _CODE_RE.fullmatch(compact) else None


def format_code(code: str) -> str:
    """'KXMBPQRTHNDZ' -> 'KXMB-PQRT-HNDZ'."""
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def matches(ref: str, quote, deal, given: str) -> bool:
    """True only if the given code equals the stored code AND a fresh HMAC of the stored fields
    (an edited row no longer matches its code). Constant-time comparisons."""
    code = normalize_code(given)
    if code is None or not quote.verify_code or quote.list_price is None:
        return False
    fresh = hmac.compare_digest(code_for(ref, quote, deal), code)
    stored = hmac.compare_digest(quote.verify_code, code)
    return fresh and stored


def public_url() -> str:
    url = (os.environ.get("DEALDESK_PUBLIC_URL") or DEFAULT_PUBLIC_URL).strip().rstrip("/")
    return url if re.fullmatch(r"https?://[A-Za-z0-9.\-:\[\]]+", url) else DEFAULT_PUBLIC_URL


def verify_url(ref: str, code: str) -> str:
    """The Verify quote page for this quote (the QR code on the PDF)."""
    return f"{public_url()}/#verify?ref={urlquote(ref)}&code={urlquote(code)}"
