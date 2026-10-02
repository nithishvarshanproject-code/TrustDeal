"""Hash-chained audit ledger: an append-only record of every decision, agent action, override,
policy change, task resolution, quote, order and tax invoice (and each demo reset).

Each entry stores seq, ts, kind, an idempotency key, a canonical JSON payload summary, prev_hash and
    hash = HMAC-SHA256(ledger secret, prev_hash + canonical JSON of {seq, ts, kind, key, payload})
so editing any column of an entry breaks it, and nobody can re-chain edited entries without the
ledger secret (a SEPARATE git-ignored file, backend/secrets/ledger_hmac.key or
DEALDESK_LEDGER_SECRET_FILE, handled like the quote secret by secret_file.py).

append() runs inside the event's own transaction, so an event that rolls back leaves no entry. It
inserts first (SQLite then holds the write lock until commit and assigns the next seq), so concurrent
writers can neither fork the chain nor reuse a number. verify() walks the chain: seq contiguous from 1
(a deleted entry is a gap), each prev_hash = previous hash, payload canonical, hash recomputes, and
the count matches SQLite's AUTOINCREMENT counter (deleting the newest entries is caught too).
Seller-only: served by routes/ledger.py under /seller/ledger, never by a customer endpoint.
"""
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.models import LedgerEntry
from backend.services.secret_file import SecretFileError, load_or_create

GENESIS = "0" * 64
KINDS = ("decision", "agent_action", "override", "policy_change", "task_resolution", "quote", "order",
         "invoice", "demo_reset")
DEFAULT_SECRET_FILE = Path(__file__).resolve().parents[1] / "secrets" / "ledger_hmac.key"

_cache: tuple[str, bytes] | None = None     # (secret file path, key)

LedgerSecretError = SecretFileError


def secret_file() -> Path:
    return Path(os.environ.get("DEALDESK_LEDGER_SECRET_FILE") or DEFAULT_SECRET_FILE)


def reset_cache() -> None:
    """Forget the loaded key (as after a restart)."""
    global _cache
    _cache = None


def _key() -> bytes:
    global _cache
    path = secret_file()
    if _cache is not None and _cache[0] == str(path):
        return _cache[1]
    key = load_or_create(path, "ledger secret")
    _cache = (str(path), key)
    return key


def canonical(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(prev_hash: str, seq: int, ts: str, kind: str, key: str, payload) -> str:
    message = prev_hash + canonical({"seq": seq, "ts": ts, "kind": kind, "key": key, "payload": payload})
    return hmac.new(_key(), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _last_reset(db: Session) -> int:
    return db.scalar(select(func.max(LedgerEntry.seq)).where(LedgerEntry.kind == "demo_reset")) or 0


def append(db: Session, kind: str, key: str, payload: dict) -> LedgerEntry:
    """Add one entry in the caller's transaction. Idempotent: an entry with the same key since the
    last demo reset (ids restart after a reset) is returned instead of adding another."""
    if kind not in KINDS:
        raise ValueError(f"unknown ledger kind {kind!r}")
    existing = db.scalar(select(LedgerEntry).where(LedgerEntry.event_key == key,
                                                   LedgerEntry.seq > _last_reset(db)).limit(1))
    if existing is not None:
        return existing
    body = canonical(payload)
    entry = LedgerEntry(ts=datetime.now(timezone.utc).isoformat(timespec="microseconds"), kind=kind,
                        event_key=key, payload=body, prev_hash="", hash="")
    db.add(entry)
    db.flush()                                   # write lock until commit; seq assigned
    prev = db.scalar(select(LedgerEntry.hash).where(LedgerEntry.seq < entry.seq)
                     .order_by(LedgerEntry.seq.desc()).limit(1))
    entry.prev_hash = prev or GENESIS
    entry.hash = _digest(entry.prev_hash, entry.seq, entry.ts, kind, key, json.loads(body))
    db.flush()
    return entry


def _written(db: Session) -> int:
    """How many entries SQLite's AUTOINCREMENT counter says were ever written (0 if unknown)."""
    if db.get_bind().dialect.name != "sqlite":
        return 0
    try:
        return db.scalar(text("SELECT seq FROM sqlite_sequence WHERE name = 'ledger_entries'")) or 0
    except OperationalError:
        return 0


def _broken(seq: int, checked: int, reason: str) -> dict:
    return {"intact": False, "entries": checked, "broken_at": seq, "reason": reason}


def verify(db: Session) -> dict:
    """Recompute the whole chain. {"intact": True, "entries", "head"} or the first broken entry."""
    prev, expected, count = GENESIS, 1, 0
    for e in db.scalars(select(LedgerEntry).order_by(LedgerEntry.seq)):
        if e.seq != expected:
            return _broken(expected, count, "entry missing (deleted)")
        if e.prev_hash != prev:
            return _broken(e.seq, count, "does not link to the previous entry")
        try:
            payload = json.loads(e.payload)
        except (TypeError, ValueError):
            return _broken(e.seq, count, "payload is not valid JSON")
        if canonical(payload) != e.payload:
            return _broken(e.seq, count, "payload was changed")
        computed = _digest(prev, e.seq, e.ts, e.kind, e.event_key, payload)
        if not hmac.compare_digest(computed.encode("ascii"), (e.hash or "").encode("utf-8")):
            return _broken(e.seq, count, "contents do not match its hash")
        prev, expected, count = e.hash, expected + 1, count + 1
    if _written(db) > count:
        return _broken(count + 1, count, "newest entries missing (deleted)")
    return {"intact": True, "entries": count, "head": prev if count else None}


def latest(db: Session, limit: int = 50) -> list[dict]:
    """The newest entries first, for the seller's Ledger panel."""
    rows = db.scalars(select(LedgerEntry).order_by(LedgerEntry.seq.desc()).limit(limit))
    out = []
    for e in rows:
        try:
            payload = json.loads(e.payload)
        except (TypeError, ValueError):
            payload = None
        out.append({"seq": e.seq, "ts": e.ts, "kind": e.kind, "key": e.event_key, "payload": payload,
                    "prev_hash": e.prev_hash, "hash": e.hash})
    return out


def count(db: Session) -> int:
    return db.scalar(select(func.count(LedgerEntry.seq))) or 0


# ---------- payload summaries for each event ----------

def _sha256(data) -> str:
    return hashlib.sha256(canonical(data).encode("ascii")).hexdigest()


def record_decision(db: Session, *, source: str, decision_id: int, case_id: int, party: str, product: str,
                    deal_input: dict, verdict: dict, runner: str, event: str | None = None) -> None:
    """A MeTTa decision: Deal check (source "deal") or the customer agent (source "agent")."""
    payload = {"source": source, "decision_id": decision_id,
               ("deal_id" if source == "deal" else "request_id"): case_id,
               ("seller" if source == "deal" else "customer"): party, "product": product,
               "quantity": deal_input["quantity"], "asked": deal_input["discount_requested"],
               "result": verdict["result"], "approved_discount": verdict["approved_discount"],
               "confidence": verdict["confidence"],
               "rules": sorted({str(line.get("rule_id")) for line in verdict["trail"] if line.get("rule_id")}),
               "trail_sha256": _sha256(verdict["trail"]), "runner": runner}
    if event is not None:
        payload["event"] = event
    append(db, "decision", f"decision:{source}:{decision_id}", payload)
