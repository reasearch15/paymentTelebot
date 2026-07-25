from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.payment_account import PaymentAccount
from app.models.payment_email import PaymentEmail
from app.models.provider import Provider
from app.schemas.payment_email import ListenerStatusResponse
from app.services.listener_state import read_heartbeat

router = APIRouter(prefix="/listener", tags=["listener"], dependencies=[Depends(require_admin)])


@router.get("/status", response_model=ListenerStatusResponse)
async def get_listener_status(db: AsyncSession = Depends(get_db)) -> ListenerStatusResponse:
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

    enabled_count = await db.scalar(
        select(func.count(PaymentAccount.id))
        .join(PaymentAccount.provider)
        .where(PaymentAccount.enabled.is_(True), Provider.enabled.is_(True))
    )
    connected_count = await db.scalar(
        select(func.count(PaymentAccount.id)).where(PaymentAccount.listener_status == "connected")
    )
    error_count = await db.scalar(
        select(func.count(PaymentAccount.id)).where(PaymentAccount.listener_status == "error")
    )
    latest_captured = await db.scalar(select(func.max(PaymentEmail.received_at)))

    return ListenerStatusResponse(
        worker_heartbeat=worker_heartbeat,
        last_heartbeat_at=heartbeat_at,
        enabled_account_count=enabled_count or 0,
        connected_account_count=connected_count or 0,
        error_account_count=error_count or 0,
        latest_captured_email_time=latest_captured,
    )
