"""Database engine factory and session management."""

from __future__ import annotations

import logging
import os

from sqlalchemy import Engine, create_engine, inspect, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from abevalflow.db.models import Base, SchemaVersion
from abevalflow.db.views import create_views

logger = logging.getLogger(__name__)

_DEFAULT_URL = "sqlite:///abevalflow.db"


def get_engine(url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine.

    Args:
        url: Database URL. Falls back to ``DATABASE_URL`` env var,
             then to a local SQLite file.
    """
    db_url = url or os.environ.get("DATABASE_URL", _DEFAULT_URL)

    connect_args: dict = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_engine(db_url, connect_args=connect_args)

    host = engine.url.host or engine.url.database or "in-memory"
    logger.info("Database engine created: dialect=%s host=%s", engine.url.get_backend_name(), host)
    return engine


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(OperationalError),
    reraise=True,
)
def init_db(engine: Engine) -> None:
    """Initialize fresh evidence schema v2 without altering or deleting an old database."""
    from sqlalchemy import text

    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(624235102)"))
        tables = set(inspect(connection).get_table_names())
        legacy = {"evaluation_runs", "scorecards", "security_scans", "gate_results", "trials"}
        if legacy.intersection(tables):
            raise ValueError("Legacy evaluation schema detected; configure a fresh database for evidence schema v2")
        version = (
            connection.scalar(select(SchemaVersion.version).where(SchemaVersion.id == 1))
            if "evidence_schema" in tables
            else None
        )
        if version is not None and version != 2:
            raise ValueError("Unsupported evidence schema version")
        Base.metadata.create_all(connection)
        if version is None:
            connection.execute(SchemaVersion.__table__.insert().values(id=1, version=2))
        create_views(connection)
    logger.info("Evidence schema v2 tables and views ensured")


def make_session(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to the given engine."""
    return sessionmaker(bind=engine)
