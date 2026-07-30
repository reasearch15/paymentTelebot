from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.api.transactions import compute_ledger_totals
from app.db.session import get_db
from app.schemas.dashboard import DashboardSummaryResponse
from app.services.listener_status import build_listener_runtime_status

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(require_admin)])


@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(db: AsyncSession = Depends(get_db)) -> DashboardSummaryResponse:
    totals = await compute_ledger_totals(db)
    listener = await build_listener_runtime_status(db)
    return DashboardSummaryResponse(
        total_in_cents=totals.total_incoming_cents,
        total_out_cents=totals.total_outgoing_cents,
        total_settled_cents=totals.total_settled_cents,
        current_unsettled_balance_cents=totals.unsettled_balance_cents,
        total_transactions=totals.total_transactions,
        connected_accounts=listener.configured_account_count,
        active_accounts=listener.active_account_count,
        accounts_with_errors=listener.error_account_count,
        worker_alive=listener.worker_alive,
        worker_heartbeat=listener.worker_heartbeat,
        last_heartbeat_at=listener.last_heartbeat_at,
        listener_health=listener.listener_health,
        latest_captured_email_at=listener.latest_captured_email_at,
    )
