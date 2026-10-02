"""Serializers. CUSTOMER views are built field by field from allowlists: a customer never sees a
cost price, a margin, a rule ID, a confidence, a trust value, an audit trail, market evidence,
tool logs, drafts, or another customer's data. The same views feed the Telegram channel and the
quote PDF (customer_quote_document). SELLER views show everything."""
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.loop import invoice_ref, quote_ref
from backend.models import (AgentActivity, AgentDeal, AgentDecision, AgentInvoice, AgentMessage, AgentQuote, AgentTask,
                            Customer, Product)
from backend.services import quote_seal
from engine.bridge import seller_trust

CARD_KEYS = {
    "offer": ("type", "status", "product_name", "quantity", "list_price", "discount", "unit_price", "total",
              "savings", "reason", "alternatives"),
    "quote": ("type", "quote_ref", "product_name", "quantity", "list_price", "discount", "unit_price", "total",
              "savings", "valid_hours", "valid_until"),
    "order": ("type", "order_ref", "quote_ref", "product_name", "quantity", "total"),
    "decline": ("type", "product_name"),
    "pending": ("type", "reason"),
}
ALTERNATIVE_KEYS = ("product_id", "product_name", "quantity", "discount", "unit_price", "total", "savings",
                    "price_difference", "compared_to", "kind")

STATUS_LABELS = {
    "NEW": "Started",
    "WAITING_CUSTOMER": "Offer waiting for you",
    "WAITING_VERIFICATION": "A manager is reviewing your request",
    "ESCALATED": "A manager is reviewing your request",
    "QUOTED": "Quote ready",
    "DECLINED": "Declined",
    "ORDERED": "Ordered",
    "CLOSED": "Closed",
}
ACTIONS = {"NEW": ["decline"], "WAITING_CUSTOMER": ["accept", "ask", "decline"], "QUOTED": ["order", "decline"]}
STORE_NAME = "TrustDeal · BASIX Store"


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


# ---------- customer ----------

def customer_card(card_json: str | None) -> dict | None:
    if not card_json:
        return None
    card = json.loads(card_json)
    keys = CARD_KEYS.get(card.get("type"))
    if not keys:
        return None
    safe = {k: card[k] for k in keys if k in card}
    if "alternatives" in safe:
        safe["alternatives"] = [{k: a[k] for k in ALTERNATIVE_KEYS if k in a} for a in safe["alternatives"]]
    return safe


def customer_product(p: Product) -> dict:
    return {"product_id": p.id, "name": p.name, "category": p.category, "list_price": p.list_price}


def _quote(db: Session, deal: AgentDeal) -> AgentQuote | None:
    return db.scalar(select(AgentQuote).where(AgentQuote.agent_deal_id == deal.id).order_by(AgentQuote.id.desc()))


def customer_status(db: Session, deal: AgentDeal) -> tuple[str, str]:
    quote = _quote(db, deal)
    if deal.state == "CLOSED" and quote is not None and quote.status == "ordered":
        return "ORDERED", STATUS_LABELS["ORDERED"]
    return deal.state, STATUS_LABELS.get(deal.state, deal.state)


def customer_request_summary(db: Session, deal: AgentDeal) -> dict:
    status, label = customer_status(db, deal)
    return {"request_id": deal.id, "product_name": db.get(Product, deal.product_id).name,
            "quantity": deal.quantity, "status": status, "status_label": label,
            "updated_at": _iso(deal.updated_at)}


def customer_request_view(db: Session, deal: AgentDeal) -> dict:
    messages = db.scalars(select(AgentMessage).where(AgentMessage.agent_deal_id == deal.id)
                          .order_by(AgentMessage.id)).all()
    sent = [m for m in messages if m.status == "sent"]
    status, label = customer_status(db, deal)
    offer = None
    if deal.state == "WAITING_CUSTOMER":
        last_offer = next((m for m in reversed(sent) if m.kind == "offer"), None)
        offer = customer_card(last_offer.card_json) if last_offer else None
    quote = _quote(db, deal)
    quote_view = None
    if quote is not None:
        quote_view = {"quote_ref": quote_ref(quote.id), "discount": quote.discount, "unit_price": quote.unit_price,
                      "total": quote.total, "savings": quote.savings, "quantity": quote.quantity,
                      "valid_until": _iso(quote.valid_until), "status": quote.status,
                      "order_ref": quote.order_ref, "ordered_at": _iso(quote.ordered_at),
                      "verify_code": quote_seal.format_code(quote.verify_code) if quote.verify_code else None,
                      "invoice_no": _invoice_no(db, quote)}
    pending_reply = bool(messages) and messages[-1].sender == "agent" and messages[-1].status == "draft"
    actions = [] if pending_reply else list(ACTIONS.get(deal.state, []))
    if offer and offer.get("alternatives") and "ask" in actions:
        actions.append("switch")
    return {
        "request_id": deal.id,
        "product": customer_product(db.get(Product, deal.product_id)),
        "quantity": deal.quantity,
        "status": status, "status_label": label,
        "pending_review": deal.state in ("ESCALATED", "WAITING_VERIFICATION"),
        "pending_reply": pending_reply,
        "messages": [{"message_id": m.id, "sender": m.sender, "text": m.text, "created_at": _iso(m.created_at),
                      "card": customer_card(m.card_json)} for m in sent],
        "offer": offer,
        "quote": quote_view,
        "actions": actions,
    }


