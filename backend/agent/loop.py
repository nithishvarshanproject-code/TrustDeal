"""The autonomous agent loop: perceive -> decide -> act -> follow up -> close.

- Perceive: the language layer (ASI:One, or the rule-based parser) turns a customer message
  into validated fields and picks information tools.
- Decide: MeTTa makes the discount decision (engine/rules.metta) and the NEXT ACTION
  (engine/agent.metta), through the same runner (Omega in omega mode).
- Act: Python only executes the action MeTTa chose (quote, message, task, order, close);
  every price and discount comes from MeTTa (quote-terms, alternatives).
- Follow up: after an action, MeTTa is asked again ("tick") until it says wait or close.
Every step is written to the activity table, linked to the decision's audit trail.
"""
import json
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agent import language, tools
from backend.models import (AgentActivity, AgentDeal, AgentDecision, AgentInvoice, AgentMessage, AgentQuote,
                            AgentSetting, AgentTask, Customer, Product)
from backend.services import gst, ledger, quote_seal
from backend.services.explain import explain
from engine import bridge

DECISION_EVENTS = {"new-request", "customer-ask", "verified", "not-verified"}
CHANNELS = ("web", "telegram")
FOLLOW_UP_STATES = {"ORDERED", "DECLINED"}
MAX_STEPS = 5
MODES = ("auto-send", "draft-for-approval")
EXAMPLE_ASK = 10       # used in the clarifying question ("for example 10%")

# Friendly, generic reasons (never a rule, a cost or a margin).
REASON_BEST = "This is the best price we can offer on this item."
REASON_BELOW = "We can't go that low on this item, but this is the best price we can offer."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def quote_ref(quote_id: int) -> str:
    return f"Q-{quote_id:05d}"


def order_ref(quote_id: int) -> str:
    return f"ORD-{quote_id:05d}"


def invoice_ref(invoice_id: int) -> str:
    return f"INV-{invoice_id:05d}"


def alternative_card(option: dict, original: Product) -> dict:
    """Customer card for a MeTTa-approved alternative. A different product is compared by price
    ("X less than <original>"); only a discount on the same product is called savings."""
    card = {k: option[k] for k in ("product_id", "product_name", "quantity", "discount", "unit_price",
                                   "total", "kind")}
    if option["product_id"] == original.id:
        card["savings"] = option["savings"]
    else:
        card["price_difference"] = option["savings"]     # MeTTa: original list x qty - this total
        card["compared_to"] = original.name
    return card


