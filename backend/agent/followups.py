"""Durable quote follow-ups and the seller's daily summary (all clock rules use India time)."""
import json
import math
import os
import threading
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.agent.language import fmt_inr
from backend.agent.loop import quote_ref
from backend.models import (AgentActivity, AgentDeal, AgentDecision, AgentMessage, AgentQuote, AgentTask, Deal,
                            Decision, Override, Product, TelegramLink)

UTC = timezone.utc
try:
    IST = ZoneInfo("Asia/Kolkata")
except ZoneInfoNotFoundError:  # Windows Python may not include the IANA database; India has no DST.
    IST = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
DEFAULT_REMINDER_HOURS = 6
DEFAULT_DAILY_SUMMARY_TIME = time(21, 0)
_RUN_LOCK = threading.RLock()


def as_utc(value: datetime) -> datetime:
    """SQLite drops timezone metadata; its stored values are UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def ist_timestamp(value: datetime) -> str:
    return as_utc(value).astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def reminder_hours() -> float:
    try:
        value = float(os.getenv("DEALDESK_REMINDER_HOURS", str(DEFAULT_REMINDER_HOURS)))
    except (TypeError, ValueError):
        return DEFAULT_REMINDER_HOURS
    return value if math.isfinite(value) and value >= 0 else DEFAULT_REMINDER_HOURS


def daily_summary_time() -> time:
    raw = os.getenv("DEALDESK_DAILY_SUMMARY_TIME", "21:00")
    try:
        hour, minute = (int(part) for part in raw.split(":"))
        return time(hour, minute)
    except (ValueError, TypeError):
        return DEFAULT_DAILY_SUMMARY_TIME


def _json(detail: str | None) -> dict:
    try:
        value = json.loads(detail or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _has_key(db: Session, key: str) -> bool:
    rows = db.scalars(select(AgentActivity).where(AgentActivity.kind == "followup_delivery")).all()
    return any(_json(row.detail_json).get("delivery_key") == key for row in rows)


def _customer_channel(db: Session, deal: AgentDeal) -> str:
    """Prefer an active linked Telegram channel; the same message remains visible on the web."""
    from backend.telegram import runtime

    linked = db.scalar(select(TelegramLink.id).where(TelegramLink.role == "customer",
                                                     TelegramLink.customer_id == deal.customer_id).limit(1))
    return "telegram" if linked is not None and runtime.status()["enabled"] else "web"


def _notify_customer(db: Session, deal: AgentDeal, quote: AgentQuote, text: str, now: datetime) -> str:
    channel = _customer_channel(db, deal)
    db.add(AgentMessage(agent_deal_id=deal.id, sender="agent", text=text, status="sent", source="template",
                        kind="status", decision_id=quote.decision_id, channel=channel, created_at=now))
    return channel


def _mark_delivery(db: Session, deal: AgentDeal, quote: AgentQuote, key: str, action: str, text: str,
                   channel: str, now: datetime) -> None:
    db.add(AgentActivity(agent_deal_id=deal.id, kind="followup_delivery", summary=text,
                         detail_json=json.dumps({"delivery_key": key, "action": action, "quote_id": quote.id,
                                                 "quote_ref": quote_ref(quote.id), "channel": channel}),
                         decision_id=quote.decision_id, created_at=now))


def _action(action: str, deal: AgentDeal, quote: AgentQuote, channel: str) -> dict:
    return {"action": action, "request_id": deal.id, "quote_ref": quote_ref(quote.id), "channel": channel}


def _expire(db: Session, deal: AgentDeal, quote: AgentQuote, current: datetime) -> bool:
    """Expire the quote and close its request with conditional UPDATEs in this transaction: only if the quote
    is still open and not ordered. An order committed at the same instant (after this session read the quote)
    is never overwritten; False then, and the stale objects are reloaded."""
    expired = db.execute(update(AgentQuote).where(AgentQuote.id == quote.id, AgentQuote.status == "open",
                                                  AgentQuote.order_ref.is_(None)).values(status="expired")).rowcount
    if not expired:
        db.expire(quote)
        db.expire(deal)
        return False
    db.execute(update(AgentDeal).where(AgentDeal.id == deal.id, AgentDeal.state == "QUOTED")
               .values(state="CLOSED", updated_at=current))
    return True


def run_followups(db: Session, now: datetime | None = None) -> dict:
    """Send due reminders and expire quotes once. Quote validity is read only from MeTTa's quote."""
    current = as_utc(now or datetime.now(UTC))
    hours = reminder_hours()
    actions = []
    checked = 0
    with _RUN_LOCK:
        quotes = db.scalars(select(AgentQuote).where(AgentQuote.status == "open")
                            .order_by(AgentQuote.id)).all()
        for quote in quotes:
            deal = db.get(AgentDeal, quote.agent_deal_id)
            if deal is None or deal.state != "QUOTED":
                continue
            checked += 1
            expiry = as_utc(quote.valid_until)
            if expiry <= current:
                key = f"quote-expiry:{quote.id}"
                if not _expire(db, deal, quote, current):
                    continue
                if not _has_key(db, key):
                    text = (f"Your quote {quote_ref(quote.id)} expired at {ist_timestamp(expiry)}, so we closed this "
                            "request. You can start a new request anytime.")
                    channel = _notify_customer(db, deal, quote, text, current)
                    _mark_delivery(db, deal, quote, key, "expired", text, channel, current)
                    actions.append(_action("expired", deal, quote, channel))
                db.commit()
                continue
            if expiry <= current + timedelta(hours=hours):
                key = f"quote-reminder:{quote.id}"
                if _has_key(db, key):
                    continue
                text = (f"Your quote {quote_ref(quote.id)} is still valid until {ist_timestamp(expiry)}. "
                        "Place your order before then if you'd like to proceed.")
                channel = _notify_customer(db, deal, quote, text, current)
                _mark_delivery(db, deal, quote, key, "reminder", text, channel, current)
                db.commit()
                actions.append(_action("reminder", deal, quote, channel))
    return {"checked_quotes": checked, "reminder_hours": hours, "actions": actions,
            "message": (f"Ran follow-ups: {len(actions)} action(s)." if actions else
                        f"Ran follow-ups: no reminders or expirations due (checked {checked} open quote(s)).")}


