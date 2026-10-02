"""API tests for on-demand what-if, D2 policy routes and D3 outcome/trust routes.
Every test uses a temp DB and a temp copy of engine/ (see conftest.stack)."""
import json

import pytest

from backend.models import Decision
from engine.bridge import ENGINE_DIR

# Meridian Supply (Silver, clean history), 60 units, 14%: 10 + 2 = 12 -> COUNTER 12
SILVER_DEAL = {"seller_id": 3, "product_id": 1, "quantity": 60, "discount_requested": 14.0,
               "claimed_tier": "Silver"}
DEAL_1 = {"seller_id": 1, "product_id": 1, "quantity": 150, "discount_requested": 10.0,
          "claimed_tier": "Gold"}
DEAL_2 = {"seller_id": 2, "product_id": 1, "quantity": 10, "discount_requested": 35.0,
          "claimed_tier": "New"}
DEAL_3 = {"seller_id": 3, "product_id": 1, "quantity": 60, "discount_requested": 20.0,
          "claimed_tier": "Gold"}
DEAL_4 = {"seller_id": 4, "product_id": 1, "quantity": 30, "discount_requested": 8.0,
          "claimed_tier": "Silver"}


def post(client, deal: dict) -> dict:
    resp = client.post("/deals/evaluate", json=deal)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- what-if on demand ----------

def test_evaluate_skips_what_if_then_computes_and_stores_it(stack):
    client = stack["client"]
    body = post(client, DEAL_3)
    assert body["what_if"] is None and body["explanation"]["what_if"] == []

    first = client.get(f"/deals/{body['deal_id']}/what-if").json()
    changes = {o["change"]: o["allowed_max"] for o in first["what_if"]}
    assert changes == {"verify-tier": 17.6, "raise-quantity": 17.0, "lower-discount": 12.0}
    assert len(first["explanation"]) == 3 and first["computed_at"]

    with stack["Session"]() as db:
        stored = db.get(Decision, body["decision_id"])
        audit = json.loads(stored.audit_json)
        assert audit["what_if"] == first["what_if"]
        # the decision itself never changes
        assert (stored.result, stored.approved_discount, stored.confidence) == ("COUNTER", 12.0, 0.9)

    second = client.get(f"/deals/{body['deal_id']}/what-if").json()
    assert second == first                                   # served from storage
    assert client.get(f"/deals/{body['deal_id']}").json()["decision"]["what_if"] == first["what_if"]


def test_what_if_empty_for_approve_and_404(stack):
    client = stack["client"]
    body = post(client, DEAL_1)
    assert client.get(f"/deals/{body['deal_id']}/what-if").json()["what_if"] == []
    assert client.get("/deals/99999/what-if").status_code == 404


# ---------- D2 policy routes ----------

def _three_silver_overrides(client) -> list[int]:
    ids = []
    for i in range(3):
        body = post(client, SILVER_DEAL)
        assert (body["result"], body["approved_discount"]) == ("COUNTER", 12.0)
        resp = client.post(f"/deals/{body['deal_id']}/override", json={
            "reviewer": "Sales lead", "new_result": "APPROVE", "reason": f"key account {i}"})
        ids.append(resp.json()["override_id"])
    return ids


def test_policy_proposal_apply_history_current(stack):
    client = stack["client"]
    real_before = (ENGINE_DIR / "policy.metta").read_text(encoding="utf-8")
    ids = _three_silver_overrides(client)

    [proposal] = client.get("/policy/proposals").json()
    assert (proposal["fact"], proposal["key"], proposal["old_value"], proposal["new_value"]) == \
        ("tier-cap", "Silver", 10.0, 12.0)
    assert [e["override_id"] for e in proposal["evidence"]] == ids

    resp = client.post("/policy/proposals/apply", json={
        "fact": "tier-cap", "key": "Silver", "old_value": 10.0, "new_value": 12.0,
        "approved_by": "Head of Sales"})
    assert resp.status_code == 200, resp.text
    applied = resp.json()
    assert (applied["old_value"], applied["new_value"]) == (10.0, 12.0)
    assert (stack["engine_dir"] / "policy_history" / applied["history_file"]).exists()

    [entry] = client.get("/policy/history").json()
    assert (entry["key"], entry["old_value"], entry["new_value"]) == ("Silver", 10.0, 12.0)
    assert entry["approved_by"] == "Head of Sales" and entry["evidence"] == ids
    assert entry["history_file"] == applied["history_file"]

    current = client.get("/policy/current").json()
    facts = [{"fact": f["fact"], "args": f["args"]} for f in current]
    assert {"fact": "tier-cap", "args": ["Silver", 12.0]} in facts
    assert {"fact": "margin-floor", "args": [15.0]} in facts
    texts = [f["text"] for f in current]
    assert "(tier-cap Silver 12.0)" in texts and "(volume-min 100)" in texts   # verbatim lines

    assert client.get("/policy/proposals").json() == []       # the gap is closed now
    body = post(client, SILVER_DEAL)
    assert (body["result"], body["approved_discount"]) == ("APPROVE", 14.0)
    assert (ENGINE_DIR / "policy.metta").read_text(encoding="utf-8") == real_before


