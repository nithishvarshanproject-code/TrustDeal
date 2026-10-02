"""GST tax invoice for ordered quotes: catalog prices include GST, so the invoice total is the quote total
and CGST + SGST are worked out inside it (gst.py, rounded half-up to 2 decimals). Only ordered quotes get
an invoice, only the owner can download it, and it never shows a cost, margin, rule or confidence."""
import io
import json
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from pypdf import PdfReader
from sqlalchemy import func, select

from backend.models import AgentInvoice, AgentQuote, Product
from backend.services import gst, ledger
from tests.test_agent import ARJUN, BUDS, PHONE, PRIYA, act, say
from tests.test_quote_pdf import INTERNAL_WORDS
from tests.test_telegram import PRIYA_CHAT, _customer_safe, tg  # noqa: F401  (tg is a fixture)

HAND_COMPUTED = [            # total, taxable value, CGST 9%, SGST 9% (worked out by hand: total x 9 / 118)
    ("35200.00", "29830.50", "2684.75", "2684.75"),
    ("17600.00", "14915.26", "1342.37", "1342.37"),
    ("2699.10", "2287.38", "205.86", "205.86"),
    ("100.00", "84.74", "7.63", "7.63"),
    ("0.59", "0.49", "0.05", "0.05"),      # 0.59 x 9 / 118 = 0.045 exactly: rounds half-up
]


@pytest.mark.parametrize("total,taxable,cgst,sgst", HAND_COMPUTED)
def test_tax_split_matches_hand_computed_examples(total, taxable, cgst, sgst):
    split = gst.split_inclusive(total, 9, 9)
    assert (split["taxable"], split["cgst"], split["sgst"], split["total"]) == \
        tuple(Decimal(x) for x in (taxable, cgst, sgst, total))
    assert split["taxable"] + split["cgst"] + split["sgst"] == Decimal(total)


def test_tax_split_follows_the_configured_rates():
    split = gst.split_inclusive("1120.00", 6, 6)                    # 12%: 1120 x 6 / 112 = 60 each
    assert (split["taxable"], split["cgst"], split["sgst"]) == (Decimal("1000.00"), Decimal("60.00"), Decimal("60.00"))
    cfg = gst.load_config()                                         # the approved defaults
    assert (cfg["cgst_rate"], cfg["sgst_rate"], cfg["seller_gstin_is_demo"]) == (9, 9, True)
    assert cfg["hsn_by_category"] == {"mobiles": "8517", "laptops": "8471", "accessories": "8504", "general": "8479"}


def _quoted(c, text="Can I get 20% off 2 units of this phone?", product_id=PHONE, customer=PRIYA):
    v = say(c, customer, text, product_id=product_id)
    if v["status"] != "QUOTED":
        v = act(c, customer, v["request_id"], "accept")
    assert v["status"] == "QUOTED" and v["quote"]["invoice_no"] is None
    return v


def _ordered(c, *args, **kwargs):
    v = _quoted(c, *args, **kwargs)
    o = act(c, kwargs.get("customer", PRIYA), v["request_id"], "order")
    assert o["status"] == "ORDERED"
    return o


def _invoice(c, request_id, customer_id=PRIYA):
    return c.get(f"/customer/requests/{request_id}/invoice.pdf", params={"customer_id": customer_id})


def _text(content: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)


