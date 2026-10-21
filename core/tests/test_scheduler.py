from app import scheduler
from app.hub import hub


def test_tasks_run_when_due_not_before(authed):
    scheduler._last_run.clear()
    first = scheduler.run_tasks()
    assert {"weather.refresh", "news.refresh", "news.digest"} <= set(first)
    assert scheduler.run_tasks() == []  # intervals (>= 15 min) have not elapsed
    assert "weather.refresh" in scheduler.run_tasks(force=True)


def test_failing_task_does_not_stop_others(authed, monkeypatch):
    ran = []

    def bad(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(hub, "tasks", [("x", "bad", 60, bad), ("x", "good", 60, lambda ctx: ran.append(1))])
    scheduler._last_run.clear()
    assert scheduler.run_tasks() == ["x.good"] and ran == [1]
