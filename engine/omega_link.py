"""Link between OmegaRunner (sync, request threads) and the Deal Desk plugin running inside
the Omega agent (connected to the backend's /omega/engine WebSocket).

No business logic: this only moves request text to Omega and result text back.

Protocol (JSON frames, at most MAX_FRAME bytes each):
  plugin -> backend  {"type": "hello", "engine": "omega", "runtime": "petta",
                      "rules_sha256": "...", "live_fingerprint": "...", "loaded": true, "error": null}
  backend -> plugin  {"type": "eval", "id": "<uuid>", "expr": "(evaluate-core (deal ...))"}
  plugin -> backend  {"type": "result", "id": "<uuid>", "ok": true, "text": "(decision ...)", "ms": 3.9,
                      "live_fingerprint": "..."}
                     {"type": "result", "id": "<uuid>", "ok": false, "error": "..."}
                     {"type": "result", "id": "<uuid>", "ok": false, "integrity_error": true, "error": "..."}
  backend -> plugin  {"type": "reload", "id": "<uuid>"}        after an approved policy change
  plugin -> backend  {"type": "reloaded", "id": "<uuid>", "ok": true, "rules_sha256": "...",
                      "live_fingerprint": "..."}

rules_sha256 proves Omega loaded the same FILES. live_fingerprint (a hash of the rule clauses
and policy facts actually in Omega's runtime) proves they were not changed in memory since:
it must stay equal to the value from the hello (or the last reload), else decisions stop (503).
"""
import asyncio
import hashlib
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# The files the plugin loads into Omega, in this order. The hash proves Omega runs the same rules.
OMEGA_RULE_FILES = ("compat_petta.pl", "policy.metta", "rules.metta", "learning.metta", "agent.metta")
MAX_FRAME = 1024 * 1024   # bytes per WebSocket frame, both directions


class OmegaUnavailable(Exception):
    """Omega is not connected, loaded different rules, or did not answer in time."""


class OmegaError(Exception):
    """Omega answered, but the evaluation failed inside Omega."""


