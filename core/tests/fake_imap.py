"""A tiny in-process IMAP server, just enough for imaplib: LOGIN, EXAMINE/SELECT, UID SEARCH, UID FETCH.

It records every command so tests can prove the mail module never writes (no STORE/EXPUNGE/COPY/...)
and always uses EXAMINE + BODY.PEEK. Plain TCP: production code always uses TLS, tests inject the connection.
"""

import imaplib
import re
import socketserver
import threading
from email.message import EmailMessage

TOKEN = re.compile(r'"[^"]*"|\([^)]*\)|\S+')


def make_raw(subject="Hello", sender="Anna <anna@example.com>", body="Treść wiadomości", date="Wed, 21 Oct 2026 10:00:00 +0200", html=None, attachment=None) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Date"] = sender, "me@example.com", subject, date
    msg["Message-ID"] = f"<{abs(hash((subject, body)))}@example.com>"
    if html is None:
        msg.set_content(body)
    else:
        msg.set_content(body) if body else None
        if body:
            msg.add_alternative(html, subtype="html")
        else:
            msg.set_content(html, subtype="html")
    if attachment:
        msg.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename=attachment)
    return msg.as_bytes()


def message(uid, raw=None, seen=False, internaldate="21-Oct-2026 10:00:00 +0200", **kw):
    return {"uid": uid, "seen": seen, "internaldate": internaldate, "raw": raw if raw is not None else make_raw(**kw)}


class _Handler(socketserver.StreamRequestHandler):
    def reply(self, line: str | bytes):
        self.wfile.write(line if isinstance(line, bytes) else line.encode())
        self.wfile.flush()

    def handle(self):
        srv: FakeImapServer = self.server.owner  # type: ignore[attr-defined]
        self.reply("* OK fake imap ready\r\n")
        selected = False
        while True:
            line = self.rfile.readline()
            if not line:
                return
            parts = TOKEN.findall(line.decode("utf-8", "replace").strip())
            if len(parts) < 2:
                continue
            tag, cmd, args = parts[0], parts[1].upper(), parts[2:]
            if cmd == "UID" and args:
                cmd, args = "UID " + args[0].upper(), args[1:]
            srv.log.append((cmd, " ".join(args)))
            if cmd == "CAPABILITY":
                self.reply(f"* CAPABILITY IMAP4rev1\r\n{tag} OK done\r\n")
            elif cmd == "LOGIN":
                user, pw = (a.strip('"') for a in args[:2])
                ok = (user, pw) == (srv.user, srv.password)
                self.reply(f"{tag} OK logged in\r\n" if ok else f"{tag} NO [AUTHENTICATIONFAILED] invalid credentials\r\n")
            elif cmd in ("SELECT", "EXAMINE"):
                folder = args[0].strip('"') if args else ""
                if folder not in srv.folders:
                    self.reply(f"{tag} NO no such mailbox\r\n")
                    continue
                selected = True
                mode = "[READ-ONLY]" if cmd == "EXAMINE" else "[READ-WRITE]"  # like a real server
                self.reply(f"* {len(srv.messages)} EXISTS\r\n* OK [UIDVALIDITY {srv.uidvalidity}] valid\r\n{tag} OK {mode} done\r\n")
            elif cmd == "UID SEARCH" and selected:
                crit = args[0].upper() if args else "ALL"
                uids = [m["uid"] for m in srv.messages if crit == "ALL" or (crit == "UNSEEN" and not m["seen"])]
                self.reply(f"* SEARCH {' '.join(map(str, uids))}\r\n{tag} OK done\r\n".replace("SEARCH \r", "SEARCH\r"))
            elif cmd == "UID FETCH" and selected:
                wanted = {int(x) for x in args[0].split(",")}
                items = args[1]
                if "BODY[" in items and "PEEK" not in items:
                    srv.violations.append(f"non-PEEK fetch: {items}")
                limit = re.search(r"<0\.(\d+)>", items)
                for i, m in enumerate(srv.messages, 1):
                    if m["uid"] in wanted:
                        raw = m["raw"][: int(limit.group(1))] if limit else m["raw"]
                        flags = "\\Seen" if m["seen"] else ""
                        head = f'* {i} FETCH (UID {m["uid"]} FLAGS ({flags}) INTERNALDATE "{m["internaldate"]}" BODY[]<0> {{{len(raw)}}}\r\n'
                        self.reply(head.encode() + raw + b")\r\n")
                self.reply(f"{tag} OK done\r\n")
            elif cmd in ("CLOSE", "NOOP"):
                self.reply(f"{tag} OK done\r\n")
            elif cmd == "LOGOUT":
                self.reply(f"* BYE\r\n{tag} OK bye\r\n")
                return
            else:  # anything else (STORE, EXPUNGE, COPY, DELETE, APPEND...) is a write we must never see
                srv.violations.append(f"unexpected command: {cmd} {args}")
                self.reply(f"{tag} NO not allowed in this test server\r\n")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeImapServer:
    def __init__(self, messages=None, user="me@example.com", password="app-pass-1234", uidvalidity=111, folders=("INBOX",)):
        self.messages = messages or []
        self.user, self.password, self.uidvalidity, self.folders = user, password, uidvalidity, set(folders)
        self.log: list[tuple[str, str]] = []
        self.violations: list[str] = []
        self._srv = _Server(("127.0.0.1", 0), _Handler)
        self._srv.owner = self  # type: ignore[attr-defined]
        self.port = self._srv.server_address[1]
        threading.Thread(target=lambda: self._srv.serve_forever(poll_interval=0.01), daemon=True).start()

    def factory(self):
        return imaplib.IMAP4("127.0.0.1", self.port, timeout=5)

    def commands(self) -> list[str]:
        return [c for c, _ in self.log]

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()
