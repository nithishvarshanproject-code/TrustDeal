"""Customer-facing API (the Customer page). Every response is built by backend/agent/views.py
from allowlists: no cost, margin, rule ID, confidence, trust, audit trail or other customer's
data ever leaves through these routes. Local demo: the customer is chosen by id (no login).
The Telegram channel calls the same helpers (handle_message, button_action, switch_request) with
channel="telegram", so both channels share the limits, the ownership checks and the agent loop."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agent.followups import expire_deal_quote_if_due
from backend.agent.loop import Agent, invoice_ref, quote_ref
from backend.agent.views import (customer_invoice_document, customer_product, customer_quote_document,
                                 customer_request_summary, customer_request_view, customer_telegram_view)
from backend.database import get_db
from backend.models import AgentDeal, AgentInvoice, AgentMessage, AgentQuote, Customer, Product
from backend.services.invoice_pdf import render_invoice_pdf
from backend.services.quote_pdf import render_quote_pdf
from backend.telegram import links as telegram_links
from backend.telegram import runtime as telegram_runtime
from engine.metta_safe import MettaInputError
from engine.omega_link import OmegaError, OmegaUnavailable

router = APIRouter(prefix="/customer", tags=["customer"])

MAX_MESSAGE = 500
RATE_LIMIT = 20                      # customer messages ...
RATE_WINDOW = timedelta(minutes=10)  # ... per window
BUSY = "Our pricing assistant is busy right now. Please try again in a moment."


class _In(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    customer_id: int


class MessageIn(_In):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE)
    product_id: int | None = None
    request_id: int | None = None
    voice: bool = False              # spoken in the browser (Web Speech API); only labels the message


class AskIn(_In):
    discount: float = Field(ge=0, le=100)


class SwitchIn(_In):
    index: int = Field(ge=0, le=2)


def _customer(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(404, "customer not found")
    return customer


def _own_request(db: Session, customer: Customer, request_id: int) -> AgentDeal:
    deal = db.get(AgentDeal, request_id)
    if deal is None or deal.customer_id != customer.id:      # never reveal other customers' requests
        raise HTTPException(404, "request not found")
    return deal


def _rate_limit(db: Session, customer: Customer) -> None:
    since = datetime.now(timezone.utc) - RATE_WINDOW
    recent = db.scalar(select(func.count(AgentMessage.id)).join(AgentDeal, AgentDeal.id == AgentMessage.agent_deal_id)
                       .where(AgentDeal.customer_id == customer.id, AgentMessage.sender == "customer",
                              AgentMessage.created_at >= since))
    if recent >= RATE_LIMIT:
        raise HTTPException(429, "Too many messages. Please wait a few minutes and try again.")


def _run(db: Session, fn):
    """Run an agent step; engine problems become a friendly 503 (details stay in the seller logs)."""
    try:
        return fn()
    except (OmegaUnavailable, OmegaError):
        db.rollback()
        raise HTTPException(503, BUSY)
    except (ValueError, MettaInputError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc))


@router.get("/customers")
def customers(db: Session = Depends(get_db)) -> list[dict]:
    """For the "Shopping as" selector: names only."""
    return [{"customer_id": c.id, "name": c.name} for c in db.scalars(select(Customer).order_by(Customer.id))]


@router.get("/me")
def me(customer_id: int, db: Session = Depends(get_db)) -> dict:
    c = _customer(db, customer_id)
    return {"customer_id": c.id, "name": c.name, "loyalty_tier": c.tier}


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)) -> list[dict]:
    return [customer_product(p) for p in db.scalars(select(Product).order_by(Product.id))]


@router.get("/requests")
def my_requests(customer_id: int, db: Session = Depends(get_db)) -> list[dict]:
    customer = _customer(db, customer_id)
    deals = db.scalars(select(AgentDeal).where(AgentDeal.customer_id == customer.id)
                       .order_by(AgentDeal.updated_at.desc(), AgentDeal.id.desc())).all()
    return [customer_request_summary(db, d) for d in deals]


@router.get("/requests/{request_id}")
def request_view(request_id: int, customer_id: int, db: Session = Depends(get_db)) -> dict:
    customer = _customer(db, customer_id)
    return customer_request_view(db, _own_request(db, customer, request_id))


def handle_message(db: Session, req: MessageIn, channel: str = "web") -> dict:
    customer = _customer(db, req.customer_id)
    _rate_limit(db, customer)
    deal = _own_request(db, customer, req.request_id) if req.request_id is not None else None
    if req.product_id is not None and db.get(Product, req.product_id) is None:
        raise HTTPException(404, "product not found")
    deal = _run(db, lambda: Agent(db, channel).on_customer_message(customer, req.text.strip(), req.product_id,
                                                                  deal, voice=req.voice))
    if isinstance(deal, dict):
        return deal
    return customer_request_view(db, deal)


@router.post("/messages")
def send_message(req: MessageIn, db: Session = Depends(get_db)) -> dict:
    return handle_message(db, req)


def button_action(db: Session, req: _In, request_id: int, action: str, discount: float | None = None,
                  channel: str = "web") -> dict:
    customer = _customer(db, req.customer_id)
    _rate_limit(db, customer)
    deal = _own_request(db, customer, request_id)
    if action == "order" and expire_deal_quote_if_due(db, deal):
        return customer_request_view(db, deal)
    deal = _run(db, lambda: Agent(db, channel).on_button(deal, action, discount))
    return customer_request_view(db, deal)


@router.post("/requests/{request_id}/accept")
def accept(request_id: int, req: _In, db: Session = Depends(get_db)) -> dict:
    return button_action(db, req, request_id, "accept")


@router.post("/requests/{request_id}/decline")
def decline(request_id: int, req: _In, db: Session = Depends(get_db)) -> dict:
    return button_action(db, req, request_id, "decline")


@router.post("/requests/{request_id}/order")
def order(request_id: int, req: _In, db: Session = Depends(get_db)) -> dict:
    return button_action(db, req, request_id, "order")


@router.post("/requests/{request_id}/ask")
def ask(request_id: int, req: AskIn, db: Session = Depends(get_db)) -> dict:
    return button_action(db, req, request_id, "ask", req.discount)


def switch_request(db: Session, req: SwitchIn, request_id: int, channel: str = "web") -> dict:
    customer = _customer(db, req.customer_id)
    _rate_limit(db, customer)
    deal = _own_request(db, customer, request_id)
    new = _run(db, lambda: Agent(db, channel).switch_to_alternative(deal, req.index))
    return customer_request_view(db, new)


@router.post("/requests/{request_id}/switch")
def switch(request_id: int, req: SwitchIn, db: Session = Depends(get_db)) -> dict:
    return switch_request(db, req, request_id)


@router.get("/requests/{request_id}/quote.pdf")
def quote_pdf(request_id: int, customer_id: int, db: Session = Depends(get_db)) -> Response:
    """The latest quote of the customer's own request as a PDF (customer-safe fields only)."""
    customer = _customer(db, customer_id)
    deal = _own_request(db, customer, request_id)
    quote = db.scalar(select(AgentQuote).where(AgentQuote.agent_deal_id == deal.id).order_by(AgentQuote.id.desc()))
    if quote is None:
        raise HTTPException(404, "no quote for this request yet")
    pdf = render_quote_pdf(customer_quote_document(db, deal, quote))
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="TrustDeal-{quote_ref(quote.id)}.pdf"',
        "Cache-Control": "no-store"})


