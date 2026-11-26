from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.hub import hub
from app.sdk import ButtonSpec, ButtonsBlock, Check, Ctx, Module, TextBlock

from . import service
from .models import AssistantMessage

router = APIRouter()  # mounted at /api/m/assistant; every route already requires login
module = Module(router=router)


def _ctx(db: Session) -> Ctx:
    return Ctx(db, "assistant", hub)


def _state(db: Session) -> dict:
    from app import ai

    ctx = _ctx(db)
    names = sorted({hub.manifests[a.module]["name"] for a in hub.assistant_actions(db) if a.module != "assistant"})
    return {"ai": ai.describe(), "modules": names, "mail": any(a.module == "mail" for a in hub.assistant_actions(db)),
            "messages": service.view(ctx), "busy": False}


class SendIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    tz: str = Field(default="UTC", max_length=64)


class DecisionIn(BaseModel):
    approve: bool
    tz: str = Field(default="UTC", max_length=64)


@router.get("/chat")
def get_chat(db: Session = Depends(get_db)):
    return _state(db)


@router.post("/chat")
def post_chat(body: SendIn, db: Session = Depends(get_db)):
    """Conversation is a UI route, not a hub action: the assistant must not be able to call itself."""
    service.send(_ctx(db), body.text, body.tz)
    return _state(db)


@router.post("/chat/calls/{call_id}")
def decide(call_id: str, body: DecisionIn, db: Session = Depends(get_db)):
    if not service.resolve(_ctx(db), call_id, body.approve, body.tz):
        raise HTTPException(404, "no_pending_call")
    return _state(db)


@router.delete("/chat")
def clear_chat(db: Session = Depends(get_db)):
    service.clear(_ctx(db))
    return _state(db)


# --- widget (the dashboard renders its own React component; this is the fallback / summary) ---------------
@module.widget("chat")
def widget_chat(ctx: Ctx):
    if not ctx.ai_available:
        return [TextBlock(text="Asystent potrzebuje skonfigurowanego AI (JARVIS_AI_* w .env)."),
                ButtonsBlock(buttons=[ButtonSpec(label="Diagnostyka", href="#/debug")])]
    last = ctx.db.scalar(select(AssistantMessage).where(AssistantMessage.role == "assistant").order_by(AssistantMessage.id.desc()).limit(1))
    return [TextBlock(text=(last.text[:200] if last and last.text else "Zapytaj o kalendarz, notatki, zakupy…")),
            ButtonsBlock(buttons=[ButtonSpec(label="Otwórz czat", href="#/assistant", primary=True)])]


# --- Diagnostyka ----------------------------------------------------------------------------------------------
@module.diagnostic("tools", "Narzędzia asystenta")
def diag_tools(ctx: Ctx) -> list[Check]:
    actions = [a for a in ctx.hub.assistant_actions(ctx.db) if a.module != "assistant"]
    if not ctx.ai_available:
        return [Check("skip", "AI nie jest skonfigurowane — czat nie działa", "Patrz grupa Asystent AI.")]
    ro = sum(1 for a in actions if a.readonly)
    out = [Check("ok", f"{len(actions)} akcji ({ro} tylko do odczytu, {len(actions) - ro} po potwierdzeniu)")]
    if not any(a.module == "mail" for a in actions):
        out.append(Check("skip", "Poczta niedostępna dla asystenta (brak zgody)", "Włącz zgodę na AI w module Poczta, jeśli chcesz pytać o maile.", "Poczta"))
    return out
