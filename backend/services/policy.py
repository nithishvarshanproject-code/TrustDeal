"""D2 data gathering: fetch overrides with their original deals and ask MeTTa for
policy proposals. No business rules here."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Deal, Decision, Override, Product, Seller
from backend.services.conflicts import build_deal_input
from engine.bridge import propose_policy_changes


def get_policy_proposals(db: Session) -> list[dict]:
    rows = db.execute(
        select(Override, Decision, Deal, Seller, Product)
        .join(Decision, Decision.id == Override.decision_id)
        .join(Deal, Deal.id == Decision.deal_id)
        .join(Seller, Seller.id == Deal.seller_id)
        .join(Product, Product.id == Deal.product_id)
        .order_by(Override.id)
    ).all()
    overrides = [
        {
            "id": o.id,
            "original_result": decision.result,
            "new_result": o.new_result,
            "reason": o.reason,
            "deal": build_deal_input(db, deal, seller, product),
        }
        for o, decision, deal, seller, product in rows
    ]
    return propose_policy_changes(overrides)
