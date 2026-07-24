from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TransactionSummary(BaseModel):
    id: int
    payment_account_id: int
    provider_id: int
    provider_name: str
    friendly_name: str
    direction: str
    amount_cents: int
    sender_name: str | None
    sender_payment_tag: str | None
    receiver_tag: str | None
    provider_reference: str | None
    gmail_message_id: str
    received_at: datetime
    telegram_status: str
    telegram_sent_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