def test_an_order_gets_a_tax_invoice_whose_total_is_the_quote_total(stack):
    c = stack["client"]
    o = _ordered(c)
    q = o["quote"]
    assert q["invoice_no"] == "INV-00001"
    resp = _invoice(c, o["request_id"])
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == 'attachment; filename="TrustDeal-INV-00001.pdf"'
    assert resp.headers["cache-control"] == "no-store"
    text = _text(resp.content)
    for expected in ("TAX INVOICE", "INV-00001", "IST", "TrustDeal · BASIX Store", "GSTIN 29ABCDE1234F1Z5", "DEMO",
                     "Place of supply: Karnataka (29)", "Priya Sharma", "Prices are inclusive of GST",
                     "CGST 9%", "SGST 9%", q["quote_ref"], f"Verification code {q['verify_code']}", q["order_ref"],
                     "Demo invoice, not valid for tax purposes."):
        assert expected in text, expected
    # one line: product, HSN, qty, unit price after discount (incl. GST), taxable value, CGST, SGST, total
    assert re.search(r"Smartphone A\s+128GB\s+8517\s+2\s+₹17,600\.00\s+₹29,830\.50\s+₹2,684\.75\s+₹2,684\.75\s+"
                     r"₹35,200\.00", text), text                                  # (the name may wrap)
    with stack["Session"]() as db:
        invoice = db.scalar(select(AgentInvoice))
        quote = db.get(AgentQuote, invoice.quote_id)
        assert invoice.total == quote.total == q["total"] == 35200.0                      # = the quote's final price
        assert (invoice.taxable_value, invoice.cgst, invoice.sgst, invoice.hsn) == (29830.5, 2684.75, 2684.75, "8517")
        assert sum(Decimal(str(x)) for x in (invoice.taxable_value, invoice.cgst, invoice.sgst)) == Decimal("35200.00")


def test_only_ordered_quotes_get_invoices(stack):
    c = stack["client"]
    v = say(c, PRIYA, "Can I get 20% off this phone?", product_id=PHONE)          # a counter offer: no quote yet
    assert _invoice(c, v["request_id"]).status_code == 404
    v = act(c, PRIYA, v["request_id"], "accept")                                   # quoted, not ordered
    assert v["quote"]["invoice_no"] is None and _invoice(c, v["request_id"]).status_code == 404

    expired = _quoted(c, "Can I get 10% off?", product_id=BUDS)                    # an expired quote cannot be ordered
    with stack["Session"]() as db:
        quote = db.scalar(select(AgentQuote).where(AgentQuote.agent_deal_id == expired["request_id"]))
        quote.valid_until = quote.valid_until - timedelta(days=3)
        db.commit()
    after = act(c, PRIYA, expired["request_id"], "order")
    assert after["quote"]["status"] == "expired" and after["quote"]["invoice_no"] is None
    assert _invoice(c, expired["request_id"]).status_code == 404
    with stack["Session"]() as db:
        assert db.scalar(select(func.count(AgentInvoice.id))) == 0

    o = act(c, PRIYA, v["request_id"], "order")
    assert o["quote"]["invoice_no"] == "INV-00001" and _invoice(c, v["request_id"]).status_code == 200


def test_only_the_owner_can_download_the_invoice(stack):
    c = stack["client"]
    o = _ordered(c)
    assert _invoice(c, o["request_id"], ARJUN).status_code == 404                  # another customer's order
    assert _invoice(c, o["request_id"], 999).status_code == 404                    # unknown customer
    assert _invoice(c, 999).status_code == 404                                     # unknown request


