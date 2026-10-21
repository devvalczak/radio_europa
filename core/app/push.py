import json
import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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
    payload = json.dumps({"title": reminder.title, "body": reminder.body, "url": reminder.url})
    sent = 0
    for sub in db.scalars(select(PushSubscription)).all():
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                db.delete(sub)
            else:
                log.warning("push failed (%s): %s", status, exc)
    db.commit()
    return sent
