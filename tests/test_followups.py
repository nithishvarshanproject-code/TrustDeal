"""Persisted, idempotent quote reminders and India-time seller summaries."""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.agent.followups import (daily_metrics, run_followups, send_daily_summary,
                                     send_scheduled_summary_if_due)
from backend.models import (AgentActivity, AgentDeal, AgentDecision, AgentMessage, AgentQuote, AgentTask,
                            Customer, Deal, Decision, Product, Seller)

UTC = timezone.utc


def _make_quote(Session, valid_until, *, state="QUOTED"):
    with Session() as db:
        customer = db.scalars(select(Customer).order_by(Customer.id)).first()
        product = db.scalars(select(Product).order_by(Product.id)).first()
        deal = AgentDeal(customer_id=customer.id, product_id=product.id, quantity=1, discount_asked=10,
                         state=state, channel="web")
        db.add(deal)
        db.flush()
        quote = AgentQuote(agent_deal_id=deal.id, discount=10, unit_price=product.list_price * .9,
                           total=product.list_price * .9, savings=product.list_price * .1, quantity=1,
                           valid_until=valid_until, status="open")
        db.add(quote)
        db.commit()
        return deal.id, quote.id


def test_quote_reminder_uses_configured_window_and_is_idempotent(stack, monkeypatch):
    monkeypatch.setenv("DEALDESK_REMINDER_HOURS", "6")
    now = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)
    expiry = now + timedelta(hours=5)
    deal_id, quote_id = _make_quote(stack["Session"], expiry)

    with stack["Session"]() as db:
        first = run_followups(db, now)
        assert first["actions"] == [{"action": "reminder", "request_id": deal_id,
                                      "quote_ref": f"Q-{quote_id:05d}", "channel": "web"}]
    with stack["Session"]() as db:
        second = run_followups(db, now)
        assert second["actions"] == []
        quote = db.get(AgentQuote, quote_id)
        assert quote.status == "open"
        assert quote.valid_until == expiry.replace(tzinfo=None)
        messages = db.scalars(select(AgentMessage).where(AgentMessage.agent_deal_id == deal_id)).all()
        markers = db.scalars(select(AgentActivity).where(AgentActivity.kind == "followup_delivery")).all()
        assert len(messages) == len(markers) == 1
        assert "03 Oct 2026, 10:30 IST" in messages[0].text


def test_reminder_window_can_be_reconfigured(stack, monkeypatch):
    monkeypatch.setenv("DEALDESK_REMINDER_HOURS", "3")
    now = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)
    _make_quote(stack["Session"], now + timedelta(hours=4))
    with stack["Session"]() as db:
        report = run_followups(db, now)
    assert report["reminder_hours"] == 3
    assert report["actions"] == []


def test_expired_quote_is_closed_and_notified_once(stack):
    now = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)
    deal_id, quote_id = _make_quote(stack["Session"], now - timedelta(seconds=1))
    with stack["Session"]() as db:
        assert len(run_followups(db, now)["actions"]) == 1
    with stack["Session"]() as db:
        assert run_followups(db, now)["actions"] == []
        assert db.get(AgentDeal, deal_id).state == "CLOSED"
        assert db.get(AgentQuote, quote_id).status == "expired"
        messages = db.scalars(select(AgentMessage).where(AgentMessage.agent_deal_id == deal_id)).all()
        assert len(messages) == 1
        assert "expired at 03 Oct 2026, 05:29 IST" in messages[0].text


def test_daily_summary_uses_ist_boundaries_and_sends_once(stack, monkeypatch):
    monkeypatch.setenv("DEALDESK_DAILY_SUMMARY_TIME", "21:00")
    local_midnight = datetime(2026, 10, 2, 18, 30, tzinfo=UTC)  # 03 Oct, 00:00 IST
    with stack["Session"]() as db:
        seller = db.scalars(select(Seller).order_by(Seller.id)).first()
        product = db.scalars(select(Product).order_by(Product.id)).first()
        deal = Deal(seller_id=seller.id, product_id=product.id, quantity=2, discount_requested=10,
                    created_at=local_midnight + timedelta(minutes=1))
        db.add(deal)
        db.flush()
        db.add(Decision(deal_id=deal.id, result="REJECT", created_at=local_midnight + timedelta(minutes=2)))
        customer = db.scalars(select(Customer).order_by(Customer.id)).first()
        agent_deal = AgentDeal(customer_id=customer.id, product_id=product.id, quantity=1, discount_asked=5,
                               state="ESCALATED", created_at=local_midnight + timedelta(minutes=1))
        db.add(agent_deal)
        db.flush()
        db.add(AgentDecision(agent_deal_id=agent_deal.id, round=1, event="new-request", result="ESCALATE",
                             audit_json=json.dumps({"input": {"discount_requested": 5}}),
                             created_at=local_midnight + timedelta(minutes=2)))
        db.add(AgentTask(agent_deal_id=agent_deal.id, kind="escalation", status="open", title="Review",
                         created_at=local_midnight + timedelta(minutes=2)))
        db.add(AgentQuote(agent_deal_id=agent_deal.id, discount=5, unit_price=product.list_price * .95,
                          total=product.list_price * .95, savings=123.45, quantity=1,
                          valid_until=local_midnight + timedelta(days=1), status="ordered",
                          ordered_at=local_midnight + timedelta(minutes=3)))
        db.commit()

        metrics = daily_metrics(db, local_midnight + timedelta(minutes=5))
        assert metrics["day_ist"] == "2026-10-03"
        assert metrics["deal_checks"] == 1
        assert metrics["customer_chat_decisions"] == 1
        assert metrics["discount_avoided"] == round(product.list_price * 2 * 0.10, 2)
        assert metrics["orders"] == 1
        assert metrics["discount_actually_given_on_orders"] == 123.45
        assert metrics["pending_escalations_excluded"] == 1
        assert metrics["open_tasks"] == 1
        assert not send_scheduled_summary_if_due(db, local_midnight + timedelta(hours=20, minutes=59))
        assert send_scheduled_summary_if_due(db, local_midnight + timedelta(hours=21))
        assert not send_scheduled_summary_if_due(db, local_midnight + timedelta(hours=22))

        summary = send_daily_summary(db, local_midnight + timedelta(hours=22))
        assert summary["created"] is False
        assert summary["sent"] is True
        assert "03 Oct 2026" in summary["summary_text"]
        assert "IST" in summary["summary_text"]
        rows = db.scalars(select(AgentActivity).where(AgentActivity.kind == "daily_summary")).all()
        assert len(rows) == 1
