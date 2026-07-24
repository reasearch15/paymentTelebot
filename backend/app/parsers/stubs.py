from app.parsers.base import ParserInput, ParserResult, PaymentParser


class NotImplementedProviderParser(PaymentParser):
    parser_key = "not_implemented"
    parser_version = "0.1.0"

    def parse(self, parser_input: ParserInput) -> ParserResult:
        return ParserResult(
            classification="unknown",
            is_payment=False,
            direction=None,
            amount_cents=None,
            sender_name=None,
            sender_payment_tag=None,
            receiver_tag=None,
            payment_timestamp=None,
            provider_reference=None,
            confidence=0.0,
            missing_fields=["parser_not_implemented"],
            parser_key=self.parser_key,
            parser_version=self.parser_version,
            debug_evidence={"reason": "parser not implemented"},
        )


class CashAppParser(NotImplementedProviderParser):
    parser_key = "cash_app"


class VenmoParser(NotImplementedProviderParser):
    parser_key = "venmo"
