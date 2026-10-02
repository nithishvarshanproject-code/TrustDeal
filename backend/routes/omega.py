"""Connection point for the Deal Desk plugin running inside the Omega agent.

The plugin connects OUT to this WebSocket (so the backend never has to reach into the
container), authenticates with the shared token in OMEGA_TOKEN, says hello with the hash
of the rule files it loaded, then answers {"type": "eval"} frames. No business logic here.
"""
import asyncio
import hmac
import json
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from engine.bridge import current_engine_dir
from engine.omega_link import MAX_FRAME, hub, rules_sha256

router = APIRouter(prefix="/omega", tags=["omega"])


class _FrameTooLarge(Exception):
    pass


def _token_ok(header: str) -> bool:
    """Constant-time comparison on bytes (a str comparison raises on non-ASCII input).
    The token itself is never logged or echoed."""
    expected = os.getenv("OMEGA_TOKEN", "")
    if not expected:
        return False
    given = header.encode("utf-8", "surrogateescape")
    return hmac.compare_digest(given, f"Bearer {expected}".encode("utf-8"))


async def _receive(ws: WebSocket) -> dict:
    """One JSON object frame of at most MAX_FRAME bytes."""
    text = await ws.receive_text()
    if len(text.encode("utf-8")) > MAX_FRAME:
        raise _FrameTooLarge()
    message = json.loads(text)
    if not isinstance(message, dict):
        raise ValueError("frame is not a JSON object")
    return message


@router.websocket("/engine")
async def omega_engine(ws: WebSocket) -> None:
    if not _token_ok(ws.headers.get("authorization", "")):
        await ws.close(code=1008)  # policy violation: missing or wrong token
        return
    await ws.accept()
    try:
        hello = await _receive(ws)
    except _FrameTooLarge:
        await ws.close(code=1009)  # message too big
        return
    except (ValueError, KeyError, WebSocketDisconnect):
        await ws.close(code=1002)
        return
    if hello.get("type") != "hello":
        await ws.close(code=1002)
        return
    send = ws.send_json
    hub.attach(send, asyncio.get_running_loop(), hello)
    try:
        while True:
            message = await _receive(ws)
            if message.get("type") in ("result", "reloaded"):
                hub.deliver(message)
    except WebSocketDisconnect:
        pass
    except _FrameTooLarge:
        await ws.close(code=1009)
    except (ValueError, KeyError):
        await ws.close(code=1002)
    finally:
        hub.detach(send)


@router.get("/status")
def status() -> dict:
    return {
        "engine_runner": os.getenv("ENGINE_RUNNER", "local").lower(),
        **hub.status(rules_sha256(current_engine_dir())),
    }
