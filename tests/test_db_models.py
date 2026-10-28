"""Schema initialization and relational integrity for fresh evidence databases."""

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from abevalflow.db.engine import get_engine, init_db, make_session
from abevalflow.db.models import CaseResult, Finding, Plugin, SchemaVersion, SkillEvaluation
from abevalflow.persistence.postgres import store_snapshot


def test_engine_settings_and_session_factory(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///from-env.db")
    assert get_engine().url.database == "from-env.db"
    engine = get_engine("sqlite://")
    init_db(engine)
    with make_session(engine)() as session:
        assert isinstance(session, Session)


def test_initialization_is_idempotent_and_creates_views():
    engine = create_engine("sqlite://")
    init_db(engine)
    init_db(engine)
    assert "skill_evaluations" in inspect(engine).get_table_names()
    assert "publication_results" not in inspect(engine).get_table_names()
    assert "plugin_results" in inspect(engine).get_view_names()
    with Session(engine) as session:
        assert session.scalar(select(SchemaVersion.version)) == 2


def test_old_database_is_rejected_without_deleting_or_adding_tables():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE evaluation_runs (id INTEGER)"))
    with pytest.raises(ValueError, match="fresh database"):
        init_db(engine)
    assert inspect(engine).get_table_names() == ["evaluation_runs"]


def test_database_version_is_checked_before_modification():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE evidence_schema (id INTEGER PRIMARY KEY, version INTEGER)"))
        connection.execute(text("INSERT INTO evidence_schema VALUES (1, 100)"))
    with pytest.raises(ValueError, match="schema version"):
        init_db(engine)
    assert inspect(engine).get_table_names() == ["evidence_schema"]


def test_deleting_an_evaluation_cascades_all_evidence(pipeline_evidence):
    config, result, url = pipeline_evidence
    store_snapshot(config, result, url)
    with Session(create_engine(url)) as session:
        session.delete(session.scalar(select(SkillEvaluation)))
        session.commit()
        assert session.scalars(select(CaseResult)).all() == []
        assert session.scalars(select(Finding)).all() == []
        assert session.scalar(select(Plugin)) is not None
