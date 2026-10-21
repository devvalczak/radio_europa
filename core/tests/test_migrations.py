from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app import migrate
from app.db import Base


def test_models_and_migrations_agree(client):
    """Fails when a model changed without a new migration."""
    from app import db

    with db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"run `make migration m=...`: {diff}"


def test_database_from_stage_1_is_upgraded(tmp_path):
    """A DB made by the pre-Alembic release (tables, no alembic_version) must upgrade in place."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    cfg = Config(str(migrate._INI))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, migrate.BASELINE)
        conn.exec_driver_sql("DROP TABLE alembic_version")  # looks like a legacy database
        conn.exec_driver_sql("INSERT INTO notes (title, content, tags, pinned, created_at, updated_at) "
                             "VALUES ('keep me', '', '', 0, '2026-01-01', '2026-01-01')")
    migrate.upgrade(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"audit_log", "user_layouts", "alembic_version"} <= tables
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT title FROM notes").scalar() == "keep me"
    migrate.upgrade(engine)  # idempotent
