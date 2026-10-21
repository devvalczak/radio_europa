import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "core"), str(ROOT)]

os.environ.update(
    JARVIS_DATABASE_URL="sqlite://",
    JARVIS_SECRET_KEY="x" * 40,
    JARVIS_SETUP_TOKEN="setup-secret",
    JARVIS_COOKIE_SECURE="false",
    JARVIS_MODULES_DIR=str(ROOT / "modules"),
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import db as app_db  # noqa: E402

# in-memory sqlite: one shared connection so every session sees the same DB
app_db.engine.dispose()
app_db.engine = app_db.create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
app_db.SessionLocal.configure(bind=app_db.engine)

from app.main import app  # noqa: E402

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def client():
    # start empty; the app's lifespan runs the Alembic migrations (so they are tested too)
    app_db.Base.metadata.drop_all(app_db.engine)
    with app_db.engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    from app.security import limiter

    limiter._fails.clear()
    with TestClient(app, headers={"X-Jarvis": "1"}) as c:
        yield c


@pytest.fixture()
def authed(client):
    r = client.post("/api/auth/setup", json={"token": "setup-secret", "username": "owner", "password": PASSWORD})
    assert r.status_code == 200
    return client
