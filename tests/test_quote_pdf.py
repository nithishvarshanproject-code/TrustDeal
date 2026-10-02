"""Quote PDF: the download on the Customer page shows every customer-facing field and nothing
internal (no cost price, margin, rule, confidence or trust), and only to the request's owner."""
import io
import re

from pypdf import PdfReader
from sqlalchemy import select

from backend.models import Product
from backend.services import quote_pdf

PRIYA, ARJUN = 1, 2
PHONE = 2
INTERNAL_WORDS = re.compile(r"(?i)\bcost|margin|floor|\brules?\b|\bR[1-7]\b|\bA\d{1,2}\b|confidence|\btrust\b|"
                            r"audit|metta|policy|tier cap|APPROVE|COUNTER|REJECT|ESCALATE")


def _quote(c, quantity_text="Can I get 20% off this phone?"):
    v = c.post("/customer/messages", json={"customer_id": PRIYA, "product_id": PHONE, "text": quantity_text}).json()
    v = c.post(f"/customer/requests/{v['request_id']}/accept", json={"customer_id": PRIYA}).json()
    assert v["status"] == "QUOTED", v
    return v


def _pdf_text(resp) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(resp.content)).pages)


def test_quote_pdf_has_every_customer_field_and_nothing_internal(stack):
    c = stack["client"]
    v = _quote(c, "Can I get 20% off 2 units of this phone?")
    q = v["quote"]
    resp = c.get(f"/customer/requests/{v['request_id']}/quote.pdf", params={"customer_id": PRIYA})
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == f'attachment; filename="TrustDeal-{q["quote_ref"]}.pdf"'
    assert resp.headers["cache-control"] == "no-store" and resp.content.startswith(b"%PDF")

    text = _pdf_text(resp)
    for expected in ("TrustDeal · BASIX Store", "The BASIX Deal Agent on Omega", "Every discount, explained and proven.",
                     "Priya Sharma", "Smartphone A 128GB", q["quote_ref"], "Issued", "VALID UNTIL", "IST",
                     "₹20,000",                        # list price (catalog)
                     "12%",                                 # discount (MeTTa)
                     "₹17,600",                        # price each (MeTTa)
                     "₹35,200",                        # total for 2 units (MeTTa)
                     "You save ₹4,800"):               # savings (MeTTa)
        assert expected in text, expected
    assert q["quantity"] == 2 and re.search(r"Smartphone A 128GB\s+2\s", text)

    assert not INTERNAL_WORDS.search(text), INTERNAL_WORDS.search(text).group(0)
    with stack["Session"]() as db:
        costs = [p.cost_price for p in db.scalars(select(Product))]
    numbers = {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
    assert not [x for x in costs if f"{x:g}" in numbers or f"{x:.2f}" in numbers], numbers
    metadata = " ".join(str(v) for v in (PdfReader(io.BytesIO(resp.content)).metadata or {}).values())
    assert not INTERNAL_WORDS.search(metadata) and "16000" not in metadata


def test_quote_pdf_only_for_the_owner_and_only_with_a_quote(stack):
    c = stack["client"]
    v = _quote(c)
    url = f"/customer/requests/{v['request_id']}/quote.pdf"
    assert c.get(url, params={"customer_id": ARJUN}).status_code == 404       # another customer's request
    assert c.get(url, params={"customer_id": 999}).status_code == 404
    other = c.post("/customer/messages", json={"customer_id": PRIYA, "product_id": 7, "text": "What does it cost?"})
    assert c.get(f"/customer/requests/{other.json()['request_id']}/quote.pdf",
                 params={"customer_id": PRIYA}).status_code == 404            # no quote yet


def test_ordered_quote_pdf_shows_the_order(stack):
    c = stack["client"]
    v = _quote(c)
    c.post(f"/customer/requests/{v['request_id']}/order", json={"customer_id": PRIYA})
    text = _pdf_text(c.get(f"/customer/requests/{v['request_id']}/quote.pdf", params={"customer_id": PRIYA}))
    assert re.search(r"Ordered · ORD-\d{5}", text)


def test_pdf_falls_back_to_inr_without_the_font(monkeypatch, tmp_path):
    monkeypatch.setattr(quote_pdf, "FONT_DIR", tmp_path)          # no font files
    monkeypatch.setattr(quote_pdf, "_fonts", None)
    from datetime import datetime, timezone
    doc = {"store": "TrustDeal · BASIX Store", "quote_ref": "Q-00042", "customer_name": "Test",
           "product_name": "Phone", "quantity": 1, "list_price": 110000.0, "discount": 10.0, "unit_price": 99000.0,
           "total": 99000.0, "savings": 11000.0, "issued_at": datetime.now(timezone.utc),
           "valid_until": datetime.now(timezone.utc), "status": "open", "order_ref": None}
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(quote_pdf.render_quote_pdf(doc))).pages)
    assert "INR 1,10,000" in text and "INR 99,000" in text and "₹" not in text
