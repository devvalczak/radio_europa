import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import auth, db, migrate, push, scheduler
from .config import settings
from .hub import hub
from .hub_api import build_router
from .modules import load_modules


def create_app() -> FastAPI:
    if len(settings.secret_key) < 32:
        raise RuntimeError("JARVIS_SECRET_KEY must be set to at least 32 characters")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        migrate.upgrade(db.engine)
        task = asyncio.create_task(scheduler.run())
        yield
        task.cancel()

    app = FastAPI(title="Jarvis", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    app.include_router(auth.router, prefix="/api")
    app.include_router(push.router, prefix="/api")
    app.include_router(build_router(hub), prefix="/api")
    load_modules(app, hub, settings.modules_dir)  # imports module models before migrations run
    return app


app = create_app()
