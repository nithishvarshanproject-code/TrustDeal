"""Prices from the Omega engine never reach a page in exponent form.

PeTTa (SWI-Prolog) prints some floats in exponent form, also inside the trail strings:
10000.0 -> 1.0e+04, 20000.0 -> 2.0e+04, 110000.0 -> 1.1e+05, 9350000.0 -> 9.35e+06
(but 14000.0 and 42900.0 stay as they are). OmegaRunner rewrites them as plain decimals,
exactly as the local engine prints them. These tests make the fake Omega plugin print floats
the PeTTa way and check every API a page uses."""
import json
import re

import pytest

from engine.bridge import normalize_floats
from tests.test_agent import PRIYA
from tests.test_omega_runner import omega  # noqa: F401  (fixture)

EXPONENT = re.compile(r"\d\.\d+e[+-]\d+", re.I)
_FLOAT = re.compile(r"(?<![\w.])(\d+)\.(\d+)(?![\w.])")


def petta_style(text: str) -> str:
    """Print floats like SWI-Prolog: a whole number ending in 4+ zeros goes to exponent form."""
    def fmt(m):
        whole, frac = m.group(1), m.group(2)
        if frac.strip("0") or len(whole) - len(whole.rstrip("0")) < 4:
            return m.group(0)
        digits = whole.rstrip("0")
        return f"{digits[0]}.{digits[1:] or '0'}e+{len(whole) - 1:02d}"
    return _FLOAT.sub(fmt, text)


@pytest.mark.parametrize("petta, plain", [
    ("1.0e+04", "10000.0"), ("2.0e+04", "20000.0"), ("1.1e+05", "110000.0"), ("9.35e+06", "9350000.0"),
    ('"step 1: price 1.0e+04 < cost 16000.0 -> REJECT"', '"step 1: price 10000.0 < cost 16000.0 -> REJECT"'),
    ("(terms 0.0 9.9e+04 9.9e+04 1.1e+04 48)", "(terms 0.0 99000.0 99000.0 11000.0 48)"),
])
def test_normalize_floats(petta, plain):
    assert normalize_floats(petta) == plain


def test_the_fake_prints_like_petta():
    # the cases observed in the real PeTTa runtime
    assert petta_style("10000.0 14000.0 16000.0 20000.0 42900.0 44000.0 110000.0 123456.0 9350000.0 12.5") == \
        "1.0e+04 14000.0 16000.0 2.0e+04 42900.0 44000.0 1.1e+05 123456.0 9.35e+06 12.5"


def _no_exponent(body, where):
    text = json.dumps(body)
    assert not EXPONENT.search(text), f"{where}: {EXPONENT.search(text).group(0)} in {text[:300]}"


def test_deal_desk_trail_never_shows_exponent_form(omega):  # noqa: F811
    omega["connect"](transform=petta_style)
    c = omega["client"]
    # Smartphone A at 50% off: price 10000.0 < cost 16000.0 -> REJECT; PeTTa writes 1.0e+04
    body = c.post("/deals/evaluate", json={"seller_id": 1, "product_id": 2, "quantity": 5,
                                            "discount_requested": 50}).json()
    assert body["result"] == "REJECT"
    decision_line = body["trail"][-1]["check"]
    assert "price 10000.0 < cost 16000.0" in decision_line
    _no_exponent(body, "POST /deals/evaluate")
    _no_exponent(c.get(f"/deals/{body['deal_id']}").json(), "GET /deals/{id}")
    _no_exponent(c.get("/deals/history").json(), "GET /deals/history")


def test_agent_pages_never_show_exponent_form(omega):  # noqa: F811
    omega["connect"](transform=petta_style)
    c = omega["client"]
    # Laptop Pro 16 (list 110000) at 10%: unit price 99000.0 and total 99000.0 -> 9.9e+04 in PeTTa
    v = c.post("/customer/messages", json={"customer_id": PRIYA, "product_id": 5,
                                           "text": "Can I get 10% off the laptop?"}).json()
    assert v["status"] == "QUOTED" and v["quote"]["total"] == 99000.0 and v["quote"]["savings"] == 11000.0
    assert "99,000" in v["messages"][-1]["text"]                  # rupee formatting in the reply
    _no_exponent(v, "customer request view")
    _no_exponent(c.get("/customer/requests", params={"customer_id": PRIYA}).json(), "my requests")
    seller = c.get(f"/seller/agent/deals/{v['request_id']}").json()
    assert seller["decisions"][0]["engine"]["runner"] == "omega"
    _no_exponent(seller, "seller agent view")
    quote_log = next(a["summary"] for a in seller["activity"] if a["summary"].startswith("create_quote"))
    assert "\u20b999,000" in quote_log
