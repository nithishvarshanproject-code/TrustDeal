"""HTTP hardening for a local-only app (no business logic): allowed hosts and origins,
a request body size cap and security headers. Wired up in backend/app.py."""
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Host header check (blocks DNS-rebinding pages from reaching the local API).
# host.docker.internal: the Omega container reaches the backend through it.
# testserver: the host name FastAPI's TestClient uses.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "host.docker.internal", "testserver"]
ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
MAX_BODY_BYTES = 2 * 1024 * 1024   # a 1 MB CSV (sent as JSON text) plus headroom

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]


async def _send_413(send: Send) -> None:
    body = b'{"detail":"request body is larger than 2 MB"}'
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class BodySizeLimit:
    """Refuse request bodies over MAX_BODY_BYTES with 413, by Content-Length and while streaming."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    too_big = int(value) > self.max_bytes
                except ValueError:
                    too_big = True
                if too_big:
                    await _send_413(send)
                    return
        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await _send_413(send)


class _BodyTooLarge(Exception):
    pass


class SecurityHeaders:
    """Add the security headers to every HTTP response (errors included)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (name, value) for name, value in SECURITY_HEADERS if name not in present]
            await send(message)

        await self.app(scope, receive, with_headers)
