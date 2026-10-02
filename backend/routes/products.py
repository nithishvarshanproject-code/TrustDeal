"""Products (Deal Desk form and Products page). No business rules: the category's margin floor
and max discount live in policy.metta and are applied by MeTTa."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Product
from backend.services.product_import import MAX_CSV_BYTES, parse_csv
from backend.validation import MAX_NAME

router = APIRouter(prefix="/products", tags=["products"])


def _payload(p: Product) -> dict:
    return {"product_id": p.id, "name": p.name, "category": p.category,
            "cost_price": p.cost_price, "list_price": p.list_price}


@router.get("")
def list_products(db: Session = Depends(get_db)) -> list[dict]:
    return [_payload(p) for p in db.scalars(select(Product).order_by(Product.id))]


class NewProduct(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)   # Infinity / NaN -> 422

    name: str = Field(min_length=1, max_length=MAX_NAME)
    category: Literal["mobiles", "laptops", "accessories", "general"]
    cost_price: float = Field(gt=0)
    list_price: float = Field(gt=0)


@router.post("", status_code=201)
def add_product(req: NewProduct, db: Session = Depends(get_db)) -> dict:
    name = req.name.strip()
    if not name:
        raise HTTPException(422, "name must not be blank")
    if req.list_price <= req.cost_price:
        raise HTTPException(422, "list price must be higher than cost price")
    product = Product(name=name, category=req.category, cost_price=req.cost_price, list_price=req.list_price)
    db.add(product)
    db.commit()
    return _payload(product)


# ---------- CSV import (the page sends the file's text; the server validates every row) ----------

class CsvImport(BaseModel):
    csv: str = Field(max_length=MAX_CSV_BYTES)   # characters; bytes are checked below


def _checked_csv(req: CsvImport) -> str:
    if len(req.csv.encode("utf-8", "surrogatepass")) > MAX_CSV_BYTES:
        raise HTTPException(422, "The CSV file is larger than 1 MB.")
    return req.csv


def _existing_names(db: Session) -> set[str]:
    return set(db.scalars(select(Product.name)))


@router.post("/import/preview")
def import_preview(req: CsvImport, db: Session = Depends(get_db)) -> dict:
    """Validate a CSV without writing anything: one entry per row, with its errors."""
    return parse_csv(_checked_csv(req), _existing_names(db))


@router.post("/import")
def import_products(req: CsvImport, db: Session = Depends(get_db)) -> dict:
    """Re-validate the CSV and import ONLY the valid rows (invalid rows are reported, not imported)."""
    result = parse_csv(_checked_csv(req), _existing_names(db))
    imported = []
    if not result["file_errors"]:
        for row in (r for r in result["rows"] if not r["errors"]):
            product = Product(name=row["name"], category=row["category"],
                              cost_price=row["cost_price"], list_price=row["list_price"])
            db.add(product)
            imported.append(product)
        db.commit()
    return {**result, "imported": len(imported), "products": [_payload(p) for p in imported]}
