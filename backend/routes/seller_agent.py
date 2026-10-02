"""Seller-side agent API: inbox by state, each conversation with its full audit trail and
activity timeline, human tasks (approve / verify), drafted replies, the autonomy switch and the
Telegram seller alerts (a chat linked here gets tasks with Approve / Reject buttons)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.loop import MODES, Agent
from backend.agent.followups import daily_summary_view, run_followups, send_daily_summary
from backend.agent.views import seller_activity, seller_deal_summary, seller_deal_view, seller_task
from backend.database import get_db
from backend.models import AgentActivity, AgentDeal, AgentMessage, AgentTask
from backend.telegram import links as telegram_links
from backend.telegram import runtime as telegram_runtime
from backend.validation import MAX_NAME
from engine.bridge import AGENT_STATES

router = APIRouter(prefix="/seller/agent", tags=["seller-agent"])

STATE_ORDER = ["NEW", "WAITING_CUSTOMER", "WAITING_VERIFICATION", "ESCALATED", "QUOTED", "DECLINED",
               "ORDERED", "CLOSED"]


class ResolveIn(BaseModel):
    answer: Literal["approve", "reject", "verified", "not-verified"]
    reviewer: str = Field(min_length=1, max_length=MAX_NAME)


class ReviewerIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=MAX_NAME)


class SettingsIn(BaseModel):
    mode: Literal["auto-send", "draft-for-approval"]


@router.get("/deals")
def inbox(db: Session = Depends(get_db)) -> dict:
    deals = db.scalars(select(AgentDeal).order_by(AgentDeal.updated_at.desc(), AgentDeal.id.desc())).all()
    counts = {s: 0 for s in STATE_ORDER}
    for d in deals:
        counts[d.state] = counts.get(d.state, 0) + 1
    return {"counts": counts, "deals": [seller_deal_summary(db, d) for d in deals]}


@router.get("/deals/{request_id}")
def deal_view(request_id: int, db: Session = Depends(get_db)) -> dict:
    deal = db.get(AgentDeal, request_id)
    if deal is None:
        raise HTTPException(404, f"request {request_id} not found")
    return seller_deal_view(db, deal)


@router.get("/tasks")
def tasks(status: Literal["open", "done", "all"] = "open", db: Session = Depends(get_db)) -> list[dict]:
    query = select(AgentTask).order_by(AgentTask.id.desc())
    if status != "all":
        query = query.where(AgentTask.status == status)
    return [seller_task(db, t) for t in db.scalars(query)]


@router.post("/tasks/{task_id}/resolve")
def resolve(task_id: int, req: ResolveIn, db: Session = Depends(get_db)) -> dict:
    task = db.get(AgentTask, task_id)
    if task is None:
        raise HTTPException(404, f"task {task_id} not found")
    try:
        deal = Agent(db).resolve_task(task, req.answer, req.reviewer.strip())
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409 if "already" in str(exc) else 422, str(exc)) from exc
    return seller_deal_view(db, deal)


@router.post("/messages/{message_id}/approve")
def approve(message_id: int, req: ReviewerIn, db: Session = Depends(get_db)) -> dict:
    message = db.get(AgentMessage, message_id)
    if message is None:
        raise HTTPException(404, f"message {message_id} not found")
    try:
        Agent(db).approve_message(message, req.reviewer.strip())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return seller_deal_view(db, db.get(AgentDeal, message.agent_deal_id))


@router.get("/settings")
def settings(db: Session = Depends(get_db)) -> dict:
    return {"mode": Agent(db).mode(), "modes": list(MODES)}


@router.post("/settings")
def set_settings(req: SettingsIn, db: Session = Depends(get_db)) -> dict:
    return {"mode": Agent(db).set_mode(req.mode), "modes": list(MODES)}


@router.get("/activity")
def activity(limit: int = 100, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(AgentActivity).order_by(AgentActivity.id.desc()).limit(max(1, min(limit, 500)))).all()
    return [seller_activity(a) for a in rows]


@router.get("/daily-summary")
def daily_summary(db: Session = Depends(get_db)) -> dict:
    return daily_summary_view(db)


@router.post("/daily-summary/send-now")
def send_summary_now(db: Session = Depends(get_db)) -> dict:
    return send_daily_summary(db)


@router.post("/followups/run")
def run_due_followups(db: Session = Depends(get_db)) -> dict:
    return run_followups(db)


@router.get("/telegram")
def telegram_status(db: Session = Depends(get_db)) -> dict:
    return {**telegram_runtime.status(), "linked_chats": telegram_links.seller_chat_count(db)}


@router.post("/telegram/code")
def telegram_code(db: Session = Depends(get_db)) -> dict:
    if not telegram_runtime.status()["enabled"]:
        raise HTTPException(409, "Telegram is not set up: add TELEGRAM_BOT_TOKEN to omega/omega.env and restart.")
    return telegram_runtime.code_view(telegram_links.new_code(db, "seller"))


assert set(STATE_ORDER) == AGENT_STATES
assert set(MODES) == {"auto-send", "draft-for-approval"}
