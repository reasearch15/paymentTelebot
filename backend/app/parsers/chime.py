import re

from app.parsers.base import ParserInput, ParserResult, PaymentParser
from app.parsers.extraction import (
    ensure_timezone_aware,
    extract_amounts,
    extract_payment_tags,
    nearby_label_value,
    normalize_whitespace,
    small_fragment,
)


class ChimeParser(PaymentParser):
    parser_key = "chime"
    parser_version = "0.2.0"

    def parse(self, parser_input: ParserInput) -> ParserResult:
        subject = normalize_whitespace(parser_input.subject)
        text = normalize_whitespace(
            " ".join(part for part in [parser_input.raw_text, parser_input.html_visible_text] if part)
        )
        combined = normalize_whitespace(f"{subject} {text}")
        lower_combined = combined.lower()
        classification = self._classify(subject, combined)
        is_payment = classification in {"incoming_payment", "outgoing_payment"}

        amounts = extract_amounts(combined)
        tags = extract_payment_tags(combined)
        receiver_tag = self._extract_receiver_tag(combined, tags)
        sender_name = (
            self._extract_recipient_name(combined)
            if classification == "outgoing_payment"
            else self._extract_sender_name(subject, combined)
        )
        timestamp = ensure_timezone_aware(parser_input.received_at)
        missing_fields: list[str] = []

        if is_payment:
            for field_name, value in [
                ("amount_cents", amounts[0] if amounts else None),
                ("sender_name", sender_name),
                ("payment_timestamp", timestamp),
            ]:
                if value is None:
                    missing_fields.append(field_name)

        confidence = 0.0
        if is_payment:
            confidence = 0.5
            if amounts:
                confidence += 0.15
            if sender_name:
                confidence += 0.1
            if receiver_tag:
                confidence += 0.05
            if timestamp:
                confidence += 0.1
            if missing_fields:
                confidence = min(confidence, 0.74)

        evidence: dict[str, str] = {}
        if amounts:
            evidence["amount"] = small_fragment(combined, f"${amounts[0] // 100}")
        if receiver_tag:
            evidence["receiver_tag"] = small_fragment(combined, receiver_tag)
        if sender_name:
            evidence["sender_name"] = sender_name[:120]

        return ParserResult(
            classification=classification,
            is_payment=is_payment,
            direction=self._direction_for_classification(classification),
            amount_cents=amounts[0] if is_payment and amounts else None,
            sender_name=sender_name if is_payment else None,
            sender_payment_tag=None,
            receiver_tag=receiver_tag if is_payment else None,
            payment_timestamp=timestamp if is_payment else None,
            provider_reference=None,
            confidence=round(confidence, 2),
            missing_fields=missing_fields,
            parser_key=self.parser_key,
            parser_version=self.parser_version,
            debug_evidence=evidence,
        )

    def _classify(self, subject: str, combined: str) -> str:
        lower_subject = subject.lower()
        lower_combined = combined.lower()
        if "is requesting" in lower_subject or "just requested" in lower_combined:
            return "payment_request"
        if "you sent money" in lower_subject or re.search(r"\byou just sent\b", lower_combined):
            return "outgoing_payment"
        if "just sent you money" in lower_subject or "sent you" in lower_subject or "paid you" in lower_subject:
            return "incoming_payment"
        return "unknown"

    def _direction_for_classification(self, classification: str) -> str | None:
        if classification == "incoming_payment":
            return "IN"
        if classification == "outgoing_payment":
            return "OUT"
        return None

    def _extract_receiver_tag(self, combined: str, tags: list[str]) -> str | None:
        labeled = self._extract_labeled_tag(
            combined,
            ["to", "receiver", "recipient", "receiving account", "account", "deposited to", "sent to"],
        )
        if labeled:
            return labeled
        if len(tags) >= 2:
            return tags[-1]
        return None

    def _extract_labeled_tag(self, combined: str, labels: list[str]) -> str | None:
        for label in labels:
            pattern = re.compile(rf"\b{re.escape(label)}\b\s*:?\s*({re.escape('$')}[A-Za-z][A-Za-z0-9_-]{{1,63}})\b", re.IGNORECASE)
            match = pattern.search(combined)
            if match:
                return match.group(1)
        return None

    def _extract_sender_name(self, subject: str, combined: str) -> str | None:
        patterns = [
            re.compile(r"(.{2,80}?)\s+just\s+sent\s+you\s+money\b", re.IGNORECASE),
            re.compile(r"(.{2,80}?)\s+(?:sent|paid)\s+you\b", re.IGNORECASE),
            re.compile(r"(?:from|sender)\s*:?\s*([A-Z][A-Za-z .'-]{1,80})", re.IGNORECASE),
        ]
        for pattern in patterns:
            match = pattern.search(subject) or pattern.search(combined)
            if match:
                candidate = normalize_whitespace(match.group(1))
                candidate = re.sub(r"^(chime|notification|alert)\s+", "", candidate, flags=re.IGNORECASE).strip()
                if candidate and "$" not in candidate:
                    return candidate[:120]
        nearby = nearby_label_value(combined, ["Sender", "From"], max_chars=60)
        for candidate in nearby:
            if "$" not in candidate:
                return candidate[:120]
        return None

    def _extract_recipient_name(self, combined: str) -> str | None:
        patterns = [
            re.compile(r"\byou\s+just\s+sent\s+\$?[0-9][0-9,]*(?:\.[0-9]{2})?.{0,120}?\bto\s+([A-Z][A-Za-z .'-]{1,80})", re.IGNORECASE),
            re.compile(r"\bto\s+([A-Z][A-Za-z .'-]{1,80})", re.IGNORECASE),
        ]
        for pattern in patterns:
            match = pattern.search(combined)
            if match:
                candidate = normalize_whitespace(match.group(1))
                candidate = re.split(r"\s+(?:on|at|for|\.|,)", candidate, maxsplit=1)[0].strip()
                candidate = candidate.rstrip(".,")
                if candidate and "$" not in candidate:
                    return candidate[:120]
        return None
