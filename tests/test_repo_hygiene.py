"""Repository hygiene: secrets, local data and build output are git-ignored, and the test run never
touches the real database (data/dealdesk.db)."""
from pathlib import Path

import backend.app as app_module
import backend.database as database
from backend.app import create_app
from tests.conftest import REAL_DB, db_fingerprint
from tests.test_agent import PHONE, PRIYA, act, say

ROOT = Path(__file__).resolve().parents[1]
MUST_IGNORE = (".env", "omega/omega.env", "omega/runtime_secret/", "omega/dealdesk_token", "backend/secrets/",
               "data/*.db", ".venv/", ".venv-codex/", "node_modules/", "dist/", "__pycache__/", ".pytest_cache/",
               "backup_codex/", "backup_bughunt/")


def test_gitignore_covers_secrets_data_and_build_output():
    lines = {line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert [entry for entry in MUST_IGNORE if entry not in lines] == []


def test_the_real_database_is_untouched_by_tests(stack):
    before = db_fingerprint()
    assert Path(app_module.engine.url.database).resolve() != REAL_DB.resolve()     # apps use a temp DB
    assert Path(database.engine.url.database).resolve() != REAL_DB.resolve()
    create_app()                                       # creates tables / adds columns: on the temp DB
    c = stack["client"]
    assert c.post("/deals/evaluate", json={"seller_id": 1, "product_id": 1, "quantity": 150,
                                           "discount_requested": 10, "claimed_tier": "Gold"}).status_code == 200
    v = say(c, PRIYA, "Can I get 20% off this phone?", product_id=PHONE)
    act(c, PRIYA, v["request_id"], "accept")
    act(c, PRIYA, v["request_id"], "order")
    assert c.post("/demo/reset", json={"confirm": "RESET"}).status_code == 200
    assert db_fingerprint() == before
