"""Demo helpers: POST /demo/reset puts the app back to a clean demo state (for re-recording).

It re-seeds the database, restores engine/policy.metta from engine/policy.original.metta,
archives engine/policy_history (nothing is deleted) and, in omega mode, hot-reloads the
restored policy into the Omega agent. The audit ledger is kept and records the reset.
No business logic here.
"""
import shutil
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.services import ledger

from backend.database import get_db
from data.seed import seed
from engine.bridge import current_engine_dir, reset_runner, sync_engine_after_policy_change

router = APIRouter(prefix="/demo", tags=["demo"])


class ResetRequest(BaseModel):
    confirm: Literal["RESET"]   # guards against accidental calls


@router.post("/reset")
def reset(_: ResetRequest, db: Session = Depends(get_db)) -> dict:
    engine_dir = current_engine_dir()

    # 1) archive the policy change history (moved, never deleted)
    archived = None
    history = engine_dir / "policy_history"
    if history.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = engine_dir / "policy_history_archive" / stamp
        target.parent.mkdir(exist_ok=True)
        shutil.move(str(history), str(target))
        archived = f"policy_history_archive/{stamp}"

    # 2) restore the original policy, then reload the engine (Omega: hot reload + hash check)
    original = engine_dir / "policy.original.metta"
    policy = engine_dir / "policy.metta"
    restored = original.read_bytes() != policy.read_bytes()
    if restored:
        shutil.copyfile(original, policy)
    reset_runner(engine_dir)
    omega_reload = sync_engine_after_policy_change()

    # 3) fresh demo data in the database this app is using
    bind = db.get_bind()
    db.close()
    seed(bind)
    with Session(bind) as session:                 # the ledger survives the reset and records it
        ledger.append(session, "demo_reset", f"demo_reset:{datetime.now(timezone.utc).isoformat()}",
                      {"policy_restored": restored, "archived_history": archived})
        session.commit()

    return {"reset": True, "policy_restored": restored, "archived_history": archived,
            "omega_reload": omega_reload}
