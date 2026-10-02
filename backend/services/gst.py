"""GST for the TAX INVOICE: settings from backend/config/gst.json (DEALDESK_GST_CONFIG in tests) and the
tax split inside a GST-inclusive total.

Accounting arithmetic only, not a pricing decision: the total is MeTTa's quote total and is never
changed. CGST and SGST are worked out inside it (Decimal, rounded half-up to 2 decimals):
    CGST = round(total x cgst_rate / (100 + cgst_rate + sgst_rate)), SGST likewise,
    taxable value = total - CGST - SGST,
so the taxable value absorbs the last paisa: the invoice total equals the quote total exactly.
"""
import json
import os
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "gst.json"
CATEGORIES = ("mobiles", "laptops", "accessories", "general")
_GSTIN_RE = re.compile(r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]")
_HSN_RE = re.compile(r"\d{4,8}")
CENT = Decimal("0.01")


class GstConfigError(RuntimeError):
    """backend/config/gst.json is missing a value or has an invalid one (a server error: the order is
    rolled back rather than placed without an invoice)."""


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def config_file() -> Path:
    return Path(os.environ.get("DEALDESK_GST_CONFIG") or DEFAULT_CONFIG)


def load_config() -> dict:
    """The validated GST settings (read on every invoice, so edits apply to the next one)."""
    path = config_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GstConfigError(f"cannot read GST settings ({path.name}): {type(exc).__name__}") from None
    try:
        cfg = {"seller_name": str(raw["seller_name"]).strip(), "seller_gstin": str(raw["seller_gstin"]).strip(),
               "seller_gstin_is_demo": bool(raw.get("seller_gstin_is_demo", True)),
               "place_of_supply": str(raw["place_of_supply"]).strip(),
               "cgst_rate": Decimal(str(raw["cgst_rate"])), "sgst_rate": Decimal(str(raw["sgst_rate"])),
               "hsn_by_category": {k: str(v) for k, v in dict(raw["hsn_by_category"]).items()}}
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise GstConfigError(f"GST settings ({path.name}) are incomplete: {type(exc).__name__} {exc}") from None
    if not cfg["seller_name"] or not cfg["place_of_supply"]:
        raise GstConfigError("GST settings need a seller name and a place of supply")
    if not _GSTIN_RE.fullmatch(cfg["seller_gstin"]):
        raise GstConfigError("seller_gstin is not a 15-character GSTIN")
    for name in ("cgst_rate", "sgst_rate"):
        if not (cfg[name].is_finite() and Decimal(0) <= cfg[name] <= Decimal(50)):
            raise GstConfigError(f"{name} must be between 0 and 50")
    missing = [c for c in CATEGORIES if c not in cfg["hsn_by_category"]]
    bad = [c for c, code in cfg["hsn_by_category"].items() if not _HSN_RE.fullmatch(code)]
    if missing or bad:
        raise GstConfigError(f"HSN codes: missing {missing or 'none'}, not 4-8 digits {bad or 'none'}")
    return cfg


def hsn_for(category: str, cfg: dict) -> str:
    return cfg["hsn_by_category"].get(category) or cfg["hsn_by_category"]["general"]


def split_inclusive(total, cgst_rate, sgst_rate) -> dict:
    """{'taxable', 'cgst', 'sgst', 'total'} as Decimals with 2 decimals; taxable + cgst + sgst == total."""
    total = _money(total)
    cgst_rate, sgst_rate = Decimal(str(cgst_rate)), Decimal(str(sgst_rate))
    divisor = Decimal(100) + cgst_rate + sgst_rate
    cgst = (total * cgst_rate / divisor).quantize(CENT, rounding=ROUND_HALF_UP)
    sgst = (total * sgst_rate / divisor).quantize(CENT, rounding=ROUND_HALF_UP)
    return {"taxable": total - cgst - sgst, "cgst": cgst, "sgst": sgst, "total": total}