def expire_deal_quote_if_due(db: Session, deal: AgentDeal, now: datetime | None = None) -> bool:
    """Prevent an order action from racing past quote expiry; uses the same durable notification key."""
    current = as_utc(now or datetime.now(UTC))
    with _RUN_LOCK:
        quote = db.scalar(select(AgentQuote).where(AgentQuote.agent_deal_id == deal.id,
                                                   AgentQuote.status == "open").order_by(AgentQuote.id.desc()))
        if quote is None or as_utc(quote.valid_until) > current or deal.state != "QUOTED":
            return False
        key = f"quote-expiry:{quote.id}"
        if not _expire(db, deal, quote, current):
            return False
        if not _has_key(db, key):
            text = (f"Your quote {quote_ref(quote.id)} expired at {ist_timestamp(as_utc(quote.valid_until))}, so we "
                    "closed this request. You can start a new request anytime.")
            channel = _notify_customer(db, deal, quote, text, current)
            _mark_delivery(db, deal, quote, key, "expired", text, channel, current)
        db.commit()
        return True


def _day_window(now: datetime) -> tuple[date, datetime, datetime]:
    local_date = as_utc(now).astimezone(IST).date()
    start = datetime.combine(local_date, time.min, tzinfo=IST).astimezone(UTC)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=IST).astimezone(UTC)
    return local_date, start, end


def _latest(rows, key):
    result = {}
    for row in rows:
        result.setdefault(key(row), row)
    return result


def _inside(row, start: datetime, end: datetime) -> bool:
    return start <= as_utc(row.created_at) < end


def _discount_value(product: Product, quantity: int, percent: float) -> float:
    return product.list_price * quantity * percent / 100


def daily_metrics(db: Session, now: datetime | None = None) -> dict:
    current = as_utc(now or datetime.now(UTC))
    local_date, start, end = _day_window(current)
    decisions = db.scalars(select(Decision).order_by(Decision.id.desc())).all()
    decisions = [row for row in decisions if _inside(row, start, end)]
    latest_deal_decisions = _latest(decisions, lambda row: row.deal_id)
    overrides = db.scalars(select(Override).order_by(Override.id.desc())).all()
    latest_overrides = _latest(overrides, lambda row: row.decision_id)
    agent_decisions = db.scalars(select(AgentDecision).order_by(AgentDecision.id.desc())).all()
    latest_agent_decisions = _latest([row for row in agent_decisions if _inside(row, start, end)],
                                     lambda row: row.agent_deal_id)
    requested_total = offered_total = 0.0
    decided = 0
    pending_escalations = 0
    counters_without_amount = 0

    for decision in latest_deal_decisions.values():
        deal = db.get(Deal, decision.deal_id)
        product = db.get(Product, deal.product_id) if deal else None
        if deal is None or product is None:
            continue
        override = latest_overrides.get(decision.id)
        result = override.new_result if override else decision.result
        if result == "ESCALATE":
            pending_escalations += 1
            continue
        if result == "APPROVE":
            offered_pct = deal.discount_requested
        elif result == "REJECT":
            offered_pct = 0.0
        elif result == "COUNTER":
            offered_pct = (decision.approved_discount if decision.result == "COUNTER" else None)
            if offered_pct is None:
                counters_without_amount += 1
                continue
        else:
            continue
        requested_total += _discount_value(product, deal.quantity, deal.discount_requested)
        offered_total += _discount_value(product, deal.quantity, offered_pct)
        decided += 1

    agent_all = db.scalars(select(AgentDeal).order_by(AgentDeal.id)).all()
    tasks = db.scalars(select(AgentTask).order_by(AgentTask.id)).all()
    latest_escalation_task = _latest([task for task in tasks if task.kind == "escalation"],
                                     lambda task: task.agent_deal_id)
    for deal in agent_all:
        decision = latest_agent_decisions.get(deal.id)
        if decision is None:
            continue
        product = db.get(Product, deal.product_id)
        if product is None:
            continue
        result = {"manager-approved": "APPROVE", "manager-rejected": "REJECT"}.get(decision.event,
                                                                                     decision.result)
        if decision.result == "ESCALATE":
            task = latest_escalation_task.get(deal.id)
            if decision.event not in ("manager-approved", "manager-rejected") and (task is None or task.status == "open"):
                pending_escalations += 1
        asked = deal.discount_asked
        if asked is None:
            inputs = _json(decision.audit_json).get("input")
            asked = inputs.get("discount_requested") if isinstance(inputs, dict) else None
        if asked is None:
            continue
        if result == "ESCALATE":
            continue
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
        requested_total += _discount_value(product, deal.quantity, asked)
        offered_total += _discount_value(product, deal.quantity, offered_pct)
        decided += 1

    quotes = db.scalars(select(AgentQuote).where(AgentQuote.status == "ordered")).all()
    ordered_today = [quote for quote in quotes if quote.ordered_at and start <= as_utc(quote.ordered_at) < end]
    open_tasks = sum(task.status == "open" for task in tasks)
    deal_check_count = len(latest_deal_decisions)
    customer_chat_count = len(latest_agent_decisions)
    return {
        "day_ist": local_date.isoformat(),
        "deals_handled": deal_check_count + customer_chat_count,
        "deal_checks": deal_check_count,
        "customer_chat_decisions": customer_chat_count,
        "orders": len(ordered_today),
        "discount_avoided": round(requested_total - offered_total, 2),
        "discount_actually_given_on_orders": round(sum(quote.savings for quote in ordered_today), 2),
        "open_tasks": open_tasks,
        "pending_escalations_excluded": pending_escalations,
        "counters_without_amount_excluded": counters_without_amount,
        "decided_deals": decided,
    }


