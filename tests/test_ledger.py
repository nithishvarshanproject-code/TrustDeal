"""Hash-chained audit ledger: every decision, agent action, override, policy change, task resolution,
quote and order is appended to a keyed chain (HMAC-SHA256 with a separate ledger secret). Verify
recomputes it and names the first broken entry; edits, deletions and re-chaining without the secret
are caught. Seller-only: no customer or public endpoint reaches it."""
import base64
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.agent.followups import run_followups
from backend.models import LedgerEntry
from backend.services import ledger, quote_seal
from engine import bridge
from engine.omega_link import OmegaUnavailable
from tests.test_agent import BUDS, PHONE, PRIYA, act, resolve, rule_ids, say, seller
from tests.test_policy_api import DEAL_1, DEAL_2, DEAL_3, _three_silver_overrides, post

ROOT = Path(__file__).resolve().parents[1]


def _ledger(c, limit=200):
    resp = c.get("/seller/ledger", params={"limit": limit})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _verify(c):
    resp = c.post("/seller/ledger/verify")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _of(entries, kind, **match):
    return [e for e in entries if e["kind"] == kind and all(e["payload"].get(k) == v for k, v in match.items())]


def _activity(c) -> dict:
    """Deal checks, an override, a customer counter -> accept -> quote -> order, an escalation resolved."""
    deals = [post(c, d) for d in (DEAL_1, DEAL_2, DEAL_3)]
    override = c.post(f"/deals/{deals[2]['deal_id']}/override", json={
        "reviewer": "Sales lead", "new_result": "APPROVE", "reason": "key account"}).json()
    v = say(c, PRIYA, "Can I get 20% off this phone?", product_id=PHONE)
    quoted = act(c, PRIYA, v["request_id"], "accept")
    ordered = act(c, PRIYA, v["request_id"], "order")
    e = say(c, PRIYA, "30% off?", product_id=BUDS)
    resolve(c, e["request_id"], "approve")
    return {"deals": deals, "override": override, "request_id": v["request_id"], "quoted": quoted,
            "ordered": ordered, "escalated": e}


def test_normal_activity_is_recorded_and_the_chain_verifies(stack):
    c = stack["client"]
    a = _activity(c)
    led = _ledger(c)
    entries = led["entries"]
    assert [e["seq"] for e in entries] == list(range(led["total"], 0, -1))           # newest first, 1..N
    assert {e["kind"] for e in entries} == {"decision", "agent_action", "override", "task_resolution", "quote",
                                            "order", "invoice"}
    assert _verify(c) == {"intact": True, "entries": led["total"], "head": entries[0]["hash"]}

    [counter] = _of(entries, "decision", source="deal", deal_id=a["deals"][2]["deal_id"])
    p = counter["payload"]
    assert (p["result"], p["approved_discount"], p["asked"], p["quantity"]) == ("COUNTER", 12.0, 20.0, 60)
    assert {"R1", "R2"} <= set(p["rules"]) and re.fullmatch(r"[0-9a-f]{64}", p["trail_sha256"])
    assert [d["payload"]["result"] for d in _of(entries, "decision", source="deal")] == \
        ["COUNTER", "REJECT", "APPROVE"]                                                  # newest first
    [ov] = _of(entries, "override")
    assert (ov["payload"]["override_id"], ov["payload"]["new_result"], ov["payload"]["reviewer"]) == \
        (a["override"]["override_id"], "APPROVE", "Sales lead")

    rid = a["request_id"]
    actions = [e["payload"]["rule_id"] for e in reversed(_of(entries, "agent_action", request_id=rid))]
    assert actions == rule_ids(seller(c, rid)) == ["A3", "A6", "A10", "A11"]
    [quote] = _of(entries, "quote", request_id=rid)
    q = a["quoted"]["quote"]
    assert (quote["payload"]["quote_ref"], quote["payload"]["total"], quote["payload"]["verify_code"]) == \
        (q["quote_ref"], q["total"], q["verify_code"])
    [order] = _of(entries, "order", request_id=rid)
    assert order["payload"]["order_ref"] == a["ordered"]["quote"]["order_ref"]
    [task] = _of(entries, "task_resolution")
    assert (task["payload"]["request_id"], task["payload"]["answer"], task["payload"]["reviewer"]) == \
        (a["escalated"]["request_id"], "approve", "Store manager")
    assert all(e["prev_hash"] == older["hash"] for e, older in zip(entries, entries[1:]))
    assert entries[-1]["prev_hash"] == ledger.GENESIS


def _bump(e):
    payload = json.loads(e.payload)
    payload["edited"] = True
    e.payload = ledger.canonical(payload)


TAMPER = {
    "payload content": _bump,
    "payload text": lambda e: setattr(e, "payload", e.payload + " "),
    "hash": lambda e: setattr(e, "hash", ("0" if e.hash[0] != "0" else "1") + e.hash[1:]),
    "prev_hash": lambda e: setattr(e, "prev_hash", "f" * 64),
    "kind": lambda e: setattr(e, "kind", "order" if e.kind != "order" else "quote"),
    "timestamp": lambda e: setattr(e, "ts", "2020-01-01T00:00:00.000000+00:00"),
    "event key": lambda e: setattr(e, "event_key", e.event_key + "-x"),
}


