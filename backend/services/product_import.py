"""CSV product import: parse and validate rows (data validation only; no business rules).

Columns: name, category, cost_price, list_price (header required, any order, extra columns
ignored). Each row is checked on its own and gets its own error list; only valid rows are
imported. The category's margin floor and max discount stay in policy.metta.
"""
import csv
import io

from backend.models import CATEGORIES

REQUIRED_COLUMNS = ("name", "category", "cost_price", "list_price")
MAX_ROWS = 500
MAX_NAME = 80
MAX_CSV_BYTES = 1024 * 1024       # 1 MB of UTF-8


def _number(raw: str):
    """Parse a price like 1500, 1500.50, "1,500" or "\u20b91,10,000"; None if not a number."""
    text = (raw or "").strip().replace("\u20b9", "").replace(",", "").replace(" ", "")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def parse_csv(text: str, existing_names: set[str]) -> dict:
    """Validate a CSV text. Returns {"file_errors": [...], "rows": [...], "valid": n, "invalid": n}.
    Each row: {"line", "name", "category", "cost_price", "list_price", "errors": [...]}."""
    text = (text or "").lstrip("\ufeff")          # Excel adds a BOM
    if not text.strip():
        return _file_error("The file is empty.")
    # The page decodes the file strictly as UTF-8; U+FFFD (a replaced invalid byte) or NUL
    # means the text did not come from a valid UTF-8 text file.
    if "\ufffd" in text or "\x00" in text:
        return _file_error("The file is not UTF-8 text. Save it as CSV UTF-8 and try again.")
    try:
        return _parse_rows(text, existing_names)
    except csv.Error as exc:
        return _file_error(f"The file is not a valid CSV ({exc}).")


def _file_error(message: str) -> dict:
    return {"file_errors": [message], "rows": [], "valid": 0, "invalid": 0}


def _parse_rows(text: str, existing_names: set[str]) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        return {"file_errors": [f"Missing column(s): {', '.join(missing)}. "
                                f"Expected: {', '.join(REQUIRED_COLUMNS)}."],
                "rows": [], "valid": 0, "invalid": 0}
    reader.fieldnames = header

    known = {n.strip().lower() for n in existing_names}
    seen: set[str] = set()
    rows = []
    for index, raw in enumerate(reader):
        line = index + 2                          # line 1 is the header
        if index >= MAX_ROWS:
            return {"file_errors": [f"Too many rows: the limit is {MAX_ROWS}."],
                    "rows": [], "valid": 0, "invalid": 0}
        if not any((v or "").strip() for v in raw.values() if isinstance(v, str)):
            continue                              # skip completely blank lines

        errors = []
        name = (raw.get("name") or "").strip()
        if not name:
            errors.append("name is empty")
        elif len(name) > MAX_NAME:
            errors.append(f"name is longer than {MAX_NAME} characters")
        elif name.lower() in known:
            errors.append("a product with this name already exists")
        elif name.lower() in seen:
            errors.append("duplicate name in this file")

        category = (raw.get("category") or "").strip().lower() or "general"
        if category not in CATEGORIES:
            errors.append(f"unknown category '{category}' (use {', '.join(CATEGORIES)})")

        cost = _number(raw.get("cost_price"))
        list_price = _number(raw.get("list_price"))
        if cost is None:
            errors.append("cost_price is not a number")
        elif cost <= 0:
            errors.append("cost_price must be positive")
        if list_price is None:
            errors.append("list_price is not a number")
        elif list_price <= 0:
            errors.append("list_price must be positive")
        if cost is not None and list_price is not None and cost > 0 and list_price > 0 and list_price <= cost:
            errors.append("list_price must be higher than cost_price")

        if name:
            seen.add(name.lower())
        rows.append({"line": line, "name": name, "category": category,
                     "cost_price": cost, "list_price": list_price, "errors": errors})

    valid = sum(1 for r in rows if not r["errors"])
    file_errors = [] if rows else ["The file has a header but no product rows."]
    return {"file_errors": file_errors, "rows": rows, "valid": valid, "invalid": len(rows) - valid}
