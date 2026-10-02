"""Telegram channel: customers negotiate with the SAME agent loop as the web chat, and sellers get
task alerts with Approve / Reject buttons.

- Customer messages and buttons call the same helpers as the /customer/* routes
  (routes/customer.py: 500-character limit, rate limit, ownership, friendly errors) with
  channel="telegram". MeTTa decides every step, exactly as for web chat.
- Everything a customer receives is built from customer_request_view() (views.py allowlists):
  the agent's sent messages (a draft only after a seller approved it), offer and quote buttons,
  and the quote PDF. No cost, margin, rule, confidence or trust value can reach Telegram.
- Seller buttons call the existing task endpoint (routes/seller_agent.resolve).
- Only linked private chats are served. An unknown chat gets one short reply per 10 minutes.
- Telegram text is untrusted: it only ever reaches the agent as a customer message (validated
  like web chat, never passed into MeTTa as text), and replies are plain text (no markup).
"""
import logging
import json
import re
import threading
import time

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from backend.agent.language import fmt_inr, fmt_pct
from backend.agent.loop import Agent, invoice_ref, quote_ref
from backend.agent.views import customer_invoice_document, customer_quote_document, customer_request_view, seller_task
from backend.database import SessionLocal
from backend.models import (AgentDeal, AgentDecision, AgentInvoice, AgentMessage, AgentQuote, AgentTask, Customer,
                            Product, AgentActivity, TelegramDelivery, TelegramLink)
from backend.routes import customer as customer_routes
from backend.routes import seller_agent as seller_routes
from backend.services.invoice_pdf import render_invoice_pdf
from backend.services.quote_pdf import render_quote_pdf
from backend.telegram import links, runtime
from backend.telegram.client import BotAPI, TelegramConflict, TelegramError
from engine.omega_link import OmegaError, OmegaUnavailable

logger = logging.getLogger("uvicorn.error")

REVIEWER = "Seller via Telegram"          # fixed: Telegram names are untrusted, never put in the audit trail
UNKNOWN_REPLY_EVERY_S = 600
LINK_ATTEMPTS, LINK_WINDOW_S = 5, 600
ASK_AGAIN_TTL_S = 600
MAX_TRACKED_CHATS = 5000
CLOSED_STATES = ("CLOSED", "ORDERED", "DECLINED")

_START = re.compile(r"^/start(?:@\w+)?(?:\s+(\S{1,64}))?\s*$")
_CALLBACK = re.compile(r"^(?:(?P<c>[adoq]):(?P<rid>\d{1,9})|s:(?P<srid>\d{1,9}):(?P<idx>[0-2])"
                       r"|t(?P<t>[ar]):(?P<tid>\d{1,9})|p:(?P<pid>\d{1,9}))$")
_PLAIN_PCT = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:%|percent)?\s*$", re.I)

# ---------- texts (customer texts never mention costs, margins or rules) ----------
WELCOME = ("Welcome to TrustDeal — the BASIX Deal Agent on Omega. Every discount, explained and proven.\n\n"
           "To start, open the BASIX Store page, click “Connect Telegram” and send the code here, "
           "for example: /start ABCD2345")
UNKNOWN = ("Hi! This chat isn't connected yet. Please connect from the store page: "
           "click “Connect Telegram” and send the code here.")
BAD_CODE = "That code is not valid or has expired. Please get a new code from the store page."
TOO_MANY_CODES = "Too many attempts. Please wait a few minutes, then get a new code from the store page."
TEXT_ONLY = "Sorry, I can only read text messages."
TOO_LONG = f"That message is too long. Please keep it under {customer_routes.MAX_MESSAGE} characters."
NOT_UNDERSTOOD = "Sorry, I couldn't do that. Please try again."
GONE = "That request is no longer open. Send a new message to start again."
NOT_OPEN = "This offer is no longer open."
ASK_AGAIN = "How much of a discount would you like? Reply with a number, for example 8%."
PICK_PRODUCT = "Which product would you like a discount on? For example: “Can I get 10% off {example}?”"
BYE = "This chat is now disconnected from TrustDeal. You can connect again from the store page."
HELP_CUSTOMER = ("Ask for a discount in your own words, for example “Can I get 10% off {example}?” "
                 "Use the buttons to accept an offer, ask again or say no thanks.\n/stop disconnects this chat.")
