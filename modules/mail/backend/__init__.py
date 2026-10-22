import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import crypto
from app.db import get_db
from app.hub import hub
from app.sdk import ActionError, ButtonsBlock, ButtonSpec, Ctx, ListBlock, ListItem, Module, StatBlock, TextBlock
from app.timeutil import iso_z

from . import imap, service
from .models import MailAccount, MailMessage

MAX_ACCOUNTS = 10
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

router = APIRouter()
module = Module(router=router)


class AccountIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    host: str = Field(max_length=253)
    port: Literal[993, 143] = 993
    security: Literal["ssl", "starttls"] = "ssl"
    username: str = Field(min_length=1, max_length=255)
    password: str | None = Field(default=None, min_length=1, max_length=256)  # None on update = keep the stored one
    folder: str = Field(default="INBOX", min_length=1, max_length=120)
    enabled: bool = True
    notify: bool = True
    notify_senders: str = Field(default="", max_length=500)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip()
        if not HOST_RE.match(v):
            raise ValueError("host must be a plain hostname such as imap.gmail.com")
        return v


def _get(db: Session, account_id: int) -> MailAccount:
    acc = db.get(MailAccount, account_id)
    if acc is None:
        raise HTTPException(404, "account_not_found")
    return acc


def _need_crypto() -> None:
    if not crypto.available():
        raise HTTPException(503, "Brak JARVIS_ENCRYPTION_KEY w .env (uruchom `make encryption-key`) i zrestartuj aplikację.")


def _test(body: AccountIn, password: str) -> None:
    """Fail fast with a readable reason when the settings do not work."""
    box = imap.connect(body.host, body.port, body.security, body.username, password, body.folder)
    box.close()


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db)):
    return [service.account_out(a) for a in db.scalars(select(MailAccount).order_by(MailAccount.id)).all()]


@router.post("/accounts", status_code=201)
def add_account(body: AccountIn, db: Session = Depends(get_db)):
    _need_crypto()
    if not body.password:
        raise HTTPException(422, "Podaj hasło aplikacji.")
    if db.scalar(select(func.count()).select_from(MailAccount)) >= MAX_ACCOUNTS:
        raise HTTPException(409, f"Maksymalnie {MAX_ACCOUNTS} skrzynek.")
    if db.scalar(select(MailAccount).where(MailAccount.host == body.host, MailAccount.username == body.username)):
        raise HTTPException(409, "Ta skrzynka jest już dodana.")
    try:
        _test(body, body.password)
    except imap.MailError as exc:
        raise HTTPException(422, str(exc)) from exc
    acc = MailAccount(**body.model_dump(exclude={"password"}), password_enc=crypto.encrypt(body.password))
    db.add(acc)
    db.commit()
    service.sync_account(db, acc, quiet=True)
    return service.account_out(db.get(MailAccount, acc.id))


@router.put("/accounts/{account_id}")
def update_account(account_id: int, body: AccountIn, db: Session = Depends(get_db)):
    acc = _get(db, account_id)
    connection_changed = (body.host, body.port, body.security, body.username, body.folder) != (acc.host, acc.port, acc.security, acc.username, acc.folder)
    if body.password or connection_changed:
        _need_crypto()
        password = body.password or _stored_password(acc)
        try:
            _test(body, password)
        except imap.MailError as exc:
            raise HTTPException(422, str(exc)) from exc
        if body.password:
            acc.password_enc = crypto.encrypt(body.password)
    if connection_changed:  # different mailbox: cached messages and UIDs no longer apply
        acc.last_synced, acc.uidvalidity, acc.last_uid = None, None, 0
    for field in ("label", "host", "port", "security", "username", "folder", "enabled", "notify", "notify_senders"):
        setattr(acc, field, getattr(body, field))
    db.commit()
    if connection_changed:
        service.sync_account(db, acc, quiet=True)
    return service.account_out(db.get(MailAccount, acc.id))


def _stored_password(acc: MailAccount) -> str:
    try:
        return crypto.decrypt(acc.password_enc)
    except crypto.CryptoError as exc:
        raise HTTPException(409, "Nie można odszyfrować zapisanego hasła. Podaj hasło ponownie.") from exc


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(account_id: int, db: Session = Depends(get_db)):
    acc = _get(db, account_id)
    db.query(MailMessage).filter(MailMessage.account_id == account_id).delete()
    db.delete(acc)
    db.commit()