class Agent:
    def __init__(self, db: Session, channel: str = "web"):
        """channel: where the customer is writing from (web | telegram). It only labels the messages
        and the request (replies go back to the same channel); it never changes a decision."""
        if channel not in CHANNELS:
            raise ValueError(f"channel must be one of {', '.join(CHANNELS)}")
        self.db = db
        self.channel = channel
        self._wanted_tools: set[str] = set()

    # ---------- settings ----------
    def mode(self) -> str:
        row = self.db.get(AgentSetting, "mode")
        return row.value if row and row.value in MODES else "auto-send"

    def set_mode(self, mode: str) -> str:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        row = self.db.get(AgentSetting, "mode")
        if row is None:
            self.db.add(AgentSetting(key="mode", value=mode))
        else:
            row.value = mode
        self.log(None, "setting", f"Autonomy set to {mode}")
        self.db.commit()
        return mode

    # ---------- activity log ----------
    def log(self, deal: AgentDeal | None, kind: str, summary: str, detail: dict | None = None,
            decision_id: int | None = None) -> AgentActivity:
        row = AgentActivity(agent_deal_id=deal.id if deal else None, kind=kind, summary=summary,
                            detail_json=json.dumps(detail, default=str) if detail else None,
                            decision_id=decision_id)
        self.db.add(row)
        self.db.flush()
        return row

    # ---------- data ----------
    def catalog(self) -> list[dict]:
        """Customer-safe catalog (never cost prices)."""
        return [{"product_id": p.id, "name": p.name, "category": p.category, "list_price": p.list_price}
                for p in self.db.scalars(select(Product).order_by(Product.id))]

    def customer(self, deal: AgentDeal) -> Customer:
        return self.db.get(Customer, deal.customer_id)

    def product(self, deal: AgentDeal) -> Product:
        return self.db.get(Product, deal.product_id)

    def deal_input(self, deal: AgentDeal, product: Product | None = None, discount: float | None = None) -> dict:
        """The MeTTa (deal ...) for this request: the customer plays the seller's role in the rules
        (loyalty tier = record tier, order history = payment history, their requests = R7)."""
        c, p = self.customer(deal), product or self.product(deal)
        return {
            "record_tier": deal.verified_tier or c.tier,
            "claimed_tier": deal.claimed_tier,
            "cost_price": p.cost_price, "list_price": p.list_price,
            "quantity": deal.quantity,
            "discount_requested": discount if discount is not None else (deal.discount_asked or 0.0),
            "late_payments": c.late_payments, "total_orders": c.total_orders,
            "competitor_price": None, "competitor_verified": False,
            "requests_this_month": deal.requests_this_month,
            "seller_name": c.name, "category": p.category,
        }

    def last_decision(self, deal: AgentDeal) -> AgentDecision | None:
        return self.db.scalar(select(AgentDecision).where(AgentDecision.agent_deal_id == deal.id)
                              .order_by(AgentDecision.id.desc()))

    def open_quote(self, deal: AgentDeal) -> AgentQuote | None:
        return self.db.scalar(select(AgentQuote).where(AgentQuote.agent_deal_id == deal.id,
                                                       AgentQuote.status == "open")
                              .order_by(AgentQuote.id.desc()))

    def last_offer_card(self, deal: AgentDeal) -> dict | None:
        msg = self.db.scalar(select(AgentMessage).where(AgentMessage.agent_deal_id == deal.id,
                                                        AgentMessage.kind == "offer")
                             .order_by(AgentMessage.id.desc()))
        return json.loads(msg.card_json) if msg and msg.card_json else None

    # ---------- starting and continuing ----------
    def new_deal(self, customer: Customer, product_id: int, quantity: int = 1,
                 discount: float | None = None, claimed_tier: str | None = None) -> AgentDeal:
        month_start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        earlier = self.db.scalar(select(func.count(AgentDeal.id)).where(
            AgentDeal.customer_id == customer.id, AgentDeal.created_at >= month_start))
        deal = AgentDeal(customer_id=customer.id, product_id=product_id, quantity=quantity,
                         discount_asked=discount, claimed_tier=claimed_tier, state="NEW", round=1,
                         requests_this_month=earlier, channel=self.channel)
        self.db.add(deal)
        self.db.flush()
        self.log(deal, "perceived", f"New request from {customer.name} for {self.product(deal).name}")
        return deal

    def customer_says(self, deal: AgentDeal, text: str, kind: str = "text", voice: bool = False) -> None:
        deal.channel = self.channel
        # Voice is browser speech-to-text on the web page: only this message is labelled, the
        # request stays on "web" (same replies, same stats).
        channel = "web-voice" if voice and self.channel == "web" else self.channel
        msg = AgentMessage(agent_deal_id=deal.id, sender="customer", text=text, status="sent",
                           source="customer", kind=kind, channel=channel)
        self.db.add(msg)
        self.db.flush()
        if self.channel == "telegram":
            self.log(deal, "telegram_in", f"Telegram: customer {'pressed a button' if kind == 'action' else 'wrote'}",
                     {"message_id": msg.id, "channel": "telegram"})

    def on_customer_message(self, customer: Customer, text: str, focus_product_id: int | None,
                            deal: AgentDeal | None, voice: bool = False) -> AgentDeal | dict:
        """A free-text message from the customer (the chat). voice=True only labels the stored
        message "web-voice" (typed or spoken, the text takes exactly the same path)."""
        focus = deal.product_id if deal is not None else focus_product_id
        understood = language.understand(text, self.catalog(), focus)
        fields = understood["fields"]
        catalog = self.catalog()
        if language.wants_catalog(text):
            return {"request_id": None, "product_choices": catalog, "show_products": True, "messages": [],
                    "status": "NEW", "actions": []}
        choices = language.product_choices(text, catalog, focus)
        if deal is None and focus is None and understood.get("unclear_product") and choices:
            return {"request_id": None, "product_choices": choices, "messages": [], "status": "NEW", "actions": []}
        product_id = fields["product_id"] or focus
        if product_id is None:
            raise ValueError("choose a product first")

        switching = deal is not None and fields["intent"] == "ask" and product_id != deal.product_id
        if deal is None or deal.state in ("CLOSED", "ORDERED", "DECLINED") or switching:
            deal = self.new_deal(customer, product_id, fields["quantity"] or 1, None, fields["claimed_tier"])
        self.customer_says(deal, text, voice=voice)
        self.log(deal, "perceived",
                 f"Understood ({understood['source']}): intent {fields['intent']}"
                 + (f", {fields['discount_asked']:g}% asked" if fields["discount_asked"] is not None else ""),
                 {"fields": fields, "source": understood["source"]})
        if understood["fallback_reason"]:
            self.log(deal, "language_unavailable", f"language model unavailable: {understood['fallback_reason']}")

        self._wanted_tools = {t["name"] for t in understood["tools"]}
        chooser = "Language model chose" if understood["source"] == "llm" else "Default choice (no language model):"
        for t in understood["tools"]:
            self.log(deal, "tool_choice", f"{chooser} tool {t['name']}", {"arguments": t["arguments"]})
        if "market_price_lookup" in self._wanted_tools:
            self.run_market_lookup(deal)

        intent = fields["intent"]
        if intent == "ask":
            if fields["quantity"]:
                deal.quantity = fields["quantity"]
            if fields["claimed_tier"] and deal.state == "NEW":
                deal.claimed_tier = fields["claimed_tier"]
            deal.discount_asked = fields["discount_asked"]
            self.run(deal, "new-request" if deal.state == "NEW" else "customer-ask")
        elif intent == "accept":
            self.run(deal, "customer-accept")
        elif intent == "decline":
            self.run(deal, "customer-decline")
        elif intent == "question":
            self.answer_question(deal)
        else:
            self.clarify(deal)
        self.db.commit()
        return deal

    # ---------- buttons (structured customer actions) ----------
    def on_button(self, deal: AgentDeal, action: str, discount: float | None = None) -> AgentDeal:
        if action == "ask":
            self.customer_says(deal, f"Could you do {language.fmt_pct(discount)}% off?", kind="action")
            deal.discount_asked = discount
            self._wanted_tools = {"suggest_alternatives"}
            self.run(deal, "new-request" if deal.state == "NEW" else "customer-ask")
        elif action == "accept":
            self.customer_says(deal, "I accept the offer.", kind="action")
            self.run(deal, "customer-accept")
        elif action == "decline":
            self.customer_says(deal, "No thanks.", kind="action")
            self.run(deal, "customer-decline")
        elif action == "order":
            self.customer_says(deal, "Place the order.", kind="action")
            self.run(deal, "order-placed")
        else:
            raise ValueError(f"unknown action {action!r}")
        self.db.commit()
        return deal

    def switch_to_alternative(self, deal: AgentDeal, index: int) -> AgentDeal:
        card = self.last_offer_card(deal)
        options = (card or {}).get("alternatives") or []
        if deal.state != "WAITING_CUSTOMER" or not 0 <= index < len(options):
            raise ValueError("that option is not available")
        option = options[index]
        self.customer_says(deal, f"I'll take the {option['product_name']} option instead.", kind="action")
        self.run(deal, "customer-decline", announce_close=False)
        new = self.new_deal(self.customer(deal), option["product_id"], option["quantity"], option["discount"])
        self.log(new, "perceived", f"Switched from request #{deal.id} to an alternative",
                 {"from_request": deal.id, "option": option})
        self.run(new, "new-request")
        self.db.commit()
        return new

    # ---------- humans ----------
    def resolve_task(self, task: AgentTask, answer: str, reviewer: str) -> AgentDeal:
        events = {("escalation", "approve"): "manager-approved", ("escalation", "reject"): "manager-rejected",
                  ("verification", "verified"): "verified", ("verification", "not-verified"): "not-verified"}
        event = events.get((task.kind, answer))
        if event is None:
            raise ValueError(f"answer for a {task.kind} task must be one of "
                             f"{', '.join(a for k, a in events if k == task.kind)}")
        if task.status != "open":
            raise ValueError("task is already resolved")
        deal = self.db.get(AgentDeal, task.agent_deal_id)
        task.status, task.answer, task.resolved_by, task.resolved_at = "done", answer, reviewer, _now()
        self.log(deal, "task_resolved", f"{reviewer} answered the {task.kind} task: {answer}",
                 {"task_id": task.id})
        ledger.append(self.db, "task_resolution", f"task_resolution:{task.id}", {
            "task_id": task.id, "request_id": deal.id, "kind": task.kind, "answer": answer, "reviewer": reviewer})
        if event == "verified":
            deal.verified_tier = deal.claimed_tier
        self.run(deal, event)
        self.db.commit()
        return deal

    def approve_message(self, message: AgentMessage, reviewer: str) -> None:
        if message.status != "draft":
            raise ValueError("message is not a draft")
        message.status = "sent"
        self.log(self.db.get(AgentDeal, message.agent_deal_id), "message_sent",
                 f"{reviewer} approved and sent a drafted reply", {"message_id": message.id})
        self.db.commit()

    # ---------- decide ----------
    def decide(self, deal: AgentDeal, event: str) -> AgentDecision:
        """The MeTTa discount decision for the current ask (same runner as the Seller app)."""
        deal_input = self.deal_input(deal)
        started = time.perf_counter()
        verdict = bridge.evaluate_deal(deal_input, include_what_if=False)
        elapsed = time.perf_counter() - started
        audit = {"input": deal_input, "engine": {"runner": bridge.runner_name(), "elapsed_s": round(elapsed, 3)},
                 "trail": verdict["trail"], "override_hint": verdict["override_hint"],
                 "explanation": explain(verdict["trail"], verdict["override_hint"])}
        row = AgentDecision(agent_deal_id=deal.id, round=deal.round, event=event, result=verdict["result"],
                            approved_discount=verdict["approved_discount"], confidence=verdict["confidence"],
                            audit_json=json.dumps(audit))
        self.db.add(row)
        self.db.flush()
        ledger.record_decision(self.db, source="agent", decision_id=row.id, case_id=deal.id,
                               party=self.customer(deal).name, product=self.product(deal).name,
                               deal_input=deal_input, verdict=verdict, runner=audit["engine"]["runner"], event=event)
        offered = "" if row.approved_discount is None else f" {row.approved_discount:g}%"
        self.log(deal, "metta_decision", f"MeTTa decision: {row.result}{offered} for {deal_input['discount_requested']:g}% asked",
                 {"result": row.result, "approved_discount": row.approved_discount,
                  "confidence": row.confidence, "runner": audit["engine"]["runner"]}, decision_id=row.id)
        return row

    # ---------- the loop ----------
    def run(self, deal: AgentDeal, event: str, announce_close: bool = True) -> None:
        """Ask MeTTa what to do for this event, do it, and follow up until MeTTa waits."""
        for _ in range(MAX_STEPS):
            if event == "customer-ask" and deal.state == "WAITING_CUSTOMER":
                deal.round += 1
            needs_decision = event in DECISION_EVENTS and (
                event != "customer-ask" or deal.state == "WAITING_CUSTOMER")
            decision = self.decide(deal, event) if needs_decision else self.last_decision(deal)
            if decision is None:          # e.g. "no thanks" before any ask: decide on the list price
                decision = self.decide(deal, event)
            offer = decision.approved_discount if needs_decision else (deal.last_offer
                                                                      if deal.last_offer is not None
                                                                      else decision.approved_discount)
            step = bridge.next_action(deal.state, decision.result, offer, self.deal_input(deal), event, deal.round)
            activity = self.log(deal, "next_action",
                                f"[{step['rule_id']}] {deal.state} + {event} -> {step['action']} -> {step['state']}",
                                {"rule_id": step["rule_id"], "event": event, "from": deal.state,
                                 "action": step["action"], "to": step["state"], "offer": step["offer"],
                                 "round": deal.round}, decision_id=decision.id)
            ledger.append(self.db, "agent_action", f"agent_action:{activity.id}", {
                "request_id": deal.id, "decision_id": decision.id, "rule_id": step["rule_id"], "event": event,
                "action": step["action"], "from": deal.state, "to": step["state"], "offer": step["offer"],
                "round": deal.round})
            self.execute(deal, step, event, decision, announce_close)
            deal.state = step["state"]
            deal.updated_at = _now()
            self.db.flush()
            if step["action"] == "wait" or deal.state == "CLOSED" or deal.state not in FOLLOW_UP_STATES:
                return
            event = "tick"

    # ---------- act ----------
    def execute(self, deal: AgentDeal, step: dict, event: str, decision: AgentDecision,
                announce_close: bool) -> None:
        action = step["action"]
        if action == "send-counter":
            self.send_offer(deal, step["offer"], decision, REASON_BELOW if step["rule_id"] == "A4" else REASON_BEST)
        elif action == "create-quote":
            self.create_quote(deal, step["offer"], decision)
        elif action == "send-decline":
            self.send_decline(deal, decision, final=step["rule_id"] == "A8")
        elif action == "create-escalation-task":
            self.create_task(deal, "escalation", decision)
        elif action == "request-verification":
            self.create_task(deal, "verification", decision)
        elif action == "place-order":
            self.place_order(deal, decision)
        elif action == "close":
            if event == "customer-decline" and announce_close:
                self.reply(deal, "closed", {"message": "request closed at the customer's wish"},
                           "No problem, your request is closed. Let us know if there's anything else we can do.",
                           set(), [], decision)
        elif action == "wait" and event in ("customer-ask", "customer-accept", "order-placed"):
            self.status_reply(deal, decision)

    def terms(self, deal: AgentDeal, offer: float) -> dict:
        terms = bridge.quote_terms(self.deal_input(deal), offer)
        if terms is None:   # agent.metta only offers what quote-terms accepts; never price around it
            raise RuntimeError(f"MeTTa refused the terms of a {offer}% offer it chose")
        return terms

    def send_offer(self, deal: AgentDeal, offer: float, decision: AgentDecision, reason: str) -> None:
        p = self.product(deal)
        t = self.terms(deal, offer)
        deal.last_offer = offer
        options = self.alternatives(deal, p) if "suggest_alternatives" in self._wanted_tools else []
        card = {"type": "offer", "status": "counter", "product_name": p.name, "quantity": deal.quantity,
                "list_price": p.list_price, "discount": t["discount"], "unit_price": t["unit_price"],
                "total": t["total"], "savings": t["savings"], "reason": reason,
                "alternatives": [alternative_card(o, p) for o in options]}
        facts = {"situation": "counter offer", "product": p.name, "quantity": deal.quantity,
                 "discount_percent": t["discount"], "price_each": t["unit_price"], "total": t["total"],
                 "you_save": t["savings"], "reason": reason, "question": "Would you like to accept this offer?"}
        allowed = {t["discount"], t["unit_price"], t["total"], t["savings"], p.list_price, float(deal.quantity)}
        template = (f"{reason} {language.fmt_pct(t['discount'])}% off {p.name} brings it to "
                    f"{language.fmt_inr(t['unit_price'])}"
                    + (f" each ({deal.quantity} units: {language.fmt_inr(t['total'])})" if deal.quantity > 1 else "")
                    + f", so you save {language.fmt_inr(t['savings'])}. Would you like to accept this offer?")
        if options:
            template += " We also found cheaper options for you below."
        self.reply(deal, "offer", facts, template, allowed, [t["discount"]], decision, card)

    def alternatives(self, deal: AgentDeal, product: Product) -> list[dict]:
        original = self.deal_input(deal, product)
        try:
            options = tools.suggest_alternatives(self.db, product, original,
                                                 lambda other: self.deal_input(deal, other, 0.0))
        except Exception as exc:          # a tool failing never stops the deal
            self.log(deal, "tool_unavailable", f"tool unavailable: suggest_alternatives ({exc.__class__.__name__})")
            return []
        self.log(deal, "tool_call", f"suggest_alternatives: {len(options)} option(s) approved by MeTTa",
                 {"tool": "suggest_alternatives", "input": {"product_id": product.id, "quantity": deal.quantity,
                                                             "discount_asked": deal.discount_asked},
                  "result": options})
        return options

    def create_quote(self, deal: AgentDeal, offer: float, decision: AgentDecision) -> None:
        p = self.product(deal)
        t = self.terms(deal, offer)
        deal.last_offer = offer
        quote = AgentQuote(agent_deal_id=deal.id, discount=t["discount"], unit_price=t["unit_price"],
                           total=t["total"], savings=t["savings"], quantity=deal.quantity,
                           valid_until=_now() + timedelta(hours=t["valid_hours"]), decision_id=decision.id,
                           list_price=p.list_price)
        self.db.add(quote)
        self.db.flush()
        ref = quote_ref(quote.id)
        quote.verify_code = quote_seal.code_for(ref, quote, deal)     # HMAC over the signed fields
        ledger.append(self.db, "quote", f"quote:{quote.id}", {
            "quote_id": quote.id, "quote_ref": ref, "request_id": deal.id, "decision_id": decision.id,
            "product": p.name, "quantity": quote.quantity, "list_price": quote.list_price,
            "discount": quote.discount, "unit_price": quote.unit_price, "total": quote.total,
            "savings": quote.savings, "valid_until": quote.valid_until.isoformat(),
            "verify_code": quote_seal.format_code(quote.verify_code)})
        self.log(deal, "tool_call", f"create_quote {ref}: {t['discount']:g}% off, total {language.fmt_inr(t['total'])}",
                 {"tool": "create_quote", "quote_id": quote.id, **t}, decision_id=decision.id)
        card = {"type": "quote", "quote_ref": ref, "product_name": p.name, "quantity": deal.quantity,
                "list_price": p.list_price, "discount": t["discount"], "unit_price": t["unit_price"],
                "total": t["total"], "savings": t["savings"], "valid_hours": t["valid_hours"],
                "valid_until": quote.valid_until.isoformat()}
        facts = {"situation": "discount approved, quote created", "product": p.name, "quantity": deal.quantity,
                 "discount_percent": t["discount"], "total": t["total"], "you_save": t["savings"],
                 "quote": ref, "valid_for_hours": t["valid_hours"], "next_step": "place the order"}
        allowed = {t["discount"], t["unit_price"], t["total"], t["savings"], p.list_price, float(deal.quantity),
                   float(t["valid_hours"]), float(quote.id)}
        if t["discount"] > 0:
            template = (f"Good news! Your {language.fmt_pct(t['discount'])}% discount on {p.name} is confirmed. "
                        f"Quote {ref}: {language.fmt_inr(t['total'])} (you save {language.fmt_inr(t['savings'])}), "
                        f"valid for {t['valid_hours']} hours. Place the order whenever you're ready.")
        else:   # e.g. a cheaper model at its list price
            facts = {k: v for k, v in facts.items() if k not in ("discount_percent", "you_save")}
            facts["situation"] = "quote created at the list price"
            template = (f"Great choice! Quote {ref}: {p.name} for {language.fmt_inr(t['total'])}, "
                        f"valid for {t['valid_hours']} hours. Place the order whenever you're ready.")
        self.reply(deal, "quote", facts, template, allowed, [t["total"]], decision, card)

    def send_decline(self, deal: AgentDeal, decision: AgentDecision, final: bool) -> None:
        p = self.product(deal)
        if final:
            text = ("We've reached our final offer on this item, so we can't go further this time. "
                    "Thank you for asking!")
        else:
            text = f"Sorry, we can't offer a discount on {p.name} for this request. Thank you for asking!"
        self.reply(deal, "decline", {"situation": "discount declined", "product": p.name,
                                     "final_offer_reached": final}, text, set(), [], decision,
                   {"type": "decline", "product_name": p.name})

    def create_task(self, deal: AgentDeal, kind: str, decision: AgentDecision) -> None:
        c, p = self.customer(deal), self.product(deal)
        if kind == "escalation":
            title = f"Approve {deal.discount_asked:g}% off {p.name} for {c.name}?"
            text = "Thanks! A manager is reviewing your request. We'll update you here shortly."
        else:
            title = f"Verify {c.name}'s {deal.claimed_tier} membership (record: {c.tier})"
            text = (f"Thanks! A manager is confirming your {deal.claimed_tier} membership. "
                    "We'll update you here shortly.")
        task = AgentTask(agent_deal_id=deal.id, kind=kind, title=title, decision_id=decision.id)
        self.db.add(task)
        self.db.flush()
        self.log(deal, "tool_call", f"create_task #{task.id} ({kind}): {title}",
                 {"tool": "create_task", "task_id": task.id, "kind": kind}, decision_id=decision.id)
        self.reply(deal, "pending", {"situation": f"waiting for a manager ({kind})"}, text, set(), [], decision,
                   {"type": "pending", "reason": "manager-review"})

    def place_order(self, deal: AgentDeal, decision: AgentDecision) -> None:
        quote = self.open_quote(deal)
        if quote is None:
            raise RuntimeError("no open quote to order")
        quote.status, quote.ordered_at = "ordered", _now()
        quote.order_ref = order_ref(quote.id)
        p = self.product(deal)
        ledger.append(self.db, "order", f"order:{quote.id}", {
            "quote_id": quote.id, "quote_ref": quote_ref(quote.id), "order_ref": quote.order_ref,
            "request_id": deal.id, "decision_id": decision.id, "product": p.name, "quantity": quote.quantity,
            "total": quote.total})
        self.create_invoice(deal, quote, p, decision)
        self.log(deal, "tool_call", f"place_order {quote.order_ref} for quote {quote_ref(quote.id)}",
                 {"tool": "place_order", "quote_id": quote.id, "order_ref": quote.order_ref}, decision_id=decision.id)
        card = {"type": "order", "order_ref": quote.order_ref, "quote_ref": quote_ref(quote.id),
                "product_name": p.name, "quantity": quote.quantity, "total": quote.total}
        facts = {"situation": "order confirmed", "order": quote.order_ref, "product": p.name,
                 "quantity": quote.quantity, "total": quote.total}
        allowed = {quote.total, float(quote.quantity), float(quote.id), quote.unit_price, quote.discount}
        template = (f"Your order {quote.order_ref} is confirmed: {quote.quantity} x {p.name} for "
                    f"{language.fmt_inr(quote.total)}. Thank you for shopping with us!")
        self.reply(deal, "order", facts, template, allowed, [quote.total], decision, card)

    def create_invoice(self, deal: AgentDeal, quote: AgentQuote, p: Product, decision: AgentDecision) -> AgentInvoice:
        """The GST tax invoice for this order, in the order's transaction. The total is the quote total
        (catalog prices include GST); gst.py only splits taxable value and tax inside it."""
        cfg = gst.load_config()
        split = gst.split_inclusive(quote.total, cfg["cgst_rate"], cfg["sgst_rate"])
        invoice = AgentInvoice(quote_id=quote.id, issued_at=quote.ordered_at, hsn=gst.hsn_for(p.category, cfg),
                               quantity=quote.quantity, unit_price=quote.unit_price,
                               taxable_value=float(split["taxable"]), cgst_rate=float(cfg["cgst_rate"]),
                               cgst=float(split["cgst"]), sgst_rate=float(cfg["sgst_rate"]), sgst=float(split["sgst"]),
                               total=float(split["total"]), seller_name=cfg["seller_name"],
                               seller_gstin=cfg["seller_gstin"], gstin_is_demo=cfg["seller_gstin_is_demo"],
                               place_of_supply=cfg["place_of_supply"])
        self.db.add(invoice)
        self.db.flush()
        ref = invoice_ref(invoice.id)
        inr = language.fmt_inr
        self.log(deal, "tool_call", f"create_invoice {ref} for {quote.order_ref}: total {inr(invoice.total)} "
                                    f"incl. CGST {inr(invoice.cgst)} + SGST {inr(invoice.sgst)}",
                 {"tool": "create_invoice", "invoice_id": invoice.id, "quote_id": quote.id}, decision_id=decision.id)
        ledger.append(self.db, "invoice", f"invoice:{quote.id}", {
            "invoice_no": ref, "quote_ref": quote_ref(quote.id), "order_ref": quote.order_ref, "request_id": deal.id,
            "hsn": invoice.hsn, "quantity": invoice.quantity, "unit_price": invoice.unit_price,
            "taxable_value": invoice.taxable_value, "cgst_rate": invoice.cgst_rate, "cgst": invoice.cgst,
            "sgst_rate": invoice.sgst_rate, "sgst": invoice.sgst, "total": invoice.total})
        return invoice

    def status_reply(self, deal: AgentDeal, decision: AgentDecision) -> None:
        if deal.state in ("ESCALATED", "WAITING_VERIFICATION"):
            text = "A manager is still reviewing your request. We'll update you here as soon as it's done."
        elif deal.state == "QUOTED":
            text = "Your quote is ready below. Place the order whenever you're ready."
        else:
            text = "Thanks! Let us know how we can help with this request."
        self.reply(deal, "status", {"situation": "status update", "state": deal.state}, text, set(), [], decision)

    def answer_question(self, deal: AgentDeal) -> None:
        p = self.product(deal)
        text = (f"{p.name} is {language.fmt_inr(p.list_price)}. If you'd like a discount, tell me how much "
                f"you're hoping for (for example {language.fmt_pct(EXAMPLE_ASK)}%).")
        self.reply(deal, "answer", {"situation": "product question", "product": p.name,
                                    "list_price": p.list_price, "example_discount_percent": EXAMPLE_ASK},
                   text, {p.list_price, EXAMPLE_ASK}, [], None)

    def clarify(self, deal: AgentDeal) -> None:
        p = self.product(deal)
        text = (f"Happy to help with {p.name}! How much of a discount are you hoping for "
                f"(for example {language.fmt_pct(EXAMPLE_ASK)}%)?")
        self.log(deal, "perceived", "Unclear request: asking a clarifying question")
        self.reply(deal, "clarify", {"situation": "ask which discount they want", "product": p.name,
                                     "example_discount_percent": EXAMPLE_ASK},
                   text, {EXAMPLE_ASK}, [], None)

    # ---------- messages ----------
    def reply(self, deal: AgentDeal, kind: str, facts: dict, template: str, allowed: set[float],
              required: list[float], decision: AgentDecision | None, card: dict | None = None) -> AgentMessage:
        """Draft (ASI:One) or template, checked by the number guardrail; sent or held as a draft."""
        p = self.product(deal)
        allowed = set(allowed) | set(language.numbers_in(p.name)) | set(language.numbers_in(template))
        drafted = language.draft_reply(facts, template, allowed, required)
        if drafted["reason"]:
            unavailable = drafted["reason"].startswith("language model unavailable")
            self.log(deal, "language_unavailable" if unavailable else "guardrail",
                     drafted["reason"] + " -> template used")
        status = "draft" if self.mode() == "draft-for-approval" else "sent"
        msg = AgentMessage(agent_deal_id=deal.id, sender="agent", text=drafted["text"], status=status,
                           source=drafted["source"], kind=kind, card_json=json.dumps(card) if card else None,
                           decision_id=decision.id if decision else None, channel=deal.channel)
        self.db.add(msg)
        self.db.flush()
        self.log(deal, "message_sent" if status == "sent" else "message_drafted",
                 f"{'Sent' if status == 'sent' else 'Drafted (awaiting approval)'} {kind} reply "
                 f"({drafted['source']})", {"message_id": msg.id}, decision_id=decision.id if decision else None)
        return msg

    # ---------- seller-only tools ----------
    def run_market_lookup(self, deal: AgentDeal) -> None:
        p = self.product(deal)
        try:
            result = tools.market_price_lookup(self.db, p.name)
        except tools.ToolUnavailable as exc:
            self.log(deal, "tool_unavailable", f"tool unavailable: market_price_lookup ({exc})")
            return
        self.log(deal, "tool_call", f"market_price_lookup: {len(result.get('prices', []))} price(s) with sources",
                 {"tool": "market_price_lookup", "input": {"product_name": p.name}, "result": result})
