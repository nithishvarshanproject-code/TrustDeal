"""Seller overview metrics from temporary SQLite data."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.database import Base, get_db
from backend.models import (AgentDeal, AgentDecision, AgentQuote, AgentTask, Deal, Decision, Override)
from data.seed import seed


def _decision(db, *, seller_id, quantity, asked, result, approved, confidence, trail=None):
    deal = Deal(seller_id=seller_id, product_id=1, quantity=quantity, discount_requested=asked)
    db.add(deal)
    db.flush()
    decision = Decision(deal_id=deal.id, result=result, approved_discount=approved, confidence=confidence,
                        audit_json=json.dumps({"trail": trail or []}))
    db.add(decision)
    db.flush()
    return deal, decision


@pytest.fixture
def empty_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty-overview.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = create_app()

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    engine.dispose()


def test_overview_uses_latest_records_and_hand_calculated_values(stack):
    db = stack["Session"]()
    try:
        _decision(db, seller_id=1, quantity=10, asked=10, result="APPROVE", approved=10, confidence=0.9)
        _decision(db, seller_id=1, quantity=10, asked=20, result="COUNTER", approved=5, confidence=0.8)
        _decision(db, seller_id=1, quantity=3, asked=30, result="ESCALATE", approved=None, confidence=0.7)
        _, unpriced_decision = _decision(db, seller_id=1, quantity=4, asked=15, result="APPROVE",
                                         approved=15, confidence=0.6)
        db.add(Override(decision_id=unpriced_decision.id, reviewer="Seller", new_result="COUNTER",
                        reason="Counter without an amount"))
        _decision(db, seller_id=2, quantity=1, asked=40, result="REJECT", approved=None, confidence=0.5,
                  trail=[{"rule_id": "R1", "status": "fail"}])

        customer_counter = AgentDeal(customer_id=1, product_id=1, quantity=2, discount_asked=10,
                                     channel="telegram")
        customer_pending = AgentDeal(customer_id=2, product_id=1, quantity=2, discount_asked=25, channel="web")
        db.add_all([customer_counter, customer_pending])
        db.flush()
        db.add_all([
            AgentDecision(agent_deal_id=customer_counter.id, round=1, event="new-request", result="APPROVE",
                          approved_discount=10, confidence=0.2, audit_json=json.dumps({"input": {"discount_requested": 10}})),
            AgentDecision(agent_deal_id=customer_counter.id, round=2, event="customer-ask", result="COUNTER",
                          approved_discount=4, confidence=0.8, audit_json=json.dumps({"input": {"discount_requested": 10}})),
            AgentDecision(agent_deal_id=customer_pending.id, round=1, event="new-request", result="ESCALATE",
                          approved_discount=None, confidence=0.4, audit_json=json.dumps({"input": {"discount_requested": 25}})),
        ])
        expiry = datetime.now(timezone.utc) + timedelta(hours=48)
        db.add_all([
            AgentQuote(agent_deal_id=customer_counter.id, discount=4, unit_price=96, total=192, savings=8,
                       quantity=2, valid_until=expiry, status="ordered", order_ref="ORD-1"),
            AgentQuote(agent_deal_id=customer_counter.id, discount=6, unit_price=94, total=188, savings=12,
                       quantity=2, valid_until=expiry, status="open"),
        ])
        db.add_all([
            AgentTask(agent_deal_id=customer_pending.id, kind="escalation", status="open", title="Review"),
            AgentTask(agent_deal_id=customer_counter.id, kind="verification", status="done", title="Verify",
                      answer="verified"),
            AgentTask(agent_deal_id=customer_pending.id, kind="verification", status="open", title="Verify"),
        ])
        db.commit()
    finally:
        db.close()

    body = stack["client"].get("/stats/overview").json()
    assert body["deals_handled"] == {"total": 7, "by_channel": {"deal_check": 5, "web_chat": 1, "telegram": 1}}
    assert body["results"] == {"APPROVE": 1, "COUNTER": 3, "REJECT": 1, "ESCALATE": 2}
    assert body["discounts"] == {
        "requested": 360.0, "offered": 158.0, "avoided": 202.0, "decided_deals": 4,
        "discount_actually_given_on_orders": 8.0, "pending_escalations_excluded": 2,
        "counters_without_amount_excluded": 1, "overridden_counters_without_amount_excluded": 1,
    }
    assert body["below_cost_blocked"] == 1
    assert body["escalations"] == {"total": 2, "resolved": 0, "pending": 2}
    assert body["verifications"] == {"total": 2, "resolved": 1, "pending": 1}
    assert body["quotes"] == {"total": 2, "ordered": 1, "conversion_rate": 0.5}
    assert body["average_confidence"] == pytest.approx(4.7 / 7)
    assert [row["name"] for row in body["top_customers_by_trust"]] == [
        "Priya Sharma", "Arjun Mehta", "Neha Kapoor"]


def test_overview_empty_database_returns_zeros(empty_client):
    body = empty_client.get("/stats/overview").json()
    assert body["deals_handled"] == {"total": 0, "by_channel": {"deal_check": 0, "web_chat": 0, "telegram": 0}}
    assert body["results"] == {"APPROVE": 0, "COUNTER": 0, "REJECT": 0, "ESCALATE": 0}
    assert body["discounts"]["requested"] == body["discounts"]["offered"] == body["discounts"]["avoided"] == 0
    assert body["discounts"]["pending_escalations_excluded"] == 0
    assert body["quotes"] == {"total": 0, "ordered": 0, "conversion_rate": 0.0}
    assert body["average_confidence"] is None
    assert body["top_customers_by_trust"] == []


def test_r4_unearned_bonus_is_fail_in_api_response(stack):
    resp = stack["client"].post("/deals/evaluate", json={"seller_id": 1, "product_id": 1,
                                                         "quantity": 1, "discount_requested": 5})
    assert resp.status_code == 200
    trail = resp.json()["trail"]
    assert next(row for row in trail if row["rule_id"] == "R4")["status"] == "fail"


def test_customer_endpoints_do_not_expose_seller_overview(stack):
    client = stack["client"]
    customers = client.get("/customer/customers")
    assert customers.status_code == 200
    assert client.get("/customer/stats/overview").status_code == 404
    assert all("deals_handled" not in row and "discount_actually_given_on_orders" not in row
               for row in customers.json())
