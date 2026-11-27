import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from . import ai_settings, auth, db, logs, migrate, push, scheduler, system_api
from .config import settings
from .hub import hub
from .hub_api import build_router
from .modules import load_modules
from .security import COOKIE, read_session

access = logging.getLogger("jarvis.http")


def create_app() -> FastAPI:
    if len(settings.secret_key) < 32:
        raise RuntimeError("JARVIS_SECRET_KEY must be set to at least 32 characters")
    logs.setup_logging()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        migrate.upgrade(db.engine)
        task = asyncio.create_task(scheduler.run())
        yield
        task.cancel()

    app = FastAPI(title="Jarvis", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        # path only: query strings may carry search terms or tokens
        start = time.perf_counter()
        path = request.url.path
        if path.startswith("/api/public/"):
            # public routes carry their secret in the path (e.g. calendar feed token): keep only the module part
            path = "/api/public/" + path[len("/api/public/"):].split("/", 1)[0] + "/***"
        try:
            response = await call_next(request)
        except Exception:
            access.exception("%s %s -> unhandled error", request.method, path, extra={"method": request.method, "path": path})
            raise
        if path != "/api/health":
            ms = round((time.perf_counter() - start) * 1000)
            status = response.status_code
            token = request.cookies.get(COOKIE)
            uid = read_session(token) if token else None
            level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.DEBUG
            access.log(level, "%s %s %d %dms", request.method, path, status, ms,
                       extra={"method": request.method, "path": path, "status": status, "ms": ms, "user_id": uid})
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    app.include_router(auth.router, prefix="/api")
    app.include_router(push.router, prefix="/api")
    app.include_router(system_api.router, prefix="/api")
    app.include_router(ai_settings.router, prefix="/api")
    app.include_router(build_router(hub), prefix="/api")
    load_modules(app, hub, settings.modules_dir)  # imports module models before migrations run
    return app


app = create_app()
