"""Links between items of different modules, shared tags, and note mentions."""
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from app import migrate


def _task(authed, title="Zadzwonić do księgowej"):
    return authed.post("/api/m/tasks", json={"title": title}).json()["id"]


def _note(authed, content="# Faktura\nszczegóły", tags=None):
    return authed.post("/api/m/notes", json={"content": content, "tags": tags or []}).json()["id"]


def test_link_both_directions_idempotent(authed):
    t, n = _task(authed), _note(authed)
    r = authed.post("/api/hub/links", json={"a": f"tasks:{t}", "b": f"notes:{n}"})
    assert r.status_code == 200
    again = authed.post("/api/hub/links", json={"a": f"notes:{n}", "b": f"tasks:{t}"})  # reversed: same link
    assert again.json()["id"] == r.json()["id"]

    from_task = authed.get("/api/hub/links", params={"ref": f"tasks:{t}"}).json()
    assert [(x["ref"], x["title"], x["href"]) for x in from_task] == [(f"notes:{n}", "Faktura", f"#/notes?id={n}")]
    from_note = authed.get("/api/hub/links", params={"ref": f"notes:{n}"}).json()
    assert from_note[0]["ref"] == f"tasks:{t}" and from_note[0]["title"] == "Zadzwonić do księgowej"

    assert authed.delete(f"/api/hub/links/{r.json()['id']}").status_code == 204
    assert authed.get("/api/hub/links", params={"ref": f"tasks:{t}"}).json() == []


def test_link_validation(authed):
    t = _task(authed)
    assert authed.post("/api/hub/links", json={"a": f"tasks:{t}", "b": f"tasks:{t}"}).status_code == 422  # itself
    assert authed.post("/api/hub/links", json={"a": f"tasks:{t}", "b": "notes:9999"}).status_code == 404  # missing item
    assert authed.post("/api/hub/links", json={"a": "nonsense", "b": f"tasks:{t}"}).status_code == 422
    assert authed.get("/api/hub/links", params={"ref": "x"}).status_code == 422


def test_deleting_an_item_removes_its_links_and_tags(authed):
    t, n = _task(authed), _note(authed, tags=["praca"])
    authed.post("/api/hub/links", json={"a": f"tasks:{t}", "b": f"notes:{n}"})
    authed.put("/api/hub/tags", json={"ref": f"tasks:{t}", "tags": ["pilne"]})
    assert authed.delete(f"/api/m/tasks/{t}").status_code == 204
    assert authed.get("/api/hub/links", params={"ref": f"notes:{n}"}).json() == []
    assert {x["tag"] for x in authed.get("/api/hub/tags").json()} == {"praca"}


def test_orphans_are_skipped_and_purged(authed, client):
    """A module that deletes rows without calling forget (e.g. a sync) leaves links that simply stop showing."""
    from sqlalchemy import text

    from app import db, links
    from app.hub import hub

    t, n = _task(authed), _note(authed)
    authed.post("/api/hub/links", json={"a": f"tasks:{t}", "b": f"notes:{n}"})
    with db.SessionLocal() as s:
        s.execute(text("DELETE FROM tasks WHERE id = :i"), {"i": t})
        s.commit()
    assert authed.get("/api/hub/links", params={"ref": f"notes:{n}"}).json() == []
    with db.SessionLocal() as s:
        assert links.purge_orphans(hub, s) == 1
        assert s.execute(text("SELECT count(*) FROM entity_links")).scalar() == 0


def test_tags_are_shared_and_normalised(authed):
    t, n = _task(authed), _note(authed)
    assert authed.put("/api/hub/tags", json={"ref": f"tasks:{t}", "tags": ["#Praca", " dom ", "praca", ""]}).json() == ["dom", "praca"]
    authed.put("/api/hub/tags", json={"ref": f"notes:{n}", "tags": ["praca"]})
    assert authed.get("/api/hub/tags").json() == [{"tag": "praca", "count": 2}, {"tag": "dom", "count": 1}]
    assert authed.get("/api/hub/tags", params={"ref": f"tasks:{t}"}).json() == ["dom", "praca"]
    items = authed.get("/api/hub/tags/praca/items").json()
    assert {i["ref"] for i in items} == {f"tasks:{t}", f"notes:{n}"}
    assert authed.put("/api/hub/tags", json={"ref": f"tasks:{t}", "tags": [f"t{i}" for i in range(13)]}).status_code == 422
    assert authed.put("/api/hub/tags", json={"ref": "tasks:9999", "tags": ["x"]}).status_code == 404


def test_search_finds_items_by_shared_tags_and_gives_refs(authed):
    t = _task(authed, "Zadzwonić do księgowej")
    authed.put("/api/hub/tags", json={"ref": f"tasks:{t}", "tags": ["faktury"]})
    groups = {g["module"]: g for g in authed.get("/api/hub/search", params={"q": "księgowej faktury"}).json()}
    assert groups["tasks"]["items"][0]["ref"] == f"tasks:{t}"
    assert [g["module"] for g in authed.get("/api/hub/search", params={"q": "#faktury"}).json()] == ["tasks"]


def test_notes_keep_the_tags_api_and_filter_by_tag(authed):
    n = _note(authed, tags=["Kuchnia", "kuchnia", "przepisy"])
    assert authed.get(f"/api/m/notes/{n}").json()["tags"] == ["kuchnia", "przepisy"]
    _note(authed, "Inna")
    assert [x["id"] for x in authed.get("/api/m/notes", params={"tag": "kuchnia"}).json()] == [n]
    authed.put(f"/api/m/notes/{n}", json={"content": "# X", "tags": ["nowy"]})
    assert authed.get(f"/api/m/notes/{n}").json()["tags"] == ["nowy"]


