"""Seller-side business metrics, calculated from stored decisions and agent records."""
import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import (AgentDeal, AgentDecision, AgentQuote, AgentTask, Customer, Deal, Decision, Override,
                            Product)
from engine.bridge import seller_trust

router = APIRouter(prefix="/stats", tags=["seller-stats"])
RESULTS = ("APPROVE", "COUNTER", "REJECT", "ESCALATE")


def _latest_by(rows, key):
    latest = {}
    for row in rows:
        latest.setdefault(key(row), row)
    return latest


def _trail_has_r1_failure(audit_json: str | None) -> bool:
    if not audit_json:
        return False
    try:
        trail = json.loads(audit_json).get("trail", [])
    except (TypeError, ValueError):
        return False
    return any(line.get("rule_id") == "R1" and line.get("status") == "fail" for line in trail)


def _discount_value(product: Product, quantity: int, percent: float) -> float:
    return product.list_price * quantity * percent / 100


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    """Summarize latest seller decisions and latest customer-agent decision per request."""
    decisions = db.scalars(select(Decision).order_by(Decision.id.desc())).all()
    latest_decisions = _latest_by(decisions, lambda row: row.deal_id)
    overrides = db.scalars(select(Override).order_by(Override.id.desc())).all()
    latest_overrides = _latest_by(overrides, lambda row: row.decision_id)
    agent_decisions = db.scalars(select(AgentDecision).order_by(AgentDecision.id.desc())).all()
    latest_agent_decisions = _latest_by(agent_decisions, lambda row: row.agent_deal_id)

    deals_handled = {"deal_check": 0, "web_chat": 0, "telegram": 0}
    results = dict.fromkeys(RESULTS, 0)
    confidence_values = []
    requested_total = 0.0
    offered_total = 0.0
    money_rows = 0
    below_cost_blocked = 0
    pending_escalation_ids = set()
    total_escalations = 0
    resolved_escalations = 0
    counters_without_amount = 0
    overridden_counters_without_amount = 0

    # A seller Deal check is one stored deal with its latest MeTTa decision.
    for decision in latest_decisions.values():
        deal = db.get(Deal, decision.deal_id)
        if deal is None:
            continue
        product = db.get(Product, deal.product_id)
        if product is None:
            continue
        deals_handled["deal_check"] += 1
        override = latest_overrides.get(decision.id)
        result = override.new_result if override else decision.result
        if result in results:
            results[result] += 1
        if decision.confidence is not None:
            confidence_values.append(decision.confidence)
        if result == "REJECT" and _trail_has_r1_failure(decision.audit_json):
            below_cost_blocked += 1

        if decision.result == "ESCALATE":
            total_escalations += 1
            if override and override.new_result != "ESCALATE":
                resolved_escalations += 1
            else:
                pending_escalation_ids.add(("deal_check", deal.id))

        if result == "ESCALATE":
            continue
        requested_pct = deal.discount_requested
        requested_value = _discount_value(product, deal.quantity, requested_pct)
        if result == "APPROVE":
            offered_pct = requested_pct
        elif result == "REJECT":
            offered_pct = 0.0
        elif result == "COUNTER":
            # A MeTTa counter has a stored percentage. A human override has no amount
            # field, so only reuse the engine percentage when the engine also countered.
            offered_pct = (decision.approved_discount
                           if decision.result == "COUNTER" and decision.approved_discount is not None else None)
            if offered_pct is None:
                counters_without_amount += 1
                if override and override.new_result == "COUNTER":
                    overridden_counters_without_amount += 1
                continue
        else:
            continue
        requested_total += requested_value
        offered_total += _discount_value(product, deal.quantity, offered_pct)
        money_rows += 1

    agent_deals = db.scalars(select(AgentDeal).order_by(AgentDeal.id)).all()
    agent_tasks = db.scalars(select(AgentTask).order_by(AgentTask.id)).all()
    latest_escalation_task = _latest_by(
        [task for task in agent_tasks if task.kind == "escalation"], lambda task: task.agent_deal_id)
    for deal in agent_deals:
        deals_handled["telegram" if deal.channel == "telegram" else "web_chat"] += 1
        decision = latest_agent_decisions.get(deal.id)
        if decision is None:
            continue
        product = db.get(Product, deal.product_id)
        if product is None:
            continue
        if decision.confidence is not None:
            confidence_values.append(decision.confidence)
        result = {"manager-approved": "APPROVE", "manager-rejected": "REJECT"}.get(decision.event,
                                                                                  decision.result)
        if result in results:
            results[result] += 1
        if result == "REJECT" and _trail_has_r1_failure(decision.audit_json):
            below_cost_blocked += 1
        if decision.result == "ESCALATE":
            task = latest_escalation_task.get(deal.id)
            if decision.event not in ("manager-approved", "manager-rejected") and (task is None or task.status == "open"):
                pending_escalation_ids.add(("agent", deal.id))

        asked = deal.discount_asked
        if asked is None:
            try:
                asked = json.loads(decision.audit_json).get("input", {}).get("discount_requested")
            except (TypeError, ValueError, AttributeError):
                asked = None
        if asked is None:
            continue
        if result == "ESCALATE":
            continue
        requested_value = _discount_value(product, deal.quantity, asked)
        if decision.event == "manager-approved":
            offered_pct = decision.approved_discount if decision.approved_discount is not None else asked
        elif decision.event == "manager-rejected" or result == "REJECT":
            offered_pct = 0.0
        elif result == "APPROVE":
            offered_pct = asked
        elif result == "COUNTER":
            offered_pct = decision.approved_discount
            if offered_pct is None:
                counters_without_amount += 1
                continue
        else:
            continue
        requested_total += requested_value
        offered_total += _discount_value(product, deal.quantity, offered_pct)
        money_rows += 1

    # Task totals include all customer-agent human reviews; Deal check escalations are
    # counted from the decision/override records above.
    escalations = [task for task in agent_tasks if task.kind == "escalation"]
    verifications = [task for task in agent_tasks if task.kind == "verification"]
    total_escalations += len(escalations)
    resolved_escalations += sum(task.status == "done" for task in escalations)
    for task in escalations:
        if task.status == "open":
            pending_escalation_ids.add(("agent", task.agent_deal_id))
    pending = len(pending_escalation_ids)

    quotes = db.scalars(select(AgentQuote)).all()
    ordered_quotes = [quote for quote in quotes if quote.status == "ordered"]
    quote_count = len(quotes)
    customers = db.scalars(select(Customer)).all()
    ranked_customers = []
    for customer in customers:
        trust = seller_trust(customer.late_payments, customer.total_orders)
        ranked_customers.append({"customer_id": customer.id, "name": customer.name,
                                 "strength": trust["strength"], "confidence": trust["confidence"],
                                 "score": trust["strength"] * trust["confidence"]})
    ranked_customers.sort(key=lambda row: (-row["score"], row["customer_id"]))

    return {
        "deals_handled": {"total": sum(deals_handled.values()), "by_channel": deals_handled},
        "results": results,
        "discounts": {
            "requested": round(requested_total, 2), "offered": round(offered_total, 2),
            "avoided": round(requested_total - offered_total, 2), "decided_deals": money_rows,
            "discount_actually_given_on_orders": round(sum(q.savings for q in ordered_quotes), 2),
            "pending_escalations_excluded": len(pending_escalation_ids),
            "counters_without_amount_excluded": counters_without_amount,
            "overridden_counters_without_amount_excluded": overridden_counters_without_amount,
        },
        "below_cost_blocked": below_cost_blocked,
        "escalations": {"total": total_escalations, "resolved": resolved_escalations,
                        "pending": pending},
        "verifications": {"total": len(verifications),
                          "resolved": sum(task.status == "done" for task in verifications),
                          "pending": sum(task.status == "open" for task in verifications)},
        "quotes": {"total": quote_count, "ordered": len(ordered_quotes),
                   "conversion_rate": len(ordered_quotes) / quote_count if quote_count else 0.0},
        "average_confidence": (sum(confidence_values) / len(confidence_values) if confidence_values else None),
        "top_customers_by_trust": ranked_customers[:3],
    }
