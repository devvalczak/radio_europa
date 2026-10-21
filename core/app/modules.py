"""Plugin discovery.

A module lives in `<modules_dir>/<name>/` with a `manifest.json` and a
`backend` package exposing `router` (mounted at /api/m/<id>) and, optionally,
`collect_reminders(db, now) -> list[Reminder]` which the scheduler polls.
"""

import importlib
import json
import logging
from pathlib import Path

from fastapi import FastAPI

from .config import settings

log = logging.getLogger("jarvis.modules")

loaded: list[dict] = []
reminder_hooks: list = []


def load_modules(app: FastAPI) -> None:
    from .auth import require_user
    from fastapi import Depends

    loaded.clear()
    reminder_hooks.clear()
    for manifest_path in sorted(Path(settings.modules_dir).glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pkg = manifest_path.parent.name
        backend = importlib.import_module(f"modules.{pkg}.backend")
        app.include_router(
            backend.router,
            prefix=f"/api/m/{manifest['id']}",
            tags=[manifest["id"]],
            dependencies=[Depends(require_user)],
        )
        if hasattr(backend, "collect_reminders"):
            reminder_hooks.append(backend.collect_reminders)
        loaded.append(manifest)
        log.info("loaded module %s", manifest["id"])