def test_note_mentions_become_links_and_follow_the_text(authed):
    t = _task(authed)
    n = _note(authed, f"# Plan\nZrobić [księgowa](#/tasks?id={t}) do piątku")
    rel = authed.get("/api/hub/links", params={"ref": f"notes:{n}"}).json()
    assert [(x["ref"], x["kind"], x["direction"]) for x in rel] == [(f"tasks:{t}", "mention", "out")]
    back = authed.get("/api/hub/links", params={"ref": f"tasks:{t}"}).json()  # backlink on the task
    assert back[0]["ref"] == f"notes:{n}" and back[0]["direction"] == "in"
    authed.put(f"/api/m/notes/{n}", json={"content": "# Plan\nbez linku", "tags": []})  # link removed from the text
    assert authed.get("/api/hub/links", params={"ref": f"tasks:{t}"}).json() == []


def test_manual_link_survives_note_edits(authed):
    t, n = _task(authed), _note(authed)
    authed.post("/api/hub/links", json={"a": f"notes:{n}", "b": f"tasks:{t}"})
    authed.put(f"/api/m/notes/{n}", json={"content": "# Nowa treść", "tags": []})
    assert len(authed.get("/api/hub/links", params={"ref": f"notes:{n}"}).json()) == 1


def test_links_need_login(client):
    assert client.get("/api/hub/links", params={"ref": "tasks:1"}).status_code == 401
    assert client.get("/api/hub/tags").status_code == 401


def test_tags_of_existing_notes_are_copied_by_the_migration(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    cfg = Config(str(migrate._INI))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0013")
        conn.exec_driver_sql("INSERT INTO notes (title, content, tags, pinned, created_at, updated_at) "
                             "VALUES ('a', '', 'Dom, #praca,dom', 0, '2026-01-01', '2026-01-01')")
        command.upgrade(cfg, "0014")
        rows = conn.exec_driver_sql("SELECT module, tag FROM entity_tags ORDER BY tag").fetchall()
    assert [tuple(r) for r in rows] == [("notes", "dom"), ("notes", "praca")]


def _mail_message(subject="Faktura za prąd"):
    from datetime import datetime

    from app import db
    from modules.mail.backend.models import MailAccount, MailMessage

    with db.SessionLocal() as s:
        acc = MailAccount(label="Dom", host="imap.example.com", username="u", password_enc="x")
        s.add(acc)
        s.flush()
        m = MailMessage(account_id=acc.id, uid=1, subject=subject, from_addr="a@b.pl", received=datetime(2026, 1, 1))
        s.add(m)
        s.commit()
        return m.id


def test_assistant_actions_never_reveal_mail_without_consent(authed):
    """The links module serves the assistant too: a link to a mail must stay invisible while the user has not allowed AI."""
    from app import db
    from app.hub import hub

    t, mail = _task(authed), _mail_message()
    # the user links them in the UI (an HTTP route: the user's own view shows the mail title)
    assert authed.post("/api/hub/links", json={"a": f"tasks:{t}", "b": f"mail:{mail}"}).status_code == 200
    assert authed.get("/api/hub/links", params={"ref": f"tasks:{t}"}).json()[0]["title"] == "Faktura za prąd"

    with db.SessionLocal() as s:
        seen = hub.call("links.list_links", {"ref": f"tasks:{t}"}, actor="module:assistant", db=s)
        assert seen == []  # consent off: the mail is not shown to the assistant
        try:
            hub.call("links.list_links", {"ref": f"mail:{mail}"}, actor="module:assistant", db=s)
            raise AssertionError("assistant must not read links of a hidden module")
        except Exception as exc:
            assert getattr(exc, "status", None) == 403 or "module_hidden" in str(exc)

    authed.put("/api/m/mail/settings", json={"allow_ai": True})
    with db.SessionLocal() as s:
        seen = hub.call("links.list_links", {"ref": f"tasks:{t}"}, actor="module:assistant", db=s)
        assert [x["title"] for x in seen] == ["Faktura za prąd"]


def test_assistant_can_link_and_tag(authed):
    from app import db
    from app.hub import hub

    t, n = _task(authed), _note(authed)
    with db.SessionLocal() as s:
        hub.call("links.link", {"a": f"tasks:{t}", "b": f"notes:{n}"}, actor="module:assistant", db=s, confirmed=True)
        s.commit()
        assert hub.call("links.set_tags", {"ref": f"tasks:{t}", "tags": ["Pilne"]}, actor="module:assistant", db=s) == ["pilne"]
        s.commit()
        found = hub.call("links.find_by_tag", {"tag": "pilne"}, actor="module:assistant", db=s)
        assert [x["ref"] for x in found] == [f"tasks:{t}"]
    assert len(authed.get("/api/hub/links", params={"ref": f"tasks:{t}"}).json()) == 1


def test_link_actions_are_marked_for_the_assistant(client):
    from app.hub import hub

    acts = {k: v for k, v in hub.actions.items() if k.startswith("links.")}
    assert {k for k, v in acts.items() if v.readonly} == {"links.list_tags", "links.find_by_tag", "links.list_links"}
    assert not acts["links.link"].readonly and not acts["links.set_tags"].readonly  # asking the user first