@pytest.mark.parametrize("field", TAMPER)
def test_editing_an_entry_breaks_verification_at_that_entry(stack, field):
    c = stack["client"]
    for d in (DEAL_1, DEAL_2, DEAL_3, DEAL_1, DEAL_2):
        post(c, d)
    assert _verify(c)["intact"] is True
    with stack["Session"]() as db:
        TAMPER[field](db.get(LedgerEntry, 3))
        db.commit()
    res = _verify(c)
    assert (res["intact"], res["broken_at"], res["entries"]) == (False, 3, 2), res


@pytest.mark.parametrize("which", ["first", "middle", "newest"])
def test_deleting_an_entry_is_detected(stack, which):
    c = stack["client"]
    for d in (DEAL_1, DEAL_2, DEAL_3, DEAL_1, DEAL_2):
        post(c, d)
    seq = {"first": 1, "middle": 3, "newest": 5}[which]
    with stack["Session"]() as db:
        db.delete(db.get(LedgerEntry, seq))
        db.commit()
    res = _verify(c)
    assert (res["intact"], res["broken_at"]) == (False, seq) and "missing" in res["reason"], res
    post(c, DEAL_3)                                     # numbers are never reused: still caught after new entries
    assert _verify(c)["broken_at"] == seq


def _rechain(db, start: int, digest) -> None:
    entries = db.scalars(select(LedgerEntry).order_by(LedgerEntry.seq)).all()
    prev = entries[start - 2].hash if start > 1 else ledger.GENESIS
    for e in entries[start - 1:]:
        e.prev_hash = prev
        e.hash = digest(prev, e)
        prev = e.hash


def test_rechaining_without_the_ledger_secret_is_detected(stack):
    c = stack["client"]
    for d in (DEAL_1, DEAL_2, DEAL_3, DEAL_1):
        post(c, d)

    def plain_sha256(prev, e):          # what an attacker without the secret file can compute
        body = ledger.canonical({"seq": e.seq, "ts": e.ts, "kind": e.kind, "key": e.event_key,
                                 "payload": json.loads(e.payload)})
        return hashlib.sha256((prev + body).encode()).hexdigest()

    with stack["Session"]() as db:
        entry = db.get(LedgerEntry, 2)
        payload = json.loads(entry.payload)
        payload["result"] = "APPROVE"
        entry.payload = ledger.canonical(payload)
        _rechain(db, 2, plain_sha256)
        db.commit()
    res = _verify(c)
    assert (res["intact"], res["broken_at"]) == (False, 2), res

    # The documented limit: someone holding BOTH the database and the ledger secret can re-chain.
    with stack["Session"]() as db:
        _rechain(db, 2, lambda prev, e: ledger._digest(prev, e.seq, e.ts, e.kind, e.event_key,
                                                       json.loads(e.payload)))
        db.commit()
    assert _verify(c)["intact"] is True


def test_appends_are_idempotent_where_events_are(stack):
    c = stack["client"]
    e = say(c, PRIYA, "30% off?", product_id=BUDS)
    resolve(c, e["request_id"], "approve")
    before = _ledger(c)["total"]
    task = next(t for t in c.get("/seller/agent/tasks", params={"status": "all"}).json()
                if t["request_id"] == e["request_id"])
    again = c.post(f"/seller/agent/tasks/{task['task_id']}/resolve",
                   json={"answer": "approve", "reviewer": "Store manager"})
    assert again.status_code == 409 and _ledger(c)["total"] == before        # resolved once, recorded once

    with stack["Session"]() as db:
        first = ledger.append(db, "override", "override:demo-key", {"n": 1})
        second = ledger.append(db, "override", "override:demo-key", {"n": 2})
        db.commit()
        assert first.seq == second.seq == before + 1 and json.loads(first.payload) == {"n": 1}
        now = datetime.now(timezone.utc)
        run_followups(db, now)
        run_followups(db, now)
    assert _ledger(c)["total"] == before + 1
    assert _verify(c)["intact"] is True


def test_a_failed_event_leaves_no_entry(stack, monkeypatch):
    c = stack["client"]
    post(c, DEAL_1)

    def down(*args, **kwargs):
        raise OmegaUnavailable("engine down")

    monkeypatch.setattr(bridge, "next_action", down)       # after MeTTa decided, before the agent acts
    resp = c.post("/customer/messages", json={"customer_id": PRIYA, "product_id": PHONE,
                                              "text": "Can I get 20% off this phone?"})
    assert resp.status_code == 503
    assert [e["kind"] for e in _ledger(c)["entries"]] == ["decision"]      # the rolled-back decision is not there
    assert _verify(c) == {"intact": True, "entries": 1, "head": _ledger(c)["entries"][0]["hash"]}


