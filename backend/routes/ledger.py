"""Audit ledger (seller only): the latest entries and a full re-verification of the keyed hash chain
(backend/services/ledger.py). Nothing here is reachable from /customer/* or /verify/*."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.services import ledger

router = APIRouter(prefix="/seller/ledger", tags=["ledger"])


@router.get("")
def entries(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)) -> dict:
    return {"total": ledger.count(db), "entries": ledger.latest(db, limit)}


@router.post("/verify")
def verify(db: Session = Depends(get_db)) -> dict:
    return ledger.verify(db)
