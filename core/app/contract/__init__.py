import json
from pathlib import Path

from jsonschema import Draft202012Validator

_schema = json.loads((Path(__file__).parent / "manifest.schema.json").read_text(encoding="utf-8"))
_validator = Draft202012Validator(_schema)


class ManifestError(ValueError):
    pass


def validate_manifest(data: object, expected_id: str | None = None) -> dict:
    errors = sorted(_validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        where = lambda e: "/".join(str(p) for p in e.path) or "(root)"  # noqa: E731
        raise ManifestError("; ".join(f"{where(e)}: {e.message}" for e in errors[:5]))
    manifest: dict = data  # type: ignore[assignment]
    if expected_id is not None and manifest["id"] != expected_id:
        raise ManifestError(f"id '{manifest['id']}' must match the directory name '{expected_id}'")
    ids = [w["id"] for w in manifest.get("widgets", [])]
    if len(ids) != len(set(ids)):
        raise ManifestError("duplicate widget ids")
    names = [a["name"] for a in manifest.get("actions", [])]
    if len(names) != len(set(names)):
        raise ManifestError("duplicate action names")
    for w in manifest.get("widgets", []):
        if w["default"] not in w["sizes"]:
            raise ManifestError(f"widget '{w['id']}': default size must be one of its sizes")
    return manifest
