"""Shopping module. The EMMA HTML below is a SYNTHETIC PrestaShop-style listing (the real site is not reachable
from the sandbox), so these tests prove our logic, not that the real site parses; see scripts/emma_probe.py."""

import httpx
import pytest

from app import http
from app.db import SessionLocal
from modules.shopping.backend import emma, service
from modules.shopping.backend.models import SearchCache

TILE = """
<article class="product-miniature js-product-miniature" data-id-product="{id}">
  <img src="/img/{id}.jpg"/>
  <h2 class="product-title"><a href="/mleko-swieze-i-uht/{id}-{slug}.html">{name}</a></h2>
  <span class="price">{price}</span>
</article>"""


def listing(*tiles):
    return "<html><body>" + "".join(TILE.format(**t) for t in tiles) + "</body></html>"


MILK = listing(
    dict(id=4931, slug="laciate-uht", name="Łaciate Mleko UHT bez laktozy 3,2% 1 l", price="3,99&nbsp;zł"),
    dict(id=4932, slug="mlekovita", name="Mlekovita Mleko 2% 1 l", price="3,49 zł"),
)
BREAD = listing(dict(id=77, slug="chleb", name="Chleb wiejski 500 g", price="5,00 zł"))
DELIVERY = "<html><body><p>Najbliższy termin dostawy: jutro, 14:00–16:00.</p><script>x()</script></body></html>"


@pytest.fixture()
def shop(monkeypatch):
    """Fake EMMA; counts requests so cache behaviour can be asserted."""
    calls = []
    pages = {"mleko": MILK, "chleb": BREAD}

    def fake_get(url, *, params=None, **kw):
        calls.append(params["s"] if params else url)
        if params:
            return httpx.Response(200, text=pages.get(params["s"], "<html></html>"))
        return httpx.Response(200, text=DELIVERY)

    monkeypatch.setattr(http, "get", fake_get)
    monkeypatch.setattr(emma, "MIN_GAP", 0)
    return calls


def add(client, query, qty=1):
    return client.post("/api/m/shopping", json={"query": query, "quantity": qty})


def test_parsers():
    assert emma.parse_price("3,99 zł") == 399
    assert emma.parse_price("1 299,00 zł") == 129900
    assert emma.parse_price("5 zł") == 500
    assert emma.parse_price("brak") is None
    products = emma.parse_search(MILK, "https://emmamarket.pl")
    assert [p["id"] for p in products] == ["4931", "4932"]
    assert products[0]["price_cents"] == 399 and products[0]["url"].startswith("https://emmamarket.pl/mleko")
    assert products[0]["image_url"] == "https://emmamarket.pl/img/4931.jpg"
    assert emma.parse_search("<html>captcha</html>", "https://emmamarket.pl") == []
    assert emma.parse_delivery(DELIVERY).startswith("Najbliższy termin dostawy: jutro")
    assert emma.parse_delivery("<p>nic</p>") is None


def test_requires_login(client):
    assert client.get("/api/m/shopping").status_code == 401
    assert client.get("/api/hub/widgets/shopping/list").status_code == 401


def test_add_matches_and_records_author(authed, shop):
    body = add(authed, "  Mleko ", 2).json()
    assert body["added_by"] == "owner" and body["warning"] is None
    assert body["product"]["name"].startswith("Łaciate") and body["subtotal_cents"] == 798
    assert shop == ["mleko"]


def test_cache_and_normalisation(authed, shop):
    add(authed, "mleko")
    add(authed, "MLEKO")
    add(authed, "chleb")
    assert shop == ["mleko", "chleb"]  # second "mleko" served from the cache


def test_stale_cache_is_refetched(authed, shop):
    add(authed, "mleko")
    with SessionLocal() as db:
        from datetime import timedelta
        row = db.get(SearchCache, "mleko")
        row.fetched_at -= service.SEARCH_TTL + timedelta(minutes=1)
        db.commit()
    add(authed, "mleko")
    assert shop == ["mleko", "mleko"]


def test_unknown_phrase_and_shop_down_still_add_item(authed, shop, monkeypatch):
    r = add(authed, "xyzzy")
    assert r.status_code == 201 and r.json()["product"] is None and "xyzzy" in r.json()["warning"]

    def boom(url, **kw):
        raise http.FetchError("HTTP 503")

    monkeypatch.setattr(http, "get", boom)
    r = add(authed, "nowe")
    assert r.status_code == 201 and r.json()["product"] is None and "niedostępny" in r.json()["warning"]


