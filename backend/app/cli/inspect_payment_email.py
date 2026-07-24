import argparse
import asyncio

from app.db.session import AsyncSessionLocal
from app.services.payment_email_parser import get_email_for_parsing, inspect_payment_email


async def main_async(email_id: int) -> None:
    async with AsyncSessionLocal() as session:
        email = await get_email_for_parsing(session, email_id)
        if email is None:
            raise SystemExit(f"Payment email {email_id} was not found.")

        inspection = inspect_payment_email(email)
        parsed = inspection.parsed_result or {}
        account = email.payment_account

        print(f"Provider: {inspection.provider_name} ({inspection.parser_key})")
        print(f"Receiver tag: {account.receiver_tag}")
        print(f"Subject: {inspection.normalized_subject or '(No subject)'}")
        print(f"Detected amounts: {inspection.detected_monetary_amounts}")
        print(f"Detected payment tags: {inspection.detected_dollar_prefixed_tags}")
        print(f"Parsed sender name: {parsed.get('sender_name')}")
        print(f"Parsed sender payment tag: {parsed.get('sender_payment_tag')}")
        print(f"Parsed timestamp: {parsed.get('payment_timestamp')}")
        print(f"Missing fields: {parsed.get('missing_fields')}")
        print(f"Confidence: {parsed.get('confidence')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect parser evidence for a captured payment email.")
    parser.add_argument("email_id", type=int, metavar="EMAIL_ID")
    args = parser.parse_args()
    asyncio.run(main_async(args.email_id))


if __name__ == "__main__":
    main()
