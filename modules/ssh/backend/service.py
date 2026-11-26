"""SSH console: keys, host-key trust (TOFU), one-time tickets, and the WebSocket <-> SSH bridge.

Security model (single-user app, but reachable from the internet):
- opening a shell needs a fresh second factor (TOTP code, or the account password when 2FA is off) -> a one-time
  ticket valid 60 s, bound to the user and the host;
- the socket also checks the session cookie and the Origin header;
- the server's host key must match the one the user accepted; a changed key is refused (possible MITM);
- credentials are encrypted at rest and never leave the server; session content is never logged or stored;
- there are no hub actions here: neither the AI assistant nor any module can reach a shell.
"""

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass

import asyncssh

from app import crypto

from .models import SshHost

log = logging.getLogger("jarvis.ssh")

TICKET_TTL = 60.0
IDLE_LIMIT = 15 * 60.0
MAX_SESSIONS = 3
CONNECT_TIMEOUT = 15.0


class SshError(Exception):
    """Message for the user (Polish)."""


# --- keys ------------------------------------------------------------------------------------------------
def generate_key(comment: str = "jarvis") -> tuple[str, str]:
    """New Ed25519 key pair -> (private OpenSSH PEM, public line for authorized_keys)."""
    key = asyncssh.generate_private_key("ssh-ed25519", comment=comment)
    return key.export_private_key().decode(), key.export_public_key().decode().strip()


def public_from_private(pem: str, passphrase: str = "") -> str:
    try:
        key = asyncssh.import_private_key(pem, passphrase or None)
    except (asyncssh.KeyImportError, ValueError) as exc:
        raise SshError("Nie udało się odczytać klucza prywatnego (zły format albo hasło klucza).") from exc
    return key.export_public_key().decode().strip()


def fingerprint(public_key: str) -> str:
    return asyncssh.import_public_key(public_key).get_fingerprint("sha256")


# --- server key (trust on first use) -------------------------------------------------------------------------
async def fetch_host_key(host: str, port: int) -> tuple[str, str, str]:
    """(public key in OpenSSH format, SHA256 fingerprint, algorithm) without logging in."""
    try:
        key = await asyncio.wait_for(asyncssh.get_server_host_key(host, port), CONNECT_TIMEOUT)
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as exc:
        raise SshError(_describe(exc, host, port)) from exc
    if key is None:
        raise SshError("Serwer nie przedstawił klucza hosta.")
    public = key.export_public_key().decode().strip()
    return public, key.get_fingerprint("sha256"), key.get_algorithm()


def _describe(exc: BaseException, host: str, port: int) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return f"{host}:{port} nie odpowiada (przekroczony czas)."
    if isinstance(exc, asyncssh.HostKeyNotVerifiable):
        return "Klucz hosta jest INNY niż zaakceptowany — możliwy atak (MITM) albo reinstalacja serwera. Sprawdź odcisk i zaakceptuj ponownie tylko, jeśli wiesz, skąd zmiana."
    if isinstance(exc, asyncssh.PermissionDenied):
        return "Serwer odrzucił logowanie (zły login, hasło albo klucz nie jest w authorized_keys)."
    if isinstance(exc, ConnectionRefusedError):
        return f"{host}:{port} odmawia połączenia (czy działa tam serwer SSH?)."
    if isinstance(exc, OSError) and ("Name or service not known" in str(exc) or "nodename nor servname" in str(exc)):
        return f"Nie znaleziono hosta {host}."
    return f"Błąd połączenia: {type(exc).__name__}: {exc}"[:300]


def connect_options(h: SshHost) -> dict:
    if not h.host_key:
        raise SshError("Najpierw sprawdź i zaakceptuj odcisk klucza serwera.")
    try:
        secret = crypto.decrypt(h.secret_enc)
        passphrase = crypto.decrypt(h.passphrase_enc) if h.passphrase_enc else None
    except crypto.CryptoError as exc:
        raise SshError("Nie można odszyfrować danych logowania (brak lub zmieniony JARVIS_ENCRYPTION_KEY) — zapisz je ponownie.") from exc
    opts = {
        "host": h.host, "port": h.port, "username": h.username,
        "known_hosts": ([asyncssh.import_public_key(h.host_key)], [], []),
        "connect_timeout": CONNECT_TIMEOUT, "keepalive_interval": 30, "agent_path": None,
        "preferred_auth": "publickey" if h.auth == "key" else "keyboard-interactive,password",
    }
    if h.auth == "key":
        opts["client_keys"] = [asyncssh.import_private_key(secret, passphrase)]
        opts["password"] = None
    else:
        opts["client_keys"] = None
        opts["password"] = secret
    return opts


