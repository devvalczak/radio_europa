from email.message import EmailMessage

from modules.mail.backend.parse import clean, html_to_text, parse_message, snippet


def raw(msg: EmailMessage) -> bytes:
    return msg.as_bytes()


def test_headers_are_decoded_and_cleaned():
    p = parse_message(
        b"From: =?UTF-8?B?SmFuIEvDs8WCa28=?= <jan@example.com>\r\nSubject: =?UTF-8?Q?Zam=C3=B3wienie_=E2=9C=93?=\r\n"
        b"Date: Wed, 21 Oct 2026 10:00:00 +0200\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nCze\xc5\x9b\xc4\x87\r\n"
    )
    assert (p.from_name, p.from_addr, p.subject, p.text) == ("Jan Kółko", "jan@example.com", "Zamówienie ✓", "Cześć")
    assert str(p.date) == "2026-10-21 08:00:00"  # converted to naive UTC


def test_control_characters_and_line_breaks_never_survive_in_headers():
    assert clean("Pilne\r\nBcc: evil@example.com\x00\x07 ok", 100) == "Pilne Bcc: evil@example.com ok"
    assert clean("a\r\nb\x1bc", 10) == "a bc"
    assert len(clean("x" * 1000, 50)) == 50


def test_plain_text_is_preferred_over_html():
    m = EmailMessage()
    m["Subject"] = "x"
    m.set_content("wersja tekstowa")
    m.add_alternative("<p>wersja <b>html</b></p>", subtype="html")
    assert parse_message(raw(m)).text == "wersja tekstowa"


def test_html_only_mail_becomes_text_without_active_content():
    html = (
        "<html><head><title>t</title><style>p{color:red}</style></head><body>"
        "<script>steal(document.cookie)</script><p>Witaj&nbsp;<b>Anno</b></p>"
        '<img src="https://tracker.example/pixel.gif"><a href="javascript:alert(1)">kliknij</a>'
        "<div>Druga linia</div></body></html>"
    )
    m = EmailMessage()
    m["Subject"] = "x"
    m.set_content(html, subtype="html")
    text = parse_message(raw(m)).text
    assert "Witaj" in text and "Anno" in text and "Druga linia" in text and "kliknij" in text
    for bad in ("steal", "<script", "tracker.example", "javascript:", "color:red", "<"):
        assert bad not in text


def test_attachments_are_listed_not_downloaded():
    m = EmailMessage()
    m["Subject"] = "faktura"
    m.set_content("w załączniku")
    m.add_attachment(b"data", maintype="application", subtype="pdf", filename="faktura.pdf")
    p = parse_message(raw(m))
    assert p.attachments == ["faktura.pdf"] and p.text == "w załączniku"


def test_base64_and_unknown_charset_do_not_crash():
    p = parse_message(b"Subject: s\r\nContent-Type: text/plain; charset=x-no-such\r\nContent-Transfer-Encoding: base64\r\n\r\nSGVsbG8gd29ybGQ=\r\n")
    assert "Hello world" in p.text


def test_truncated_multipart_is_tolerated():
    m = EmailMessage()
    m["Subject"] = "duży"
    m.set_content("początek treści " * 50)
    m.add_attachment(b"x" * 20000, maintype="application", subtype="octet-stream", filename="big.bin")
    cut = raw(m)[:900]  # as if the partial fetch ended inside the attachment
    p = parse_message(cut)
    assert p.subject == "duży" and p.text.startswith("początek treści")


def test_garbage_input_never_raises():
    for junk in (b"", b"\x00\xff\xfe", b"not an email at all", b"Subject: =?UTF-8?B?!!!?=\r\n\r\n", b"From: <<<>>>\r\nDate: nope\r\n\r\n"):
        p = parse_message(junk)
        assert isinstance(p.text, str) and isinstance(p.subject, str)


def test_html_to_text_handles_broken_markup():
    assert "tekst" in html_to_text("<div><p>tekst<span>")
    assert html_to_text("<script>x</script>") == ""


def test_snippet_collapses_whitespace_and_truncates():
    assert snippet("a\n\n  b\tc", 100) == "a b c"
    assert len(snippet("x" * 500, 200)) == 200