def test_invoice_pdf_has_nothing_internal(stack):
    c = stack["client"]
    o = _ordered(c)
    content = _invoice(c, o["request_id"]).content
    text = _text(content)
    assert not INTERNAL_WORDS.search(text), INTERNAL_WORDS.search(text).group(0)
    with stack["Session"]() as db:
        costs = [p.cost_price for p in db.scalars(select(Product))]
    numbers = {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
    assert not [x for x in costs if f"{x:g}" in numbers or f"{x:.2f}" in numbers], numbers
    metadata = " ".join(str(v) for v in (PdfReader(io.BytesIO(content)).metadata or {}).values())
    assert not INTERNAL_WORDS.search(metadata) and "16000" not in metadata


def test_invoice_numbers_are_consecutive_when_quote_ids_skip(stack):
    c = stack["client"]
    _quoted(c)                                                                     # quote 1 is never ordered
    first = _ordered(c, "Can I get 10% off?", product_id=BUDS)
    second = _ordered(c, "Can I get 10% off 2 units?", product_id=BUDS)
    assert [first["quote"]["invoice_no"], second["quote"]["invoice_no"]] == ["INV-00001", "INV-00002"]
    assert [first["quote"]["order_ref"], second["quote"]["order_ref"]] == ["ORD-00002", "ORD-00003"]


def test_settings_apply_at_order_time_and_issued_invoices_never_change(stack, tmp_path, monkeypatch):
    c = stack["client"]
    first = _ordered(c)
    cfg = json.loads(gst.DEFAULT_CONFIG.read_text(encoding="utf-8"))
    cfg.update(cgst_rate=6, sgst_rate=6)
    cfg["hsn_by_category"]["accessories"] = "8518"
    path = tmp_path / "gst.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("DEALDESK_GST_CONFIG", str(path))
    second = _ordered(c, "Can I get 10% off?", product_id=BUDS)
    with stack["Session"]() as db:
        old, new = db.scalars(select(AgentInvoice).order_by(AgentInvoice.id)).all()
        assert (old.cgst_rate, old.hsn) == (9, "8517") and (new.cgst_rate, new.sgst_rate, new.hsn) == (6, 6, "8518")
        split = gst.split_inclusive(new.total, 6, 6)
        assert (new.taxable_value, new.cgst, new.sgst) == tuple(float(split[k]) for k in ("taxable", "cgst", "sgst"))
    assert "CGST 9%" in _text(_invoice(c, first["request_id"]).content)              # the issued invoice is unchanged
    assert "CGST 6%" in _text(_invoice(c, second["request_id"]).content)

    path.write_text(json.dumps({**cfg, "cgst_rate": 80}), encoding="utf-8")       # invalid: no order without invoice
    v = _quoted(c, "Can I get 10% off 3 units?", product_id=BUDS)
    with pytest.raises(gst.GstConfigError):
        c.post(f"/customer/requests/{v['request_id']}/order", json={"customer_id": PRIYA})
    after = c.get(f"/customer/requests/{v['request_id']}", params={"customer_id": PRIYA}).json()
    assert after["status"] == "QUOTED" and after["quote"]["status"] == "open" and after["quote"]["invoice_no"] is None


def test_invoice_is_in_the_ledger_once(stack):
    c = stack["client"]
    o = _ordered(c)
    entries = c.get("/seller/ledger").json()["entries"]
    [entry] = [e for e in entries if e["kind"] == "invoice"]
    p = entry["payload"]
    assert (p["invoice_no"], p["order_ref"], p["total"], p["cgst"], p["sgst"], p["taxable_value"], p["hsn"]) == \
        ("INV-00001", o["quote"]["order_ref"], 35200.0, 2684.75, 2684.75, 29830.5, "8517")
    with stack["Session"]() as db:
        again = ledger.append(db, "invoice", entry["key"], {"invoice_no": "INV-99999"})
        db.commit()
        assert again.seq == entry["seq"]                                           # keyed: no second entry
    assert [e["kind"] for e in c.get("/seller/ledger").json()["entries"]].count("invoice") == 1
    assert c.post("/seller/ledger/verify").json()["intact"] is True


def test_telegram_sends_the_invoice_once_after_the_order(tg):  # noqa: F811
    c, fake, say_tg, press = tg["client"], tg["fake"], tg["say"], tg["press"]
    tg["link"](PRIYA_CHAT, PRIYA)
    say_tg(PRIYA_CHAT, "Can I get 10% off Wireless Earbuds?")                     # approved: quote + quote PDF
    _, buttons = fake.last(PRIYA_CHAT)
    rid = int(buttons[0].split(":")[1])
    assert buttons[0] == f"o:{rid}" and len(fake.documents(PRIYA_CHAT)) == 1
    press(PRIYA_CHAT, f"o:{rid}", message_id=fake.next_id)                         # Place order
    assert "order ORD-" in fake.last(PRIYA_CHAT)[0]
    (payload, files) = fake.documents(PRIYA_CHAT)[-1]
    filename, pdf, mime = files["document"]
    assert (filename, mime, payload["caption"]) == ("TrustDeal-INV-00001.pdf", "application/pdf",
                                                    "Your tax invoice INV-00001 (PDF)")
    text = _text(pdf)
    assert "TAX INVOICE" in text and "INV-00001" in text and "Wireless Earbuds" in text and "8504" in text
    assert not INTERNAL_WORDS.search(text)
    _customer_safe([payload["caption"]], tg["Session"])
    tg["bot"].sweep()                                                              # delivered once, never again
    assert len(fake.documents(PRIYA_CHAT)) == 2
    detail = c.get(f"/seller/agent/deals/{rid}").json()
    assert any(a["summary"] == "Telegram: sent tax invoice INV-00001" for a in detail["activity"])
