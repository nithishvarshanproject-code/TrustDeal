"""SQLite connection and session helpers."""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "dealdesk.db"
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def ensure_schema(bind=engine) -> None:
    """Small in-place migrations for databases created by an older version (no data loss)."""
    from sqlalchemy import inspect, text

    columns = {c["name"] for c in inspect(bind).get_columns("products")}
    if "category" not in columns:
        with bind.begin() as conn:
            conn.execute(text("ALTER TABLE products ADD COLUMN category VARCHAR NOT NULL DEFAULT 'general'"))
    for table in ("agent_deals", "agent_messages"):        # Telegram channel: web | telegram
        if not inspect(bind).has_table(table):
            continue
        if "channel" not in {c["name"] for c in inspect(bind).get_columns(table)}:
            with bind.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN channel VARCHAR NOT NULL DEFAULT 'web'"))
    if inspect(bind).has_table("agent_quotes"):             # tamper-proof quotes (nullable: older quotes)
        quote_columns = {c["name"] for c in inspect(bind).get_columns("agent_quotes")}
        for column, kind in (("list_price", "FLOAT"), ("verify_code", "VARCHAR")):
            if column not in quote_columns:
                with bind.begin() as conn:
                    conn.execute(text(f"ALTER TABLE agent_quotes ADD COLUMN {column} {kind}"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
