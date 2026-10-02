"""Runs the 6 test deals from data/test_deals.json through the MeTTa engine."""
import json
from pathlib import Path

import pytest

from data.seed import PRODUCTS, SELLERS, _prior_deals_for_repeat_seller
from engine.bridge import evaluate_deal

ROOT = Path(__file__).resolve().parent.parent
TEST_DEALS = {d["case"]: d for d in json.loads((ROOT / "data" / "test_deals.json").read_text())}


def build_deal(case: int) -> dict:
    """Join a test deal with its seed seller/product records (what the backend will do)."""
    deal = TEST_DEALS[case]
    seller = next(s for s in SELLERS if s["id"] == deal["seller_id"])
    product = next(p for p in PRODUCTS if p["id"] == deal["product_id"])
    prior = sum(1 for d in _prior_deals_for_repeat_seller() if d.seller_id == seller["id"])
    return {
        "record_tier": seller["tier"],
        "claimed_tier": deal["claimed_tier"],
        "cost_price": product["cost_price"],
        "list_price": product["list_price"],
        "quantity": deal["quantity"],
        "discount_requested": deal["discount_requested"],
        "late_payments": seller["late_payments"],
        "total_orders": seller["total_orders"],
        "competitor_price": deal["competitor_price"],
        "competitor_verified": deal["competitor_verified"],
        "requests_this_month": prior,
        "seller_name": seller["name"],
    }


def entry(result: dict, rule_id: str) -> dict:
    return next(e for e in result["trail"] if e["rule_id"] == rule_id)


@pytest.fixture(scope="module")
def results() -> dict:
    return {case: evaluate_deal(build_deal(case)) for case in TEST_DEALS}


def test_every_trail_has_all_rules(results):
    expected = ["CONFLICT", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "TRUST", "ALLOWED", "DECISION"]
    for r in results.values():
        assert [e["rule_id"] for e in r["trail"]] == expected


def test_deal1_approve(results):
    r = results[1]
    assert r["result"] == "APPROVE"
    assert r["approved_discount"] == 10.0
    assert r["confidence"] == 1.0
    assert r["override_hint"] is None


def test_deal2_reject_below_cost(results):
    r = results[2]
    assert r["result"] == "REJECT"
    assert r["approved_discount"] is None
    assert entry(r, "R1")["status"] == "fail"
    assert "step 1" in entry(r, "DECISION")["check"]
    # D3 (deliberate change): Nova Startups has only 2 orders -> trust confidence 0.17,
    # so decision confidence drops from 1.0 to 0.83.
    assert r["confidence"] == pytest.approx(0.83)
    assert entry(r, "TRUST")["status"] == "warn"


def test_deal3_counter_12_with_tier_conflict(results):
    r = results[3]
    assert r["result"] == "COUNTER"
    assert r["approved_discount"] == 12.0
    assert entry(r, "CONFLICT")["status"] == "warn"
    assert r["confidence"] < 1.0
    assert "step 4" in entry(r, "DECISION")["check"]
    # Exact text, identical on hyperon and PeTTa/Omega: zeros are computed floats (zero-float)
    assert entry(r, "ALLOWED")["check"] == \
        "min(max(cap 10.0 + R3 2.0 + R4 0.0 = 12.0, competitor 0.0), margin limit 17.6) = 12.0"
    r4 = entry(r, "R4")["value"]
    assert isinstance(r4, float) and r4 == 0.0
    hint = r["override_hint"]
    assert hint["action"] == "verify-tier"
    assert hint["claimed_tier"] == "Gold"
    assert hint["allowed_max"] == pytest.approx(17.6, abs=0.05)


def test_deal4_missing_history(results):
    r = results[4]
    assert entry(r, "R3")["status"] == "skip"
    assert r["confidence"] < 1.0
    assert r["result"] == "APPROVE"


def test_deal5_unverified_competitor_skipped(results):
    r = results[5]
    assert entry(r, "R5")["status"] == "skip"
    assert "not verified" in entry(r, "R5")["check"]
    assert r["confidence"] < 1.0


def test_deal6_repeat_request_flag(results):
    r = results[6]
    assert entry(r, "R7")["status"] == "warn"
    assert r["confidence"] < 1.0
