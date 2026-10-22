"""Scraper for the EMMA online store (emmamarket.pl, PrestaShop). No official API exists.

!! NOT VERIFIED AGAINST THE LIVE SITE: the sandbox cannot reach emmamarket.pl. The selectors below follow the
standard PrestaShop 1.7 theme (`article.product-miniature`, `.product-title a`, `.price`). On a real machine run
`python scripts/emma_probe.py mleko` (saves fixtures + prints what was parsed) and fix `parse_search` if needed.
All the site-specific guesswork lives in this file on purpose.
"""

import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app import http
from app.config import settings

MAX_RESULTS = 12
MIN_GAP = 1.5  # seconds between requests to the shop; we are guests there
_last_request = 0.0

PRICE_RE = re.compile(r"(\d[\d\s .]*)[,.](\d{2})\b|(\d+)\s*(?:zł|PLN)", re.I)


class ShopError(Exception):
    """The shop could not be reached or answered with something we cannot read."""


def parse_price(text: str) -> int | None:
    """'12,99 zł' / '1 299,00 zł' / '5 zł' -> grosze."""
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    if m.group(3):
        return int(m.group(3)) * 100
    whole = re.sub(r"[\s .]", "", m.group(1))
    return int(whole) * 100 + int(m.group(2))


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _img(node, base: str) -> str | None:
    if node is None:
        return None
    src = node.get("data-full-size-image-url") or node.get("data-src") or node.get("src")
    return urljoin(base, src) if src and not src.startswith("data:") else None


def parse_search(html: str, base: str) -> list[dict]:
    """Product tiles of a PrestaShop listing -> [{id, name, url, price_cents, image_url}]."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for tile in soup.select("article.product-miniature, .js-product-miniature"):
        link = tile.select_one(".product-title a, h2 a, h3 a, a.thumbnail")
        if link is None or not link.get("href"):
            continue
        url = urljoin(base, link["href"])
        pid = tile.get("data-id-product")
        if not pid:  # fall back to the "/category/<id>-slug.html" shape of the URL
            m = re.search(r"/(\d+)-[^/]+\.html", url)
            pid = m.group(1) if m else None
        name = _text(link) or link.get("title", "")
        if not pid or not name:
            continue
        price = parse_price(_text(tile.select_one(".price, .product-price-and-shipping")))
        out.append({"id": str(pid), "name": name[:300], "url": url, "price_cents": price,
                    "image_url": _img(tile.select_one("img"), base)})
        if len(out) >= MAX_RESULTS:
            break
    return out


DELIVERY_RE = re.compile(
    r"(najbliższ\w+\s+(?:termin\s+)?dostaw\w*|dostaw\w+\s+(?:już\s+)?(?:od|na|w)\b)[^.\n]{0,80}", re.I)


def parse_delivery(html: str) -> str | None:
    """Best-effort: first short phrase about the nearest delivery on the page, else None."""
    soup = BeautifulSoup(html, "html.parser")
    for bad in soup(["script", "style", "noscript"]):
        bad.decompose()
    m = DELIVERY_RE.search(" ".join(soup.get_text(" ", strip=True).split()))
    return m.group(0).strip()[:300] if m else None


def _get(url: str, params: dict | None = None) -> str:
    global _last_request
    wait = MIN_GAP - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    try:
        return http.get(url, params=params, timeout=15.0, max_bytes=3_000_000).text
    except http.FetchError as exc:
        raise ShopError(f"Sklep EMMA jest niedostępny ({exc}).") from exc


def search(phrase: str) -> list[dict]:
    base = settings.emma_base_url.rstrip("/")
    return parse_search(_get(f"{base}/szukaj", {"s": phrase}), base)


def delivery_text() -> str | None:
    url = settings.emma_delivery_url or settings.emma_base_url
    return parse_delivery(_get(url))
