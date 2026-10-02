"""Starts and stops the Telegram channel with the backend (FastAPI lifespan, so start.bat and
stop.bat need no extra window). It runs in this process on purpose: in Omega mode the agent's
decisions go through the Omega runner, which lives here. Without a token the channel is off,
with one clear log line, and the app works as before."""
import logging
import re
import threading
from datetime import timezone

from backend.telegram import client

logger = logging.getLogger("uvicorn.error")   # shown in the backend window
_USERNAME = re.compile(r"^[A-Za-z0-9_]{5,32}$")

_state = {"enabled": False, "bot_username": None}
_thread: threading.Thread | None = None
_stop = threading.Event()


def status() -> dict:
    return dict(_state)


def set_username(username) -> None:
    _state["bot_username"] = username if isinstance(username, str) and _USERNAME.match(username) else None


def code_view(row) -> dict:
    """A one-time link code for the page: the code, its expiry and the deep link to the bot."""
    username = _state["bot_username"]
    expires_at = row.expires_at
    if expires_at.tzinfo is None:  # SQLite returns UTC datetimes without their timezone
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return {"code": row.code, "command": f"/start {row.code}",
            "expires_at": expires_at.isoformat(), "bot_username": username,
            "link": f"https://t.me/{username}?start={row.code}" if username else None}


def start() -> None:
    global _thread
    token = client.bot_token()
    if not token:
        _state.update(enabled=False, bot_username=None)
        reason = ("DEALDESK_TELEGRAM=off" if client.switched_off()
                  else "TELEGRAM_BOT_TOKEN is not set (omega/omega.env)")
        logger.warning("Telegram channel disabled: %s. TrustDeal works as before without it.", reason)
        return
    if _thread is not None and _thread.is_alive():
        return
    from backend.telegram.bot import TelegramBot   # here, not at import: bot.py imports the routes

    _state["enabled"] = True
    _stop.clear()
    bot = TelegramBot(client.BotAPI(token))
    _thread = threading.Thread(target=bot.run, args=(_stop,), name="telegram-bot", daemon=True)
    _thread.start()
    logger.info("Telegram channel enabled: long polling started (no public URL needed).")


def stop() -> None:
    global _thread
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=2)
        _thread = None
