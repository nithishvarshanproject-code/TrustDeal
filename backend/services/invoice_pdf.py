"""GST TAX INVOICE PDF for an ordered quote (Customer page download, and a document on Telegram).

Rendered only from views.customer_invoice_document(), an allowlist: the stored invoice (number, date,
seller and demo GSTIN, HSN, GST split), customer and product names, and the linked quote ref, its
verification code and the order ref. No cost price, margin, rule, confidence or trust value can reach
the page. Catalog prices include GST: the total is the quote total (MeTTa), split by gst.py at order time.
"""
import io
from decimal import ROUND_HALF_UP, Decimal

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from backend.agent.language import fmt_inr, fmt_pct
from backend.services.quote_pdf import ACCENT, INK, LINE, MUTED, PRODUCT_NAME, SOFT, TAGLINE, _register_fonts, _when

WARN = HexColor("#b45309")
FOOTER_NOTE = "Demo invoice, not valid for tax purposes."


def _amount(x: float, rupee: bool) -> str:
    """Always 2 decimals with Indian grouping: 29830.5 -> ₹29,830.50."""
    paise = int((Decimal(str(x)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    whole, frac = divmod(paise, 100)
    text = f"{fmt_inr(whole)}.{frac:02d}"
    return text if rupee else text.replace("₹", "INR ")


def render_invoice_pdf(doc: dict) -> bytes:
    """One A4 page for the invoice described by `doc` (views.customer_invoice_document)."""
    regular, bold, rupee = _register_fonts()
    money = lambda x: _amount(x, rupee)  # noqa: E731
    rate = lambda r: f"{fmt_pct(r)}%"   # noqa: E731
    buf = io.BytesIO()
    width, height = A4
    left, right = 50, width - 50
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Tax invoice {doc['invoice_no']} - {doc['seller_name']}")
    c.setAuthor(doc["seller_name"])
    c.setSubject(f"{PRODUCT_NAME} tax invoice for {doc['product_name']}")
    c.setCreator(PRODUCT_NAME)

    # header: seller and GSTIN (demo) | TAX INVOICE, number, date
    c.setFillColor(ACCENT)
    c.rect(0, height - 8, width, 8, stroke=0, fill=1)
    y = height - 62
    c.setFillColor(INK)
    c.setFont(bold, 17)
    c.drawString(left, y, doc["seller_name"])
    c.setFont(regular, 9.5)
    c.drawString(left, y - 17, f"GSTIN {doc['seller_gstin']}")
    if doc["gstin_is_demo"]:
        c.setFillColor(WARN)
        c.setFont(bold, 9.5)
        c.drawString(left + 4 + c.stringWidth(f"GSTIN {doc['seller_gstin']}", regular, 9.5), y - 17,
                     "DEMO (sample number, not a registered taxpayer)")
    c.setFillColor(MUTED)
    c.setFont(regular, 9)
    c.drawString(left, y - 31, f"Place of supply: {doc['place_of_supply']}")
    c.setFillColor(ACCENT)
    c.setFont(bold, 22)
    c.drawRightString(right, y, "TAX INVOICE")
    c.setFillColor(INK)
    c.setFont(bold, 11)
    c.drawRightString(right, y - 17, doc["invoice_no"])
    c.setFillColor(MUTED)
    c.setFont(regular, 9)
    c.drawRightString(right, y - 31, f"Date {_when(doc['issued_at'])}")

    y -= 52
    c.setStrokeColor(LINE)
    c.setLineWidth(1)
    c.line(left, y, right, y)

    # billed to / order / linked quote
    y -= 26
    blocks = [("BILLED TO", doc["customer_name"], None), ("ORDER", doc["order_ref"] or "-", None),
              ("LINKED QUOTE", doc["quote_ref"],
               f"Verification code {doc['verify_code']}" if doc.get("verify_code") else None)]
    col = (right - left) / 3
    for i, (label, value, extra) in enumerate(blocks):
        x = left + i * col
        c.setFillColor(MUTED)
        c.setFont(bold, 7.5)
        c.drawString(x, y, label)
        c.setFillColor(INK)
        c.setFont(bold, 11)
        c.drawString(x, y - 16, value)
        if extra:
            c.setFillColor(MUTED)
            c.setFont(regular, 8.5)
            c.drawString(x, y - 29, extra)

    # line item
    y -= 62
    cols = [("Product", 98, "l"), ("HSN", 34, "r"), ("Qty", 26, "r"), ("Unit price", 66, "r"),
            ("Taxable value", 70, "r"), (f"CGST {rate(doc['cgst_rate'])}", 64, "r"),
            (f"SGST {rate(doc['sgst_rate'])}", 64, "r"), ("Total", 73, "r")]
    c.setFillColor(SOFT)
    c.rect(left, y - 14, right - left, 32, stroke=0, fill=1)
    x = left
    for title, w, align in cols:
        c.setFillColor(MUTED)
        c.setFont(bold, 8)
        if align == "l":
            c.drawString(x + 6, y + 2, title)
        else:
            c.drawRightString(x + w - 6, y + 2, title)
            if title in ("Unit price", "Total"):
                c.setFont(regular, 7)
                c.drawRightString(x + w - 6, y - 8, "(incl. GST)")
        x += w
    name_lines = simpleSplit(doc["product_name"], regular, 9.5, cols[0][1] - 10)[:3]
    y -= 34
    c.setFillColor(INK)
    c.setFont(regular, 9.5)
    for i, line in enumerate(name_lines):
        c.drawString(left + 6, y - 13 * i, line)
    values = [doc["hsn"], str(doc["quantity"]), money(doc["unit_price"]), money(doc["taxable_value"]),
              money(doc["cgst"]), money(doc["sgst"]), money(doc["total"])]
    x = left + cols[0][1]
    for (title, w, _), value in zip(cols[1:], values):
        c.setFont(bold if title == "Total" else regular, 8.5)
        c.drawRightString(x + w - 6, y, value)
        x += w
    y -= 13 * len(name_lines) + 6
    c.setStrokeColor(LINE)
    c.line(left, y, right, y)

    # totals
    rows = [("Taxable value", money(doc["taxable_value"])), (f"CGST @ {rate(doc['cgst_rate'])}", money(doc["cgst"])),
            (f"SGST @ {rate(doc['sgst_rate'])}", money(doc["sgst"]))]
    y -= 24
    for label, value in rows:
        c.setFillColor(MUTED)
        c.setFont(regular, 10)
        c.drawRightString(right - 130, y, label)
        c.setFillColor(INK)
        c.drawRightString(right - 6, y, value)
        y -= 17
    c.setStrokeColor(LINE)
    c.line(right - 250, y + 6, right, y + 6)
    y -= 14
    c.setFillColor(MUTED)
    c.setFont(regular, 10)
    c.drawRightString(right - 130, y, "Total (incl. GST)")
    c.setFillColor(INK)
    c.setFont(bold, 15)
    c.drawRightString(right - 6, y - 1, money(doc["total"]))

    # how the tax is worked out
    total_rate = Decimal(str(doc["cgst_rate"])) + Decimal(str(doc["sgst_rate"]))
    y -= 46
    c.setFillColor(SOFT)
    c.roundRect(left, y - 46, right - left, 62, 8, stroke=0, fill=1)
    c.setFillColor(INK)
    c.setFont(bold, 9.5)
    c.drawString(left + 14, y, "Prices are inclusive of GST")
    c.setFillColor(MUTED)
    c.setFont(regular, 9)
    divisor, cgst, sgst = fmt_pct(100 + total_rate), fmt_pct(doc["cgst_rate"]), fmt_pct(doc["sgst_rate"])
    notes = [f"The taxable value and the tax are calculated within the price: "
             f"CGST = total x {cgst} / {divisor}, SGST = total x {sgst} / {divisor},",
             "each rounded to 2 decimals; taxable value = total - CGST - SGST. The total is the quote's final price."]
    for i, line in enumerate(notes):
        c.drawString(left + 14, y - 16 - 13 * i, line)

    # footer
    c.setStrokeColor(LINE)
    c.line(left, 78, right, 78)
    c.setFillColor(WARN)
    c.setFont(bold, 9)
    c.drawString(left, 62, FOOTER_NOTE)
    c.setFillColor(MUTED)
    c.setFont(regular, 8.5)
    c.drawString(left, 48, f"{PRODUCT_NAME} · {TAGLINE}")
    c.drawRightString(right, 48, f"{doc['invoice_no']} · page 1 of 1")
    c.showPage()
    c.save()
    return buf.getvalue()
