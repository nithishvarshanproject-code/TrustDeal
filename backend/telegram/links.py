"""Linking a Telegram chat to a demo customer (Customer page) or to the seller alerts (Agent inbox)
with a one-time code: 8 characters, valid 10 minutes, usable once; a new code replaces the old one."""
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.models import AgentMessage, AgentTask, TelegramCode, TelegramLink

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I
CODE_LENGTH = 8
CODE_TTL = timedelta(minutes=10)
CODE_PATTERN = re.compile(rf"^[{CODE_ALPHABET}]{{{CODE_LENGTH}}}$")
ROLES = ("customer", "seller")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)     # SQLite returns naive UTC


def new_code(db: Session, role: str, customer_id: int | None = None) -> TelegramCode:
    if role not in ROLES or (role == "customer") != (customer_id is not None):
        raise ValueError("invalid link target")
    db.execute(delete(TelegramCode).where(TelegramCode.role == role, TelegramCode.customer_id == customer_id,
                                          TelegramCode.used_at.is_(None)))
    row = TelegramCode(code="".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH)), role=role,
                       customer_id=customer_id, expires_at=_now() + CODE_TTL)
    db.add(row)
    db.commit()
    return row


def redeem(db: Session, code: str, chat_id: int) -> TelegramLink | None:
    """Link this chat with a valid, unused, unexpired code; None otherwise."""
    code = (code or "").strip().upper()
    if not CODE_PATTERN.match(code):
        return None
    row = db.scalar(select(TelegramCode).where(TelegramCode.code == code))
    if row is None or row.used_at is not None or _aware(row.expires_at) < _now():
        return None
    row.used_at = _now()
    if row.role == "customer":       # one chat per customer: a new chat replaces the old one
        db.execute(delete(TelegramLink).where(TelegramLink.customer_id == row.customer_id))
    db.execute(delete(TelegramLink).where(TelegramLink.chat_id == chat_id))
    link = TelegramLink(chat_id=chat_id, role=row.role, customer_id=row.customer_id,
                        after_message_id=db.scalar(select(func.max(AgentMessage.id))) or 0,
                        after_task_id=db.scalar(select(func.max(AgentTask.id))) or 0)
    db.add(link)
    db.commit()
    return link


def for_chat(db: Session, chat_id) -> TelegramLink | None:
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        return None
    return db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))


def unlink(db: Session, chat_id: int) -> None:
    db.execute(delete(TelegramLink).where(TelegramLink.chat_id == chat_id))
    db.commit()


def customer_linked(db: Session, customer_id: int) -> bool:
    return db.scalar(select(TelegramLink.id).where(TelegramLink.role == "customer",
                                                   TelegramLink.customer_id == customer_id)) is not None


def seller_chat_count(db: Session) -> int:
    return db.scalar(select(func.count(TelegramLink.id)).where(TelegramLink.role == "seller")) or 0
