"""Quote PDF (Customer page download, and a document on Telegram).

Rendered only from views.customer_quote_document(), an allowlist: store, customer, product,
quantity, list price, discount, unit price, total, savings, quote ref, dates, and the verification
code with a QR code of the Verify quote link (quote_seal.py). No cost price,
margin, rule, confidence or trust value can reach the page, and no number is computed here:
every amount is a catalog price or a number MeTTa returned (quote-terms).
Amounts use Noto Sans (SIL Open Font License, backend/assets/fonts/OFL.txt) for the rupee sign;
without the font files the PDF falls back to Helvetica and "INR".
"""
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from backend.agent.language import fmt_inr, fmt_pct

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
PRODUCT_NAME = "TrustDeal"
SUBTITLE = "The BASIX Deal Agent on Omega"
TAGLINE = "Every discount, explained and proven."
IST = timezone(timedelta(hours=5, minutes=30), "IST")

INK = HexColor("#111827")
MUTED = HexColor("#6b7280")
LINE = HexColor("#e5e7eb")
SOFT = HexColor("#f5f3ff")
ACCENT = HexColor("#7c5cff")
GOOD = HexColor("#15803d")

_fonts: tuple[str, str, bool] | None = None


def _register_fonts() -> tuple[str, str, bool]:
    """(regular, bold, has_rupee_sign)"""
    global _fonts
    if _fonts is None:
        try:
            pdfmetrics.registerFont(TTFont("NotoSans", str(FONT_DIR / "NotoSans-Regular.ttf")))
            pdfmetrics.registerFont(TTFont("NotoSans-Bold", str(FONT_DIR / "NotoSans-Bold.ttf")))
            _fonts = ("NotoSans", "NotoSans-Bold", True)
        except Exception:   # font files missing or unreadable: built-in fonts, "INR" instead of the sign
            _fonts = ("Helvetica", "Helvetica-Bold", False)
    return _fonts


def _money(x: float, rupee: bool) -> str:
    text = fmt_inr(x)
    return text if rupee else text.replace("₹", "INR ")


def _when(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    if dt.tzinfo is None:            # SQLite returns naive datetimes; they are stored in UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def _draw_qr(c: canvas.Canvas, url: str, x: float, y: float, size: float) -> None:
    """A vector QR code of `url` (reportlab's built-in encoder), size x size points at (x, y)."""
    widget = QrCodeWidget(url, barLevel="M")
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, x, y)


