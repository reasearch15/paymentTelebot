"""Bot Ledger schemas.

Bot Ledger is an operational accounting view per Telegram integration.
Membership comes from telegram_deliveries, not current route assignments.
A transaction routed to multiple bots counts once in each Bot Ledger and once in the global Ledger.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.settlement import parse_dollar_amount_to_cents


class BotLedgerIntegrationItem(BaseModel):
    id: int
    name: str
    bot_username: str | None = None
    group_id: str | None = None
    enabled: bool


class BotLedgerDeliveryCounts(BaseModel):
    sent: int = 0
    failed: int = 0
    pending: int = 0
    sending: int = 0


class BotLedgerIntegrationSummary(BaseModel):
    id: int
    name: str
    bot_username: str | None = None
    group_id: str | None = None
    enabled: bool


class BotLedgerSummary(BaseModel):
    telegram_integration: BotLedgerIntegrationSummary
    current_unsettled_cents: int
    total_in_cents: int
    total_settled_cents: int
    payments_today: int
    amount_today_cents: int
    payments_week: int
    amount_week_cents: int
    payments_month: int
    amount_month_cents: int
    all_time_payments: int
    all_time_amount_cents: int
    assigned_gmail_accounts: int
    delivery_counts: BotLedgerDeliveryCounts
    last_payment_at: datetime | None = None
    last_settlement_at: datetime | None = None


class BotLedgerGmailBreakdownItem(BaseModel):
    payment_account_id: int
    gmail_account: str
    friendly_name: str
    provider_name: str
    payment_count: int
    total_amount_cents: int
    last_payment_at: datetime | None = None


class BotLedgerTransactionItem(BaseModel):
    transaction_id: int
    received_at: datetime
    sender_name: str | None = None
    payment_account_id: int
    payment_account_name: str
    payment_gmail: str
    provider_id: int
    provider_name: str
    amount_cents: int
    delivery_status: str
    attempt_count: int
    telegram_message_id: str | None = None
    delivery_id: int
    last_error: str | None = None


class BotLedgerTransactionListResponse(BaseModel):
    items: list[BotLedgerTransactionItem]
    total: int
    page: int
    page_size: int


class BotLedgerSettlementCreate(BaseModel):
    amount: str = Field(min_length=1, max_length=32, description="Dollar amount, e.g. 15.00")
    note: str | None = Field(default=None, max_length=500)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: str) -> str:
        parse_dollar_amount_to_cents(value)
        return value.strip()

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @property
    def amount_cents(self) -> int:
        return parse_dollar_amount_to_cents(self.amount)


class BotLedgerSettlementItem(BaseModel):
    id: int
    telegram_integration_id: int
    amount_cents: int
    balance_before_cents: int
    balance_after_cents: int
    note: str | None = None
    created_by_user_id: str
    settled_at: datetime
    created_at: datetime
    running_settled_total_cents: int = 0

    model_config = ConfigDict(from_attributes=True)


class BotLedgerSettlementListResponse(BaseModel):
    items: list[BotLedgerSettlementItem]
    total: int
    page: int
    page_size: int