def rules_sha256(engine_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in OMEGA_RULE_FILES:
        digest.update(name.encode())
        # normalise line endings: the plugin reads the same files on Linux
        digest.update((Path(engine_dir) / name).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


class _Pending:
    def __init__(self):
        self.done = threading.Event()
        self.reply = None


class OmegaHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._send = None           # coroutine function: await send(dict)
        self._loop = None           # event loop the connection lives on
        self._hello = None
        self._connected_at = None
        self._pending: dict[str, _Pending] = {}

    # ----- called by the WebSocket endpoint (event loop side) -----

    def attach(self, send, loop, hello: dict) -> None:
        with self._lock:
            self._send, self._loop, self._hello = send, loop, hello
            self._connected_at = datetime.now(timezone.utc).isoformat()

    def detach(self, send) -> None:
        with self._lock:
            if self._send is not send:
                return
            self._send = self._loop = self._hello = self._connected_at = None
            pending, self._pending = self._pending, {}
        for p in pending.values():  # wake waiting requests; they report "disconnected"
            p.done.set()

    def deliver(self, message: dict) -> None:
        with self._lock:
            pending = self._pending.pop(message.get("id"), None)
        if pending is not None:
            pending.reply = message
            pending.done.set()

    # ----- called by OmegaRunner (request threads) -----

    def status(self, expected_sha256: str) -> dict:
        with self._lock:
            hello = self._hello or {}
            connected = self._send is not None
            return {
                "connected": connected,
                "connected_since": self._connected_at,
                "omega_rules_sha256": hello.get("rules_sha256"),
                "local_rules_sha256": expected_sha256,
                "rules_match": connected and hello.get("rules_sha256") == expected_sha256,
                "live_fingerprint": hello.get("live_fingerprint"),
                "rules_loaded": bool(hello.get("loaded")),
                "load_error": hello.get("error"),
                "runtime": hello.get("runtime"),
            }

    def request(self, expr: str, expected_sha256: str, timeout: float) -> tuple[str, float]:
        """Send one expression to Omega and wait for its result text. Never falls back."""
        with self._lock:
            send, loop, hello = self._send, self._loop, self._hello or {}
            if send is None:
                raise OmegaUnavailable(
                    "Omega agent is not connected. Start it with omega\\start-omega.bat, "
                    "then check GET /omega/status.")
            if not hello.get("loaded"):
                raise OmegaUnavailable(f"Omega could not load the Deal Desk rules: {hello.get('error')}")
            if hello.get("rules_sha256") != expected_sha256:
                raise OmegaUnavailable(
                    "Omega loaded different rule files than this backend "
                    f"(omega {str(hello.get('rules_sha256'))[:12]}..., local {expected_sha256[:12]}...). "
                    "Restart Omega so it reloads engine/.")
            live = hello.get("live_fingerprint")
            if not live:
                raise OmegaUnavailable(
                    "The Omega plugin did not report a live rule fingerprint. "
                    "Restart Omega with the current plugin (omega\\start-omega.bat).")
        started = time.perf_counter()
        reply = self._round_trip(send, loop, {"type": "eval", "expr": expr}, timeout)
        if reply.get("integrity_error"):
            raise OmegaUnavailable(f"Omega refused to decide: {reply.get('error')}")
        if not reply.get("ok"):
            raise OmegaError(f"Omega could not evaluate the request: {reply.get('error')}")
        if reply.get("live_fingerprint") != live:
            raise OmegaUnavailable(
                "Omega's live Deal Desk rules differ from the rules it loaded "
                f"(live {str(reply.get('live_fingerprint'))[:12]}..., loaded {live[:12]}...). "
                "Restart Omega.")
        return reply["text"], (time.perf_counter() - started) * 1000

    def reload(self, expected_sha256: str, timeout: float) -> dict:
        """After an approved policy change: ask the plugin to reload policy.metta into the
        agent's space, then check that Omega's rules hash equals the new local files."""
        with self._lock:
            send, loop = self._send, self._loop
        if send is None:
            raise OmegaUnavailable(
                "Policy saved, but the Omega agent is not connected, so it could not reload it. "
                "Start Omega (omega\\start-omega.bat); it loads the current files on startup.")
        reply = self._round_trip(send, loop, {"type": "reload"}, timeout)
        if not reply.get("ok"):
            raise OmegaUnavailable(f"Policy saved, but Omega could not reload it: {reply.get('error')}")
        if reply.get("rules_sha256") != expected_sha256:
            raise OmegaUnavailable(
                "Omega reloaded the policy, but its rules still differ from this backend's files "
                f"(omega {str(reply.get('rules_sha256'))[:12]}..., local {expected_sha256[:12]}...). "
                "Restart Omega (omega\\start-omega.bat).")
        if not reply.get("live_fingerprint"):
            raise OmegaUnavailable("Policy saved, but Omega did not report its live rule "
                                   "fingerprint after reloading. Restart Omega.")
        with self._lock:
            if self._send is send and self._hello is not None:
                self._hello = {**self._hello, "rules_sha256": reply["rules_sha256"],
                               "live_fingerprint": reply["live_fingerprint"]}
        return reply

    def _round_trip(self, send, loop, frame: dict, timeout: float) -> dict:
        """Send one frame (an id is added) and wait for the reply with the same id."""
        request_id = str(uuid.uuid4())
        pending = _Pending()
        with self._lock:
            self._pending[request_id] = pending
        try:
            asyncio.run_coroutine_threadsafe(send({**frame, "id": request_id}), loop).result(timeout)
        except Exception as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise OmegaUnavailable(f"Could not send the request to Omega: {exc}") from exc
        if not pending.done.wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise OmegaUnavailable(f"Omega did not answer within {timeout:g} s.")
        if pending.reply is None:
            raise OmegaUnavailable("Omega disconnected before answering.")
        return pending.reply


hub = OmegaHub()
