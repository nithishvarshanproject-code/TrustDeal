"""Telegram Bot API transport: plain HTTPS calls and long polling (getUpdates), so the bot needs
no public URL and no webhook.

The token comes from TELEGRAM_BOT_TOKEN (environment, else omega/omega.env). It is part of every
API URL, so: it is registered with the log redaction (and a token pattern is masked too), errors
are reported by method and status only, and the URL is never logged or put into an exception.
DEALDESK_TELEGRAM=off disables the channel (tests use it).
"""
import json
import os
from pathlib import Path

import httpx

from engine import log_redaction

ENV_FILE = Path(__file__).resolve().parents[2] / "omega" / "omega.env"
API_BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")
POLL_TIMEOUT_S = int(os.getenv("TELEGRAM_POLL_TIMEOUT_S", "5"))   # long poll; the sweep runs in between
HTTP_TIMEOUT_S = 20.0
MAX_ERROR_TEXT = 120


class TelegramError(Exception):
    """A Bot API call failed (network, HTTP status or ok=false). Never contains the token."""


class TelegramConflict(TelegramError):
    """HTTP 409: another process is polling with the same token (e.g. during a backend reload)."""


def switched_off() -> bool:
    return os.getenv("DEALDESK_TELEGRAM", "on").lower() == "off"


def bot_token() -> str | None:
    if switched_off():
        return None
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token and ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip()
    log_redaction.register_secret(token)
    return token or None


def _api_call(token: str, method: str, payload: dict, files: dict | None = None,
              timeout: float = HTTP_TIMEOUT_S) -> object:
    """The only network call of the Telegram channel (tests replace it). Returns the `result`."""
    url = f"{API_BASE}/bot{token}/{method}"
    try:
        if files:
            data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in payload.items()}
            resp = httpx.post(url, data=data, files=files, timeout=timeout)
        else:
            resp = httpx.post(url, json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        raise TelegramError(f"{method}: network error ({exc.__class__.__name__})") from None
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code == 409:
        raise TelegramConflict(f"{method}: HTTP 409 (another poller is running)")
    if resp.status_code != 200 or not body.get("ok"):
        text = log_redaction.redact(str(body.get("description") or ""))[:MAX_ERROR_TEXT]
        raise TelegramError(f"{method}: HTTP {resp.status_code} {text}".strip())
    return body.get("result")


class BotAPI:
    """The few Bot API methods the channel uses. Messages are plain text (no parse mode)."""

    def __init__(self, token: str):
        self._token = token

    def call(self, method: str, payload: dict, files: dict | None = None, timeout: float = HTTP_TIMEOUT_S):
        return _api_call(self._token, method, payload, files, timeout)

    def get_me(self) -> dict:
        return self.call("getMe", {})

    def get_updates(self, offset: int | None, timeout: int = POLL_TIMEOUT_S) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload, timeout=timeout + HTTP_TIMEOUT_S) or []

    def send_message(self, chat_id: int, text: str, buttons: list[list[dict]] | None = None) -> dict:
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return self.call("sendMessage", payload)

    def send_document(self, chat_id: int, filename: str, data: bytes, caption: str) -> dict:
        return self.call("sendDocument", {"chat_id": chat_id, "caption": caption},
                         files={"document": (filename, data, "application/pdf")})

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def clear_buttons(self, chat_id: int, message_id: int) -> None:
        self.call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                             "reply_markup": {"inline_keyboard": []}})
