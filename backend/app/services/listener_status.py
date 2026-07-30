from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.payment_account import PaymentAccount
from app.models.payment_email import PaymentEmail
from app.models.provider import Provider
from app.services.listener_state import read_heartbeat


@dataclass(frozen=True)
class ListenerRuntimeStatus:
    worker_heartbeat: str | None
    last_heartbeat_at: datetime | None
    worker_alive: bool
    configured_account_count: int
    active_account_count: int
    connected_listener_count: int
    error_account_count: int
    latest_captured_email_at: datetime | None
    listener_health: str


def derive_listener_health(
    *,
    worker_alive: bool,
    active_accounts: int,
    accounts_with_errors: int,
) -> str:
    if not worker_alive:
        return "offline"
    if accounts_with_errors > 0:
        return "degraded"
    if active_accounts <= 0:
        return "idle"
    return "healthy"


async def build_listener_runtime_status(db: AsyncSession) -> ListenerRuntimeStatus:
    heartbeat, heartbeat_at = await read_heartbeat(db)
    now = datetime.now(UTC)
    heartbeat_interval = (
        settings.gmail_idle_healthcheck_seconds
        if settings.gmail_idle_enabled
        else settings.gmail_poll_interval_seconds
    )
    stale_after = timedelta(seconds=max(heartbeat_interval * 3, 60))
    worker_heartbeat = heartbeat
    if heartbeat_at is None or heartbeat_at < now - stale_after:
        worker_heartbeat = "offline"
    worker_alive = worker_heartbeat == "alive"

    configured_account_count = int((await db.scalar(select(func.count(PaymentAccount.id)))) or 0)
    active_account_count = int(
        (
            await db.scalar(
                select(func.count(PaymentAccount.id))
                .join(PaymentAccount.provider)
                .where(PaymentAccount.enabled.is_(True), Provider.enabled.is_(True))
            )
        )
        or 0
    )
    connected_listener_count = int(
        (
            await db.scalar(
                select(func.count(PaymentAccount.id)).where(PaymentAccount.listener_status == "connected")
            )
        )
        or 0
    )
    error_account_count = int(
        (await db.scalar(select(func.count(PaymentAccount.id)).where(PaymentAccount.listener_status == "error")))
        or 0
    )
    latest_captured_email_at = await db.scalar(select(func.max(PaymentEmail.received_at)))

    return ListenerRuntimeStatus(
        worker_heartbeat=worker_heartbeat,
        last_heartbeat_at=heartbeat_at,
        worker_alive=worker_alive,
        configured_account_count=configured_account_count,
        active_account_count=active_account_count,
        connected_listener_count=connected_listener_count,
        error_account_count=error_account_count,
        latest_captured_email_at=latest_captured_email_at,
        listener_health=derive_listener_health(
            worker_alive=worker_alive,
            active_accounts=active_account_count,
            accounts_with_errors=error_account_count,
        ),
    )
