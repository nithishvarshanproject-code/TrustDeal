"""D1-D3 differentiators. All expected behaviour comes from the MeTTa engine."""
from pathlib import Path

import pytest

from backend.services.policy import get_policy_proposals
from engine import bridge
from engine.bridge import evaluate_deal, seller_trust, update_trust
from engine.policy_admin import PolicyChangeError, apply_policy_proposal
from tests.test_engine import build_deal


def entry(result: dict, rule_id: str) -> dict:
    return next(e for e in result["trail"] if e["rule_id"] == rule_id)


# ---------- D1: what would it take ----------

def _apply_option(deal: dict, option: dict) -> dict:
    """Build the changed deal an option describes, at min(request, offered max)."""
    changed = dict(deal)
    if option["change"] == "verify-tier":
        changed["record_tier"] = option["detail"]
    elif option["change"] == "verify-competitor":
        changed["competitor_verified"] = True
    elif option["change"] == "raise-quantity":
        changed["quantity"] = option["detail"]
    changed["discount_requested"] = min(deal["discount_requested"], option["allowed_max"])
    return changed


def test_d1_deal3_what_if_options():
    r = evaluate_deal(build_deal(3))
    options = {o["change"]: o for o in r["what_if"]}
    assert options["verify-tier"]["detail"] == "Gold"
    assert options["verify-tier"]["allowed_max"] == pytest.approx(17.6)
    assert options["raise-quantity"]["allowed_max"] == pytest.approx(17.0)
    assert options["lower-discount"]["allowed_max"] == pytest.approx(12.0)
    assert len(r["what_if"]) <= 3


@pytest.mark.parametrize("case", [2, 3, 5])
def test_d1_every_option_really_approves_and_keeps_margin(case):
    deal = build_deal(case)
    r = evaluate_deal(deal)
    assert r["result"] != "APPROVE" and r["what_if"]
    for option in r["what_if"]:
        rerun = evaluate_deal(_apply_option(deal, option))
        assert rerun["result"] == "APPROVE", option
        assert entry(rerun, "R1")["status"] == "pass", option


def test_d1_approve_has_no_what_if():
    assert evaluate_deal(build_deal(1))["what_if"] == []


def test_d1_unverified_quote_suggests_verification():
    r = evaluate_deal(build_deal(5))
    assert {"change": "verify-competitor", "detail": 84.0, "allowed_max": 16.0} in r["what_if"]


def test_d1_escalation_suggests_lower_discount_within_margin():
    deal = dict(build_deal(1), quantity=60, discount_requested=28.0)
    r = evaluate_deal(deal)
    assert r["result"] == "ESCALATE"
    assert r["what_if"] == [{"change": "lower-discount", "detail": 17.6, "allowed_max": 17.6}]


# ---------- D2: learning from overrides ----------

REAL_ENGINE = Path(bridge.ENGINE_DIR)
# Meridian Supply (Silver, clean history), 60 units, 14%: allowed 10 + 2 = 12 -> COUNTER 12
SILVER_DEAL = {"seller_id": 3, "product_id": 1, "quantity": 60, "discount_requested": 14.0,
               "claimed_tier": "Silver"}


def _counter_then_override(client, n: int) -> list[int]:
    ids = []
    for i in range(n):
        body = client.post("/deals/evaluate", json=SILVER_DEAL).json()
        assert (body["result"], body["approved_discount"]) == ("COUNTER", 12.0)
        resp = client.post(f"/deals/{body['deal_id']}/override", json={
            "reviewer": "Sales lead", "new_result": "APPROVE",
            "reason": f'Long-term "Silver" partner #{i}',   # quotes must survive MeTTa
        })
        ids.append(resp.json()["override_id"])
    return ids


def test_d2_three_overrides_give_one_proposal(stack):
    ids = _counter_then_override(stack["client"], 3)
    with stack["Session"]() as db:
        proposals = get_policy_proposals(db)
    assert len(proposals) == 1
    p = proposals[0]
    assert (p["fact"], p["key"], p["old_value"], p["new_value"]) == ("tier-cap", "Silver", 10.0, 12.0)
    assert [e["override_id"] for e in p["evidence"]] == ids
    assert p["evidence"][0]["reason"] == 'Long-term "Silver" partner #0'


