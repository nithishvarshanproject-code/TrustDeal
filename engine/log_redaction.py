"""Log redaction: mask anything that looks like a key, token or secret before it is logged.

Installed once per process (backend and Omega plugin) through the log record factory, so it
covers every logger, including uvicorn's and OmegaClaw's, and exception tracebacks.
Standard library only (the plugin loads this file inside the Omega container).
"""
import logging
import os
import re
import threading

MASK = "***"
# Environment variables whose exact values are always masked when they appear in a log line.
SECRET_ENV_VARS = ("ASIONE_API_KEY", "DEALDESK_TOKEN", "OMEGA_TOKEN", "OMEGACLAW_AUTH_SECRET",
                   "LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN",
                   "TAVILY_API_KEY")
_MIN_SECRET_LEN = 6

_PATTERNS = [
    # Authorization: Bearer <token>
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{6,}"), r"\1" + MASK),
    # api_key=..., token: ..., "secret": "...", password=..., authorization=...
    (re.compile(r"""(?ix)
        \b([A-Za-z0-9_-]*(?:api[_-]?key|token|secret|passw(?:or)?d|authorization|auth[_-]?code))
        (["']?\s*[=:]\s*["']?)
        (?!\*\*\*)[^\s"',;&]{4,}"""), r"\1\2" + MASK),
    # provider-style keys: sk-..., sk_..., pk_live_...
    (re.compile(r"\b(?:sk|pk|rk)[-_](?:live[-_]|test[-_])?[A-Za-z0-9_-]{16,}"), MASK),
    # Telegram bot tokens (<bot id>:<secret>), also inside API URLs (.../bot<token>/getUpdates)
    (re.compile(r"\d{5,12}:[A-Za-z0-9_-]{30,}"), MASK),
]

_extra_secrets: set[str] = set()
_lock = threading.Lock()
_installed = False


def register_secret(value: str | None) -> None:
    """Mask this exact value from now on (e.g. a token read from a file, not the env)."""
    if value and len(value) >= _MIN_SECRET_LEN:
        with _lock:
            _extra_secrets.add(value)


def _known_secrets() -> list[str]:
    values = {os.environ.get(name, "") for name in SECRET_ENV_VARS}
    with _lock:
        values |= _extra_secrets
    # longest first, so a secret that contains another is masked whole
    return sorted((v for v in values if len(v) >= _MIN_SECRET_LEN), key=len, reverse=True)


def redact(text: str) -> str:
    if not text:
        return text
    for secret in _known_secrets():
        text = text.replace(secret, MASK)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_value(value):
    """Numbers stay numbers (for %d); anything else (strings, exceptions...) becomes redacted text."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(value if isinstance(value, str) else str(value))


def _redact_record(record: logging.LogRecord) -> logging.LogRecord:
    # The message and each argument are redacted in place; the args structure is kept,
    # because some formatters (uvicorn's access log) unpack record.args themselves.
    if isinstance(record.msg, str):
        record.msg = redact(record.msg)
    elif record.msg is not None and not record.args:
        record.msg = redact(str(record.msg))
    if isinstance(record.args, tuple):
        record.args = tuple(_redact_value(a) for a in record.args)
    elif isinstance(record.args, dict):
        record.args = {k: _redact_value(v) for k, v in record.args.items()}
    if record.exc_info and not record.exc_text:
        record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
    if record.stack_info:
        record.stack_info = redact(record.stack_info)
    return record


class RedactingFilter(logging.Filter):
    """For handlers configured elsewhere (idempotent: redacting twice is harmless)."""

    def filter(self, record: logging.LogRecord) -> bool:
        _redact_record(record)
        return True


def install() -> None:
    """Redact every log record created in this process from now on (idempotent)."""
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        return _redact_record(previous(*args, **kwargs))

    logging.setLogRecordFactory(factory)
