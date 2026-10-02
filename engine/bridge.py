"""Python <-> MeTTa/Omega bridge.

All decision logic lives in policy.metta and rules.metta. This module only
(1) turns a deal dict into a MeTTa `(deal ...)` expression, (2) runs it through a
runner, and (3) converts the resulting `(decision ...)` atom back into a dict.

Runner is chosen with ENGINE_RUNNER=local|omega (default: local).
"""
import os
import re
import threading
from pathlib import Path

from engine import metta_safe

ENGINE_DIR = Path(__file__).resolve().parent
RULE_FILES = ("policy.metta", "rules.metta", "learning.metta", "agent.metta")


# ---------- runners ----------

class LocalMettaRunner:
    """Runs the rules in-process with hyperon."""

    name = "local-hyperon"

    def __init__(self, engine_dir: Path = ENGINE_DIR) -> None:
        from hyperon import MeTTa

        self._metta = MeTTa()
        # FastAPI serves sync routes from a thread pool; one hyperon instance must not
        # be used by two threads at once.
        self._lock = threading.Lock()
        for name in RULE_FILES:
            self._metta.run((Path(engine_dir) / name).read_text(encoding="utf-8"))

    def run(self, expr: str):
        metta_safe.check_request(expr)  # exactly one entry-point call, never extra code
        with self._lock:
            results = self._metta.run(f"!{expr}")
        if not results or not results[0]:
            raise RuntimeError(f"MeTTa returned no result for {expr}")
        return results[0][0]


_EXP_FLOAT = re.compile(r"(?<![\w.])(-?\d+\.\d+e[+-]\d+)(?![\w.])")


def normalize_floats(text: str) -> str:
    """PeTTa (SWI-Prolog) prints some floats in exponent form, also inside strings:
    20000.0 -> 2.0e+04, 9350000.0 -> 9.35e+06. Rewrite them as plain decimals, exactly as the
    local engine (hyperon) prints them, so no page ever shows exponent form. Same value;
    very small or huge numbers (not prices or percentages) are left unchanged."""
    def plain(m: re.Match) -> str:
        value = float(m.group(1))
        if value != 0 and not 1e-4 <= abs(value) < 1e16:
            return m.group(1)
        return repr(value)
    return _EXP_FLOAT.sub(plain, text)


class OmegaRunner:
    """Runs every request inside the Omega agent: the Deal Desk plugin evaluates the exact
    expression in the agent's PeTTa runtime, where the same rule files are loaded
    (checked by hash). No LLM is involved. Never falls back to local: if Omega is not
    available, run() raises OmegaUnavailable."""

    name = "omega"

    def __init__(self, engine_dir: Path = ENGINE_DIR) -> None:
        from hyperon import MeTTa

        from engine.omega_link import rules_sha256

        self._parser = MeTTa()  # used only to parse Omega's result text into an atom
        self._parse_lock = threading.Lock()
        self.expected_sha256 = rules_sha256(engine_dir)
        self.timeout = float(os.getenv("OMEGA_TIMEOUT_S", "10"))
        self.last_ms: float | None = None

    def run(self, expr: str):
        from engine.omega_link import hub

        metta_safe.check_request(expr)  # the plugin checks again on its side
        text, self.last_ms = hub.request(expr, self.expected_sha256, self.timeout)
        with self._parse_lock:
            return self._parser.parse_single(normalize_floats(text))


_runner = None
_engine_dir = ENGINE_DIR


def reset_runner(engine_dir: Path | None = None) -> None:
    """Drop the cached runner so the .metta files are reloaded on next use
    (after a policy change, or to point tests at a copy of engine/)."""
    global _runner, _engine_dir
    _runner = None
    _engine_dir = Path(engine_dir) if engine_dir else ENGINE_DIR