@router.post("/sync")
def sync(db: Session = Depends(get_db)):
    service.sync_now(db)
    return list_accounts(db)


@router.get("/messages")
def list_messages(account_id: int | None = None, unread: bool = False, limit: int = 50, db: Session = Depends(get_db)):
    stmt = select(MailMessage, MailAccount.label).join(MailAccount).order_by(MailMessage.received.desc()).limit(min(max(limit, 1), 200))
    if account_id is not None:
        stmt = stmt.where(MailMessage.account_id == account_id)
    if unread:
        stmt = stmt.where(MailMessage.seen.is_(False))
    return [service.message_out(m, label) for m, label in db.execute(stmt).all()]


@router.get("/accounts/{account_id}/messages/{uid}")
def read_message(account_id: int, uid: int, db: Session = Depends(get_db)):
    acc = _get(db, account_id)
    try:
        return service.fetch_body(acc, uid)
    except imap.MailError as exc:
        raise HTTPException(502, str(exc)) from exc
    except ActionError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/summary")
def make_summary(db: Session = Depends(get_db)):
    try:
        return {"text": service.summarize(Ctx(db, "mail", hub))}
    except ActionError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


# --- actions (note: there is deliberately no action to add or change accounts: it would carry a password
# into the audit log and into AI tool calls; accounts are managed through the UI routes only) -------------
class ListUnreadIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    account_id: int | None = None


class GetMessageIn(BaseModel):
    account_id: int
    uid: int = Field(ge=1)


@module.action("list_accounts")
def action_list_accounts(ctx: Ctx):
    return list_accounts(ctx.db)


@module.action("list_unread", input=ListUnreadIn)
def action_list_unread(ctx: Ctx, data: ListUnreadIn):
    return list_messages(data.account_id, True, data.limit, ctx.db)


@module.action("get_message", input=GetMessageIn)
def action_get_message(ctx: Ctx, data: GetMessageIn):
    acc = ctx.db.get(MailAccount, data.account_id)
    if acc is None:
        raise ActionError("Nie ma takiej skrzynki.", 404, "account_not_found")
    try:
        return service.fetch_body(acc, data.uid)
    except imap.MailError as exc:
        raise ActionError(str(exc), 502, "mail_failed") from exc


@module.action("summarize_unread")
def action_summarize(ctx: Ctx):
    return {"text": service.summarize(ctx)}


# --- widgets (database only) ---------------------------------------------------------------------------
@module.widget("unread_count")
def widget_unread_count(ctx: Ctx):
    accounts = ctx.db.scalars(select(MailAccount).where(MailAccount.enabled.is_(True)).order_by(MailAccount.id)).all()
    if not accounts:
        return [TextBlock(text="Brak skrzynek."), ButtonsBlock(buttons=[ButtonSpec(label="Dodaj skrzynkę", href="#/mail")])]
    total = sum(a.unread_count for a in accounts)
    broken = [a.label for a in accounts if a.last_error]
    parts = [f"{a.label}: {a.unread_count}" for a in accounts if len(accounts) > 1]
    hint = " · ".join(parts)
    if broken:
        hint = (hint + " · " if hint else "") + "błąd synchronizacji: " + ", ".join(broken)
    return [StatBlock(label="Nieprzeczytane wiadomości", value=str(total), hint=hint or None)]


@module.widget("inbox")
def widget_inbox(ctx: Ctx):
    rows = ctx.db.execute(
        select(MailMessage).where(MailMessage.seen.is_(False)).order_by(MailMessage.received.desc()).limit(8)
    ).scalars().all()
    return [ListBlock(
        items=[ListItem(title=m.subject or "(bez tematu)", subtitle=m.from_name or m.from_addr, time=iso_z(m.received), href="#/mail") for m in rows],
        empty="Brak nieprzeczytanych wiadomości.",
    )]


@module.periodic("sync")
def sync_task(ctx: Ctx):
    service.sync_all(ctx)
