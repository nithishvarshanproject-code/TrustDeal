"""Data gathering only: build the full deal dict that engine.bridge.evaluate_deal expects.

No business rules here. Conflicts (claimed vs record tier) are detected in rules.metta;
this module just puts both values side by side.
"""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import Deal, Product, Seller


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def count_requests_this_month(db: Session, seller_id: int, now: datetime | None = None) -> int:
    """Number of deals this seller already submitted since the 1st of the current month (UTC)."""
    start = _month_start(now or datetime.now(timezone.utc))
    return db.scalar(
        select(func.count(Deal.id)).where(Deal.seller_id == seller_id, Deal.created_at >= start)
    )


def build_deal_input(db: Session, deal: Deal, seller: Seller, product: Product) -> dict:
    """Join a (not yet saved) deal with its seller and product records.

    Call this BEFORE adding `deal` to the session, so requests_this_month counts
    only earlier requests.
    """
    return {
        "record_tier": seller.tier,
        "claimed_tier": deal.claimed_tier,
        "cost_price": product.cost_price,
        "list_price": product.list_price,
        "quantity": deal.quantity,
        "discount_requested": deal.discount_requested,
        "late_payments": seller.late_payments,
        "total_orders": seller.total_orders,
        "competitor_price": deal.competitor_price,
        "competitor_verified": deal.competitor_verified,
        "requests_this_month": count_requests_this_month(db, seller.id),
        "seller_name": seller.name,
        "category": product.category,
    }
