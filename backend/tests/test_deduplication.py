from sqlalchemy.exc import IntegrityError

from app.services.deduplication import is_duplicate_payment_email_error


def test_detects_payment_email_unique_constraint() -> None:
    error = IntegrityError(
        "insert",
        {},
        Exception("duplicate key value violates unique constraint uq_payment_emails_account_mailbox_uid"),
    )

    assert is_duplicate_payment_email_error(error)


def test_ignores_unrelated_integrity_error() -> None:
    error = IntegrityError("insert", {}, Exception("foreign key violation"))

    assert not is_duplicate_payment_email_error(error)