def test_policy_apply_rejects_tampered_or_unapproved(stack):
    client = stack["client"]
    _three_silver_overrides(client)
    tampered = {"fact": "tier-cap", "key": "Silver", "old_value": 10.0, "new_value": 25.0,
                "approved_by": "Mallory"}
    assert client.post("/policy/proposals/apply", json=tampered).status_code == 409
    margin = {"fact": "margin-floor", "key": "x", "old_value": 15.0, "new_value": 5.0,
              "approved_by": "Mallory"}
    assert client.post("/policy/proposals/apply", json=margin).status_code == 409
    blank = {"fact": "tier-cap", "key": "Silver", "old_value": 10.0, "new_value": 12.0,
             "approved_by": "   "}
    assert client.post("/policy/proposals/apply", json=blank).status_code == 422
    missing = {"fact": "tier-cap", "key": "Silver", "old_value": 10.0, "new_value": 12.0}
    assert client.post("/policy/proposals/apply", json=missing).status_code == 422
    assert "(tier-cap Silver 10.0)" in (stack["engine_dir"] / "policy.metta").read_text()
    assert client.get("/policy/history").json() == []


# ---------- D3 outcomes and trust ----------

def test_outcome_updates_history_and_trust(stack):
    client = stack["client"]
    before = client.get("/sellers/1/trust").json()
    assert before["total_orders"] == 40 and before["trust"]["confidence"] == pytest.approx(0.8)

    body = post(client, DEAL_1)
    resp = client.post(f"/deals/{body['deal_id']}/outcome", json={"paid_on_time": True})
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert (out["total_orders"], out["late_payments"]) == (41, 0)
    assert out["trust_before"]["confidence"] == pytest.approx(40 / 50)
    assert out["trust_after"]["confidence"] == pytest.approx(41 / 51)
    assert out["trust_after"]["confidence"] > out["trust_before"]["confidence"]

    after = client.get("/sellers/1/trust").json()
    assert after["total_orders"] == 41
    assert after["trust"]["confidence"] == pytest.approx(out["trust_after"]["confidence"])

    again = client.post(f"/deals/{body['deal_id']}/outcome", json={"paid_on_time": True})
    assert again.status_code == 409


def test_outcome_for_seller_without_history(stack):
    client = stack["client"]
    assert client.get("/sellers/4/trust").json()["trust"] == {"strength": 0.5, "confidence": 0.0}
    body = post(client, DEAL_4)
    out = client.post(f"/deals/{body['deal_id']}/outcome", json={"paid_on_time": False}).json()
    assert out["trust_before"] == {"strength": 0.5, "confidence": 0.0}
    assert out["trust_after"] == {"strength": 0.0, "confidence": pytest.approx(1 / 11)}
    assert (out["total_orders"], out["late_payments"]) == (1, 1)
    now = client.get("/sellers/4/trust").json()["trust"]
    assert now == {"strength": 0.0, "confidence": pytest.approx(1 / 11)}


def test_outcome_only_for_sales(stack):
    client = stack["client"]
    rejected = post(client, DEAL_2)
    assert client.post(f"/deals/{rejected['deal_id']}/outcome",
                       json={"paid_on_time": True}).status_code == 409
    # COUNTER overridden to REJECT: no sale either
    countered = post(client, DEAL_3)
    client.post(f"/deals/{countered['deal_id']}/override", json={
        "reviewer": "Ops", "new_result": "REJECT", "reason": "customer walked away"})
    assert client.post(f"/deals/{countered['deal_id']}/outcome",
                       json={"paid_on_time": True}).status_code == 409
    assert client.post("/deals/99999/outcome", json={"paid_on_time": True}).status_code == 404
    assert client.get("/sellers/999/trust").status_code == 404


# ---------- demo reset ----------

def test_demo_reset_restores_policy_history_and_data(stack):
    client = stack["client"]
    original = (stack["engine_dir"] / "policy.original.metta").read_text(encoding="utf-8")
    _three_silver_overrides(client)
    assert client.post("/policy/proposals/apply", json={
        "fact": "tier-cap", "key": "Silver", "old_value": 10.0, "new_value": 12.0,
        "approved_by": "Head of Sales"}).status_code == 200
    assert (stack["engine_dir"] / "policy.metta").read_text(encoding="utf-8") != original

    assert client.post("/demo/reset", json={}).status_code == 422          # confirmation required
    resp = client.post("/demo/reset", json={"confirm": "RESET"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["policy_restored"] is True and body["archived_history"].startswith("policy_history_archive/")

    assert (stack["engine_dir"] / "policy.metta").read_text(encoding="utf-8") == original
    assert not (stack["engine_dir"] / "policy_history").exists()
    archive = stack["engine_dir"] / body["archived_history"]
    assert (archive / "changes.log").exists()                             # archived, not deleted
    assert client.get("/policy/history").json() == []
    assert len(client.get("/deals/history").json()) == 3                  # only the 3 seeded deals
    after = post(client, SILVER_DEAL)                                      # rules back to Silver 10
    assert (after["result"], after["approved_discount"]) == ("COUNTER", 12.0)
