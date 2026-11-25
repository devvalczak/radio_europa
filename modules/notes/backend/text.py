"""Plain-text helpers for Markdown notes: a title from the first line and short snippets for lists/widgets."""

import re

TITLE_LEN = 60

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")  # [text](url), ![alt](src) -> text
_PREFIX = re.compile(r"^\s{0,3}(#{1,6}\s+|>\s*|[-*+]\s+\[[ xX]\]\s+|[-*+]\s+|\d+[.)]\s+)+")
_MARKS = re.compile(r"(\*\*|__|~~|`+|\*|_(?=\S)|(?<=\S)_)")
_HTML = re.compile(r"<[^>]{1,200}>")
_RULE = re.compile(r"^\s{0,3}([-*_]\s*){3,}$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}")


def plain_line(line: str) -> str:
    line = _PREFIX.sub("", line)
    line = _LINK.sub(r"\1", line)
    line = _HTML.sub("", line)
    line = _MARKS.sub("", line)
    line = line.replace("|", " ").replace("\\", "")
    return re.sub(r"\s+", " ", line).strip()


def plain_lines(md: str) -> list[str]:
    out, fence = [], False
    for raw in md.splitlines():
        if raw.strip().startswith("```"):
            fence = not fence
            continue
        if _RULE.match(raw) or _TABLE_SEP.match(raw):
            continue
        text = raw.strip() if fence else plain_line(raw)
        if text:
            out.append(text)
    return out


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] if " " in text[: limit] else text[:limit]
    return cut.rstrip(" ,.;:-–—") + "…"


def derive_title(md: str) -> str:
    """First non-empty line of the note as plain text, at most TITLE_LEN characters (word boundary)."""
    lines = plain_lines(md)
    return shorten(lines[0], TITLE_LEN) if lines else ""


def snippet(md: str, limit: int = 90) -> str:
    """Text after the title line, for list subtitles."""
    return shorten(" ".join(plain_lines(md)[1:]), limit)
