import os

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/engine")
def engine_info() -> dict:
    """Which engine decides (the UI polls Omega's status only in omega mode)."""
    return {"engine_runner": os.getenv("ENGINE_RUNNER", "local").lower()}