def render_quote_pdf(doc: dict) -> bytes:
    """One A4 page for the quote described by `doc` (views.customer_quote_document)."""
    regular, bold, rupee = _register_fonts()
    money = lambda x: _money(x, rupee)  # noqa: E731
    buf = io.BytesIO()
    width, height = A4
    left, right = 50, width - 50
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Quote {doc['quote_ref']} - {doc['store']}")
    c.setAuthor(doc["store"])
    c.setSubject(f"{PRODUCT_NAME} quote for {doc['product_name']}")
    c.setCreator(PRODUCT_NAME)

    # header band
    c.setFillColor(ACCENT)
    c.rect(0, height - 8, width, 8, stroke=0, fill=1)
    y = height - 62
    c.setFillColor(INK)
    c.setFont(bold, 19)
    c.drawString(left, y, doc["store"])
    c.setFillColor(MUTED)
    c.setFont(regular, 9.5)
    c.drawString(left, y - 16, SUBTITLE)
    c.setFillColor(ACCENT)
    c.setFont(bold, 22)
    c.drawRightString(right, y, "QUOTE")
    c.setFillColor(INK)
    c.setFont(bold, 11)
    c.drawRightString(right, y - 17, doc["quote_ref"])
    c.setFillColor(MUTED)
    c.setFont(regular, 9)
    c.drawRightString(right, y - 31, f"Issued {_when(doc['issued_at'])}")

    y -= 52
    c.setStrokeColor(LINE)
    c.setLineWidth(1)
    c.line(left, y, right, y)

    # who / until / status
    y -= 26
    ordered = doc["status"] == "ordered" and doc.get("order_ref")
    blocks = [("PREPARED FOR", doc["customer_name"]),
              ("VALID UNTIL", _when(doc["valid_until"])),
              ("STATUS", f"Ordered · {doc['order_ref']}" if ordered else "Open")]
    col = (right - left) / 3
    for i, (label, value) in enumerate(blocks):
        x = left + i * col
        c.setFillColor(MUTED)
        c.setFont(bold, 7.5)
        c.drawString(x, y, label)
        c.setFillColor(INK)
        c.setFont(bold if i == 0 else regular, 11)
        c.drawString(x, y - 16, value)

    # item table
    y -= 54
    cols = [("Product", 175, "l"), ("Qty", 40, "r"), ("List price", 75, "r"), ("Discount", 60, "r"),
            ("Price each", 75, "r"), ("Amount", 70, "r")]
    c.setFillColor(SOFT)
    c.rect(left, y - 8, right - left, 24, stroke=0, fill=1)
    c.setFillColor(MUTED)
    c.setFont(bold, 8.5)
    x = left + 8
    for title, w, align in cols:
        if align == "l":
            c.drawString(x, y, title)
        else:
            c.drawRightString(x + w - 16, y, title)
        x += w
    name_lines = simpleSplit(doc["product_name"], regular, 10.5, cols[0][1] - 12)[:3]
    row_h = 14 * len(name_lines) + 14
    y -= 30
    c.setFillColor(INK)
    c.setFont(regular, 10.5)
    for i, line in enumerate(name_lines):
        c.drawString(left + 8, y - 14 * i, line)
    values = [str(doc["quantity"]), money(doc["list_price"]),
              f"{fmt_pct(doc['discount'])}%" if doc["discount"] > 0 else "-",
              money(doc["unit_price"]), money(doc["total"])]
    x = left + 8 + cols[0][1]
    for (title, w, _), value in zip(cols[1:], values):
        c.setFont(bold if title == "Amount" else regular, 10.5)
        c.drawRightString(x + w - 16, y, value)
        x += w
    y -= row_h - 10
    c.setStrokeColor(LINE)
    c.line(left, y, right, y)

    # totals
    y -= 34
    c.setFillColor(MUTED)
    c.setFont(regular, 10)
    c.drawRightString(right - 150, y, "Total")
    c.setFillColor(INK)
    c.setFont(bold, 17)
    c.drawRightString(right - 8, y - 2, money(doc["total"]))
    if doc["savings"] > 0:
        y -= 22
        c.setFillColor(GOOD)
        c.setFont(bold, 10.5)
        c.drawRightString(right - 8, y, f"You save {money(doc['savings'])} ({fmt_pct(doc['discount'])}% off)")

    # terms
    y -= 48
    c.setFillColor(SOFT)
    c.roundRect(left, y - 46, right - left, 62, 8, stroke=0, fill=1)
    c.setFillColor(INK)
    c.setFont(bold, 9.5)
    c.drawString(left + 14, y, "Terms")
    c.setFillColor(MUTED)
    c.setFont(regular, 9)
    terms = [f"This quote is valid until {_when(doc['valid_until'])}. Prices are in Indian rupees.",
             "To order, use “Place order” on the store page or in the Telegram chat."]
    for i, line in enumerate(terms):
        c.drawString(left + 14, y - 16 - 13 * i, line)

    # verification code + QR code of the Verify quote link (quotes made before codes existed have none)
    if doc.get("verify_code") and doc.get("verify_url"):
        qr = 92
        top = y - 66
        c.setStrokeColor(LINE)
        c.roundRect(left, top - qr - 12, right - left, qr + 24, 8, stroke=1, fill=0)
        _draw_qr(c, doc["verify_url"], right - qr - 10, top - qr - 6, qr)
        c.setFillColor(MUTED)
        c.setFont(bold, 7.5)
        c.drawString(left + 14, top - 14, "VERIFY THIS QUOTE")
        c.setFillColor(INK)
        c.setFont(bold, 17)
        c.drawString(left + 14, top - 38, doc["verify_code"])
        c.setFillColor(MUTED)
        c.setFont(regular, 9)
        c.drawString(left + 14, top - 58, "Scan the QR code or open this link to check that the quote is genuine:")
        c.setFillColor(ACCENT)
        c.setFont(regular, 8)
        c.drawString(left + 14, top - 72, doc["verify_url"])
        c.linkURL(doc["verify_url"], (left + 14, top - 75, right - qr - 20, top - 64), relative=0)
        c.setFillColor(MUTED)
        c.setFont(regular, 8.5)
        c.drawString(left + 14, top - 90, "A changed price, quantity or date will not match this code.")

    # footer
    c.setStrokeColor(LINE)
    c.line(left, 64, right, 64)
    c.setFillColor(MUTED)
    c.setFont(regular, 8.5)
    c.drawString(left, 48, f"{PRODUCT_NAME} · {TAGLINE}")
    c.drawRightString(right, 48, f"{doc['quote_ref']} · page 1 of 1")
    c.showPage()
    c.save()
    return buf.getvalue()