HELP_SELLER = ("This chat receives TrustDeal seller alerts: a message when a request needs a manager "
               "(escalation or tier verification), with Approve / Reject buttons. "
               "The full audit trail is in the Agent inbox.\n/stop disconnects this chat.")
SELLER_LINKED = ("Connected for TrustDeal seller alerts. You'll get a message here when a request needs a "
                 "manager, with Approve / Reject buttons.")


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def _message_id(sent) -> int | None:
    return sent.get("message_id") if isinstance(sent, dict) and isinstance(sent.get("message_id"), int) else None


# ---------- customer messages (built from the customer view only) ----------

def customer_message(view: dict, message: dict, latest: bool) -> tuple[str, list[list[dict]] | None]:
    """Text and buttons for one agent message of `view` (customer_request_view). Buttons only on
    the latest reply, and only for actions the view allows (as on the Customer page)."""
    text, card = message["text"], message.get("card") or {}
    actions, rid = view["actions"], view["request_id"]
    buttons = None
    if card.get("type") == "offer" and latest and "accept" in actions:
        buttons = [[_btn("Accept ✓", f"a:{rid}"), _btn("Ask again", f"q:{rid}"), _btn("No thanks", f"d:{rid}")]]
        options = card.get("alternatives") or []
        if options and "switch" in actions:
            lines = []
            for i, o in enumerate(options):
                qty = f" × {o['quantity']}" if o["quantity"] > 1 else ""
                off = f", {fmt_pct(o['discount'])}% off" if o["discount"] > 0 else ""
                gain = (f"{fmt_inr(o['price_difference'])} less than {o['compared_to']}"
                        if o.get("price_difference") is not None else f"you save {fmt_inr(o['savings'])}")
                lines.append(f"{i + 1}. {o['product_name']}{qty}: {fmt_inr(o['total'])}{off} ({gain})")
                buttons.append([_btn(f"Switch to option {i + 1}", f"s:{rid}:{i}")])
            text += "\n\nOther options within your budget:\n" + "\n".join(lines)
    elif card.get("type") == "quote" and latest and "order" in actions:
        buttons = [[_btn("Place order", f"o:{rid}"), _btn("No thanks", f"d:{rid}")]]
    return text, buttons