def get_runner():
    global _runner
    if _runner is None:
        kind = os.getenv("ENGINE_RUNNER", "local").lower()
        if kind == "local":
            _runner = LocalMettaRunner(_engine_dir)
        elif kind == "omega":
            _runner = OmegaRunner(_engine_dir)
        else:
            raise ValueError(f"Unknown ENGINE_RUNNER: {kind!r}")
    return _runner


# ---------- dict -> MeTTa ----------

# Every value goes through engine/metta_safe.py (validated, escaped: one value = one atom).
_sym = metta_safe.symbol
_num = metta_safe.number
_str = metta_safe.string


def to_metta(deal: dict) -> str:
    """Build (deal record claimed cost list qty disc late orders comp verified requests name category).
    A missing category is passed as None; MeTTa treats it as general."""
    name = deal.get("seller_name")
    return "(deal {} {} {} {} {} {} {} {} {} {} {} {} {})".format(
        _sym(deal["record_tier"]),
        _sym(deal.get("claimed_tier")),
        _num(deal["cost_price"], float),
        _num(deal["list_price"], float),
        _num(deal["quantity"], int),
        _num(deal["discount_requested"], float),
        _num(deal.get("late_payments"), int),
        _num(deal.get("total_orders"), int),
        _num(deal.get("competitor_price"), float),
        "True" if deal.get("competitor_verified") else "False",
        _num(deal.get("requests_this_month", 0), int),
        _str(name) if name else "None",
        _sym(deal.get("category")),
    )


# ---------- MeTTa -> Python ----------

def _to_py(atom):
    """Convert a hyperon atom to plain Python (lists, str, int, float, bool, None)."""
    from hyperon import AtomKind

    if atom.get_metatype() == AtomKind.EXPR:
        return [_to_py(child) for child in atom.get_children()]
    if atom.get_metatype() == AtomKind.SYMBOL:
        name = atom.get_name()
        return None if name == "None" else name
    return atom.get_object().value  # grounded: number, bool, string


def from_metta(atom) -> dict:
    tag, result, discount, confidence, trail, hint, what_if = _to_py(atom)
    if tag != "decision":
        raise RuntimeError(f"Unexpected MeTTa output: {atom}")
    return {
        "result": result,
        "approved_discount": None if discount is None else float(discount),
        "confidence": float(confidence),
        "trail": [
            {"rule_id": rule_id, "check": check, "value": value, "status": status}
            for rule_id, check, value, status in trail
        ],
        "override_hint": (
            {"action": hint[1], "claimed_tier": hint[2], "allowed_max": float(hint[3])}
            if hint[1] == "verify-tier"
            else None
        ),
        "what_if": _what_if_list(what_if),
    }


def _what_if_list(what_if) -> list[dict] | None:
    """(what-if ((change detail allowed) ...)) -> list; (what-if not-computed) -> None."""
    tag, options = what_if
    if tag != "what-if":
        raise RuntimeError(f"Unexpected what-if output: {what_if}")
    if options == "not-computed":
        return None
    return [
        {"change": change, "detail": detail, "allowed_max": float(allowed)}
        for change, detail, allowed in options
    ]


def evaluate_deal(deal: dict, include_what_if: bool = True) -> dict:
    """Send a deal to the rule engine and return its decision.

    `deal` must already include record data: record_tier, claimed_tier, cost_price,
    list_price, quantity, discount_requested, late_payments, total_orders (None if
    unknown), competitor_price, competitor_verified, requests_this_month.

    Returns:
        {
            "result": "APPROVE" | "REJECT" | "COUNTER" | "ESCALATE",
            "approved_discount": float | None,   # percent, e.g. 12.0
            "confidence": float,                  # 0.0 - 1.0
            "trail": [
                {"rule_id": "R1", "check": str, "value": ...,
                 "status": "pass" | "fail" | "warn" | "skip"},
                ...
            ],
            "override_hint": None | {"action": "verify-tier",
                                     "claimed_tier": str, "allowed_max": float},
            "what_if": [   # D1: [] for APPROVE; None if include_what_if=False
                {"change": "verify-tier" | "verify-competitor" | "raise-quantity"
                           | "lower-discount", "detail": ..., "allowed_max": float},
            ],
        }
    """
    entry = "evaluate" if include_what_if else "evaluate-core"
    atom = get_runner().run(f"({entry} {to_metta(deal)})")
    return from_metta(atom)


