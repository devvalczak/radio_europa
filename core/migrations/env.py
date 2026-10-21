from alembic import context
from sqlalchemy import create_engine

from app.db import Base

# Importing the app registers the models of every module (see app/modules.py).
# Skipped when the app already loaded them and runs migrations itself.
import app.main  # noqa: F401  (side effect: model registration)
from app.config import settings

target_metadata = Base.metadata
config = context.config


def run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:  # called from app.migrate with an open connection
    run(connection)
else:  # called from the alembic CLI
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        run(conn)
