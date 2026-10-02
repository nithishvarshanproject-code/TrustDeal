"""D2: apply a MeTTa-generated policy proposal after a human approves it.

This module never decides WHAT to change: the proposal comes from learning.metta.
It only rewrites that one fact in policy.metta, keeps a copy of the old file in
policy_history/, logs the change, and reloads the engine. It is never called
automatically.
"""
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from engine import metta_safe
from engine.bridge import ENGINE_DIR, reset_runner

# Safety net on top of learning.metta: the only fact a proposal may rewrite.
# The margin floor can never be changed this way.
REWRITABLE_FACTS = {"tier-cap"}


class PolicyChangeError(Exception):
    pass


def rewrite_fact_text(text: str, fact: str, key: str, old_value: float, new_value: float) -> str:
    """Return policy text with exactly one `(fact key old)` line changed to `(fact key new)`.
    Pure text operation (no file access): shared by the approval and the impact simulator."""
    try:  # every part of the new line goes through the shared MeTTa validation
        fact, key = metta_safe.symbol(fact), metta_safe.symbol(key)
        new_text = metta_safe.number(new_value, float)
        old = float(metta_safe.number(old_value, float))
    except metta_safe.MettaInputError as exc:
        raise PolicyChangeError(str(exc)) from exc
    line = re.compile(rf"^\({re.escape(fact)} {re.escape(key)} ([0-9.]+)\)[ \t]*$", re.MULTILINE)
    matches = list(line.finditer(text))
    if len(matches) != 1:
        raise PolicyChangeError(f"expected exactly one ({fact} {key} ...) line, found {len(matches)}")
    current = float(matches[0].group(1))
    if current != old:
        raise PolicyChangeError(f"stale proposal: policy has {current}, proposal expects {old}")
    m = matches[0]
    return text[:m.start()] + f"({fact} {key} {new_text})" + text[m.end():]


def apply_policy_proposal(proposal: dict, approved_by: str, engine_dir: Path = ENGINE_DIR) -> dict:
    """Rewrite the single fact named in `proposal` and return a log record."""
    if not approved_by or not approved_by.strip():
        raise PolicyChangeError("a human approver is required")
    fact = proposal["fact"]
    if fact not in REWRITABLE_FACTS:
        raise PolicyChangeError(f"fact {fact!r} cannot be changed by a proposal")

    engine_dir = Path(engine_dir)
    policy_path = engine_dir / "policy.metta"
    text = policy_path.read_text(encoding="utf-8")
    try:
        key = metta_safe.symbol(proposal["key"])
    except metta_safe.MettaInputError as exc:
        raise PolicyChangeError(str(exc)) from exc
    old, new = float(proposal["old_value"]), float(proposal["new_value"])
    new_text = rewrite_fact_text(text, fact, key, old, new)

    now = datetime.now(timezone.utc)
    history = engine_dir / "policy_history"
    history.mkdir(exist_ok=True)
    backup = history / f"policy-{now.strftime('%Y%m%dT%H%M%S%fZ')}.metta"
    shutil.copy2(policy_path, backup)

    policy_path.write_text(new_text, encoding="utf-8")

    evidence_ids = [e["override_id"] for e in proposal.get("evidence", [])]
    record = {
        "timestamp": now.isoformat(),
        "fact": fact, "key": key, "old_value": old, "new_value": new,
        "approved_by": approved_by.strip(), "evidence": evidence_ids,
        "backup": backup.name,
    }
    with (history / "changes.log").open("a", encoding="utf-8") as log:
        log.write(
            f"{record['timestamp']} ({fact} {key}) {old} -> {new} "
            f"approved_by={record['approved_by']!r} evidence={evidence_ids} backup={backup.name}\n"
        )

    reset_runner(engine_dir)  # next evaluation loads the new policy
    return record
