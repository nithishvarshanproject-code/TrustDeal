"""Verify quote: a public, customer-safe check that a quote (PDF or QR code) is genuine.
A quote is genuine only if its code matches the stored code and a fresh HMAC of the stored fields
(backend/services/quote_seal.py). A wrong code, an edited quote and an unknown ref all get the same
answer, so refs cannot be probed. POST keeps codes out of access logs."""
import logging
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.agent.loop import quote_ref
from backend.agent.views import NOT_GENUINE, quote_verification_view
from backend.database import get_db
from backend.models import AgentDeal, AgentQuote
from backend.services import quote_seal

router = APIRouter(prefix="/verify", tags=["verify"])
log = logging.getLogger("dealdesk.verify")
REF_RE = re.compile(r"Q-(\d{5,9})")


class VerifyIn(BaseModel):
    ref: str = Field(min_length=1, max_length=20)
    code: str = Field(min_length=1, max_length=40)


@router.post("/quote")
def verify_quote(req: VerifyIn, db: Session = Depends(get_db)) -> dict:
    m = REF_RE.fullmatch(req.ref.strip().upper())
    quote = db.get(AgentQuote, int(m.group(1))) if m else None
    deal = db.get(AgentDeal, quote.agent_deal_id) if quote is not None else None
    if quote is None or deal is None:
        return NOT_GENUINE
    ref = quote_ref(quote.id)
    if quote_seal.matches(ref, quote, deal, req.code):
        return quote_verification_view(db, deal, quote)
    if quote.verify_code and quote_seal.normalize_code(req.code) == quote.verify_code:
        log.warning("quote %s: the stored fields no longer match its verification code", ref)
    return NOT_GENUINE
