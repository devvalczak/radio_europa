"""In-memory CalDAV server for tests (httpx.MockTransport handler): discovery, REPORT, PUT/DELETE with ETags and
412 on precondition failures, Basic auth, a well-known redirect, and a plain iCal feed. Records every request."""

import base64
import itertools
from xml.sax.saxutils import escape

import httpx

BASE = "https://dav.example.com"
PRINCIPAL = "/principals/anna/"
HOME = "/calendars/anna/"
CAL = "/calendars/anna/dom/"
TODO = "/calendars/anna/zadania/"
FEED = "/feeds/secret-token/basic.ics"


def ms(responses: str) -> httpx.Response:
    body = f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">{responses}</d:multistatus>'
    return httpx.Response(207, content=body.encode(), headers={"Content-Type": "application/xml"})


def resp(href: str, props: str) -> str:
    return f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"


class FakeCalDav:
    def __init__(self, user: str = "anna", password: str = "app-password"):
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.resources: dict[str, tuple[str, str]] = {}  # path -> (etag, ics)
        self.feed = ""
        self.requests: list[tuple[str, str, dict]] = []
        self._etags = itertools.count(1)

    def etag(self) -> str:
        return f'"e{next(self._etags)}"'

    def add(self, name: str, ics: str) -> str:
        path = CAL + name
        self.resources[path] = (self.etag(), ics)
        return path

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.requests.append((method, path, dict(request.headers)))
        if path == FEED and method == "GET":
            return httpx.Response(200, text=self.feed, headers={"Content-Type": "text/calendar"})
        if request.headers.get("authorization") != self.auth:
            return httpx.Response(401, headers={"WWW-Authenticate": 'Basic realm="dav"'})
        if method == "PROPFIND":
            return self.propfind(path, request.headers.get("depth", "0"))
        if method == "REPORT" and path == CAL:
            return ms("".join(resp(p, f"<d:getetag>{e}</d:getetag><c:calendar-data>{escape(ics)}</c:calendar-data>")
                              for p, (e, ics) in self.resources.items()))
        if method == "PUT" and path.startswith(CAL):
            cur = self.resources.get(path)
            if request.headers.get("if-none-match") == "*" and cur is not None:
                return httpx.Response(412)
            if (im := request.headers.get("if-match")) and (cur is None or (im != "*" and im != cur[0])):
                return httpx.Response(412)
            etag = self.etag()
            self.resources[path] = (etag, request.content.decode())
            return httpx.Response(204 if cur else 201, headers={"ETag": etag})
        if method == "DELETE" and path.startswith(CAL):
            cur = self.resources.get(path)
            if cur is None:
                return httpx.Response(404)
            if (im := request.headers.get("if-match")) and im != cur[0]:
                return httpx.Response(412)
            del self.resources[path]
            return httpx.Response(204)
        return httpx.Response(405)

    def propfind(self, path: str, depth: str) -> httpx.Response:
        cal_props = ('<d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>Dom</d:displayname>'
                     '<c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>')
        todo_props = ('<d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>Zadania</d:displayname>'
                      '<c:supported-calendar-component-set><c:comp name="VTODO"/></c:supported-calendar-component-set>')
        if path == "/.well-known/caldav":
            return httpx.Response(301, headers={"Location": "/"})
        if path == "/":
            return ms(resp("/", f"<d:resourcetype><d:collection/></d:resourcetype><d:current-user-principal><d:href>{PRINCIPAL}</d:href></d:current-user-principal>"))
        if path == PRINCIPAL:
            return ms(resp(PRINCIPAL, f"<c:calendar-home-set><d:href>{HOME}</d:href></c:calendar-home-set>"))
        if path == HOME:
            out = resp(HOME, "<d:resourcetype><d:collection/></d:resourcetype>")
            if depth == "1":
                out += resp(CAL, cal_props) + resp(TODO, todo_props)
            return ms(out)
        if path == CAL:
            return ms(resp(CAL, cal_props))
        return httpx.Response(404)
