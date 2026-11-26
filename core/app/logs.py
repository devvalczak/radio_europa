"""Application logging: readable text on stdout, JSON lines in a rotating file, secret redaction.

The file (`JARVIS_LOG_DIR/jarvis.jsonl`, rotated as .1, .2, ...) is what the UI reads through
`read_logs`. Never log secrets or message bodies; the redaction filter is a safety net, not a licence.
"""

import json
import logging
import logging.handlers
import os
import re
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

FILE_NAME = "jarvis.jsonl"
MARK = "_jarvis_handler"
SECRET_KEY = re.compile(r"pass(word)?|token|ticket|secret|api_?key|totp|cookie|authorization|credential", re.I)
# key=value / key: value / "key": "value" fragments inside free-form messages
SECRET_TEXT = re.compile(
    r"""(?P<k>["']?\b[\w-]*(?:password|passwd|token|ticket|secret|api_?key|totp|cookie|authorization)[\w-]*["']?\s*[=:]\s*)"""
    r"""(?P<v>"[^"]*"|'[^']*'|(?:Bearer\s+)?[^\s,;&}]+)""",
    re.I,
)
_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}


def redact_text(text: str) -> str:
    return SECRET_TEXT.sub(lambda m: m.group("k") + "***", text)


def _redact_value(key: str, value):
    if SECRET_KEY.search(key):
        return "***"
    if isinstance(value, dict):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, str):
        return redact_text(value)
    return value


class RedactFilter(logging.Filter):
    """Freezes the message (args applied) with secrets masked, and masks secret-looking extra fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "_redacted", False):
            try:
                msg = record.getMessage()
            except Exception:  # bad format args: keep the raw template rather than crash logging
                msg = str(record.msg)
            record.msg, record.args = redact_text(msg), None
            for key in set(vars(record)) - _STD_ATTRS:
                setattr(record, key, _redact_value(key, getattr(record, key)))
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:  # another handler may have formatted it already: redact either way
                record.exc_text = redact_text(record.exc_text)
            record._redacted = True
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in set(vars(record)) - _STD_ATTRS - {"_redacted"}:
            data[key] = getattr(record, key)
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            data["exc"] = record.exc_text
        return json.dumps(data, ensure_ascii=False, default=str)


def log_path() -> Path | None:
    return Path(settings.log_dir) / FILE_NAME if settings.log_dir else None


def setup_logging() -> None:
    """Idempotent: replaces handlers installed by a previous call (tests create the app more than once)."""
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, MARK, False)]:
        root.removeHandler(h)
        h.close()
    root.setLevel(settings.log_level.upper())
    redact = RedactFilter()

    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handlers: list[logging.Handler] = [out]
    path = log_path()
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            f = logging.handlers.RotatingFileHandler(
                path, maxBytes=int(settings.log_max_mb * 1024 * 1024), backupCount=settings.log_backups, encoding="utf-8"
            )
            f.setFormatter(JsonFormatter())
            handlers.append(f)
        except OSError as exc:
            path = None
            print(f"jarvis: log file disabled ({type(exc).__name__}: {exc})", file=sys.stderr)
    for h in handlers:
        h.addFilter(redact)
        setattr(h, MARK, True)
        root.addHandler(h)

    # uvicorn installs its own handlers; route its error log through ours and replace its access log
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # replaced by the request middleware in main.py
    for noisy in ("httpx", "httpcore", "multipart", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if settings.log_dir and path is None:
        logging.getLogger("jarvis.logs").warning("log file disabled, logging to stdout only")


# --- reading -------------------------------------------------------------------------------

def _reverse_lines(path: Path, chunk: int = 64 * 1024) -> Iterator[str]:
    """Yields the lines of a file from the last to the first without reading it all at once."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos, tail = fh.tell(), b""
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            fh.seek(pos)
            parts = (fh.read(step) + tail).split(b"\n")
            tail = parts.pop(0)
            for line in reversed(parts):
                if line.strip():
                    yield line.decode("utf-8", "replace")
        if tail.strip():
            yield tail.decode("utf-8", "replace")


def log_files() -> list[Path]:
    path = log_path()
    if path is None:
        return []
    files = [path] + [path.with_name(f"{FILE_NAME}.{i}") for i in range(1, settings.log_backups + 1)]
    return [p for p in files if p.is_file()]


def read_logs(*, level: str = "", logger: str = "", q: str = "", limit: int = 200) -> list[dict]:
    """Newest first. `level` is a minimum (WARNING also shows ERROR), `logger` a name prefix, `q` a substring."""
    min_level = logging.getLevelName(level.upper()) if level else 0
    if not isinstance(min_level, int):
        min_level = 0
    needle = q.lower()
    out: list[dict] = []
    for path in log_files():
        for line in _reverse_lines(path):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            lvl = logging.getLevelName(str(entry.get("level", "")))
            if isinstance(lvl, int) and lvl < min_level:
                continue
            name = str(entry.get("logger", ""))
            if logger and not (name == logger or name.startswith(logger + ".")):
                continue
            if needle and needle not in str(entry.get("msg", "")).lower() and needle not in name.lower():
                continue
            out.append(entry)
            if len(out) >= limit:
                return out
    return out
