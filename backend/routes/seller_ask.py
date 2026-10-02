"""Seller-only "Ask why": a question about one stored decision, answered from its audit trail.
Read-only (see backend/agent/ask_why.py); no /customer/* route reaches it."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.agent import ask_why
from backend.database import get_db

router = APIRouter(prefix="/seller/decisions", tags=["seller-ask-why"])


class AskWhyIn(BaseModel):
    question: str = Field(min_length=1, max_length=ask_why.MAX_QUESTION)
    source: Literal["deal", "agent"] = "deal"   # decisions (Decisions page) | agent_decisions (Agent inbox)


@router.get("/ask/suggestions")
def suggestions() -> list[str]:
    return ask_why.SUGGESTED


@router.post("/{decision_id}/ask")
def ask(decision_id: int, req: AskWhyIn, db: Session = Depends(get_db)) -> dict:
    try:
        return ask_why.ask(db, req.source, decision_id, req.question)
    except ask_why.AskError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
