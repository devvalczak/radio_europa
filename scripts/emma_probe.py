"""Check the EMMA scraper against the real shop (the dev sandbox cannot reach it).

    PYTHONPATH=core:. python scripts/emma_probe.py mleko [outdir]

Saves the raw search page and the delivery page to outdir (default: modules/shopping/tests/fixtures) and prints
what the parser extracted. If 0 products or no delivery text come out, send the saved HTML files back
(or fix `parse_search` / `parse_delivery` in modules/shopping/backend/emma.py) and turn them into test fixtures.
"""

import sys
from pathlib import Path

from app import http
from app.config import settings
from modules.shopping.backend import emma

phrase = sys.argv[1] if len(sys.argv) > 1 else "mleko"
out = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / "modules/shopping/tests/fixtures")
out.mkdir(parents=True, exist_ok=True)
base = settings.emma_base_url.rstrip("/")

html = http.get(f"{base}/szukaj", params={"s": phrase}, max_bytes=3_000_000).text
(out / f"search_{phrase}.html").write_text(html, encoding="utf-8")
products = emma.parse_search(html, base)
print(f"search '{phrase}': {len(html)} bytes, {len(products)} products")
for p in products:
    print(f"  {p['id']:>8}  {(p['price_cents'] or 0) / 100:7.2f} zł  {p['name']}  {p['url']}")

page = http.get(settings.emma_delivery_url or base, max_bytes=3_000_000).text
(out / "delivery.html").write_text(page, encoding="utf-8")
print("delivery:", emma.parse_delivery(page) or "NOT FOUND (check delivery.html)")
print("saved to", out)
