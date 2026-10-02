"""ORM tables: sellers, products, deals, decisions, overrides, outcomes; customers, the agent's tables
and the Telegram channel's tables."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Seller(Base):
    __tablename__ = "sellers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False)  # Gold / Silver / New
    # Nullable: missing payment history is a valid state (test deal 4).
    late_payments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_orders: Mapped[int | None] = mapped_column(Integer, nullable=True)


CATEGORIES = ("mobiles", "laptops", "accessories", "general")


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # mobiles | laptops | accessories | general - each has its own floor/max in policy.metta
    category: Mapped[str] = mapped_column(String, nullable=False, default="general", server_default="general")
    cost_price: Mapped[float] = mapped_column(Float, nullable=False)
    list_price: Mapped[float] = mapped_column(Float, nullable=False)


class Deal(Base):
    __tablename__ = "deals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_requested: Mapped[float] = mapped_column(Float, nullable=False)  # percent
    claimed_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    competitor_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    competitor_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), nullable=False)
    result: Mapped[str] = mapped_column(String, nullable=False)  # APPROVE/REJECT/COUNTER/ESCALATE
    approved_discount: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    audit_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Override(Base):
    __tablename__ = "overrides"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decisions.id"), nullable=False)
    reviewer: Mapped[str] = mapped_column(String, nullable=False)
    new_result: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Outcome(Base):
    """D3: how a completed deal was paid. One row per deal (recorded once)."""
    __tablename__ = "outcomes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), nullable=False, unique=True)
    paid_on_time: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------- Customers and the autonomous agent (engine/agent.metta decides) ----------

class Customer(Base):
    """A shopper. Plays the role the seller plays in the rules: loyalty tier = record tier,
    order history = payment history (R3, trust), their requests this month = R7."""
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False)          # loyalty: Gold / Silver / New
    total_orders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    late_payments: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AgentDeal(Base):
    """One customer discount request, handled by the agent until it is CLOSED."""
    __tablename__ = "agent_deals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_asked: Mapped[float | None] = mapped_column(Float, nullable=True)   # the latest ask
    claimed_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    verified_tier: Mapped[str | None] = mapped_column(String, nullable=True)  # set by a manager
    state: Mapped[str] = mapped_column(String, nullable=False, default="NEW")
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_offer: Mapped[float | None] = mapped_column(Float, nullable=True)
    requests_this_month: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # R7, at creation
    # Where the customer last wrote from (web | telegram); the agent's replies go there too.
    channel: Mapped[str] = mapped_column(String, nullable=False, default="web", server_default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentDecision(Base):
    """A MeTTa decision made for an agent deal (every round), with its full audit trail."""
    __tablename__ = "agent_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_deal_id: Mapped[int] = mapped_column(ForeignKey("agent_deals.id"), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String, nullable=False)
    result: Mapped[str] = mapped_column(String, nullable=False)
    approved_discount: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    audit_json: Mapped[str] = mapped_column(Text, nullable=False)   # input, engine, trail, explanation
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentMessage(Base):
    """Conversation line. sender: customer | agent. status: sent | draft (awaiting approval)."""
    __tablename__ = "agent_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_deal_id: Mapped[int] = mapped_column(ForeignKey("agent_deals.id"), nullable=False)
    sender: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="sent")
    source: Mapped[str] = mapped_column(String, nullable=False)     # customer | llm | template
    kind: Mapped[str] = mapped_column(String, nullable=False, default="text")
    card_json: Mapped[str | None] = mapped_column(Text, nullable=True)   # customer-safe card data
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("agent_decisions.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String, nullable=False, default="web", server_default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentTask(Base):
    """Work for a human: escalation (> authority line) or tier verification."""
    __tablename__ = "agent_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_deal_id: Mapped[int] = mapped_column(ForeignKey("agent_deals.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)       # escalation | verification
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    title: Mapped[str] = mapped_column(String, nullable=False)
    answer: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("agent_decisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentQuote(Base):
    """A priced offer the customer accepted; valid for the hours MeTTa returns (48)."""
    __tablename__ = "agent_quotes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_deal_id: Mapped[int] = mapped_column(ForeignKey("agent_deals.id"), nullable=False)
    discount: Mapped[float] = mapped_column(Float, nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    savings: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")   # open | ordered
    order_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("agent_decisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Tamper-proof quotes (backend/services/quote_seal.py): the catalog list price when quoted, and the
    # HMAC verification code over the signed fields. NULL for quotes made before verification existed.
    list_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    verify_code: Mapped[str | None] = mapped_column(String, nullable=True)


class AgentInvoice(Base):
    """GST tax invoice for an ordered quote (backend/services/gst.py), INV-00001... (AUTOINCREMENT: consecutive).
    Every amount and setting is stored when the order is placed, so a later config edit never changes it.
    Catalog prices include GST: total == the quote total; taxable + cgst + sgst == total."""
    __tablename__ = "agent_invoices"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("agent_quotes.id"), nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    hsn: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)          # incl. GST (the quote's)
    taxable_value: Mapped[float] = mapped_column(Float, nullable=False)
    cgst_rate: Mapped[float] = mapped_column(Float, nullable=False)
    cgst: Mapped[float] = mapped_column(Float, nullable=False)
    sgst_rate: Mapped[float] = mapped_column(Float, nullable=False)
    sgst: Mapped[float] = mapped_column(Float, nullable=False)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    seller_name: Mapped[str] = mapped_column(String, nullable=False)
    seller_gstin: Mapped[str] = mapped_column(String, nullable=False)
    gstin_is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    place_of_supply: Mapped[str] = mapped_column(String, nullable=False)


class LedgerEntry(Base):
    """Append-only, keyed hash chain of the audited events (backend/services/ledger.py).
    seq is AUTOINCREMENT, so a number is never reused (a deleted entry leaves a gap). Kept across demo resets."""
    __tablename__ = "ledger_entries"
    __table_args__ = {"sqlite_autoincrement": True}
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[str] = mapped_column(String, nullable=False)          # UTC ISO-8601, exactly as hashed
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)       # canonical JSON summary
    prev_hash: Mapped[str] = mapped_column(String, nullable=False)
    hash: Mapped[str] = mapped_column(String, nullable=False)


class AgentActivity(Base):
    """Every agent step: perceived, tool call, MeTTa decision, next action, message, fallbacks."""
    __tablename__ = "agent_activity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_deal_id: Mapped[int | None] = mapped_column(ForeignKey("agent_deals.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("agent_decisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentSetting(Base):
    __tablename__ = "agent_settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


# ---------- Telegram channel (backend/telegram/) ----------

class TelegramLink(Base):
    """A private Telegram chat linked to a customer (role customer, one chat per customer) or to the
    store's seller alerts (role seller). Only items newer than the link are pushed to the chat."""
    __tablename__ = "telegram_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String, nullable=False)          # customer | seller
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    after_message_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    after_task_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TelegramCode(Base):
    """One-time link code shown on the Customer page or the Agent inbox (expires in 10 minutes)."""
    __tablename__ = "telegram_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String, nullable=False)          # customer | seller
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TelegramDelivery(Base):
    """What was already pushed to a chat (agent message, quote PDF, seller alert): never sent twice."""
    __tablename__ = "telegram_deliveries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)          # message | quote_pdf | invoice_pdf | task_alert
    ref_id: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ToolCache(Base):
    """Cached tool results (e.g. market prices), reused for 24 hours."""
    __tablename__ = "tool_cache"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
