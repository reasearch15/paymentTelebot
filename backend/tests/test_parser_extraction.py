from app.parsers.extraction import extract_amounts, extract_payment_tags, html_to_visible_text


def test_payment_tag_extraction_preserves_valid_tags() -> None:
    text = "Tags: $DerekS $Demaul_Goins $john-smith $USER123"

    assert extract_payment_tags(text) == ["$DerekS", "$Demaul_Goins", "$john-smith", "$USER123"]


def test_payment_tag_extraction_excludes_money() -> None:
    text = "Derek sent $13.00 from $DerekS and another amount $1,250.25"

    assert extract_payment_tags(text) == ["$DerekS"]


def test_amount_extraction_returns_integer_cents() -> None:
    assert extract_amounts("You received $13.00 and $1,250.25") == [1300, 125025]


def test_html_visible_text_extraction_skips_scripts() -> None:
    html = "<html><body><p>Hello <strong>Derek</strong></p><script>alert('x')</script></body></html>"

    assert html_to_visible_text(html) == "Hello Derek"
