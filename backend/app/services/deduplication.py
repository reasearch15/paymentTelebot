from sqlalchemy.exc import IntegrityError

DEDUPLICATION_CONSTRAINTS = (
    "uq_payment_emails_gmail_message_id",
    "uq_payment_emails_account_mailbox_uid",
)


def is_duplicate_payment_email_error(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return any(constraint in message for constraint in DEDUPLICATION_CONSTRAINTS)
