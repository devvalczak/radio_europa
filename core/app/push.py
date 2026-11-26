import json
import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_user
from .config import settings
from .db import get_db
from .models import PushSubscription

log = logging.getLogger("jarvis.push")
router = APIRouter(prefix="/push", tags=["push"], dependencies=[Depends(require_user)])


@dataclass
class Reminder:
    title: str
    body: str = ""
    url: str = "/"
    # buttons under the notification (Android/desktop; iOS ignores them). Each: {"id", "title", "url", "body"}: the service
    # worker POSTs `body` to `url` (a /api/m/… route of the same origin, never a hub action) when the button is tapped.
    actions: list[dict] = field(default_factory=list)


class SubscriptionIn(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


@router.get("/key")
def public_key():
    return {"key": settings.vapid_public_key}


@router.post("/subscribe")
def subscribe(body: SubscriptionIn, db: Session = Depends(get_db)):
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if sub is None:
        db.add(PushSubscription(endpoint=body.endpoint, p256dh=body.p256dh, auth=body.auth))
    else:
        sub.p256dh, sub.auth = body.p256dh, body.auth
    db.commit()
    return {"ok": True}


@router.post("/test")
def test(db: Session = Depends(get_db)):
    if not settings.vapid_private_key:
        raise HTTPException(503, "push_not_configured")
    sent = send_push(db, Reminder("Jarvis", "Powiadomienia działają."))
    return {"sent": sent}


def send_push(db: Session, reminder: Reminder) -> int:
    """Send to every subscription; drop the ones the push service says are gone."""
    if not settings.vapid_private_key:
        log.warning("VAPID keys not configured; skipping push: %s", reminder.title)
        return 0
    return sum(1 for r in send_push_detailed(db, reminder) if r["ok"])


def send_push_detailed(db: Session, reminder: Reminder, endpoint: str | None = None) -> list[dict]:
    """Like send_push, with a result per device (for Diagnostyka). `endpoint` limits it to one device."""
    if not settings.vapid_private_key:
        return []
    payload = json.dumps({"title": reminder.title, "body": reminder.body, "url": reminder.url, "actions": reminder.actions})
    stmt = select(PushSubscription)
    if endpoint:
        stmt = stmt.where(PushSubscription.endpoint == endpoint)
    results = []
    for sub in db.scalars(stmt).all():
        host = urlsplit(sub.endpoint).hostname or "?"
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
                timeout=10,
            )
            results.append({"host": host, "ok": True, "detail": "Przyjęte przez usługę push"})
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                db.delete(sub)
                results.append({"host": host, "ok": False, "detail": f"Subskrypcja wygasła ({status}) — usunięta",
                                "hint": "Włącz powiadomienia ponownie na tym urządzeniu."})
            else:
                log.warning("push failed (%s): %s", status, exc)
                hint = "Klucze VAPID nie pasują do subskrypcji — włącz powiadomienia ponownie." if status in (401, 403) else ""
                results.append({"host": host, "ok": False, "detail": f"Odrzucone ({status or type(exc).__name__})", "hint": hint})
        except Exception as exc:  # noqa: BLE001 — network errors must not stop the other devices
            log.warning("push failed: %s", type(exc).__name__)
            results.append({"host": host, "ok": False, "detail": f"Błąd połączenia: {type(exc).__name__}"})
    db.commit()
    return results


class EndpointIn(BaseModel):
    endpoint: str = Field(max_length=2000)


@router.post("/status")
def device_status(body: EndpointIn, db: Session = Depends(get_db)):
    """Is this browser's subscription known to the server? (Diagnostyka)"""
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    return {"registered": sub is not None}


@router.post("/test-device")
def test_device(body: EndpointIn, db: Session = Depends(get_db)):
    if not settings.vapid_private_key:
        raise HTTPException(503, "push_not_configured")
    results = send_push_detailed(db, Reminder("Jarvis — test", "To urządzenie dostaje powiadomienia."), endpoint=body.endpoint)
    if not results:
        raise HTTPException(404, "device_not_registered")
    return results[0]