def invoice_for(db: Session, quote: AgentQuote) -> AgentInvoice | None:
    return db.scalar(select(AgentInvoice).where(AgentInvoice.quote_id == quote.id))


def _invoice_no(db: Session, quote: AgentQuote) -> str | None:
    invoice = invoice_for(db, quote)
    return invoice_ref(invoice.id) if invoice else None


def customer_invoice_document(db: Session, deal: AgentDeal, quote: AgentQuote, invoice: AgentInvoice) -> dict:
    """Everything the TAX INVOICE PDF shows, and nothing else: the stored invoice (HSN, GST split inside
    the quote total), catalog names and the linked quote. Never a cost, margin, rule or confidence."""
    customer, product = db.get(Customer, deal.customer_id), db.get(Product, deal.product_id)
    return {"invoice_no": invoice_ref(invoice.id), "issued_at": invoice.issued_at,
            "seller_name": invoice.seller_name, "seller_gstin": invoice.seller_gstin,
            "gstin_is_demo": invoice.gstin_is_demo, "place_of_supply": invoice.place_of_supply,
            "customer_name": customer.name, "product_name": product.name, "hsn": invoice.hsn,
            "quantity": invoice.quantity, "unit_price": invoice.unit_price, "taxable_value": invoice.taxable_value,
            "cgst_rate": invoice.cgst_rate, "cgst": invoice.cgst, "sgst_rate": invoice.sgst_rate,
            "sgst": invoice.sgst, "total": invoice.total, "quote_ref": quote_ref(quote.id),
            "verify_code": quote_seal.format_code(quote.verify_code) if quote.verify_code else None,
            "order_ref": quote.order_ref}


def customer_quote_document(db: Session, deal: AgentDeal, quote: AgentQuote) -> dict:
    """Everything the quote PDF shows, and nothing else: catalog and MeTTa numbers only
    (list price, discount, unit price, total, savings), never a cost, margin or rule."""
    customer, product = db.get(Customer, deal.customer_id), db.get(Product, deal.product_id)
    ref = quote_ref(quote.id)
    return {"store": STORE_NAME, "quote_ref": ref, "customer_name": customer.name,
            "product_name": product.name, "quantity": quote.quantity,
            "list_price": quote.list_price if quote.list_price is not None else product.list_price,
            "discount": quote.discount, "unit_price": quote.unit_price, "total": quote.total,
            "savings": quote.savings, "issued_at": quote.created_at, "valid_until": quote.valid_until,
            "status": quote.status, "order_ref": quote.order_ref,
            "verify_code": quote_seal.format_code(quote.verify_code) if quote.verify_code else None,
            "verify_url": quote_seal.verify_url(ref, quote.verify_code) if quote.verify_code else None}


# ---------- Verify quote (public page) ----------

NOT_GENUINE = {"genuine": False,
               "message": "We couldn't verify this quote. Check the quote number and code exactly as printed, "
                          "or contact the store."}


def masked_name(name: str) -> str:
    """'Priya Sharma' -> 'Priya S.' (enough to match the PDF, not a full name on a public page)."""
    parts = name.split()
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."


def quote_verification_view(db: Session, deal: AgentDeal, quote: AgentQuote) -> dict:
    """A genuine quote, field by field: only the signed values (and its open / ordered / expired
    status), the customer's first name and initial. Never a cost, margin, rule or the code itself."""
    customer, product = db.get(Customer, deal.customer_id), db.get(Product, deal.product_id)
    valid_until = quote.valid_until if quote.valid_until.tzinfo else quote.valid_until.replace(tzinfo=timezone.utc)
    status = quote.status if quote.status in ("ordered", "expired") else "open"
    if status == "open" and valid_until <= datetime.now(timezone.utc):
        status = "expired"
    return {"genuine": True, "quote_ref": quote_ref(quote.id), "customer": masked_name(customer.name),
            "product_name": product.name, "quantity": quote.quantity, "list_price": quote.list_price,
            "discount": quote.discount, "unit_price": quote.unit_price, "total": quote.total,
            "savings": quote.savings, "valid_until": _iso(quote.valid_until), "status": status,
            "order_ref": quote.order_ref if status == "ordered" else None}


def customer_telegram_view(enabled: bool, bot_username: str | None, linked: bool) -> dict:
    """Telegram connection status for the Customer page (never a chat id)."""
    return {"enabled": enabled, "bot_username": bot_username if enabled else None, "linked": linked}


# ---------- seller ----------