def test_d2_two_overrides_give_no_proposal(stack):
    _counter_then_override(stack["client"], 2)
    with stack["Session"]() as db:
        assert get_policy_proposals(db) == []


def test_d2_apply_updates_policy_keeps_copy_and_changes_result(stack):
    real_before = (REAL_ENGINE / "policy.metta").read_text(encoding="utf-8")
    _counter_then_override(stack["client"], 3)
    with stack["Session"]() as db:
        [proposal] = get_policy_proposals(db)

    record = apply_policy_proposal(proposal, approved_by="Head of Sales",
                                   engine_dir=stack["engine_dir"])

    policy = (stack["engine_dir"] / "policy.metta").read_text(encoding="utf-8")
    assert "(tier-cap Silver 12.0)" in policy and "(tier-cap Silver 10.0)" not in policy
    assert "(margin-floor 15.0)" in policy
    history = stack["engine_dir"] / "policy_history"
    assert "(tier-cap Silver 10.0)" in (history / record["backup"]).read_text(encoding="utf-8")
    assert "Silver) 10.0 -> 12.0" in (history / "changes.log").read_text(encoding="utf-8")

    body = stack["client"].post("/deals/evaluate", json=SILVER_DEAL).json()
    assert (body["result"], body["approved_discount"]) == ("APPROVE", 14.0)
    # the real engine/policy.metta was never touched
    assert (REAL_ENGINE / "policy.metta").read_text(encoding="utf-8") == real_before


def test_d2_guards(stack):
    margin = {"fact": "margin-floor", "key": "x", "old_value": 15.0, "new_value": 10.0}
    with pytest.raises(PolicyChangeError):
        apply_policy_proposal(margin, approved_by="someone", engine_dir=stack["engine_dir"])
    stale = {"fact": "tier-cap", "key": "Silver", "old_value": 9.0, "new_value": 12.0}
    with pytest.raises(PolicyChangeError):
        apply_policy_proposal(stale, approved_by="someone", engine_dir=stack["engine_dir"])
    ok = {"fact": "tier-cap", "key": "Silver", "old_value": 10.0, "new_value": 12.0}
    with pytest.raises(PolicyChangeError):
        apply_policy_proposal(ok, approved_by="  ", engine_dir=stack["engine_dir"])


# ---------- D3: evidence-based seller trust ----------


def test_d3_trust_from_history():
    aurora = seller_trust(0, 40)                      # 40 orders, 0 late
    assert aurora == {"strength": 1.0, "confidence": pytest.approx(0.8)}
    blank = seller_trust(None, None)                  # Blank Slate: no history
    assert blank == {"strength": 0.5, "confidence": 0.0}


def test_d3_trust_line_in_decisions():
    aurora = entry(evaluate_deal(build_deal(1)), "TRUST")
    assert aurora["status"] == "pass" and "Aurora Traders" in aurora["check"]
    assert aurora["value"] == ["stv", 1.0, pytest.approx(0.8)]
    blank = evaluate_deal(build_deal(4))
    assert entry(blank, "TRUST")["status"] == "warn"
    assert blank["confidence"] == pytest.approx(0.8)   # same as the old fixed penalty


def test_d3_revision_after_on_time_deal():
    echo = seller_trust(1, 15)                        # Echo Retail: (stv 0.933 0.6)
    revised = update_trust(echo, "on-time")
    assert revised["strength"] > echo["strength"]
    assert revised["confidence"] > echo["confidence"]
    assert revised == {"strength": pytest.approx(15 / 16), "confidence": pytest.approx(16 / 26)}

    blank = update_trust(seller_trust(None, None), "on-time")
    assert blank["strength"] > 0.5 and blank["confidence"] > 0.0

    late = update_trust(echo, "late")
    assert late["strength"] < echo["strength"] and late["confidence"] > echo["confidence"]
