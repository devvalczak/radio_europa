import importlib.util
import sys

from fastapi import FastAPI

from app.hub import Hub
from app.modules import load_modules
from conftest import ROOT


def test_scaffolded_module_satisfies_the_contract(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("new_module", ROOT / "scripts" / "new_module.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    pkg = tmp_path / "scafmods"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    gen.scaffold("pogoda", pkg)

    hub = Hub()
    load_modules(FastAPI(), hub, str(pkg), package="scafmods")
    assert [(m["id"], m["status"], m["error"]) for m in hub.modules] == [("pogoda", "ok", "")]
    assert "pogoda.status" in hub.actions and ("pogoda", "summary") in hub.widgets
    sys.modules.pop("scafmods", None)


def test_scaffold_rejects_bad_ids(tmp_path):
    import pytest

    spec = importlib.util.spec_from_file_location("new_module", ROOT / "scripts" / "new_module.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    for bad in ["Pogoda", "1x", "a", "../evil", "x-y"]:
        with pytest.raises(SystemExit):
            gen.scaffold(bad, tmp_path)