def test_policy_change_and_demo_reset_are_recorded_and_the_ledger_is_kept(stack):
    c = stack["client"]
    first = post(c, DEAL_1)
    ids = _three_silver_overrides(c)
    apply = {"fact": "tier-cap", "key": "Silver", "old_value": 10.0, "new_value": 12.0,
             "approved_by": "Head of Sales"}
    resp = c.post("/policy/proposals/apply", json=apply)
    assert resp.status_code == 200, resp.text
    [change] = _of(_ledger(c)["entries"], "policy_change")
    p = change["payload"]
    assert (p["fact"], p["key"], p["old_value"], p["new_value"], p["approved_by"], p["evidence"]) == \
        ("tier-cap", "Silver", 10.0, 12.0, "Head of Sales", ids)
    assert p["backup"] == resp.json()["history_file"]
    before = _ledger(c)["total"]
    assert c.post("/policy/proposals/apply", json=apply).status_code == 409   # stale: nothing recorded
    assert _ledger(c)["total"] == before

    assert c.post("/demo/reset", json={"confirm": "RESET"}).status_code == 200
    led = _ledger(c)
    assert led["total"] == before + 1 and led["entries"][0]["kind"] == "demo_reset"
    assert led["entries"][0]["payload"]["policy_restored"] is True
    assert _verify(c)["intact"] is True

    again = post(c, DEAL_1)                     # ids restart after a reset: a new entry, not a duplicate
    assert again["decision_id"] == first["decision_id"]
    same_key = [e for e in _ledger(c)["entries"] if e["key"] == f"decision:deal:{first['decision_id']}"]
    assert len(same_key) == 2
    assert _verify(c) == {"intact": True, "entries": before + 2, "head": _ledger(c)["entries"][0]["hash"]}


def test_customer_and_public_endpoints_never_reach_the_ledger(stack):
    c = stack["client"]
    v = say(c, PRIYA, "Can I get 20% off this phone?", product_id=PHONE)
    v = act(c, PRIYA, v["request_id"], "accept")
    paths = c.app.openapi()["paths"]                    # (app.routes hides paths inside included routers)
    assert sorted(p for p in paths if "ledger" in p) == ["/seller/ledger", "/seller/ledger/verify"]
    assert not [p for p in paths if p.startswith(("/customer", "/verify")) and "ledger" in p]
    for path in ("/customer/ledger", "/customer/ledger/verify", "/verify/ledger"):
        assert c.get(path).status_code in (404, 405) and c.post(path, json={}).status_code in (404, 405)
    q = v["quote"]
    bodies = [v, c.get("/customer/requests", params={"customer_id": PRIYA}).json(),
              c.get(f"/customer/requests/{v['request_id']}", params={"customer_id": PRIYA}).json(),
              c.post("/verify/quote", json={"ref": q["quote_ref"], "code": q["verify_code"]}).json()]
    for body in bodies:
        assert not re.search(r"ledger|prev_hash|event_key|trail_sha256", json.dumps(body))


def test_ledger_secret_is_separate_private_and_never_leaks(stack, caplog):
    caplog.set_level(logging.DEBUG)
    path = ledger.secret_file()
    assert not path.exists()                            # created on first use (a temporary file in tests)
    c = stack["client"]
    v = say(c, PRIYA, "Can I get 20% off this phone?", product_id=PHONE)
    v = act(c, PRIYA, v["request_id"], "accept")
    secret = path.read_text(encoding="ascii").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", secret)
    assert path != quote_seal.secret_file() and secret != quote_seal.secret_file().read_text(encoding="ascii").strip()
    assert ledger.DEFAULT_SECRET_FILE.parent == ROOT / "backend" / "secrets"       # git-ignored folder
    assert ledger.DEFAULT_SECRET_FILE != quote_seal.DEFAULT_SECRET_FILE
    assert "backend/secrets/" in (ROOT / ".gitignore").read_text(encoding="utf-8").split()

    raw = bytes.fromhex(secret)
    pdf = c.get(f"/customer/requests/{v['request_id']}/quote.pdf", params={"customer_id": PRIYA}).content
    blobs = [json.dumps(b) for b in (v, _ledger(c), _verify(c), seller(c, v["request_id"]))]
    blobs += [pdf.decode("latin-1"), caplog.text]
    forms = (secret, secret.upper(), base64.b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode())
    assert not [f for f in forms for blob in blobs if f in blob]

    ledger.reset_cache()                                # a restart reuses the same secret
    assert _verify(c)["intact"] is True and path.read_text(encoding="ascii").strip() == secret

    path.write_text("1" * 64, encoding="ascii")         # another secret: nothing verifies (the chain is keyed)
    ledger.reset_cache()
    res = _verify(c)
    assert (res["intact"], res["broken_at"]) == (False, 1)

    path.write_text("not-a-secret-value", encoding="ascii")
    ledger.reset_cache()
    with pytest.raises(ledger.LedgerSecretError) as err:
        ledger._key()
    assert "not-a-secret-value" not in str(err.value)
    assert path.read_text(encoding="ascii") == "not-a-secret-value"                # never overwritten