async def test_login(h: SshHost) -> str:
    """Connect and authenticate, open no shell. Returns a short description."""
    started = time.monotonic()
    try:
        async with asyncssh.connect(**connect_options(h)) as conn:
            version = conn.get_extra_info("server_version", "")
    except SshError:
        raise
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as exc:
        raise SshError(_describe(exc, h.host, h.port)) from exc
    return f"Zalogowano w {round((time.monotonic() - started) * 1000)} ms · {version}"


# --- tickets and sessions --------------------------------------------------------------------------------------
@dataclass
class Ticket:
    user_id: int
    host_id: int
    expires: float


_tickets: dict[str, Ticket] = {}
_sessions: dict[int, int] = {}  # user id -> open sessions


def issue_ticket(user_id: int, host_id: int) -> str:
    now = time.monotonic()
    for k in [k for k, t in _tickets.items() if t.expires < now]:
        _tickets.pop(k, None)
    token = secrets.token_urlsafe(24)
    _tickets[token] = Ticket(user_id, host_id, now + TICKET_TTL)
    return token


def redeem_ticket(token: str, user_id: int) -> int | None:
    """One use only. Returns the host id when the ticket is valid for this user."""
    t = _tickets.pop(token, None)
    if t is None or t.expires < time.monotonic() or t.user_id != user_id:
        return None
    return t.host_id


def session_slot(user_id: int) -> bool:
    if _sessions.get(user_id, 0) >= MAX_SESSIONS:
        return False
    _sessions[user_id] = _sessions.get(user_id, 0) + 1
    return True


def release_slot(user_id: int) -> None:
    _sessions[user_id] = max(0, _sessions.get(user_id, 0) - 1)


# --- the bridge ---------------------------------------------------------------------------------------------------
async def bridge(ws, opts: dict, cols: int, rows: int) -> str:
    """Pipe a WebSocket to an interactive shell until either side closes. Returns why it ended (for the audit log).

    Client -> server: JSON text frames {"t": "in", "d": str} and {"t": "resize", "c": int, "r": int}.
    Server -> client: binary frames with terminal output; JSON {"t": "status"|"error"|"closed", "msg": str}."""
    from starlette.websockets import WebSocketDisconnect

    try:
        conn = await asyncssh.connect(**opts)
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as exc:
        msg = _describe(exc, opts["host"], opts["port"])
        await ws.send_text(json.dumps({"t": "error", "msg": msg}, ensure_ascii=False))
        return f"connect failed: {type(exc).__name__}"
    last = time.monotonic()
    reason = "closed by server"
    try:
        proc = await conn.create_process(term_type="xterm-256color", term_size=(cols, rows), encoding=None,
                                         stderr=asyncssh.STDOUT)
        try:
            await ws.send_text(json.dumps({"t": "status", "msg": "connected"}))
        except WebSocketDisconnect:
            return "closed by user"

        async def pump_out():
            nonlocal last
            while True:
                data = await proc.stdout.read(65536)
                if not data:
                    return
                last = time.monotonic()
                try:
                    await ws.send_bytes(data)
                except WebSocketDisconnect:
                    return  # browser gone: pump_in notices too

        async def pump_in():
            nonlocal last, reason
            while True:
                try:
                    raw = await ws.receive_text()
                except WebSocketDisconnect:
                    reason = "closed by user"
                    return
                last = time.monotonic()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("t") == "in" and isinstance(msg.get("d"), str):
                    proc.stdin.write(msg["d"].encode("utf-8", "replace"))
                elif msg.get("t") == "resize":
                    c, r = int(msg.get("c", 80)), int(msg.get("r", 24))
                    if 10 <= c <= 500 and 3 <= r <= 300:
                        proc.change_terminal_size(c, r)

        async def idle():
            nonlocal reason
            while True:
                await asyncio.sleep(5)
                if time.monotonic() - last > IDLE_LIMIT:
                    reason = "idle timeout"
                    return

        tasks = [asyncio.create_task(x) for x in (pump_out(), pump_in(), idle())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if isinstance(exc, WebSocketDisconnect):
                reason = "closed by user"
            elif exc is not None:
                reason = f"error: {type(exc).__name__}"
    finally:
        conn.close()
    try:
        text = {"idle timeout": "Rozłączono po 15 minutach bezczynności.", "closed by server": "Sesja zakończona przez serwer."}.get(reason)
        if text:
            await ws.send_text(json.dumps({"t": "closed", "msg": text}, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — the socket may already be gone
        pass
    return reason
