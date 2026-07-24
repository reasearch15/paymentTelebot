import re
from html import unescape
from datetime import UTC, datetime
from html.parser import HTMLParser

TAG_PATTERN = re.compile(r"(?<![\w])\$[A-Za-z][A-Za-z0-9_-]{1,63}\b")
MONEY_PATTERN = re.compile(r"(?<!\w)\$?\s*([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)(?:\.([0-9]{2}))?(?![\w-])")
DATETIME_PATTERN = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})"
    r"(?:\s+(?:at\s+)?\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)?\b"
)


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
        if tag in {"br", "p", "div", "tr", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
        if tag in {"p", "div", "tr", "li"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0:
            self.parts.append(data)


def normalize_whitespace(value: str | None) -> str:
    if not value:
        return ""
    normalized = unescape(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", normalized).strip()


def html_to_visible_text(html: str | None) -> str:
    if not html:
        return ""
    parser = VisibleTextParser()
    parser.feed(html)
    return normalize_whitespace(" ".join(parser.parts))


def extract_payment_tags(text: str | None) -> list[str]:
    normalized = normalize_whitespace(text)
    return list(dict.fromkeys(match.group(0) for match in TAG_PATTERN.finditer(normalized)))


def extract_amounts(text: str | None) -> list[int]:
    normalized = normalize_whitespace(text)
    amounts: list[int] = []
    for match in MONEY_PATTERN.finditer(normalized):
        original = match.group(0).strip()
        if not original.startswith("$"):
            continue
        dollars = match.group(1).replace(",", "")
        cents = match.group(2) or "00"
        amounts.append(int(dollars) * 100 + int(cents))
    return amounts


def extract_datetime_candidates(text: str | None) -> list[str]:
    normalized = normalize_whitespace(text)
    return list(dict.fromkeys(match.group(0) for match in DATETIME_PATTERN.finditer(normalized)))


def ensure_timezone_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def nearby_label_value(text: str | None, labels: list[str], max_chars: int = 80) -> list[str]:
    normalized = normalize_whitespace(text)
    values: list[str] = []
    for label in labels:
        pattern = re.compile(rf"\b{re.escape(label)}\b\s*:?\s*(.{{1,{max_chars}}})", re.IGNORECASE)
        for match in pattern.finditer(normalized):
            value = normalize_whitespace(match.group(1))
            if value:
                values.append(value)
    return values


def small_fragment(text: str, needle: str, radius: int = 40) -> str:
    index = text.find(needle)
    if index < 0:
        return needle[: radius * 2]
    start = max(0, index - radius)
    end = min(len(text), index + len(needle) + radius)
    return text[start:end].strip()
