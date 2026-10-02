"""Seller routes. Trust values are computed by MeTTa (history-stv)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Seller
from engine.bridge import seller_trust

router = APIRouter(prefix="/sellers", tags=["sellers"])


def _seller_payload(seller: Seller) -> dict:
    return {
        "seller_id": seller.id,
        "name": seller.name,
        "tier": seller.tier,
        "total_orders": seller.total_orders,
        "late_payments": seller.late_payments,
        "trust": seller_trust(seller.late_payments, seller.total_orders),
    }


@router.get("")
def list_sellers(db: Session = Depends(get_db)) -> list[dict]:
    return [_seller_payload(s) for s in db.scalars(select(Seller).order_by(Seller.id))]


@router.get("/{seller_id}/trust")
def trust(seller_id: int, db: Session = Depends(get_db)) -> dict:
    seller = db.get(Seller, seller_id)
    if seller is None:
        raise HTTPException(404, f"seller {seller_id} not found")
    return _seller_payload(seller)
