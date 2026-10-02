"""Drafted replies show rupee amounts like the rest of the app (₹17,600, never ₹17,600.00): display only,
after the number guardrail has matched every number. ASI:One is mocked (see test_agent.FakeASI)."""
import pytest

from backend.agent.language import tidy_amounts
from tests.test_agent import PHONE, PRIYA, FakeASI, act, asi, say  # noqa: F401  (asi is a fixture)


@pytest.mark.parametrize("raw,shown", [
    ("Your order is confirmed for ₹35,200.00.", "Your order is confirmed for ₹35,200."),
    ("Total ₹ 17600.00, you save Rs. 2,400.00", "Total ₹17,600, you save ₹2,400"),
    ("Only INR 110,000.00 today", "Only ₹1,10,000 today"),
    ("Price each ₹2099.3, total ₹2,099.30.", "Price each ₹2,099.30, total ₹2,099.30."),
    ("₹17,600, valid for 48 hours.", "₹17,600, valid for 48 hours."),          # already right: unchanged
    ("Quote Q-00001 is ready: 12% off.", "Quote Q-00001 is ready: 12% off."),  # no rupee amount: unchanged
])
def test_rupee_amounts_are_written_the_app_way(raw, shown):
    assert tidy_amounts(raw) == shown


def test_order_confirmation_draft_shows_whole_rupees(stack, asi):  # noqa: F811
    c = stack["client"]
    v = say(c, PRIYA, "Can I get 20% off 2 units of this phone?", product_id=PHONE)
    v = act(c, PRIYA, v["request_id"], "accept")
    asi(FakeASI(draft="Your order ORD-00001 is confirmed: 2 units for ₹35,200.00. Thank you!"))
    v = act(c, PRIYA, v["request_id"], "order")
    assert v["status"] == "ORDERED"
    last = [m for m in v["messages"] if m["sender"] == "agent"][-1]
    assert last["text"] == "Your order ORD-00001 is confirmed: 2 units for ₹35,200. Thank you!"
    stored = [m for m in c.get(f"/seller/agent/deals/{v['request_id']}").json()["messages"]
              if m["sender"] == "agent"][-1]
    assert stored["source"] == "llm" and stored["text"] == last["text"]     # the same text goes to Telegram