def seller_deal_summary(db: Session, deal: AgentDeal) -> dict:
    c, p = db.get(Customer, deal.customer_id), db.get(Product, deal.product_id)
    open_tasks = db.scalars(select(AgentTask.id).where(AgentTask.agent_deal_id == deal.id,
                                                       AgentTask.status == "open")).all()
    drafts = db.scalars(select(AgentMessage.id).where(AgentMessage.agent_deal_id == deal.id,
                                                      AgentMessage.status == "draft")).all()
    return {"request_id": deal.id, "customer": {"customer_id": c.id, "name": c.name, "tier": c.tier},
            "product": {"product_id": p.id, "name": p.name, "category": p.category},
            "quantity": deal.quantity, "discount_asked": deal.discount_asked, "claimed_tier": deal.claimed_tier,
            "verified_tier": deal.verified_tier, "state": deal.state, "round": deal.round,
            "last_offer": deal.last_offer, "open_tasks": len(open_tasks), "drafts": len(drafts),
            "channel": deal.channel, "created_at": _iso(deal.created_at), "updated_at": _iso(deal.updated_at)}


def seller_task(db: Session, t: AgentTask) -> dict:
    deal = db.get(AgentDeal, t.agent_deal_id)
    return {"task_id": t.id, "request_id": t.agent_deal_id, "kind": t.kind, "status": t.status, "title": t.title,
            "answer": t.answer, "resolved_by": t.resolved_by, "decision_id": t.decision_id,
            "state": deal.state, "created_at": _iso(t.created_at), "resolved_at": _iso(t.resolved_at),
            "answers": ["approve", "reject"] if t.kind == "escalation" else ["verified", "not-verified"]}


def seller_activity(a: AgentActivity) -> dict:
    return {"activity_id": a.id, "request_id": a.agent_deal_id, "kind": a.kind, "summary": a.summary,
            "detail": json.loads(a.detail_json) if a.detail_json else None, "decision_id": a.decision_id,
            "created_at": _iso(a.created_at)}


def seller_deal_view(db: Session, deal: AgentDeal) -> dict:
    c = db.get(Customer, deal.customer_id)
    decisions = db.scalars(select(AgentDecision).where(AgentDecision.agent_deal_id == deal.id)
                           .order_by(AgentDecision.id)).all()
    messages = db.scalars(select(AgentMessage).where(AgentMessage.agent_deal_id == deal.id)
                          .order_by(AgentMessage.id)).all()
    activity = db.scalars(select(AgentActivity).where(AgentActivity.agent_deal_id == deal.id)
                          .order_by(AgentActivity.id)).all()
    tasks = db.scalars(select(AgentTask).where(AgentTask.agent_deal_id == deal.id).order_by(AgentTask.id)).all()
    quotes = db.scalars(select(AgentQuote).where(AgentQuote.agent_deal_id == deal.id).order_by(AgentQuote.id)).all()
    market = [seller_activity(a) for a in activity
              if a.kind == "tool_call" and a.summary.startswith("market_price_lookup")]
    # The agent action(s) MeTTa chose on each decision (engine/agent.metta), e.g. REJECT -> [A4] send-counter.
    actions: dict[int, list[dict]] = {}
    for a in activity:
        if a.kind == "next_action" and a.decision_id is not None and a.detail_json:
            d = json.loads(a.detail_json)
            actions.setdefault(a.decision_id, []).append(
                {"rule_id": d.get("rule_id"), "event": d.get("event"), "action": d.get("action"),
                 "from": d.get("from"), "to": d.get("to"), "offer": d.get("offer"),
                 "created_at": _iso(a.created_at)})
    return {
        **seller_deal_summary(db, deal),
        "customer_record": {"name": c.name, "tier": c.tier, "total_orders": c.total_orders,
                            "late_payments": c.late_payments,
                            "trust": seller_trust(c.late_payments, c.total_orders)},
        "decisions": [{"decision_id": d.id, "round": d.round, "event": d.event, "result": d.result,
                       "approved_discount": d.approved_discount, "confidence": d.confidence,
                       **{k: v for k, v in json.loads(d.audit_json).items() if k != "input"},
                       "input": json.loads(d.audit_json).get("input"),
                       "agent_actions": actions.get(d.id, []), "created_at": _iso(d.created_at)}
                      for d in decisions],
        "messages": [{"message_id": m.id, "sender": m.sender, "text": m.text, "status": m.status,
                      "source": m.source, "kind": m.kind, "decision_id": m.decision_id, "channel": m.channel,
                      "card": json.loads(m.card_json) if m.card_json else None, "created_at": _iso(m.created_at)}
                     for m in messages],
        "activity": [seller_activity(a) for a in activity],
        "tasks": [seller_task(db, t) for t in tasks],
        "quotes": [{"quote_ref": quote_ref(q.id), "discount": q.discount, "unit_price": q.unit_price,
                    "total": q.total, "savings": q.savings, "quantity": q.quantity, "status": q.status,
                    "order_ref": q.order_ref, "valid_until": _iso(q.valid_until), "decision_id": q.decision_id}
                   for q in quotes],
        "market_evidence": market,
    }
