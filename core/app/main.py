import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from . import auth, modules, push, scheduler
from .auth import require_user
from .config import settings
from .db import Base, engine


def create_app() -> FastAPI:
    if len(settings.secret_key) < 32:
        raise RuntimeError("JARVIS_SECRET_KEY must be set to at least 32 characters")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(engine)
        task = asyncio.create_task(scheduler.run())
        yield
        task.cancel()

    app = FastAPI(title="Jarvis", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/modules", dependencies=[Depends(require_user)])
    def list_modules():
        return modules.loaded

    app.include_router(auth.router, prefix="/api")
    app.include_router(push.router, prefix="/api")
    modules.load_modules(app)  # imports module models before create_all runs
    return app


app = create_app()
