"""Tamper-proof quotes: each quote's code is an HMAC over its signed fields with a server secret.
The public Verify quote check says "genuine" only for the exact stored quote; a wrong code, an edited
field or an unknown ref is "not genuine", with the same answer. The secret never leaves the server."""
import base64
import io
import json
import logging
import re
from datetime import timedelta
from pathlib import Path

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from backend.agent.views import NOT_GENUINE
from backend.models import AgentDeal, AgentQuote, Product
from backend.services import quote_pdf, quote_seal
from tests.test_agent import ARJUN, BUDS, FORBIDDEN, PHONE, PRIYA, act, say

CODE = re.compile(r"[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}")
ROOT = Path(__file__).resolve().parents[1]


def _quoted(c, text="Can I get 20% off 2 units of this phone?", product_id=PHONE):
    v = say(c, PRIYA, text, product_id=product_id)
    if v["status"] != "QUOTED":
        v = act(c, PRIYA, v["request_id"], "accept")
    assert v["status"] == "QUOTED", v
    return v


def _verify(c, ref, code):
    resp = c.post("/verify/quote", json={"ref": ref, "code": code})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _latest_quote(db):
    return db.scalar(select(AgentQuote).order_by(AgentQuote.id.desc()))


def test_a_new_quote_has_a_code_that_verifies(stack):
    c = stack["client"]
    q = _quoted(c)["quote"]
    assert CODE.fullmatch(q["verify_code"]), q["verify_code"]
    res = _verify(c, q["quote_ref"], q["verify_code"])
    assert res == {"genuine": True, "quote_ref": q["quote_ref"], "customer": "Priya S.",
                   "product_name": "Smartphone A 128GB", "quantity": 2, "list_price": 20000.0, "discount": 12.0,
                   "unit_price": 17600.0, "total": 35200.0, "savings": 4800.0, "valid_until": q["valid_until"],
                   "status": "open", "order_ref": None}
    # case, spaces and dashes do not matter
    assert _verify(c, q["quote_ref"].lower(), q["verify_code"].replace("-", "").lower())["genuine"] is True
    assert _verify(c, f" {q['quote_ref']} ", " ".join(q["verify_code"].split("-")))["genuine"] is True


def test_wrong_code_or_unknown_ref_is_not_genuine_with_one_answer(stack):
    c = stack["client"]
    q = _quoted(c)["quote"]
    code = q["verify_code"].replace("-", "")
    answers = [_verify(c, q["quote_ref"], code[:i] + ("B" if code[i] != "B" else "C") + code[i + 1:])
               for i in range(len(code))]                                  # any single changed letter
    answers += [_verify(c, "Q-99999", code), _verify(c, "Q-00000", code), _verify(c, "not a ref", code),
                _verify(c, q["quote_ref"], code[:-1]), _verify(c, q["quote_ref"], "AEIO-U123-4567")]
    assert all(a == NOT_GENUINE for a in answers)
    assert set(NOT_GENUINE) == {"genuine", "message"}
    too_long = c.post("/verify/quote", json={"ref": "Q-" + "1" * 40, "code": code})
    assert too_long.status_code == 422 and "1" * 40 not in too_long.text   # input never echoed


def _bump_valid_until(q, d):
    q.valid_until = q.valid_until + timedelta(hours=24)


EDITS = {
    "quantity": lambda q, d: setattr(q, "quantity", q.quantity + 1),
    "list_price": lambda q, d: setattr(q, "list_price", q.list_price + 1),
    "discount": lambda q, d: setattr(q, "discount", q.discount + 0.5),
    "unit_price": lambda q, d: setattr(q, "unit_price", q.unit_price - 1),
    "total": lambda q, d: setattr(q, "total", q.total - 1),
    "savings": lambda q, d: setattr(q, "savings", q.savings + 1),
    "valid_until": _bump_valid_until,
    "customer": lambda q, d: setattr(d, "customer_id", ARJUN),
    "product": lambda q, d: setattr(d, "product_id", 3),
    "code": lambda q, d: setattr(q, "verify_code", "B" * 12),
}


@pytest.mark.parametrize("field", EDITS)
def test_any_edited_field_breaks_the_code(stack, field):
    c = stack["client"]
    q = _quoted(c)["quote"]
    with stack["Session"]() as db:
        quote = _latest_quote(db)
        EDITS[field](quote, db.get(AgentDeal, quote.agent_deal_id))
        db.commit()
    assert _verify(c, q["quote_ref"], q["verify_code"]) == NOT_GENUINE


def test_a_code_only_works_for_its_own_quote(stack):
    c = stack["client"]
    first = _quoted(c)["quote"]
    second = _quoted(c, "Can I get 10% off?", product_id=BUDS)["quote"]
    assert first["quote_ref"] != second["quote_ref"] and first["verify_code"] != second["verify_code"]
    assert _verify(c, second["quote_ref"], first["verify_code"]) == NOT_GENUINE
    assert _verify(c, first["quote_ref"], second["verify_code"]) == NOT_GENUINE
    assert _verify(c, second["quote_ref"], second["verify_code"])["genuine"] is True


