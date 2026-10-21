import json
import sys
import textwrap

import pytest
from fastapi import FastAPI

from app.contract import ManifestError, validate_manifest
from app.hub import Hub
from app.modules import load_modules

GOOD = {"contract": 1, "id": "demo", "name": "Demo", "version": "1.0.0"}


def test_minimal_manifest_is_valid():
    assert validate_manifest(GOOD, "demo")["id"] == "demo"


@pytest.mark.parametrize(
    "patch",
    [
        {"contract": 2},
        {"id": "Bad-Id"},
        {"version": "1.0"},
        {"surprise": True},
        {"permissions": ["root.access"]},
        {"widgets": [{"id": "w", "title": "W", "sizes": ["3x3"], "default": "3x3"}]},
        {"widgets": [{"id": "w", "title": "W", "sizes": ["1x1"], "default": "2x2"}]},
        {"widgets": [{"id": "w", "title": "W", "sizes": ["1x1"], "default": "1x1"}] * 2},
        {"actions": [{"name": "a", "description": "x"}] * 2},
    ],
)
def test_invalid_manifests_are_rejected(patch):
    with pytest.raises(ManifestError):
        validate_manifest({**GOOD, **patch}, "demo")


def test_id_must_match_directory():
    with pytest.raises(ManifestError):
        validate_manifest(GOOD, "other")


def _write(root, name, manifest, backend):
    d = root / name
    d.mkdir()
    (d / "__init__.py").write_text("")
    (d / "manifest.json").write_text(json.dumps(manifest))
    (d / "backend.py").write_text(textwrap.dedent(backend))


def test_bad_modules_are_skipped_not_fatal(tmp_path, monkeypatch):
    pkg = tmp_path / "fakemods"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    ok = "from app.sdk import Module\nmodule = Module()\n"
    _write(pkg, "good", {**GOOD, "id": "good"}, ok)
    _write(pkg, "bad_manifest", {"id": "bad_manifest"}, ok)
    _write(pkg, "no_module", {**GOOD, "id": "no_module"}, "x = 1\n")
    _write(pkg, "broken_import", {**GOOD, "id": "broken_import"}, "import does_not_exist\n")
    _write(  # code declares an action the manifest does not
        pkg, "mismatch", {**GOOD, "id": "mismatch"},
        "from app.sdk import Module\nmodule = Module()\n@module.action('sneaky')\ndef f(ctx): pass\n",
    )
    _write(  # reminders without the push.send permission
        pkg, "no_perm", {**GOOD, "id": "no_perm"},
        "from app.sdk import Module\nmodule = Module()\n@module.reminders\ndef f(db, now): return []\n",
    )

    hub = Hub()
    load_modules(FastAPI(), hub, str(pkg), package="fakemods")
    status = {m["id"]: m["status"] for m in hub.modules}
    assert status == {
        "bad_manifest": "error", "broken_import": "error", "good": "ok",
        "mismatch": "error", "no_module": "error", "no_perm": "error",
    }
    assert "sneaky" in next(m for m in hub.modules if m["id"] == "mismatch")["error"]
    assert list(hub.manifests) == ["good"]  # failed modules register nothing
    sys.modules.pop("fakemods", None)
