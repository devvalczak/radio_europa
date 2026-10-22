"""Thin, read-only IMAP layer over imaplib.

Safety properties, enforced here and covered by tests:
 - the folder is opened with EXAMINE (read-only) and bodies are fetched with BODY.PEEK, so nothing is ever
   marked read, moved or deleted — there is no STORE/EXPUNGE/COPY anywhere in this module;
 - TLS is mandatory (implicit TLS on 993, or STARTTLS on 143) with certificate verification;
 - only ports 993 and 143 are allowed and the host must resolve to a public address (SSRF guard).
"""

import imaplib
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta

from app import http
from app.config import settings

ALLOWED_PORTS = {993, 143}
PARTIAL_BYTES = 8192  # enough for headers + the start of the text
FULL_LIMIT = 1_500_000
BATCH = 25
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


class MailError(Exception):
    """Message is meant to be shown to the user (Polish)."""


@dataclass
class Raw:
    uid: int
    seen: bool
    received: datetime | None  # naive UTC (INTERNALDATE)
    data: bytes


def _internaldate(text: str) -> datetime | None:
    m = re.search(r'INTERNALDATE "\s*(\d{1,2})-(\w{3})-(\d{4}) (\d\d):(\d\d):(\d\d) ([+-])(\d\d)(\d\d)"', text)
    if not m or m.group(2) not in _MONTHS:
        return None
    day, mon, year, hh, mm, ss, sign, oh, om = m.groups()
    local = datetime(int(year), _MONTHS[mon], int(day), int(hh), int(mm), int(ss))
    offset = (int(oh) * 60 + int(om)) * (1 if sign == "+" else -1)
    return local - timedelta(minutes=offset)


class Box:
    def __init__(self, conn: imaplib.IMAP4):
        self.conn = conn
        typ, data = conn.response("UIDVALIDITY")
        self.uidvalidity = int(data[0]) if data and data[0] else None

    def _uids(self, *criteria: str) -> list[int]:
        typ, data = self.conn.uid("SEARCH", None, *criteria)
        if typ != "OK":
            raise MailError("Serwer odrzucił wyszukiwanie wiadomości.")
        return [int(x) for x in (data[0] or b"").split()]

    def unseen(self) -> set[int]:
        return set(self._uids("UNSEEN"))

    def all_uids(self) -> list[int]:
        return self._uids("ALL")

    def fetch(self, uids: list[int], nbytes: int) -> list[Raw]:
        out: list[Raw] = []
        for i in range(0, len(uids), BATCH):
            chunk = ",".join(str(u) for u in uids[i : i + BATCH])
            typ, data = self.conn.uid("FETCH", chunk, f"(FLAGS INTERNALDATE BODY.PEEK[]<0.{nbytes}>)")
            if typ != "OK":
                raise MailError("Serwer odrzucił pobieranie wiadomości.")
            out.extend(_parse_fetch(data))
        return out

    def close(self) -> None:
        for fn in (self.conn.close, self.conn.logout):
            try:
                fn()
            except Exception:  # noqa: BLE001 — closing is best effort
                pass


def _parse_fetch(data: list) -> list[Raw]:
    """imaplib returns [(b'1 (UID 5 FLAGS (..) ... BODY[]<0> {n}', literal), b')' | b' UID 5 ...)', ...]."""
    records: list[list] = []  # [meta, literal, trailer]
    for item in data:
        if isinstance(item, tuple):
            records.append([item[0].decode("latin-1"), item[1], ""])
        elif isinstance(item, bytes) and records:
            records[-1][2] += item.decode("latin-1")
    out = []
    for meta, literal, trailer in records:
        info = meta + " " + trailer
        uid = re.search(r"\bUID (\d+)", info)
        if not uid:
            continue
        flags = re.search(r"FLAGS \(([^)]*)\)", info)
        out.append(Raw(int(uid.group(1)), "\\Seen" in (flags.group(1) if flags else ""), _internaldate(info), bytes(literal)))
    return out


def connect(host: str, port: int, security: str, username: str, password: str, folder: str, *, factory=None, timeout: float = 20.0) -> Box:
    """Open `folder` read-only. `factory` (tests only) supplies an already-connected imaplib object."""
    if factory is None:
        if port not in ALLOWED_PORTS:
            raise MailError("Dozwolone porty IMAP: 993 (SSL) i 143 (STARTTLS).")
        if not settings.allow_private_hosts:
            try:
                http.check_host(host, port)
            except http.FetchError as exc:
                raise MailError(f"Nie można połączyć się z serwerem {host}: {exc}") from exc
    try:
        if factory is not None:
            conn = factory()
        elif security == "starttls":
            conn = imaplib.IMAP4(host, port, timeout=timeout)
            conn.starttls(ssl_context=ssl.create_default_context())
        else:
            conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=timeout)
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
        raise MailError(f"Nie udało się połączyć z {host}:{port} ({type(exc).__name__}).") from exc
    try:
        try:
            conn.login(username, password)
        except imaplib.IMAP4.error as exc:
            raise MailError("Logowanie nie powiodło się. Sprawdź login i hasło aplikacji.") from exc
        except UnicodeError as exc:
            raise MailError("Hasło zawiera znaki spoza ASCII, których IMAP nie obsługuje.") from exc
        typ, _ = conn.select('"' + folder.replace('"', "") + '"', readonly=True)  # sends EXAMINE
        if typ != "OK":
            raise MailError(f"Nie znaleziono folderu „{folder}”.")
        return Box(conn)
    except Exception:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
        raise
