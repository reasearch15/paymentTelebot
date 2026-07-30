from app.services.listener_status import derive_listener_health


def test_derive_listener_health_healthy() -> None:
    assert (
        derive_listener_health(worker_alive=True, active_accounts=1, accounts_with_errors=0) == "healthy"
    )


def test_derive_listener_health_degraded() -> None:
    assert (
        derive_listener_health(worker_alive=True, active_accounts=1, accounts_with_errors=2) == "degraded"
    )


def test_derive_listener_health_offline() -> None:
    assert (
        derive_listener_health(worker_alive=False, active_accounts=1, accounts_with_errors=0) == "offline"
    )


def test_derive_listener_health_idle_when_no_active_accounts() -> None:
    assert derive_listener_health(worker_alive=True, active_accounts=0, accounts_with_errors=0) == "idle"


def test_dashboard_summary_maps_ledger_totals_fields() -> None:
    from app.schemas.dashboard import DashboardSummaryResponse

    summary = DashboardSummaryResponse(
        total_in_cents=62409,
        total_out_cents=56300,
        total_settled_cents=0,
        current_unsettled_balance_cents=6109,
        total_transactions=74,
        connected_accounts=1,
        active_accounts=1,
        accounts_with_errors=0,
        worker_alive=True,
        worker_heartbeat="alive",
        last_heartbeat_at=None,
        listener_health="healthy",
        latest_captured_email_at=None,
    )
    assert summary.current_unsettled_balance_cents == (
        summary.total_in_cents - summary.total_out_cents - summary.total_settled_cents
    )
    assert summary.listener_health == "healthy"
