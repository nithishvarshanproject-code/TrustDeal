"""D2 policy routes. Proposals come from MeTTa (learning.metta); the apply route is the
only code path that writes policy.metta, and only after a named human approves."""
import ast
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.validation import MAX_NAME
from backend.services import ledger
from backend.services.policy import get_policy_proposals
from engine.bridge import current_engine_dir, sync_engine_after_policy_change
from engine.policy_admin import PolicyChangeError, apply_policy_proposal

router = APIRouter(prefix="/policy", tags=["policy"])

_LOG_LINE = re.compile(
    r"^(?P<timestamp>\S+) \((?P<fact>\S+) (?P<key>\S+)\) (?P<old>[0-9.]+) -> (?P<new>[0-9.]+) "
    r"approved_by=(?P<approved_by>.+) evidence=\[(?P<evidence>[^\]]*)\] backup=(?P<backup>\S+)$"
)
_FACT_LINE = re.compile(r"^\(([^\s()]+)((?: [^\s()]+)+)\)\s*$")


@router.get("/proposals")
def proposals(db: Session = Depends(get_db)) -> list[dict]:
    return get_policy_proposals(db)


class ApplyRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    fact: str = Field(max_length=32)
    key: str = Field(max_length=32)
    old_value: float
    new_value: float
    approved_by: str = Field(min_length=1, max_length=MAX_NAME)


def _same(proposal: dict, req: ApplyRequest) -> bool:
    return (proposal["fact"], proposal["key"]) == (req.fact, req.key) \
        and abs(proposal["old_value"] - req.old_value) < 1e-9 \
        and abs(proposal["new_value"] - req.new_value) < 1e-9


@router.post("/proposals/apply")
def apply(req: ApplyRequest, db: Session = Depends(get_db)) -> dict:
    if not req.approved_by.strip():
        raise HTTPException(422, "approved_by must not be blank")
    # Only a proposal MeTTa currently makes may be applied: never trust client-sent values.
    match = next((p for p in get_policy_proposals(db) if _same(p, req)), None)
    if match is None:
        raise HTTPException(409, "not a current MeTTa proposal (it may be stale or modified)")
    try:
        record = apply_policy_proposal(match, approved_by=req.approved_by,
                                       engine_dir=current_engine_dir())
    except PolicyChangeError as exc:
        raise HTTPException(409, str(exc)) from exc
    # Recorded as soon as the file changed (before the Omega reload, which may fail with a 503).
    ledger.append(db, "policy_change", f"policy_change:{record['backup']}", {
        "fact": record["fact"], "key": record["key"], "old_value": record["old_value"],
        "new_value": record["new_value"], "approved_by": record["approved_by"],
        "evidence": record["evidence"], "backup": record["backup"], "timestamp": record["timestamp"]})
    db.commit()
    # Omega mode: hot-reload the new policy into the agent and re-verify the rules hash.
    # On failure this raises OmegaUnavailable -> clear 503 ("Policy saved, but Omega ...").
    omega_reload = sync_engine_after_policy_change()
    return {
        "fact": record["fact"], "key": record["key"],
        "old_value": record["old_value"], "new_value": record["new_value"],
        "approved_by": record["approved_by"], "evidence": record["evidence"],
        "history_file": record["backup"], "timestamp": record["timestamp"],
        "omega_reload": omega_reload,
    }


@router.get("/history")
def history() -> list[dict]:
    log = current_engine_dir() / "policy_history" / "changes.log"
    if not log.exists():
        return []
    entries = []
    for line in log.read_text(encoding="utf-8").splitlines():
        m = _LOG_LINE.match(line.strip())
        if m is None:
            continue
        entries.append({
            "timestamp": m["timestamp"], "fact": m["fact"], "key": m["key"],
            "old_value": float(m["old"]), "new_value": float(m["new"]),
            "approved_by": ast.literal_eval(m["approved_by"]),
            "evidence": [int(i) for i in m["evidence"].split(",") if i.strip()],
            "history_file": m["backup"],
        })
    return entries[::-1]  # newest first


def _value(token: str):
    try:
        return float(token)
    except ValueError:
        return token


@router.get("/current")
def current() -> list[dict]:
    """The facts in the loaded policy.metta, as written (display only)."""
    facts = []
    text = (current_engine_dir() / "policy.metta").read_text(encoding="utf-8")
    for line in text.splitlines():
        fact_text = line.split(";", 1)[0].strip()
        m = _FACT_LINE.match(fact_text)
        if m:
            facts.append({"fact": m[1], "args": [_value(t) for t in m[2].split()], "text": fact_text})
    return facts
