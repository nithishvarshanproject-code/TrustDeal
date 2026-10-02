"""Server secrets kept in git-ignored files (quote codes, audit ledger).

A secret is 32 random bytes stored as 64 hex characters. It is created on first use and never
overwritten afterwards; a corrupt file is a clear error (never silently replaced, which would make
everything signed with the old secret fail); the value is registered with the log redaction and is
never returned, printed or logged. Error messages never contain the file's content.
"""
import os
import re
import secrets
from pathlib import Path

from engine import log_redaction

_SECRET_RE = re.compile(r"[0-9a-f]{64}")


class SecretFileError(RuntimeError):
    """The secret file exists but cannot be used."""


def load_or_create(path: Path, label: str) -> bytes:
    """The 32-byte secret in `path`, created there on first use (`label` names it in errors)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # first run: create, never overwrite
    except FileExistsError:
        try:
            text = path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            raise SecretFileError(f"cannot read the {label} ({path.name})") from None
    else:
        text = secrets.token_hex(32)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(text + "\n")
    if not _SECRET_RE.fullmatch(text):
        raise SecretFileError(f"the {label} ({path.name}) is not 64 hex characters; restore it, or delete it "
                              "to start a new one (everything signed with the old one will then fail)")
    log_redaction.register_secret(text)
    return bytes.fromhex(text)
