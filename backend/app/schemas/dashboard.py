from datetime import datetime

from pydantic import BaseModel


class DashboardSummaryResponse(BaseModel):
    total_in_cents: int
    total_out_cents: int
    total_settled_cents: int
    current_unsettled_balance_cents: int
    total_transactions: int
    connected_accounts: int
    active_accounts: int
    accounts_with_errors: int
    worker_alive: bool
    worker_heartbeat: str | None
    last_heartbeat_at: datetime | None
    listener_health: str
    latest_captured_email_at: datetime | None
