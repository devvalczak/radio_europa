"""Scaffold a module that already satisfies contract v1.

    python scripts/new_module.py pogoda            # -> modules/pogoda/
    make new-module name=pogoda
"""

import json
import re
import sys
from pathlib import Path

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 12h6"/></svg>
"""

BACKEND = '''from fastapi import APIRouter

from app.sdk import Ctx, Module, TextBlock

router = APIRouter()  # mounted at /api/m/{id}; every route already requires login
module = Module(router=router)


@router.get("")
def overview():
    return {{"module": "{id}"}}


# Every action / widget / subscription below must also be listed in manifest.json.
@module.action("status")
def status(ctx: Ctx):
    return {{"ok": True}}


@module.widget("summary")
def summary(ctx: Ctx):
    return [TextBlock(text="{title}: działa.")]

# Models go in backend/models.py (subclass app.db.Base) and are imported here; then run
# `make migration m="add {id} tables"` and commit the generated file in core/migrations/versions.
'''

FRONTEND = '''import {{ createT, ModuleDef }} from "@jarvis/sdk";
import {{ Card, PageTitle }} from "@jarvis/ui";
import pl from "../locales/pl.json";

const t = createT(pl);

function Page() {{
  return (
    <>
      <PageTitle>{{t("title")}}</PageTitle>
      <Card>{{t("hello")}}</Card>
    </>
  );
}}

export default {{ id: "{id}", Page }} satisfies ModuleDef;
'''


def scaffold(module_id: str, modules_dir: Path) -> Path:
    if not ID_RE.match(module_id):
        raise SystemExit(f"invalid id '{module_id}': use lowercase letters, digits, _ (2-32 chars, start with a letter)")
    root = modules_dir / module_id
    if root.exists():
        raise SystemExit(f"{root} already exists")
    title = module_id.replace("_", " ").capitalize()
    for sub in ("backend", "frontend", "locales"):
        (root / sub).mkdir(parents=True)
    (root / "__init__.py").write_text("")
    manifest = {
        "contract": 1,
        "id": module_id,
        "name": title,
        "version": "0.1.0",
        "description": f"{title}.",
        "widgets": [{"id": "summary", "title": title, "sizes": ["1x1", "2x1"], "default": "1x1", "refresh": 300}],
        "actions": [{"name": "status", "description": f"Sprawdza, czy moduł {title} działa."}],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (root / "icon.svg").write_text(ICON)
    (root / "locales" / "pl.json").write_text(
        json.dumps({"title": title, "hello": f"Moduł {title} jest gotowy do rozbudowy."}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "backend" / "__init__.py").write_text(BACKEND.format(id=module_id, title=title))
    (root / "frontend" / "index.tsx").write_text(FRONTEND.format(id=module_id))
    return root


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    created = scaffold(sys.argv[1], Path(__file__).resolve().parents[1] / "modules")
    print(f"Created {created}. Restart the app (and rebuild the frontend) to load it.")
