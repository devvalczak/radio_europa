"""Turning raw RFC 822 bytes into safe plain text. Mail is untrusted input (phishing, tracking, hostile markup):
we never keep or render HTML, only text."""

import email
import email.policy
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANKS = re.compile(r"\n{3,}")
MAX_TEXT = 100_000


def clean(value: object, limit: int) -> str:
    """Single-line header text: no control characters or line breaks (header injection / UI spoofing)."""
    return re.sub(r"\s+", " ", _CTRL.sub("", str(value or ""))).strip()[:limit]


class _TextExtractor(HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table", "section", "article"}
    SKIP = {"script", "style", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — malformed markup must never break a sync
        pass
    return normalize_text("".join(parser.parts))


def normalize_text(text: str) -> str:
    text = _CTRL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()[:MAX_TEXT]


@dataclass
class Parsed:
    from_name: str = ""
    from_addr: str = ""
    subject: str = ""
    date: datetime | None = None  # naive UTC
    text: str = ""
    attachments: list[str] = field(default_factory=list)


def _body_text(msg: email.message.EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeError, KeyError, ValueError):  # unknown charset / truncated encoding
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    return html_to_text(content) if part.get_content_type() == "text/html" else normalize_text(content)


def parse_message(raw: bytes) -> Parsed:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = parseaddr(str(msg.get("from", "")))
    date = None
    try:
        parsed = parsedate_to_datetime(str(msg.get("date", "")))
        date = parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError, IndexError):
        pass
    attachments = []
    try:
        attachments = [clean(a.get_filename() or a.get_content_type(), 120) for a in msg.iter_attachments()][:20]
    except Exception:  # noqa: BLE001 — truncated multipart
        pass
    try:
        text = _body_text(msg)
    except Exception:  # noqa: BLE001 — hostile or broken MIME
        text = ""
    return Parsed(clean(name, 200), clean(addr, 320), clean(msg.get("subject", ""), 500), date, text, attachments)


def snippet(text: str, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]
