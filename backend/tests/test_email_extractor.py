from app.services.email_extractor import extract_email


def test_extracts_multipart_text_and_html() -> None:
    raw = (
        b"Message-ID: <abc@example.com>\r\n"
        b"From: Sender <sender@example.com>\r\n"
        b"Subject: =?utf-8?q?Payment_=E2=9C=93?=\r\n"
        b"Date: Wed, 22 Jul 2026 10:30:00 +0000\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=frontier\r\n"
        b"\r\n"
        b"--frontier\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"SGVsbG8gcGxhaW4=\r\n"
        b"--frontier\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n"
        b"\r\n"
        b"<p>Hello=20HTML</p>\r\n"
        b"--frontier--\r\n"
    )

    extracted = extract_email(raw)

    assert extracted.gmail_message_id == "<abc@example.com>"
    assert extracted.sender_address == "Sender <sender@example.com>"
    assert extracted.subject == "Payment ✓"
    assert extracted.received_at is not None
    assert extracted.raw_text == "Hello plain"
    assert extracted.raw_html == "<p>Hello HTML</p>"
    assert extracted.raw_headers_json["Message-ID"] == "<abc@example.com>"


def test_extracts_html_only_without_attachments() -> None:
    raw = (
        b"From: sender@example.com\r\n"
        b"Subject: HTML only\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<strong>Only HTML</strong>"
    )

    extracted = extract_email(raw)

    assert extracted.raw_text is None
    assert extracted.raw_html == "<strong>Only HTML</strong>"