def what_if_for(deal: dict) -> list[dict]:
    """D1 what-if options on their own ([] for APPROVE)."""
    return _what_if_list(_to_py(get_runner().run(f"(what-if-for {to_metta(deal)})")))


def category_profile(category: str | None) -> dict:
    """The margin floor and max discount MeTTa uses for a product category (for the UI)."""
    atom = get_runner().run(f"(category-profile-of {_sym(category or 'general')})")
    tag, name, floor, cat_max = _to_py(atom)
    if tag != "category-profile":
        raise RuntimeError(f"Unexpected MeTTa output: {atom}")
    return {"category": name, "margin_floor": float(floor), "category_max": float(cat_max)}


def sync_engine_after_policy_change() -> dict | None:
    """Call after policy.metta changed on disk (an approved proposal).
    Local mode: nothing to do, the runner was reset and reloads the files on next use.
    Omega mode: ask the plugin to reload policy.metta into the agent's space and verify the
    rules hash; raises OmegaUnavailable if that fails (never falls back to local)."""
    runner = get_runner()
    if not isinstance(runner, OmegaRunner):
        return None
    from engine.omega_link import hub

    reply = hub.reload(runner.expected_sha256, runner.timeout)
    return {"reloaded": True, "removed": reply.get("removed", []), "added": reply.get("added", [])}


def runner_name() -> str:
    """Which runner makes decisions (shown in the UI footer)."""
    return get_runner().name


def current_engine_dir() -> Path:
    """The engine folder the runner loads from (the real engine/ unless tests reset it)."""
    return _engine_dir


# ---------- D2: policy proposals from overrides ----------

def propose_policy_changes(overrides: list[dict]) -> list[dict]:
    """Ask MeTTa for policy proposals. Python only formats data; MeTTa finds the pattern.

    Each override: {"id", "original_result", "new_result", "reason", "deal": <deal dict>}.
    Returns [{"fact", "key", "old_value", "new_value",
              "evidence": [{"override_id", "reason"}, ...]}, ...]
    """
    items = " ".join(
        "(override {} {} {} {} {})".format(
            int(o["id"]), _sym(o["original_result"]), _sym(o["new_result"]),
            _str(o["reason"]), to_metta(o["deal"]),
        )
        for o in overrides
    )
    atom = get_runner().run(f"(propose-policy-changes ({items}))")
    proposals = []
    for tag, fact, key, old, new, evidence in _to_py(atom):
        if tag != "policy-proposal":
            raise RuntimeError(f"Unexpected MeTTa output: {atom}")
        proposals.append({
            "fact": fact, "key": key, "old_value": float(old), "new_value": float(new),
            "evidence": [{"override_id": i, "reason": r} for i, r in evidence[1]],
        })
    return proposals


# ---------- D3: seller trust ----------

def _stv(atom_py) -> dict:
    tag, strength, confidence = atom_py
    if tag != "stv":
        raise RuntimeError(f"Expected (stv s c), got {atom_py}")
    return {"strength": float(strength), "confidence": float(confidence)}


def seller_trust(late_payments: int | None, total_orders: int | None) -> dict:
    """Trust truth value MeTTa derives from a payment history."""
    atom = get_runner().run(
        f"(history-stv {_num(late_payments, int)} {_num(total_orders, int)})")
    return _stv(_to_py(atom))


