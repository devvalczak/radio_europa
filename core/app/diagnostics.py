"""Diagnostyka: checks of everything that can quietly go wrong (configuration, background tasks, push, AI,
external services, modules). Core checks live here; modules add their own with `@module.diagnostic(...)`.

Fast checks only read the database/configuration and run when the page opens. Slow ones (network) run on demand.
Results never contain secrets: they say *whether* something is set, not its value.
"""

import platform
import shutil
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import ai, crypto, logs, scheduler
from .config import settings
from .hub import hub
from .models import PushSubscription, User
from .sdk import Check, Ctx
from .timeutil import utcnow

VERSION = "0.4.0"
GROUPS = {"system": "System", "push": "Powiadomienia push", "ai": "Asystent AI", "modules": "Moduły i pulpit"}


@dataclass
class Env:
    db: Session
    request: Request
    user: User


@dataclass
class Def:
    id: str
    group: str  # GROUPS key, or a module id
    title: str
    slow: bool
    run: Callable[[Env], Check | list[Check]]


_core: list[Def] = []


def core_check(check_id: str, group: str, title: str, slow: bool = False):
    def deco(fn):
        _core.append(Def(check_id, group, title, slow, fn))
        return fn

    return deco


def _ago(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "nigdy"
    s = int((now - dt).total_seconds())
    return "przed chwilą" if s < 60 else f"{s // 60} min temu" if s < 3600 else f"{s // 3600} h temu" if s < 86400 else f"{s // 86400} dni temu"


# --- system ------------------------------------------------------------------------------------------------
@core_check("system.version", "system", "Wersja i środowisko")
def _version(env: Env) -> Check:
    db = env.db.get_bind().dialect.name
    return Check("ok", f"Jarvis {settings.version or VERSION} · Python {platform.python_version()} · baza {db}")


@core_check("system.migrations", "system", "Schemat bazy (migracje)")
def _migrations(env: Env) -> Check:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from .migrate import _INI

    heads = set(ScriptDirectory.from_config(Config(str(_INI))).get_heads())
    current = set(env.db.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if current == heads:
        return Check("ok", f"Aktualny ({', '.join(sorted(current))})")
    return Check("error", f"Baza: {', '.join(sorted(current)) or 'brak'}, kod: {', '.join(sorted(heads))}",
                 "Uruchom ponownie core — migracje wykonują się przy starcie; sprawdź log startu.")


@core_check("system.secrets", "system", "Konfiguracja i sekrety")
def _secrets(env: Env) -> list[Check]:
    out = [Check("ok" if len(settings.secret_key) >= 32 else "error", "Klucz sesji ustawiony" if len(settings.secret_key) >= 32 else "Za krótki klucz sesji",
                 title="JARVIS_SECRET_KEY")]
    if settings.setup_token:
        out.append(Check("warn", "Token pierwszej konfiguracji wciąż jest w .env",
                         "Konto już istnieje — usuń JARVIS_SETUP_TOKEN z .env (niepotrzebny sekret).", "JARVIS_SETUP_TOKEN"))
    out.append(Check("ok", "Klucz szyfrowania haseł ustawiony", title="JARVIS_ENCRYPTION_KEY") if crypto.available() else
               Check("warn", "Brak klucza — nie da się zapisać haseł skrzynek ani hostów SSH",
                     "Uruchom `make encryption-key` i zrestartuj core.", "JARVIS_ENCRYPTION_KEY"))
    return out


@core_check("system.account", "system", "Bezpieczeństwo konta")
def _account(env: Env) -> Check:
    if env.user.totp_enabled:
        return Check("ok", "Weryfikacja dwuetapowa (TOTP) włączona")
    return Check("warn", "Weryfikacja dwuetapowa wyłączona",
                 "Włącz 2FA w Ustawieniach — aplikacja jest dostępna z internetu i daje dostęp do konsoli SSH.")


@core_check("system.proxy", "system", "Połączenie i proxy")
def _proxy(env: Env) -> list[Check]:
    h = env.request.headers
    proto = h.get("x-forwarded-proto") or env.request.url.scheme
    via = "Cloudflare" if h.get("cf-ray") else "proxy (Caddy)" if h.get("x-forwarded-for") else "bezpośrednio"
    out = [Check("ok", f"Żądanie przyszło {via}, protokół {proto}", title="Trasa")]
    host = (env.request.url.hostname or "")
    local = host in ("localhost", "127.0.0.1") or host.endswith(".local")
    if settings.cookie_secure and proto != "https" and not local:
        out.append(Check("error", "Ciasteczko sesji wymaga https, a połączenie jest po http",
                         "Wejdź przez adres https (Cloudflare) albo ustaw JARVIS_COOKIE_SECURE=false tylko w sieci lokalnej.", "Ciasteczko"))
    elif not settings.cookie_secure and proto == "https":
        out.append(Check("warn", "JARVIS_COOKIE_SECURE=false przy połączeniu https",
                         "Ustaw JARVIS_COOKIE_SECURE=true, żeby ciasteczko sesji nie mogło iść po http.", "Ciasteczko"))
    else:
        out.append(Check("ok", "Ustawienie ciasteczka pasuje do połączenia", title="Ciasteczko"))
    return out


@core_check("system.clock", "system", "Zegar serwera")
def _clock(env: Env) -> Check:
    return Check("ok", f"Czas serwera (UTC): {utcnow():%Y-%m-%d %H:%M:%S}. Różnicę z tym urządzeniem pokazuje przeglądarka.")


@core_check("system.storage", "system", "Dysk i logi")
def _storage(env: Env) -> list[Check]:
    out = []
    path = logs.log_path()
    if path is None:
        out.append(Check("warn", "Plik logów wyłączony (JARVIS_LOG_DIR puste) — Dziennik nie pokaże logów", title="Logi"))
    else:
        size = sum(p.stat().st_size for p in logs.log_files())
        out.append(Check("ok", f"{path} · {size / 1_048_576:.1f} MB w {len(logs.log_files())} plikach · retencja audytu {settings.retention_days} dni",
                         title="Logi"))
    target = Path(settings.log_dir or ".")
    while not target.exists() and target != target.parent:
        target = target.parent
    free = shutil.disk_usage(target).free / 1_073_741_824
    out.append(Check("ok" if free > 1 else "warn", f"Wolne miejsce: {free:.1f} GB",
                     "" if free > 1 else "Mało miejsca — zwolnij dysk, inaczej baza i logi przestaną się zapisywać.", "Dysk"))
    return out


@core_check("system.tasks", "system", "Zadania w tle")
def _tasks(env: Env) -> list[Check]:
    now = utcnow()
    out = []
    rs = scheduler.reminder_status
    if rs["last_tick"] is None:
        out.append(Check("warn", "Pętla przypomnień jeszcze nie ruszyła", "Jeśli to trwa dłużej niż minutę, sprawdź log startu core.", "Przypomnienia i alarmy"))
    else:
        stale = now - rs["last_tick"] > timedelta(seconds=settings.scheduler_interval * 4)
        out.append(Check("error" if rs["error"] else "warn" if stale else "ok",
                         f"Ostatnie sprawdzenie {_ago(rs['last_tick'], now)}, wysłano od startu: {rs['sent']}"
                         + (f" · błąd: {rs['error']}" if rs["error"] else ""),
                         "Pętla stoi — zrestartuj core." if stale else "", "Przypomnienia i alarmy"))
    for module, task_id, every, _fn in hub.tasks:
        st = scheduler.task_status.get(f"{module}.{task_id}")
        name = f"{module}.{task_id} (co {every // 60} min)"
        if st is None:
            out.append(Check("skip", "Jeszcze się nie uruchomiło", title=name))
        elif not st["ok"]:
            out.append(Check("error", f"Ostatnio {_ago(st['last_run'], now)}: {st['error']}", "Szczegóły w Dzienniku → Logi (jarvis.scheduler).", name))
        else:
            late = now - st["last_run"] > timedelta(seconds=every * 3)
            out.append(Check("warn" if late else "ok", f"Ostatnio {_ago(st['last_run'], now)}, trwało {st['ms']} ms", "" if not late else "Opóźnione — sprawdź logi.", name))
    return out


# --- push ----------------------------------------------------------------------------------------------------
@core_check("push.server", "push", "Serwer powiadomień")
def _push_server(env: Env) -> list[Check]:
    if not (settings.vapid_public_key and settings.vapid_private_key):
        return [Check("error", "Brak kluczy VAPID — serwer nie może wysyłać powiadomień",
                      "Wygeneruj je `python scripts/gen_secrets.py`, wpisz JARVIS_VAPID_* do .env i zrestartuj core.")]
    subs = env.db.scalars(select(PushSubscription)).all()
    hosts = Counter(urlsplit(s.endpoint).hostname or "?" for s in subs)
    return [Check("ok", "Klucze VAPID ustawione", title="Klucze"),
            Check("ok" if subs else "warn", f"{len(subs)} urządzeń: " + ", ".join(f"{h} ×{n}" for h, n in hosts.items()) if subs else "Żadne urządzenie nie ma włączonych powiadomień",
                  "" if subs else "Włącz powiadomienia w Ustawieniach na telefonie i komputerze.", "Subskrypcje")]


@core_check("push.send", "push", "Wysyłka testowa na wszystkie urządzenia", slow=True)
def _push_send(env: Env) -> list[Check]:
    from .push import Reminder, send_push_detailed

    results = send_push_detailed(env.db, Reminder("Jarvis — test", "Powiadomienie z panelu Diagnostyka."))
    if not results:
        return [Check("warn", "Nie ma do kogo wysłać (brak subskrypcji)")]
    return [Check("ok" if r["ok"] else "error", r["detail"], r.get("hint", ""), r["host"]) for r in results]


# --- AI -----------------------------------------------------------------------------------------------------
@core_check("ai.config", "ai", "Konfiguracja AI")
def _ai_config(env: Env) -> Check:
    d = ai.describe()
    if not d["configured"]:
        return Check("skip", "AI wyłączone (brak JARVIS_AI_*) — streszczenia i czat nie działają",
                     "Ustaw JARVIS_AI_PROVIDER, JARVIS_AI_MODEL i klucz w .env (przykład Gemini w README).")
    return Check("ok", f"{d['provider']} · {d['model']} · {d['host']}")


@core_check("ai.call", "ai", "Zapytanie testowe do AI", slow=True)
def _ai_call(env: Env) -> Check:
    if not ai.configured():
        return Check("skip", "AI nie jest skonfigurowane")
    try:
        answer = ai.complete("Jesteś testem łączności. Odpowiedz dokładnie jednym słowem.", "Napisz: OK", max_tokens=20, timeout=30)
    except ai.AIError as exc:
        return Check("error", exc.user_message())
    return Check("ok", f"Odpowiedź: {answer[:60]!r}")


# --- modules and dashboard ------------------------------------------------------------------------------------
@core_check("modules.status", "modules", "Moduły")
def _modules(env: Env) -> list[Check]:
    return [Check("ok" if m["status"] == "ok" else "error", f"wersja {m.get('version', '?')}" if m["status"] == "ok" else m["error"],
                  "" if m["status"] == "ok" else "Moduł jest wyłączony do czasu naprawy; reszta działa.", m["name"]) for m in hub.modules]


@core_check("modules.widgets", "modules", "Widgety pulpitu (czas generowania)")
def _widgets(env: Env) -> list[Check]:
    from pydantic import TypeAdapter

    from .widgets import Block

    blocks = TypeAdapter(list[Block])
    out = []
    for m in hub.modules:
        if m["status"] != "ok":
            continue
        for w in hub.manifests[m["id"]].get("widgets", []):
            started = time.monotonic()
            try:
                blocks.validate_python(hub.render_widget(Ctx(env.db, m["id"], hub), w["id"]))
                ms = round((time.monotonic() - started) * 1000)
                out.append(Check("ok" if ms < 300 else "warn", f"{ms} ms", "" if ms < 300 else "Wolny widget — spowalnia pulpit.", f"{m['name']}: {w['title']}"))
            except Exception as exc:  # noqa: BLE001
                env.db.rollback()
                out.append(Check("error", f"{type(exc).__name__}: {exc}"[:300], "Ten widget pokaże błąd na pulpicie.", f"{m['name']}: {w['title']}"))
    return out


@core_check("modules.frontend", "modules", "Błędy w przeglądarce (24 h)")
def _frontend(env: Env) -> Check:
    if logs.log_path() is None:
        return Check("skip", "Plik logów wyłączony")
    since = utcnow() - timedelta(hours=24)
    entries = [e for e in logs.read_logs(logger="jarvis.frontend", level="WARNING", limit=1000)
               if str(e.get("ts", "")) >= since.strftime("%Y-%m-%dT%H:%M:%S")]
    if not entries:
        return Check("ok", "Brak zgłoszonych błędów")
    top = Counter(str(e.get("msg", ""))[:80] for e in entries).most_common(3)
    errors = sum(1 for e in entries if e.get("level") == "ERROR")
    return Check("error" if errors else "warn", f"{len(entries)} wpisów ({errors} błędów). Najczęstsze: " + " | ".join(f"{m} ×{n}" for m, n in top),
                 "Szczegóły: Dziennik → Logi → Tylko przeglądarka.")


# --- registry ---------------------------------------------------------------------------------------------------
def all_defs() -> list[Def]:
    defs = list(_core)
    for module, did, title, slow, fn in hub.diagnostics:
        defs.append(Def(f"{module}.{did}", module, title, slow, lambda env, fn=fn, module=module: fn(Ctx(env.db, module, hub))))
    return defs


def run(d: Def, env: Env) -> dict:
    started = time.monotonic()
    try:
        res = d.run(env)
        results = res if isinstance(res, list) else [res]
    except Exception as exc:  # noqa: BLE001 — a failing check is a result, not a crash
        env.db.rollback()
        results = [Check("error", f"Test się wysypał: {type(exc).__name__}: {exc}"[:300])]
    return {"id": d.id, "ms": round((time.monotonic() - started) * 1000), "results": [asdict(r) for r in results]}


def group_title(group: str) -> str:
    if group in GROUPS:
        return GROUPS[group]
    return next((m["name"] for m in hub.modules if m["id"] == group), group)


def python_info() -> str:
    return sys.version.split()[0]