def test_edit_choose_alternative_remembered(authed, shop):
    item = add(authed, "mleko").json()
    alts = authed.get(f"/api/m/shopping/{item['id']}/alternatives").json()
    assert [a["id"] for a in alts] == ["4931", "4932"]
    r = authed.put(f"/api/m/shopping/{item['id']}", json={"product_id": "4932", "quantity": 3})
    assert r.json()["product"]["id"] == "4932" and r.json()["subtotal_cents"] == 1047
    # the choice is remembered for the next "mleko"
    assert add(authed, "mleko").json()["product"]["id"] == "4932"
    assert authed.put(f"/api/m/shopping/{item['id']}", json={"product_id": "999"}).status_code == 422
    # changing the phrase re-matches
    r = authed.put(f"/api/m/shopping/{item['id']}", json={"query": "chleb"})
    assert r.json()["product"]["id"] == "77"
    assert authed.put(f"/api/m/shopping/{item['id']}", json={"quantity": 0}).status_code == 422
    assert authed.put("/api/m/shopping/9999", json={"quantity": 1}).status_code == 404


def test_delete_and_validation(authed, shop):
    item = add(authed, "mleko").json()
    assert authed.delete(f"/api/m/shopping/{item['id']}").status_code == 204
    assert authed.delete(f"/api/m/shopping/{item['id']}").status_code == 404
    assert add(authed, "").status_code == 422
    assert add(authed, "x", 100).status_code == 422


def test_summary_cart_and_delivery(authed, shop):
    add(authed, "mleko", 2)
    add(authed, "chleb")
    add(authed, "xyzzy")
    done = add(authed, "chleb").json()
    authed.put(f"/api/m/shopping/{done['id']}", json={"done": True})
    assert authed.post("/api/m/shopping/refresh", json={}).status_code == 200
    ov = authed.get("/api/m/shopping").json()
    assert ov["summary"]["count"] == 3 and ov["summary"]["unmatched"] == 1
    assert ov["summary"]["total"] == "12,98 zł"
    assert ov["summary"]["delivery"]["text"].startswith("Najbliższy termin dostawy")
    cart = authed.get("/api/m/shopping/cart").json()
    assert [l["quantity"] for l in cart["lines"]] == [2, 1] and cart["missing"] == ["xyzzy"]
    assert cart["total"] == "12,98 zł"


def test_refresh_updates_prices(authed, shop, monkeypatch):
    add(authed, "mleko")
    cheaper = MILK.replace("3,99", "2,99")
    monkeypatch.setattr(http, "get", lambda url, *, params=None, **kw: httpx.Response(200, text=cheaper if params else DELIVERY))
    authed.post("/api/m/shopping/refresh", json={})
    assert authed.get("/api/m/shopping").json()["items"][0]["product"]["price_cents"] == 299


def test_hub_actions(authed, shop):
    call = lambda name, inp=None, **kw: authed.post(f"/api/hub/actions/shopping.{name}", json={"input": inp or {}, **kw})
    added = call("add_item", {"query": "mleko"}).json()["result"]
    assert added["added_by"] == "owner"  # actor "user:owner" reaches the action
    assert call("update_item", {"id": added["id"], "quantity": 4}).json()["result"]["quantity"] == 4
    assert call("search_products", {"query": "mleko"}).json()["result"][0]["id"] == "4931"
    assert call("list_items").json()["result"]["summary"]["count"] == 1
    assert call("build_cart").json()["result"]["lines"][0]["quantity"] == 4
    assert call("refresh_prices").status_code == 200
    assert call("remove_item", {"id": added["id"]}).status_code == 409  # needs confirmation
    assert call("remove_item", {"id": added["id"]}, confirmed=True).json()["result"] == {"removed": added["id"]}
    assert call("remove_item", {"id": added["id"]}, confirmed=True).status_code == 404


def test_widgets_read_only_from_db(authed, shop):
    empty = authed.get("/api/hub/widgets/shopping/list").json()["blocks"]
    assert empty[0]["empty"]
    add(authed, "mleko", 2)
    n = len(shop)
    summ = authed.get("/api/hub/widgets/shopping/summary").json()["blocks"]
    lst = authed.get("/api/hub/widgets/shopping/list").json()["blocks"]
    assert summ[0]["value"] == "1 poz." and "7,98 zł" in summ[0]["hint"]
    assert lst[0]["items"][0]["badge"] == "×2" and "dodał: owner" in lst[0]["items"][0]["subtitle"]
    assert len(shop) == n  # widgets never touch the network


def test_background_task_retries_unmatched(authed, shop):
    from app import scheduler

    assert "shopping.refresh" in scheduler.run_tasks(force=True)
    assert shop == []  # empty list: the shop is not touched
    add(authed, "xyzzy")
    shop.clear()
    assert "shopping.refresh" in scheduler.run_tasks(force=True)
    assert shop == ["https://emmamarket.pl"]  # "xyzzy" (empty result) is cached; only the delivery slot is read
    assert authed.get("/api/m/shopping").json()["summary"]["delivery"] is not None
