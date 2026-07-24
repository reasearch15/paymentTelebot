from app.parsers.base import PaymentParser
from app.parsers.chime import ChimeParser
from app.parsers.stubs import CashAppParser, VenmoParser

parser_registry: dict[str, PaymentParser] = {
    "chime": ChimeParser(),
    "cash_app": CashAppParser(),
    "venmo": VenmoParser(),
}


def get_parser(parser_key: str) -> PaymentParser:
    normalized_key = parser_key.strip().lower()
    try:
        return parser_registry[normalized_key]
    except KeyError as exc:
        raise ValueError(f"No parser registered for provider parser_key '{normalized_key}'.") from exc
