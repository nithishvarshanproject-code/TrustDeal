"""Quote expiry vs. order at the same instant: the follow-up worker has read the quote as open, then an
order commits before the worker writes. The conditional expiry must leave the order untouched."""
from datetime import timedelta

from sqlalchemy import select

from backend.agent import followups
from backend.agent.loop import Agent
from backend.models import AgentDeal, AgentMessage, AgentQuote
from tests.test_agent import PHONE, PRIYA, act, say


def _quoted(c) -> int:
    v = say(c, PRIYA, "Can I get 20% off this phone?", product_id=PHONE)
    v = act(c, PRIYA, v["request_id"], "accept")
    assert v["status"] == "QUOTED"
    return v["request_id"]


def test_an_order_committed_while_expiry_runs_is_never_overwritten(stack, monkeypatch):
    c, Session = stack["client"], stack["Session"]
    rid = _quoted(c)
    with Session() as db:
        after_expiry = followups.as_utc(db.scalar(select(AgentQuote)).valid_until) + timedelta(seconds=1)

    real_as_utc, placed, quote_expiry = followups.as_utc, [], after_expiry - timedelta(seconds=1)

    def order_lands_now(value):         # the worker's as_utc(quote.valid_until): it has read the open quote
        if not placed and real_as_utc(value) == quote_expiry:
            placed.append(True)
            with Session() as other:    # the customer's order, in its own transaction
                Agent(other).on_button(other.get(AgentDeal, rid), "order")
                other.commit()
        return real_as_utc(value)

    monkeypatch.setattr(followups, "as_utc", order_lands_now)
    with Session() as worker:
        report = followups.run_followups(worker, after_expiry)
    assert placed and report["actions"] == []                     # nothing expired, no notice

    with Session() as db:
        quote = db.scalar(select(AgentQuote))
        assert (quote.status, quote.order_ref) == ("ordered", "ORD-00001")
        assert db.get(AgentDeal, rid).state == "CLOSED"            # closed by the order (A10 -> A11)
        assert not [m for m in db.scalars(select(AgentMessage)) if "expired" in m.text]


def test_an_open_quote_still_expires(stack):
    c, Session = stack["client"], stack["Session"]
    rid = _quoted(c)
    with Session() as db:
        after_expiry = followups.as_utc(db.scalar(select(AgentQuote)).valid_until) + timedelta(seconds=1)
        report = followups.run_followups(db, after_expiry)
        assert [a["action"] for a in report["actions"]] == ["expired"]
    with Session() as db:
        assert db.scalar(select(AgentQuote)).status == "expired" and db.get(AgentDeal, rid).state == "CLOSED"
    assert c.post(f"/customer/requests/{rid}/order", json={"customer_id": PRIYA}).json()["quote"]["status"] == "expired"
