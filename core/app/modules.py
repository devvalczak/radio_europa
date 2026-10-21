"""Plugin discovery.

A module is a directory `<modules_dir>/<id>/` with a `manifest.json` (contract v1, see
contract/manifest.schema.json) and a `backend` package exposing `module = Module(...)`.
A module that fails validation or import is skipped and reported with status "error";
it never prevents the core or other modules from starting.
"""

import importlib
import json
import logging
from pathlib import Path

from fastapi import Depends, FastAPI

from .auth import require_user
from .contract import validate_manifest
from .hub import Hub
from .sdk import Module

log = logging.getLogger("jarvis.modules")


def load_modules(app: FastAPI, hub: Hub, modules_dir: str, package: str = "modules") -> None:
    hub.reset()
    for manifest_path in sorted(Path(modules_dir).glob("*/manifest.json")):
        directory = manifest_path.parent
        entry: dict = {"id": directory.name, "name": directory.name, "status": "error", "error": ""}
        try:
            manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")), directory.name)
            entry.update(
                name=manifest["name"],
                version=manifest["version"],
                description=manifest.get("description", ""),
                has_icon=(directory / "icon.svg").is_file(),
            )
            backend = importlib.import_module(f"{package}.{directory.name}.backend")
            module = getattr(backend, "module", None)
            if not isinstance(module, Module):
                raise ValueError("backend must expose `module = Module(...)` (from app.sdk)")
            hub.register(manifest, module)  # runs the manifest/code consistency checks first
            app.include_router(
                module.router,
                prefix=f"/api/m/{manifest['id']}",
                tags=[manifest["id"]],
                dependencies=[Depends(require_user)],
            )
            entry.update(status="ok", error="")
            log.info("loaded module %s %s", manifest["id"], manifest["version"])
        except Exception as exc:  # noqa: BLE001 — one bad module must not stop the rest
            entry["error"] = f"{type(exc).__name__}: {exc}"[:500]
            log.error("module %s skipped: %s", directory.name, entry["error"])
        hub.modules.append(entry)
