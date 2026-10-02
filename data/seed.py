"""Reset and seed the database with the sellers/products the 6 test deals need.

Run from the project root: python -m data.seed
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend import models
from backend.database import Base, engine as default_engine

# Product 1 is used by the 6 test deals: cost 70 / list 100, general (15% floor -> max ~17.6%).
# Products 2-9: a small INR catalog; each category has its own margin floor and max discount
# (policy.metta: margin-floor-for, category-max).
PRODUCTS = [
    {"id": 1, "name": "Industrial Widget", "category": "general", "cost_price": 70.0, "list_price": 100.0},
    {"id": 2, "name": "Smartphone A 128GB", "category": "mobiles", "cost_price": 16000.0, "list_price": 20000.0},
    {"id": 3, "name": "Smartphone Pro 256GB", "category": "mobiles", "cost_price": 46000.0, "list_price": 57000.0},
    {"id": 4, "name": "Laptop 14 i5 16GB", "category": "laptops", "cost_price": 44000.0, "list_price": 55000.0},
    {"id": 5, "name": "Laptop Pro 16", "category": "laptops", "cost_price": 85000.0, "list_price": 110000.0},
    {"id": 6, "name": "Fast Charger 65W", "category": "accessories", "cost_price": 700.0, "list_price": 1500.0},
    {"id": 7, "name": "Wireless Earbuds", "category": "accessories", "cost_price": 1200.0, "list_price": 2999.0},
    {"id": 8, "name": "Phone Case", "category": "accessories", "cost_price": 120.0, "list_price": 499.0},
    {"id": 9, "name": "Laptop Sleeve", "category": "accessories", "cost_price": 400.0, "list_price": 1199.0},
    # Product 10: a cheaper phone, so the agent can suggest a cheaper model (products 1-9 unchanged).
    {"id": 10, "name": "Smartphone Lite 64GB", "category": "mobiles", "cost_price": 9500.0, "list_price": 11999.0},
]

# Customers for the Customer page and the agent. The loyalty tier and order history play the
# role of the seller's record tier and payment history in the rules (R2, R3, trust, R7).
CUSTOMERS = [
    {"id": 1, "name": "Priya Sharma", "tier": "Gold", "late_payments": 0, "total_orders": 40},
    {"id": 2, "name": "Arjun Mehta", "tier": "Silver", "late_payments": 0, "total_orders": 25},
    {"id": 3, "name": "Neha Kapoor", "tier": "New", "late_payments": 0, "total_orders": 2},
    {"id": 4, "name": "Rahul Verma", "tier": "Silver", "late_payments": None, "total_orders": None},  # no history
]

SELLERS = [
    # Deal 1: Gold + clean history -> APPROVE 10%
    {"id": 1, "name": "Aurora Traders", "tier": "Gold", "late_payments": 0, "total_orders": 40},
    # Deal 2: New seller asking 35% -> below cost -> REJECT
    {"id": 2, "name": "Nova Startups", "tier": "New", "late_payments": 0, "total_orders": 2},
    # Deal 3: claims Gold, record Silver; 10% cap + 2% R3 -> COUNTER 12%
    {"id": 3, "name": "Meridian Supply", "tier": "Silver", "late_payments": 0, "total_orders": 25},
    # Deal 4: no payment history -> lower confidence
    {"id": 4, "name": "Blank Slate Co", "tier": "Silver", "late_payments": None, "total_orders": None},
    # Deal 5: unverified competitor quote -> ignored, logged
    {"id": 5, "name": "Echo Retail", "tier": "Silver", "late_payments": 1, "total_orders": 15},
    # Deal 6: already has 3 requests this month -> 4th triggers R7
    {"id": 6, "name": "Repeat Rex Ltd", "tier": "Gold", "late_payments": 0, "total_orders": 30},
]


def _prior_deals_for_repeat_seller() -> list[models.Deal]:
    now = datetime.now(timezone.utc)
    return [
        models.Deal(seller_id=6, product_id=1, quantity=20, discount_requested=5.0,
                    competitor_verified=False, raw_text=f"prior request {i}",
                    created_at=now - timedelta(minutes=i))
        for i in (1, 2, 3)
    ]


def seed(engine: Engine = default_engine) -> None:
    """Drop, recreate and fill all tables except the audit ledger, which is append-only and kept
    (a demo reset appends a demo_reset entry). Pass `engine` to seed another DB (tests)."""
    Base.metadata.drop_all(bind=engine, tables=[t for t in Base.metadata.sorted_tables
                                                if t.name != models.LedgerEntry.__tablename__])
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        db.add_all(models.Product(**p) for p in PRODUCTS)
        db.add_all(models.Seller(**s) for s in SELLERS)
        db.add_all(models.Customer(**c) for c in CUSTOMERS)
        db.flush()
        db.add_all(_prior_deals_for_repeat_seller())
        db.commit()
    print(f"Seeded {len(SELLERS)} sellers, {len(PRODUCTS)} product(s), {len(CUSTOMERS)} customers, 3 prior deals.")


if __name__ == "__main__":
    seed()
