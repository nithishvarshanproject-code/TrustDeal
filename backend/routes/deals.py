"""Deal routes. No business rules: decisions come from engine.bridge (MeTTa)."""
import json
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Deal, Decision, Outcome, Override, Product, Seller
from backend.services import ledger
from backend.services.conflicts import build_deal_input
from backend.services.explain import explain
from backend.validation import MAX_NAME, MAX_QUANTITY, MAX_REASON, normalize_tier
from engine.bridge import (category_profile, evaluate_deal, runner_name, seller_trust, update_trust,
                           what_if_for)

router = APIRouter(prefix="/deals", tags=["deals"])

REQUIRED_FIELDS = ("seller_id", "product_id", "quantity", "discount_requested")


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)   # Infinity / NaN -> 422

    seller_id: int | None = None
    product_id: int | None = None
    quantity: int | None = Field(default=None, ge=1, le=MAX_QUANTITY)
    discount_requested: float | None = Field(default=None, ge=0, le=100)
    claimed_tier: str | None = Field(default=None, max_length=32)
    competitor_price: float | None = Field(default=None, gt=0)
    competitor_verified: bool = False
    raw_text: str | None = Field(default=None, max_length=2000)

    @field_validator("claimed_tier")
    @classmethod
    def _tier(cls, value: str | None) -> str | None:
        return normalize_tier(value)


def _decision_payload(decision: Decision) -> dict:
    audit = json.loads(decision.audit_json)
    return {
        "decision_id": decision.id,
        "result": decision.result,
        "approved_discount": decision.approved_discount,
        "confidence": decision.confidence,
        "trail": audit["trail"],
        "override_hint": audit["override_hint"],
        "what_if": audit.get("what_if"),  # None until GET /deals/{id}/what-if
        "engine": audit.get("engine"),
        "category": audit.get("category"),  # floor and max MeTTa used for this product's category
        "explanation": audit["explanation"],
        "created_at": decision.created_at.isoformat(),
    }


@router.post("/evaluate")
def evaluate(req: EvaluateRequest, db: Session = Depends(get_db)) -> dict:
    if req.raw_text is not None:
        raise HTTPException(501, "free-text input not available yet")
    missing = [f for f in REQUIRED_FIELDS if getattr(req, f) is None]
    if missing:
        raise HTTPException(422, f"missing fields: {', '.join(missing)}")

    seller = db.get(Seller, req.seller_id)
    if seller is None:
        raise HTTPException(404, f"seller {req.seller_id} not found")
    product = db.get(Product, req.product_id)
    if product is None:
        raise HTTPException(404, f"product {req.product_id} not found")

    deal = Deal(
        seller_id=seller.id,
        product_id=product.id,
        quantity=req.quantity,
        discount_requested=req.discount_requested,
        claimed_tier=req.claimed_tier,
        competitor_price=req.competitor_price,
        competitor_verified=req.competitor_verified,
    )
    # Built before the deal is added, so the R7 count covers earlier requests only.
    deal_input = build_deal_input(db, deal, seller, product)
    try:
        # What-if options are slow (they re-run the rules), so they are computed on demand.
        started = time.perf_counter()
        verdict = evaluate_deal(deal_input, include_what_if=False)
        elapsed = time.perf_counter() - started
        profile = category_profile(product.category)
    except ValueError as exc:  # e.g. an invalid tier symbol
        raise HTTPException(422, str(exc)) from exc

    db.add(deal)
    db.flush()
    decision = Decision(
        deal_id=deal.id,
        result=verdict["result"],
        approved_discount=verdict["approved_discount"],
        confidence=verdict["confidence"],
        audit_json=json.dumps({
            "input": deal_input,  # exact engine input, reused by GET /deals/{id}/what-if
            "engine": {"runner": runner_name(), "elapsed_s": round(elapsed, 3)},
            "category": profile,
            "trail": verdict["trail"],
            "override_hint": verdict["override_hint"],
            "what_if": None,
            "explanation": explain(verdict["trail"], verdict["override_hint"]),
        }),
    )
    db.add(decision)
    db.flush()
    ledger.record_decision(db, source="deal", decision_id=decision.id, case_id=deal.id, party=seller.name,
                           product=product.name, deal_input=deal_input, verdict=verdict, runner=runner_name())
    db.commit()
    return {"deal_id": deal.id, **_decision_payload(decision)}


def _deal_payload(deal: Deal, db: Session) -> dict:
    seller = db.get(Seller, deal.seller_id)
    product = db.get(Product, deal.product_id)
    decision = db.scalar(select(Decision).where(Decision.deal_id == deal.id))
    overrides = []
    if decision is not None:
        overrides = db.scalars(
            select(Override).where(Override.decision_id == decision.id).order_by(Override.id)
        ).all()
    return {
        "deal_id": deal.id,
        "seller": {"id": seller.id, "name": seller.name, "tier": seller.tier},
        "product": {"id": product.id, "name": product.name},
        "quantity": deal.quantity,
        "discount_requested": deal.discount_requested,
        "claimed_tier": deal.claimed_tier,
        "competitor_price": deal.competitor_price,
        "competitor_verified": deal.competitor_verified,
        "created_at": deal.created_at.isoformat(),
        "decision": _decision_payload(decision) if decision is not None else None,
        "overrides": [
            {"override_id": o.id, "reviewer": o.reviewer, "new_result": o.new_result,
             "reason": o.reason, "created_at": o.created_at.isoformat()}
            for o in overrides
        ],
    }