def test_ordered_and_expired_quotes_still_verify_with_their_status(stack):
    c = stack["client"]
    v = _quoted(c)
    q = v["quote"]
    ordered = act(c, PRIYA, v["request_id"], "order")["quote"]
    res = _verify(c, q["quote_ref"], q["verify_code"])
    assert (res["genuine"], res["status"], res["order_ref"]) == (True, "ordered", ordered["order_ref"])

    other = _quoted(c, "Can I get 10% off?", product_id=BUDS)["quote"]
    with stack["Session"]() as db:                     # what the follow-ups do at expiry (status is not signed)
        _latest_quote(db).status = "expired"
        db.commit()
    res = _verify(c, other["quote_ref"], other["verify_code"])
    assert (res["genuine"], res["status"], res["order_ref"]) == (True, "expired", None)


def test_verify_answer_is_customer_safe(stack):
    c = stack["client"]
    q = _quoted(c)["quote"]
    res = _verify(c, q["quote_ref"], q["verify_code"])
    with stack["Session"]() as db:
        costs = {f"{p.cost_price:g}" for p in db.scalars(select(Product))}
    for body in (res, NOT_GENUINE):
        text = json.dumps(body)
        assert not FORBIDDEN.search(text), FORBIDDEN.search(text).group(0)
        assert not re.search(r"(?i)cost|margin|rule|confidence|trust|secret|hmac", text)
        numbers = {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
        assert not {n for n in numbers if n in costs or n.rstrip("0").rstrip(".") in costs}, text
    assert "verify_code" not in res and "Sharma" not in json.dumps(res)      # masked name, no code echoed


def _pdf(c, request_id):
    resp = c.get(f"/customer/requests/{request_id}/quote.pdf", params={"customer_id": PRIYA})
    assert resp.status_code == 200
    return resp.content


def _pdf_text(content):
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)


def test_pdf_shows_the_code_the_link_and_a_qr_of_the_link(stack, monkeypatch):
    seen = []
    real = quote_pdf._draw_qr
    monkeypatch.setattr(quote_pdf, "_draw_qr", lambda c, url, *rest: (seen.append(url), real(c, url, *rest)))
    c = stack["client"]
    v = _quoted(c)
    q = v["quote"]
    url = f"http://localhost:5173/#verify?ref={q['quote_ref']}&code={q['verify_code'].replace('-', '')}"
    text = _pdf_text(_pdf(c, v["request_id"]))
    assert "VERIFY THIS QUOTE" in text and q["verify_code"] in text and url in text
    assert seen == [url]

    monkeypatch.setenv("DEALDESK_PUBLIC_URL", "http://127.0.0.1:5173/")
    seen.clear()
    _pdf(c, v["request_id"])
    assert seen == [url.replace("localhost", "127.0.0.1")]

    with stack["Session"]() as db:                     # a quote from before codes existed: no block
        _latest_quote(db).verify_code = None
        db.commit()
    seen.clear()
    text = _pdf_text(_pdf(c, v["request_id"]))
    assert "VERIFY" not in text and seen == []
    assert _verify(c, q["quote_ref"], q["verify_code"]) == NOT_GENUINE


def test_secret_is_created_once_never_leaks_and_keys_the_code(stack, caplog):
    caplog.set_level(logging.DEBUG)
    path = quote_seal.secret_file()
    assert not path.exists()                           # created on first use (a temporary file in tests)
    c = stack["client"]
    v = _quoted(c)
    q = v["quote"]
    secret = path.read_text(encoding="ascii").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", secret)
    raw = bytes.fromhex(secret)

    bodies = [v, _verify(c, q["quote_ref"], q["verify_code"]),
              c.get(f"/customer/requests/{v['request_id']}", params={"customer_id": PRIYA}).json(),
              c.get(f"/seller/agent/deals/{v['request_id']}").json(), c.get("/seller/agent/deals").json(),
              c.get("/seller/agent/activity").json()]
    pdf = _pdf(c, v["request_id"])
    blobs = [json.dumps(b) for b in bodies] + [pdf.decode("latin-1"), _pdf_text(pdf), caplog.text]
    forms = (secret, secret.upper(), base64.b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode())
    for blob in blobs:
        assert not any(form in blob for form in forms)
    assert raw not in pdf

    quote_seal.reset_cache()                           # a restart reuses the same secret: the code still verifies
    assert _verify(c, q["quote_ref"], q["verify_code"])["genuine"] is True
    assert path.read_text(encoding="ascii").strip() == secret

    path.write_text("0" * 64, encoding="ascii")        # another secret: the same fields no longer match
    quote_seal.reset_cache()
    assert _verify(c, q["quote_ref"], q["verify_code"]) == NOT_GENUINE


def test_a_corrupt_secret_is_an_error_and_is_never_replaced(stack):
    path = quote_seal.secret_file()
    path.write_text("not-a-secret-value", encoding="ascii")
    quote_seal.reset_cache()
    with pytest.raises(quote_seal.QuoteSecretError) as err:
        quote_seal._key()
    assert "not-a-secret-value" not in str(err.value)
    assert path.read_text(encoding="ascii") == "not-a-secret-value"


def test_the_real_secret_file_is_git_ignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert "backend/secrets/" in ignored
    assert quote_seal.DEFAULT_SECRET_FILE.parent == ROOT / "backend" / "secrets"
