from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.db.session import get_db
from app.schemas.payment_email import ListenerStatusResponse
from app.services.listener_status import build_listener_runtime_status

router = APIRouter(prefix="/listener", tags=["listener"], dependencies=[Depends(require_admin)])


@router.get("/status", response_model=ListenerStatusResponse)
async def get_listener_status(db: AsyncSession = Depends(get_db)) -> ListenerStatusResponse:
    status = await build_listener_runtime_status(db)
    return ListenerStatusResponse(
        worker_heartbeat=status.worker_heartbeat,
        last_heartbeat_at=status.last_heartbeat_at,
        enabled_account_count=status.active_account_count,
        connected_account_count=status.connected_listener_count,
        error_account_count=status.error_account_count,
        latest_captured_email_time=status.latest_captured_email_at,
    )