# Must be declared before /{deal_id}, or "history" would be parsed as a deal id.
@router.get("/history")
def history(limit: int = 50, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(Deal, Seller.name, Decision)
        .join(Seller, Seller.id == Deal.seller_id)
        .outerjoin(Decision, Decision.deal_id == Deal.id)
        .order_by(Deal.created_at.desc(), Deal.id.desc())
        .limit(min(max(limit, 1), 200))
    ).all()
    return [
        {
            "deal_id": deal.id,
            "seller_name": seller_name,
            "quantity": deal.quantity,
            "discount_requested": deal.discount_requested,
            "created_at": deal.created_at.isoformat(),
            "result": decision.result if decision else None,
            "approved_discount": decision.approved_discount if decision else None,
            "confidence": decision.confidence if decision else None,
        }
        for deal, seller_name, decision in rows
    ]


@router.get("/{deal_id}")
def get_deal(deal_id: int, db: Session = Depends(get_db)) -> dict:
    deal = db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, f"deal {deal_id} not found")
    return _deal_payload(deal, db)


class OverrideRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=MAX_NAME)
    new_result: Literal["APPROVE", "REJECT", "COUNTER", "ESCALATE"]
    reason: str = Field(min_length=1, max_length=MAX_REASON)


@router.post("/{deal_id}/override", status_code=201)
def override(deal_id: int, req: OverrideRequest, db: Session = Depends(get_db)) -> dict:
    """Record a human reviewer's decision. The original Decision row is never modified."""
    if not req.reviewer.strip() or not req.reason.strip():
        raise HTTPException(422, "reviewer and reason must not be blank")
    decision = db.scalar(select(Decision).where(Decision.deal_id == deal_id))
    if decision is None:
        raise HTTPException(404, f"no decision found for deal {deal_id}")
    row = Override(
        decision_id=decision.id,
        reviewer=req.reviewer.strip(),
        new_result=req.new_result,
        reason=req.reason.strip(),
    )
    db.add(row)
    db.flush()
    ledger.append(db, "override", f"override:{row.id}", {
        "override_id": row.id, "decision_id": decision.id, "deal_id": deal_id,
        "original_result": decision.result, "new_result": row.new_result,
        "reviewer": row.reviewer, "reason": row.reason})
    db.commit()
    return {
        "override_id": row.id,
        "deal_id": deal_id,
        "decision_id": decision.id,
        "original_result": decision.result,
        "new_result": row.new_result,
        "reviewer": row.reviewer,
        "reason": row.reason,
        "created_at": row.created_at.isoformat(),
    }


def _decision_for(db: Session, deal_id: int) -> Decision:
    decision = db.scalar(select(Decision).where(Decision.deal_id == deal_id))
    if decision is None:
        raise HTTPException(404, f"no decision found for deal {deal_id}")
    return decision


@router.get("/{deal_id}/what-if")
def what_if(deal_id: int, db: Session = Depends(get_db)) -> dict:
    """D1 options, computed by MeTTa on first request and stored with the decision.
    Only the what-if part of audit_json is written; result/discount/confidence never change."""
    decision = _decision_for(db, deal_id)
    audit = json.loads(decision.audit_json)
    if audit.get("what_if") is None:
        deal_input = audit.get("input")
        if deal_input is None:  # decisions saved before inputs were stored
            deal = db.get(Deal, deal_id)
            deal_input = build_deal_input(
                db, deal, db.get(Seller, deal.seller_id), db.get(Product, deal.product_id))
        options = what_if_for(deal_input)
        audit["what_if"] = options
        audit["what_if_computed_at"] = datetime.now(timezone.utc).isoformat()
        audit["explanation"] = explain(audit["trail"], audit["override_hint"], options)
        decision.audit_json = json.dumps(audit)
        db.commit()
    return {
        "deal_id": deal_id,
        "result": decision.result,
        "what_if": audit["what_if"],
        "explanation": audit["explanation"]["what_if"],
        "computed_at": audit.get("what_if_computed_at"),
    }


class OutcomeRequest(BaseModel):
    paid_on_time: bool


def _effective_result(db: Session, decision: Decision) -> str:
    """Latest human override if there is one, else the engine's result."""
    latest = db.scalar(
        select(Override).where(Override.decision_id == decision.id).order_by(Override.id.desc())
    )
    return latest.new_result if latest else decision.result


@router.post("/{deal_id}/outcome", status_code=201)
def record_outcome(deal_id: int, req: OutcomeRequest, db: Session = Depends(get_db)) -> dict:
    """D3: record how a sold deal was paid, update the seller's history, and return the
    trust stv before and after (both computed by MeTTa)."""
    decision = _decision_for(db, deal_id)
    effective = _effective_result(db, decision)
    if effective not in ("APPROVE", "COUNTER"):
        raise HTTPException(409, f"deal {deal_id} ended as {effective}: no sale to record")
    if db.scalar(select(Outcome).where(Outcome.deal_id == deal_id)) is not None:
        raise HTTPException(409, f"outcome for deal {deal_id} already recorded")

    seller = db.get(Seller, db.get(Deal, deal_id).seller_id)
    before = seller_trust(seller.late_payments, seller.total_orders)
    after = update_trust(before, "on-time" if req.paid_on_time else "late")

    seller.total_orders = (seller.total_orders or 0) + 1
    seller.late_payments = (seller.late_payments or 0) + (0 if req.paid_on_time else 1)
    db.add(Outcome(deal_id=deal_id, paid_on_time=req.paid_on_time))
    db.commit()
    return {
        "deal_id": deal_id,
        "seller_id": seller.id,
        "effective_result": effective,
        "paid_on_time": req.paid_on_time,
        "total_orders": seller.total_orders,
        "late_payments": seller.late_payments,
        "trust_before": before,
        "trust_after": after,
    }
