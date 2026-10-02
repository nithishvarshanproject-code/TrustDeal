"""API tests against a temporary, freshly seeded SQLite DB (never data/dealdesk.db)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import create_app
from backend.database import get_db
from backend.services.conflicts import count_requests_this_month
from data.seed import seed

ROOT = Path(__file__).resolve().parent.parent
TEST_DEALS = {d["case"]: d for d in json.loads((ROOT / "data" / "test_deals.json").read_text())}
DEAL_FIELDS = ("seller_id", "product_id", "quantity", "discount_requested",
               "claimed_tier", "competitor_price", "competitor_verified")


@pytest.fixture(scope="module")
def session_factory(tmp_path_factory):
    engine = create_engine(f"sqlite:///{tmp_path_factory.mktemp('db') / 'test.db'}")
    seed(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture(scope="module")
def client(session_factory):
    app = create_app()

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


@pytest.fixture(scope="module")
def evaluated(client, session_factory):
    """POST all 6 test deals once. Also records seller 6's DB count before posting."""
    with session_factory() as db:
        before = {"seller6": count_requests_this_month(db, 6), "seller1": count_requests_this_month(db, 1)}
    responses = {}
    for case, deal in TEST_DEALS.items():
        resp = client.post("/deals/evaluate", json={k: deal[k] for k in DEAL_FIELDS})
        assert resp.status_code == 200, resp.text
        responses[case] = resp.json()
    return {"before": before, "responses": responses}


def entry(body: dict, rule_id: str) -> dict:
    return next(e for e in body["trail"] if e["rule_id"] == rule_id)


# ---------- POST /deals/evaluate ----------

def test_deal1_approve(evaluated):
    body = evaluated["responses"][1]
    assert (body["result"], body["approved_discount"], body["confidence"]) == ("APPROVE", 10.0, 1.0)


def test_deal2_reject(evaluated):
    body = evaluated["responses"][2]
    assert body["result"] == "REJECT"
    assert body["approved_discount"] is None
    assert body["confidence"] == pytest.approx(0.83)  # D3 trust penalty (was 1.0)


def test_deal3_counter_with_override_hint(evaluated):
    body = evaluated["responses"][3]
    assert (body["result"], body["approved_discount"]) == ("COUNTER", 12.0)
    assert body["override_hint"]["claimed_tier"] == "Gold"
    assert body["override_hint"]["allowed_max"] == pytest.approx(17.6, abs=0.05)
    assert "Gold" in body["explanation"]["override"]


def test_deal4_missing_history(evaluated):
    body = evaluated["responses"][4]
    assert entry(body, "R3")["status"] == "skip"
    assert body["confidence"] < 1.0


def test_deal5_unverified_competitor(evaluated):
    body = evaluated["responses"][5]
    assert entry(body, "R5")["status"] == "skip"
    assert body["confidence"] < 1.0


def test_deal6_r7_count_comes_from_db(evaluated):
    assert evaluated["before"] == {"seller6": 3, "seller1": 0}
    body = evaluated["responses"][6]
    r7 = entry(body, "R7")
    assert r7["status"] == "warn"
    assert r7["value"] == 3
    assert entry(evaluated["responses"][1], "R7")["value"] == 0


def test_explanation_matches_trail_one_to_one(evaluated):
    for body in evaluated["responses"].values():
        lines = body["explanation"]["lines"]
        assert len(lines) == len(body["trail"])
        for n, (line, e) in enumerate(zip(lines, body["trail"]), start=1):
            assert line.startswith(f"{n}. [{e['rule_id']}] ")
            assert line.endswith(e["check"])


def test_raw_text_not_available(client):
    resp = client.post("/deals/evaluate", json={"raw_text": "Gold seller wants 10% off"})
    assert resp.status_code == 501
    assert resp.json()["detail"] == "free-text input not available yet"


def test_missing_fields_and_unknown_seller(client):
    assert client.post("/deals/evaluate", json={"seller_id": 1}).status_code == 422
    resp = client.post("/deals/evaluate", json={"seller_id": 999, "product_id": 1,
                                                "quantity": 1, "discount_requested": 5})
    assert resp.status_code == 404


# ---------- GET /deals/{id} ----------

def test_get_deal_returns_saved_decision(client, evaluated):
    posted = evaluated["responses"][3]
    resp = client.get(f"/deals/{posted['deal_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["seller"]["tier"] == "Silver"
    assert body["claimed_tier"] == "Gold"
    assert body["decision"]["result"] == "COUNTER"
    assert body["decision"]["trail"] == posted["trail"]
    assert body["decision"]["override_hint"] == posted["override_hint"]
    assert body["overrides"] == []


def test_get_unknown_deal(client):
    assert client.get("/deals/99999").status_code == 404


# ---------- GET /deals/history ----------

def test_history_lists_newest_first(client, evaluated):
    resp = client.get("/deals/history")
    assert resp.status_code == 200
    rows = resp.json()
    posted_ids = [evaluated["responses"][c]["deal_id"] for c in sorted(TEST_DEALS)]
    by_id = {r["deal_id"]: r for r in rows}
    assert all(i in by_id for i in posted_ids)
    assert by_id[posted_ids[2]]["result"] == "COUNTER"
    assert by_id[posted_ids[2]]["seller_name"] == "Meridian Supply"
    # the last deal posted is the newest
    assert rows[0]["deal_id"] == posted_ids[-1]


def test_month_count_ignores_other_months(session_factory):
    from datetime import datetime, timezone
    with session_factory() as db:
        assert count_requests_this_month(db, 6, now=datetime(2099, 1, 15, tzinfo=timezone.utc)) == 0


# ---------- POST /deals/{id}/override ----------

def test_override_is_recorded_and_original_decision_unchanged(client, evaluated, session_factory):
    from backend.models import Decision

    posted = evaluated["responses"][3]
    with session_factory() as db:
        before = db.get(Decision, posted["decision_id"])
        snapshot = (before.result, before.approved_discount, before.confidence, before.audit_json)

    resp = client.post(f"/deals/{posted['deal_id']}/override", json={
        "reviewer": "Priya (sales lead)",
        "new_result": "APPROVE",
        "reason": "Gold contract signed yesterday; record not updated yet.",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["original_result"], body["new_result"]) == ("COUNTER", "APPROVE")

    with session_factory() as db:
        after = db.get(Decision, posted["decision_id"])
        assert (after.result, after.approved_discount, after.confidence, after.audit_json) == snapshot

    deal = client.get(f"/deals/{posted['deal_id']}").json()
    assert deal["decision"]["result"] == "COUNTER"
    assert [o["new_result"] for o in deal["overrides"]] == ["APPROVE"]


def test_override_validation(client, evaluated):
    deal_id = evaluated["responses"][1]["deal_id"]
    bad_result = {"reviewer": "x", "new_result": "MAYBE", "reason": "y"}
    blank_reason = {"reviewer": "x", "new_result": "REJECT", "reason": "   "}
    assert client.post(f"/deals/{deal_id}/override", json=bad_result).status_code == 422
    assert client.post(f"/deals/{deal_id}/override", json=blank_reason).status_code == 422
    ok = {"reviewer": "x", "new_result": "REJECT", "reason": "y"}
    assert client.post("/deals/99999/override", json=ok).status_code == 404