@router.get("/requests/{request_id}/invoice.pdf")
def invoice_pdf(request_id: int, customer_id: int, db: Session = Depends(get_db)) -> Response:
    """The GST tax invoice of the customer's own ordered request (customer-safe fields only)."""
    customer = _customer(db, customer_id)
    deal = _own_request(db, customer, request_id)
    found = db.execute(select(AgentInvoice, AgentQuote).join(AgentQuote, AgentQuote.id == AgentInvoice.quote_id)
                       .where(AgentQuote.agent_deal_id == deal.id).order_by(AgentInvoice.id.desc())).first()
    if found is None:
        raise HTTPException(404, "no invoice for this request (invoices are issued when an order is placed)")
    invoice, quote = found
    pdf = render_invoice_pdf(customer_invoice_document(db, deal, quote, invoice))
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="TrustDeal-{invoice_ref(invoice.id)}.pdf"',
        "Cache-Control": "no-store"})


# ---------- Telegram: link this customer's chat with a one-time code ----------

@router.get("/telegram")
def telegram_status(customer_id: int, db: Session = Depends(get_db)) -> dict:
    customer = _customer(db, customer_id)
    state = telegram_runtime.status()
    return customer_telegram_view(state["enabled"], state["bot_username"],
                                  telegram_links.customer_linked(db, customer.id))


@router.post("/telegram/code")
def telegram_code(req: _In, db: Session = Depends(get_db)) -> dict:
    customer = _customer(db, req.customer_id)
    if not telegram_runtime.status()["enabled"]:
        raise HTTPException(409, "Telegram is not set up for this store.")
    return telegram_runtime.code_view(telegram_links.new_code(db, "customer", customer.id))
