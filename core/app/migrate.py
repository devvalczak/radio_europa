"""Run Alembic migrations at startup.

A database created by the pre-Alembic release (tables made by create_all, no
alembic_version) is stamped as the baseline first, so it upgrades cleanly.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

BASELINE = "0001"
_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def upgrade(engine: Engine) -> None:
    with engine.begin() as conn:
        cfg = Config(str(_INI))
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "alembic_version" not in tables and "users" in tables:
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")
