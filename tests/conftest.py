"""Shared fixtures."""
import hashlib
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.app as app_module
import backend.database as database
from backend.app import create_app
from backend.database import get_db
from data.seed import seed
from engine import bridge

REAL_DB = Path(database.DB_PATH)


def db_fingerprint(path: Path = REAL_DB):
    """(size, mtime, sha256) of the real database file, or None if it does not exist."""
    if not path.exists():
        return None
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True, scope="session")
def real_database_is_never_used(tmp_path_factory):
    """Every app the tests build (create_app() creates tables and adds columns; the Omega parity test's live
    backend serves requests) uses a seeded temporary database, never data/dealdesk.db. Session-wide, so it is
    in place before module-scoped fixtures; at the end of the run the real file must be unchanged."""
    before = db_fingerprint()
    temp_engine = create_engine(f"sqlite:///{(tmp_path_factory.mktemp('db') / 'session.db').as_posix()}",
                                connect_args={"check_same_thread": False})
    seed(temp_engine)
    factory = sessionmaker(bind=temp_engine, autoflush=False, autocommit=False)
    with pytest.MonkeyPatch.context() as mp:
        for module in (database, app_module):
            mp.setattr(module, "engine", temp_engine)
            mp.setattr(module, "SessionLocal", factory)
        yield temp_engine
    temp_engine.dispose()
    assert db_fingerprint() == before, "the test run changed data/dealdesk.db"


def _blocked(service):
    def call(*args, **kwargs):
        raise AssertionError(f"a test tried to call the real {service}")
    return call


def _no_real_network(mp) -> None:
    """ASI:One, Tavily and Telegram are off, and each one's network function refuses to run."""
    from backend.agent import language, tools
    from backend.telegram import client

    for var in ("DEALDESK_LANGUAGE", "DEALDESK_WEB_SEARCH", "DEALDESK_TELEGRAM"):
        mp.setenv(var, "off")
    mp.setattr(language, "_post", _blocked("ASI:One API"))
    mp.setattr(tools, "_tavily_post", _blocked("web search API"))
    mp.setattr(client, "_api_call", _blocked("Telegram Bot API"))


@pytest.fixture(autouse=True, scope="session")
def no_real_network_for_the_session(tmp_path_factory):
    """Session-wide, so it is in place before module-scoped fixtures too: the Omega parity test's
    live backend runs the app lifespan, which would otherwise start the Telegram bot with the
    token in omega/omega.env. Quote codes are signed with a temporary secret, never the real one."""
    with pytest.MonkeyPatch.context() as mp:
        _no_real_network(mp)
        secrets_dir = tmp_path_factory.mktemp("secrets")
        mp.setenv("DEALDESK_QUOTE_SECRET_FILE", str(secrets_dir / "quote_hmac.key"))
        mp.setenv("DEALDESK_LEDGER_SECRET_FILE", str(secrets_dir / "ledger_hmac.key"))
        yield


@pytest.fixture(autouse=True)
def temp_secrets_per_test(monkeypatch, tmp_path):
    """Each test signs quotes and the audit ledger with its own new secrets (never the real files)."""
    from backend.services import ledger, quote_seal

    monkeypatch.setenv("DEALDESK_QUOTE_SECRET_FILE", str(tmp_path / "quote_hmac.key"))
    monkeypatch.setenv("DEALDESK_LEDGER_SECRET_FILE", str(tmp_path / "ledger_hmac.key"))
    quote_seal.reset_cache()
    ledger.reset_cache()
    yield
    quote_seal.reset_cache()
    ledger.reset_cache()


@pytest.fixture(autouse=True)
def no_real_language_model(monkeypatch):
    """Tests never call ASI:One, Tavily or Telegram. Tests that need answers replace the one
    network function with a fake (restored after each test)."""
    _no_real_network(monkeypatch)


@pytest.fixture
def stack(tmp_path):
    """Fresh DB + a private copy of engine/*.metta, so the real policy is never touched."""
    engine_copy = tmp_path / "engine"
    engine_copy.mkdir()
    for f in (*bridge.RULE_FILES, "compat_petta.pl", "policy.original.metta"):
        shutil.copy(bridge.ENGINE_DIR / f, engine_copy / f)
    bridge.reset_runner(engine_copy)

    db_engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    seed(db_engine)
    Session = sessionmaker(bind=db_engine)
    app = create_app()

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield {"client": TestClient(app), "Session": Session, "engine_dir": engine_copy}
    bridge.reset_runner()
    db_engine.dispose()