def _summary_text(metrics: dict, generated_at: datetime) -> str:
    day_label = datetime.fromisoformat(metrics["day_ist"]).strftime("%d %b %Y")
    return "\n".join((
        f"TrustDeal daily summary · {day_label} (IST)",
        f"Generated: {ist_timestamp(generated_at)}",
        f"Deals handled: {metrics['deals_handled']} ({metrics['deal_checks']} Deal checks, "
        f"{metrics['customer_chat_decisions']} customer chat decisions)",
        f"Orders: {metrics['orders']}",
        f"Discount avoided: {fmt_inr(metrics['discount_avoided'])}",
        f"Discount actually given on orders: {fmt_inr(metrics['discount_actually_given_on_orders'])}",
        f"Open tasks: {metrics['open_tasks']}",
        f"Pending escalations excluded: {metrics['pending_escalations_excluded']}",
        f"Counters without an amount excluded: {metrics['counters_without_amount_excluded']}",
    ))


def _summary_for_day(db: Session, day: str) -> AgentActivity | None:
    rows = db.scalars(select(AgentActivity).where(AgentActivity.kind == "daily_summary")
                      .order_by(AgentActivity.id.desc())).all()
    return next((row for row in rows if _json(row.detail_json).get("day_ist") == day), None)


def daily_summary_view(db: Session, now: datetime | None = None) -> dict:
    current = as_utc(now or datetime.now(UTC))
    current_metrics = daily_metrics(db, current)
    previous = _summary_for_day(db, current_metrics["day_ist"])
    saved = _json(previous.detail_json) if previous else {}
    metrics = ({key: saved[key] for key in current_metrics if key in saved}
               if previous is not None else current_metrics)
    generated = as_utc(previous.created_at) if previous else current
    text = saved.get("summary_text") or _summary_text(metrics, current)
    return {"day_ist": current_metrics["day_ist"], "time_zone": "Asia/Kolkata",
            "send_time_ist": daily_summary_time().strftime("%H:%M"), "metrics": metrics,
            "summary_text": text, "sent": previous is not None,
            "sent_at_ist": ist_timestamp(generated) if previous else None}


def send_daily_summary(db: Session, now: datetime | None = None) -> dict:
    current = as_utc(now or datetime.now(UTC))
    with _RUN_LOCK:
        metrics = daily_metrics(db, current)
        previous = _summary_for_day(db, metrics["day_ist"])
        if previous is not None:
            return {**daily_summary_view(db, current), "created": False,
                    "message": "Today's summary was already sent; no duplicate was created."}
        text = _summary_text(metrics, current)
        db.add(AgentActivity(agent_deal_id=None, kind="daily_summary",
                             summary=f"Daily seller summary for {metrics['day_ist']} (IST)",
                             detail_json=json.dumps({**metrics, "day_ist": metrics["day_ist"],
                                                     "summary_text": text,
                                                     "send_time_ist": daily_summary_time().strftime("%H:%M")}),
                             created_at=current))
        db.commit()
        return {**daily_summary_view(db, current), "created": True,
                "message": "Daily summary created and queued for linked seller Telegram chats."}


def send_scheduled_summary_if_due(db: Session, now: datetime | None = None) -> bool:
    current = as_utc(now or datetime.now(UTC))
    local = current.astimezone(IST)
    if local.timetz().replace(tzinfo=None) < daily_summary_time():
        return False
    return bool(send_daily_summary(db, current).get("created"))