class TelegramBot:
    def __init__(self, api: BotAPI, session_factory=SessionLocal):
        self.api = api
        self.Session = session_factory
        self.offset: int | None = None
        self._unknown_replied: dict[int, float] = {}
        self._link_attempts: dict[int, list[float]] = {}
        self._asking: dict[int, tuple[int, float]] = {}    # chat -> (request id, since) after "Ask again"
        self._selected_products: dict[int, int] = {}

    # ---------- polling ----------
    def run(self, stop: threading.Event) -> None:
        """The polling thread: getMe once, then getUpdates (long poll) + sweep until stopped."""
        backoff, known = 1, False
        while not stop.is_set():
            try:
                if not known:                       # once: the username for the t.me deep links
                    runtime.set_username((self.api.get_me() or {}).get("username"))
                    known = True
                self.poll_once()
                backoff = 1
            except TelegramConflict:
                logger.warning("Telegram: another copy of the bot is polling (e.g. a backend reload); retrying in 5 s")
                stop.wait(5)
            except TelegramError as exc:
                logger.warning("Telegram: %s; retrying in %d s", exc, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, 60)
            except Exception:                       # never let the channel stop the backend
                logger.exception("Telegram channel error")
                stop.wait(5)

    def poll_once(self) -> None:
        for update in self.api.get_updates(self.offset):
            if not isinstance(update, dict):
                continue
            if isinstance(update.get("update_id"), int):
                self.offset = update["update_id"] + 1     # before handling: a failing update is not retried
            try:
                self.handle_update(update)
            except TelegramError as exc:
                logger.warning("Telegram: could not answer an update (%s)", exc)
        self.sweep()

    def handle_update(self, update: dict) -> None:
        if isinstance(update.get("callback_query"), dict):
            self.on_callback(update["callback_query"])
        elif isinstance(update.get("message"), dict):
            self.on_message(update["message"])
        else:
            return                                   # edits, channel posts, ...: ignored
        self.sweep()                                 # deliver the agent's replies right away

    def _send(self, chat_id: int, text: str, buttons=None) -> dict:
        return self.api.send_message(chat_id, text, buttons)

    @staticmethod
    def _chat(obj) -> tuple[int | None, str | None]:
        chat = obj.get("chat") if isinstance(obj, dict) and isinstance(obj.get("chat"), dict) else {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return None, None
        return chat_id, chat.get("type")

    # ---------- messages ----------
    def on_message(self, msg: dict) -> None:
        chat_id, chat_type = self._chat(msg)
        if chat_id is None or chat_type != "private":
            return                                   # groups and channels are never served
        text = msg.get("text") if isinstance(msg.get("text"), str) else None
        with self.Session() as db:
            start = _START.match(text) if text is not None else None
            if start:
                self._start(db, chat_id, start.group(1))
                return
            link = links.for_chat(db, chat_id)
            if link is None:
                self._unknown(chat_id)
                return
            if text is None or not text.strip():
                self._send(chat_id, TEXT_ONLY)
                return
            command = text.strip().split()[0].lower() if text.strip().startswith("/") else None
            if command == "/stop":
                links.unlink(db, chat_id)
                self._asking.pop(chat_id, None)
                self._send(chat_id, BYE)
            elif link.role == "seller":
                self._send(chat_id, HELP_SELLER)
            elif command is not None:
                self._send(chat_id, HELP_CUSTOMER.format(example=self._example_product(db)))
            else:
                self._customer_text(db, link, chat_id, text.strip())

    def _start(self, db: Session, chat_id: int, code: str | None) -> None:
        if not code:
            self._send(chat_id, WELCOME)
            return
        now = time.monotonic()
        attempts = [t for t in self._link_attempts.get(chat_id, []) if now - t < LINK_WINDOW_S]
        if len(attempts) >= LINK_ATTEMPTS:
            self._send(chat_id, TOO_MANY_CODES)
            return
        self._link_attempts[chat_id] = attempts + [now]
        self._trim(self._link_attempts)
        link = links.redeem(db, code, chat_id)
        if link is None:
            self._send(chat_id, BAD_CODE)
            return
        self._link_attempts.pop(chat_id, None)
        if link.role == "seller":
            Agent(db).log(None, "telegram_link", "Telegram: a seller chat was connected for task alerts",
                          {"channel": "telegram", "role": "seller"})
            db.commit()
            self._send(chat_id, SELLER_LINKED)
            return
        customer = db.get(Customer, link.customer_id)
        Agent(db).log(None, "telegram_link", f"Telegram: {customer.name} connected a chat",
                      {"channel": "telegram", "role": "customer", "customer_id": customer.id})
        db.commit()
        names = ", ".join(p.name for p in db.scalars(select(Product).order_by(Product.id)))
        self._send(chat_id, f"Connected! You're chatting with TrustDeal for BASIX Store as {customer.name}.\n\n"
                            f"Ask for a discount in your own words, for example “Can I get 10% off "
                            f"{self._example_product(db)}?”\n\nProducts: {names}")

    def _unknown(self, chat_id: int) -> None:
        now = time.monotonic()
        if now - self._unknown_replied.get(chat_id, -UNKNOWN_REPLY_EVERY_S) < UNKNOWN_REPLY_EVERY_S:
            return
        self._unknown_replied[chat_id] = now
        self._trim(self._unknown_replied)
        self._send(chat_id, UNKNOWN)

    @staticmethod
    def _trim(table: dict) -> None:
        if len(table) > MAX_TRACKED_CHATS:
            for key in list(table)[: len(table) - MAX_TRACKED_CHATS]:
                table.pop(key, None)

    @staticmethod
    def _example_product(db: Session) -> str:
        p = (db.scalar(select(Product).where(Product.category != "general").order_by(Product.id))
             or db.scalar(select(Product).order_by(Product.id)))
        return p.name if p else "this item"

    @staticmethod
    def _active_request(db: Session, customer_id: int) -> AgentDeal | None:
        return db.scalar(select(AgentDeal).where(AgentDeal.customer_id == customer_id,
                                                 AgentDeal.state.not_in(CLOSED_STATES))
                         .order_by(AgentDeal.updated_at.desc(), AgentDeal.id.desc()))

    def _customer_text(self, db: Session, link: TelegramLink, chat_id: int, text: str) -> None:
        active = self._active_request(db, link.customer_id)
        pending = self._asking.pop(chat_id, None)
        selected_product = self._selected_products.pop(chat_id, None)
        number = _PLAIN_PCT.match(text)
        try:
            if (pending and number and active is not None and active.id == pending[0]
                    and time.monotonic() - pending[1] < ASK_AGAIN_TTL_S):
                # The answer to "Ask again": the same structured ask as the web's Ask again button.
                req = customer_routes.AskIn(customer_id=link.customer_id, discount=float(number.group(1)))
                customer_routes.button_action(db, req, active.id, "ask", req.discount, channel="telegram")
            else:
                req = customer_routes.MessageIn(customer_id=link.customer_id, text=text, product_id=selected_product,
                                                request_id=active.id if active is not None and selected_product is None else None)
                result = customer_routes.handle_message(db, req, channel="telegram")
                if result.get("product_choices"):
                    buttons = [[_btn(f"{p['name']} · {fmt_inr(p['list_price'])}", f"p:{p['product_id']}")]
                               for p in result["product_choices"]]
                    self._send(chat_id, "Choose a product:", buttons)
        except ValidationError:
            self._send(chat_id, TOO_LONG if len(text) > customer_routes.MAX_MESSAGE else NOT_UNDERSTOOD)
        except HTTPException as exc:
            self._send(chat_id, self._friendly(db, exc))

    def _friendly(self, db: Session, exc: HTTPException) -> str:
        if exc.status_code in (429, 503):
            return str(exc.detail)                   # already customer-friendly (rate limit, busy)
        if exc.status_code == 422 and exc.detail == "choose a product first":
            return PICK_PRODUCT.format(example=self._example_product(db))
        if exc.status_code == 404:
            return GONE
        return NOT_UNDERSTOOD

    # ---------- buttons ----------
    def on_callback(self, cq: dict) -> None:
        callback_id = cq.get("id") if isinstance(cq.get("id"), str) else None
        message = cq.get("message") if isinstance(cq.get("message"), dict) else {}
        chat_id, chat_type = self._chat(message)
        message_id = message.get("message_id") if isinstance(message.get("message_id"), int) else None
        if callback_id is None or chat_id is None or chat_type != "private":
            return
        with self.Session() as db:
            link = links.for_chat(db, chat_id)
            if link is None:
                self._answer(callback_id, "Please connect this chat from the store page first.")
                return
            data = cq.get("data") if isinstance(cq.get("data"), str) else ""
            m = _CALLBACK.match(data)
            if m is None:
                self._answer(callback_id, "This button is no longer valid.")
                return
            if m.group("t"):
                if link.role != "seller":
                    self._answer(callback_id, "This button is no longer valid.")
                    return
                self._resolve_task(db, chat_id, message_id, callback_id, int(m.group("tid")), m.group("t") == "a")
                return
            if link.role != "customer":
                self._answer(callback_id, "This button is no longer valid.")
                return
            if m.group("pid"):
                product = db.get(Product, int(m.group("pid")))
                if product is None:
                    self._answer(callback_id, NOT_OPEN)
                    return
                self._answer(callback_id)
                self._clear(chat_id, message_id)
                self._selected_products[chat_id] = product.id
                self._send(chat_id, f"You chose {product.name} ({fmt_inr(product.list_price)}). Now tell me what discount you'd like, for example 10%.")
                return
            if m.group("c"):
                action = {"a": "accept", "d": "decline", "o": "order", "q": "ask"}[m.group("c")]
                request_id, index = int(m.group("rid")), None
            else:
                action, request_id, index = "switch", int(m.group("srid")), int(m.group("idx"))
            self._customer_button(db, link, chat_id, message_id, callback_id, action, request_id, index)

    def _answer(self, callback_id: str, text: str = "") -> None:
        try:
            self.api.answer_callback(callback_id, text)
        except TelegramError:
            pass                                     # e.g. an old button: the action still counts

    def _clear(self, chat_id: int, message_id: int | None) -> None:
        if message_id is None:
            return
        try:
            self.api.clear_buttons(chat_id, message_id)
        except TelegramError:
            pass

    def _customer_button(self, db: Session, link: TelegramLink, chat_id: int, message_id: int | None,
                         callback_id: str, action: str, request_id: int, index: int | None) -> None:
        deal = db.get(AgentDeal, request_id)
        if deal is None or deal.customer_id != link.customer_id:      # never another customer's request
            self._answer(callback_id, NOT_OPEN)
            return
        if action not in customer_request_view(db, deal)["actions"]:  # same buttons as the Customer page
            self._answer(callback_id, NOT_OPEN)
            self._clear(chat_id, message_id)
            return
        self._answer(callback_id)
        self._clear(chat_id, message_id)                              # a button works once
        if action == "ask":
            self._asking[chat_id] = (request_id, time.monotonic())
            self._trim(self._asking)
            self._send(chat_id, ASK_AGAIN)
            return
        try:
            if action == "switch":
                customer_routes.switch_request(db, customer_routes.SwitchIn(customer_id=link.customer_id, index=index),
                                               request_id, channel="telegram")
            else:
                customer_routes.button_action(db, customer_routes._In(customer_id=link.customer_id), request_id,
                                              action, channel="telegram")
        except HTTPException as exc:
            self._send(chat_id, self._friendly(db, exc))

    def _resolve_task(self, db: Session, chat_id: int, message_id: int | None, callback_id: str, task_id: int,
                      approve: bool) -> None:
        task = db.get(AgentTask, task_id)
        if task is None:
            self._answer(callback_id, "Task not found.")
            return
        if task.kind == "escalation":
            answer = "approve" if approve else "reject"
        else:
            answer = "verified" if approve else "not-verified"
        deal = db.get(AgentDeal, task.agent_deal_id)
        Agent(db).log(deal, "telegram_in", f"Telegram: seller pressed {'Approve' if approve else 'Reject'} "
                                           f"on task #{task.id}", {"task_id": task.id, "channel": "telegram"})
        try:
            seller_routes.resolve(task.id, seller_routes.ResolveIn(answer=answer, reviewer=REVIEWER), db)
        except HTTPException as exc:
            db.rollback()
            self._answer(callback_id, "Already resolved." if exc.status_code == 409 else "Could not resolve this task.")
            self._clear(chat_id, message_id)
            return
        except (OmegaUnavailable, OmegaError):
            db.rollback()
            self._answer(callback_id, "The pricing engine is unavailable. Try again from the Agent inbox.")
            return
        self._answer(callback_id, "Done")
        self._clear(chat_id, message_id)
        label = {"approve": "approved", "reject": "rejected", "verified": "membership verified",
                 "not-verified": "membership not verified"}[answer]
        self._send(chat_id, f"Task #{task.id} {label}. The agent has updated the customer.")

    # ---------- sweep: push what is new ----------
    def sweep(self) -> None:
        """Deliver new sent agent messages (and quote PDFs) to linked customer chats, and new open
        tasks to linked seller chats. Each item is sent once (telegram_deliveries)."""
        with self.Session() as db:
            for link in db.scalars(select(TelegramLink).order_by(TelegramLink.id)).all():
                try:
                    if link.role == "customer":
                        self._deliver_customer(db, link)
                    else:
                        self._alert_seller(db, link)
                except TelegramError as exc:
                    db.rollback()
                    if "HTTP 403" in str(exc):           # the user blocked the bot or deleted the chat
                        links.unlink(db, link.chat_id)
                        logger.warning("Telegram: a linked chat blocked the bot; it was disconnected")
                    else:
                        logger.warning("Telegram: delivery failed (%s)", exc)

    @staticmethod
    def _not_delivered(chat_id: int, kind: str, ref):
        return ~exists().where(and_(TelegramDelivery.chat_id == chat_id, TelegramDelivery.kind == kind,
                                    TelegramDelivery.ref_id == ref))

    def _delivered(self, db: Session, chat_id: int, kind: str, ref_id: int, sent, deal: AgentDeal | None,
                   summary: str, detail: dict, decision_id: int | None = None) -> None:
        db.add(TelegramDelivery(chat_id=chat_id, kind=kind, ref_id=ref_id, telegram_message_id=_message_id(sent)))
        Agent(db).log(deal, "telegram_alert" if kind == "task_alert" else "telegram_out", summary,
                      {**detail, "channel": "telegram"}, decision_id=decision_id)
        db.commit()

    def _deliver_customer(self, db: Session, link: TelegramLink) -> None:
        pending = db.scalars(
            select(AgentMessage).join(AgentDeal, AgentDeal.id == AgentMessage.agent_deal_id)
            .where(AgentDeal.customer_id == link.customer_id, AgentMessage.sender == "agent",
                   AgentMessage.status == "sent", AgentMessage.channel == "telegram",
                   AgentMessage.id > link.after_message_id,
                   self._not_delivered(link.chat_id, "message", AgentMessage.id))
            .order_by(AgentMessage.id)).all()
        for m in pending:
            deal = db.get(AgentDeal, m.agent_deal_id)
            view = customer_request_view(db, deal)                      # allowlisted
            safe = next((x for x in view["messages"] if x["message_id"] == m.id), None)
            if safe is None:
                continue
            last_agent = next((x for x in reversed(view["messages"]) if x["sender"] == "agent"), None)
            text, buttons = customer_message(view, safe, latest=last_agent is safe)
            sent = self._send(link.chat_id, text, buttons)
            self._delivered(db, link.chat_id, "message", m.id, sent, deal, f"Telegram: sent the {m.kind} reply",
                            {"message_id": m.id}, m.decision_id)
            card = safe.get("card") or {}
            if card.get("type") == "quote":
                self._send_quote_pdf(db, link, deal, card.get("quote_ref"))
            elif card.get("type") == "order":
                self._send_invoice_pdf(db, link, deal)

    def _send_quote_pdf(self, db: Session, link: TelegramLink, deal: AgentDeal, ref: str | None) -> None:
        quote = db.scalar(select(AgentQuote).where(AgentQuote.agent_deal_id == deal.id,
                                                   self._not_delivered(link.chat_id, "quote_pdf", AgentQuote.id))
                          .order_by(AgentQuote.id.desc()))
        if quote is None or quote_ref(quote.id) != ref:
            return
        pdf = render_quote_pdf(customer_quote_document(db, deal, quote))
        sent = self.api.send_document(link.chat_id, f"TrustDeal-{ref}.pdf", pdf, f"Your quote {ref} (PDF)")
        self._delivered(db, link.chat_id, "quote_pdf", quote.id, sent, deal, f"Telegram: sent quote PDF {ref}",
                        {"quote_ref": ref}, quote.decision_id)

    def _send_invoice_pdf(self, db: Session, link: TelegramLink, deal: AgentDeal) -> None:
        """The GST tax invoice after an order (once per chat; built from the customer-safe allowlist)."""
        found = db.execute(select(AgentInvoice, AgentQuote).join(AgentQuote, AgentQuote.id == AgentInvoice.quote_id)
                           .where(AgentQuote.agent_deal_id == deal.id,
                                  self._not_delivered(link.chat_id, "invoice_pdf", AgentInvoice.id))
                           .order_by(AgentInvoice.id.desc())).first()
        if found is None:
            return
        invoice, quote = found
        ref = invoice_ref(invoice.id)
        pdf = render_invoice_pdf(customer_invoice_document(db, deal, quote, invoice))
        sent = self.api.send_document(link.chat_id, f"TrustDeal-{ref}.pdf", pdf, f"Your tax invoice {ref} (PDF)")
        self._delivered(db, link.chat_id, "invoice_pdf", invoice.id, sent, deal, f"Telegram: sent tax invoice {ref}",
                        {"invoice_no": ref}, quote.decision_id)

    def _alert_seller(self, db: Session, link: TelegramLink) -> None:
        tasks = db.scalars(select(AgentTask).where(AgentTask.status == "open", AgentTask.id > link.after_task_id,
                                                   self._not_delivered(link.chat_id, "task_alert", AgentTask.id))
                           .order_by(AgentTask.id)).all()
        for task in tasks:
            deal = db.get(AgentDeal, task.agent_deal_id)
            sent = self._send(link.chat_id, seller_alert(db, task), [[_btn("Approve ✓", f"ta:{task.id}"),
                                                                      _btn("Reject ✗", f"tr:{task.id}")]])
            self._delivered(db, link.chat_id, "task_alert", task.id, sent, deal,
                            f"Telegram: alerted the seller about task #{task.id} ({task.kind})",
                            {"task_id": task.id}, task.decision_id)
        summaries = db.scalars(
            select(AgentActivity).where(AgentActivity.kind == "daily_summary",
                                        AgentActivity.created_at > link.linked_at,
                                        self._not_delivered(link.chat_id, "seller_summary", AgentActivity.id))
            .order_by(AgentActivity.id)).all()
        for summary in summaries:
            try:
                text = json.loads(summary.detail_json or "{}").get("summary_text")
            except (TypeError, ValueError):
                continue
            if not isinstance(text, str) or not text:
                continue
            sent = self._send(link.chat_id, text)
            detail = json.loads(summary.detail_json or "{}")
            self._delivered(db, link.chat_id, "seller_summary", summary.id, sent, None,
                            f"Telegram: sent daily summary for {detail.get('day_ist', 'today')}",
                            {"activity_id": summary.id, "day_ist": detail.get("day_ist")})


def seller_alert(db: Session, task: AgentTask) -> str:
    """Seller-only alert. Kept short (Telegram is a third party): the full audit trail stays in the inbox."""
    t = seller_task(db, task)
    deal = db.get(AgentDeal, task.agent_deal_id)
    customer, product = db.get(Customer, deal.customer_id), db.get(Product, deal.product_id)
    decision = db.get(AgentDecision, task.decision_id) if task.decision_id else None
    asked = f" · asked {fmt_pct(deal.discount_asked)}%" if deal.discount_asked is not None else ""
    qty = f" × {deal.quantity}" if deal.quantity > 1 else ""
    meaning = ("Approve = give the asked discount (capped by the pricing limits); Reject = counter at the allowed max."
               if task.kind == "escalation" else "Approve = membership verified; Reject = not verified.")
    lines = [f"TrustDeal · task #{t['task_id']} needs your decision ({t['kind']})", t["title"],
             f"Request #{deal.id} · {customer.name} ({customer.tier}) · {product.name}{qty}{asked}"]
    if decision is not None:
        lines.append(f"MeTTa decision: {decision.result}")
    lines += [meaning, f"Full audit trail: Agent inbox, request #{deal.id}."]
    return "\n".join(lines)