def update_trust(trust: dict, outcome: str) -> dict:
    """Revise a trust stv with one completed deal; outcome is 'on-time' or 'late'."""
    if outcome not in ("on-time", "late"):
        raise ValueError(f"outcome must be 'on-time' or 'late', got {outcome!r}")
    atom = get_runner().run("(update-trust (stv {} {}) {})".format(
        _num(trust["strength"], float), _num(trust["confidence"], float), outcome))
    return _stv(_to_py(atom))


# ---------- Agent (engine/agent.metta): next action and offer numbers ----------

AGENT_STATES = frozenset({"NEW", "WAITING_CUSTOMER", "WAITING_VERIFICATION", "ESCALATED", "QUOTED",
                          "DECLINED", "ORDERED", "CLOSED"})
AGENT_EVENTS = frozenset({"new-request", "customer-ask", "customer-accept", "customer-decline", "verified",
                          "not-verified", "manager-approved", "manager-rejected", "order-placed", "tick"})
AGENT_ACTIONS = frozenset({"create-quote", "send-counter", "send-decline", "create-escalation-task",
                           "request-verification", "place-order", "wait", "close"})
RESULTS = frozenset({"APPROVE", "REJECT", "COUNTER", "ESCALATE"})


def next_action(state: str, result: str, offer: float | None, deal: dict, event: str, round_: int) -> dict:
    """MeTTa decides what the agent does next. Returns
    {"action", "state", "rule_id", "offer"}; Python only executes it."""
    expr = "(next-action {} (verdict {} {} {}) (event {} {}))".format(
        metta_safe.symbol(state, AGENT_STATES), metta_safe.symbol(result, RESULTS), _num(offer, float),
        to_metta(deal), metta_safe.symbol(event, AGENT_EVENTS), _num(round_, int))
    tag, action, new_state, rule_id, chosen = _to_py(get_runner().run(expr))
    if tag != "agent-action" or action not in AGENT_ACTIONS or new_state not in AGENT_STATES:
        raise RuntimeError(f"Unexpected MeTTa output for next-action: {tag} {action} {new_state}")
    return {"action": action, "state": new_state, "rule_id": rule_id,
            "offer": None if chosen is None else float(chosen)}


def quote_terms(deal: dict, offer: float) -> dict | None:
    """Price terms MeTTa computes for an offer, or None if the offer breaks the margin floor
    or the category max. {"discount", "unit_price", "total", "savings", "valid_hours"}"""
    atom = _to_py(get_runner().run(f"(quote-terms {to_metta(deal)} {_num(offer, float)})"))
    if atom[0] == "terms-refused":
        return None
    tag, disc, unit, total, savings, hours = atom
    if tag != "terms":
        raise RuntimeError(f"Unexpected MeTTa output for quote-terms: {atom}")
    return {"discount": float(disc), "unit_price": float(unit), "total": float(total),
            "savings": float(savings), "valid_hours": int(hours)}


def alternatives(original: dict, product_id: int, candidates: list[tuple[str, int, dict]]) -> list[dict]:
    """Up to 3 catalog alternatives that meet the customer's budget, each approved by MeTTa.
    candidates: [(kind, product_id, deal), ...]; MeTTa adds a bigger-quantity candidate itself."""
    kinds = {"cheaper-model", "bigger-quantity", "bundle"}
    items = " ".join(
        f"(candidate {metta_safe.symbol(kind, kinds)} {_num(pid, int)} {to_metta(d)})"
        for kind, pid, d in candidates)
    options = _to_py(get_runner().run(
        f"(alternatives {to_metta(original)} {_num(product_id, int)} ({items}))"))
    result = []
    for tag, kind, pid, qty, disc, unit, total, savings in options:
        if tag != "alternative":
            raise RuntimeError(f"Unexpected MeTTa output for alternatives: {options}")
        result.append({"kind": kind, "product_id": int(pid), "quantity": int(qty), "discount": float(disc),
                       "unit_price": float(unit), "total": float(total), "savings": float(savings)})
    return result
