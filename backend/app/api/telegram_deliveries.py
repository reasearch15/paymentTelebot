"""Delivery Operations Center API for telegram_deliveries."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.db.session import get_db
from app.models.telegram_delivery import TelegramDelivery
from app.schemas.telegram import (
    TelegramDeliveryAttemptRead,
    TelegramDeliveryBulkRetryResponse,
    TelegramDeliveryDetail,
    TelegramDeliveryListItem,
    TelegramDeliveryListResponse,
    TelegramDeliveryRetryFilterRequest,
    TelegramDeliveryRetryResponse,
    TelegramDeliveryTimelineEvent,
)
from app.services.telegram import is_delivery_eligible_for_manual_retry, retry_telegram_delivery, sanitize_telegram_error
from app.services.telegram_delivery_ops import (
    DeliveryListFilters,
    build_delivery_timeline,
    deliveries_to_csv_rows,
    get_telegram_delivery_detail,
    list_all_filtered_delivery_ids,
    list_telegram_deliveries,
    parse_amount_to_cents,
    retry_deliveries_by_ids,
)

router = APIRouter(
    prefix="/telegram-deliveries",
    tags=["telegram-deliveries"],
    dependencies=[Depends(require_admin)],
)


def _filters_from_params(
    *,
    status_value: str | None = None,
    integration_id: int | None = None,
    payment_account_id: int | None = None,
    provider_id: int | None = None,
    sender: str | None = None,
    amount_min: str | None = None,
    amount_max: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    search: str | None = None,
    transaction_id: int | None = None,
    retryable_only: bool = False,
) -> DeliveryListFilters:
    try:
        amount_min_cents = parse_amount_to_cents(amount_min)
        amount_max_cents = parse_amount_to_cents(amount_max)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if status_value and status_value not in {"pending", "sending", "sent", "failed"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status filter.")

    return DeliveryListFilters(
        status=status_value,
        integration_id=integration_id,
        payment_account_id=payment_account_id,
        provider_id=provider_id,
        sender=sender,
        amount_min_cents=amount_min_cents,
        amount_max_cents=amount_max_cents,
        date_from=date_from,
        date_to=date_to,
        search=search,
        transaction_id=transaction_id,
        retryable_only=retryable_only,
    )


def serialize_delivery_list_item(delivery: TelegramDelivery) -> TelegramDeliveryListItem:
    txn = delivery.transaction
    account = txn.payment_account
    provider = account.provider
    integration = delivery.telegram_integration
    return TelegramDeliveryListItem(
        id=delivery.id,
        status=delivery.status,
        telegram_integration_id=delivery.telegram_integration_id,
        integration_name=integration.name if integration else "Unknown",
        bot_username=integration.bot_username if integration else None,
        group_id=integration.group_id if integration else None,
        transaction_id=delivery.transaction_id,
        sender_name=txn.sender_name,
        amount_cents=txn.amount_cents,
        provider_id=provider.id,
        provider_name=provider.name,
        payment_account_id=account.id,
        payment_account_name=account.friendly_name,
        payment_gmail=account.gmail_address,
        gmail_message_id=txn.gmail_message_id,
        attempt_count=int(delivery.attempt_count or 0),
        telegram_message_id=delivery.telegram_message_id,
        created_at=delivery.created_at,
        last_attempt_at=delivery.last_attempt_at,
        sent_at=delivery.sent_at,
        last_error=sanitize_telegram_error(delivery.last_error) if delivery.last_error else None,
        can_retry=is_delivery_eligible_for_manual_retry(delivery),
    )


def serialize_delivery_detail(delivery: TelegramDelivery) -> TelegramDeliveryDetail:
    base = serialize_delivery_list_item(delivery)
    txn = delivery.transaction
    attempts = [
        TelegramDeliveryAttemptRead(
            id=attempt.id,
            attempt_number=attempt.attempt_number,
            status=attempt.status,
            telegram_message_id=attempt.telegram_message_id,
            error_message=sanitize_telegram_error(attempt.error_message) if attempt.error_message else None,
            attempted_at=attempt.attempted_at,
            completed_at=attempt.completed_at,
        )
        for attempt in sorted(delivery.attempts, key=lambda item: item.attempt_number)
    ]
    timeline = [
        TelegramDeliveryTimelineEvent(event=event["event"], at=event["at"], detail=event["detail"])
        for event in build_delivery_timeline(delivery)
    ]
    return TelegramDeliveryDetail(
        **base.model_dump(),
        attempts=attempts,
        timeline=timeline,
        receiver_tag=txn.receiver_tag or txn.payment_account.receiver_tag,
        provider_reference=txn.provider_reference,
        transaction_received_at=txn.received_at,
        direction=txn.direction.value if txn.direction else None,
    )


@router.get("", response_model=TelegramDeliveryListResponse)
async def list_deliveries(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None, alias="status"),
    integration: int | None = Query(default=None),
    payment_account: int | None = Query(default=None),
    provider: int | None = Query(default=None),
    sender: str | None = Query(default=None),
    amount_min: str | None = Query(default=None),
    amount_max: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    search: str | None = Query(default=None),
    sort: str | None = Query(default="newest"),
    db: AsyncSession = Depends(get_db),
) -> TelegramDeliveryListResponse:
    del sort  # Newest-first is the only supported sort for now.
    filters = _filters_from_params(
        status_value=status,
        integration_id=integration,
        payment_account_id=payment_account,
        provider_id=provider,
        sender=sender,
        amount_min=amount_min,
        amount_max=amount_max,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    rows, total = await list_telegram_deliveries(db, filters, page=page, page_size=page_size)
    return TelegramDeliveryListResponse(
        items=[serialize_delivery_list_item(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/export")
async def export_deliveries_csv(
    status: str | None = Query(default=None),
    integration: int | None = Query(default=None),
    payment_account: int | None = Query(default=None),
    provider: int | None = Query(default=None),
    sender: str | None = Query(default=None),
    amount_min: str | None = Query(default=None),
    amount_max: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    search: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    filters = _filters_from_params(
        status_value=status,
        integration_id=integration,
        payment_account_id=payment_account,
        provider_id=provider,
        sender=sender,
        amount_min=amount_min,
        amount_max=amount_max,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    rows, _total = await list_telegram_deliveries(db, filters, page=1, page_size=200)
    csv_rows = deliveries_to_csv_rows(rows)
    buffer = io.StringIO()
    fieldnames = list(csv_rows[0].keys()) if csv_rows else [
        "id",
        "status",
        "integration_name",
        "bot_username",
        "group_id",
        "transaction_id",
        "sender_name",
        "amount_cents",
        "provider",
        "payment_gmail",
        "payment_account",
        "attempt_count",
        "telegram_message_id",
        "gmail_message_id",
        "created_at",
        "last_attempt_at",
        "sent_at",
        "last_error",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(csv_rows)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="telegram-deliveries.csv"'},
    )


@router.post("/retry-filter", response_model=TelegramDeliveryBulkRetryResponse)
async def retry_filtered_deliveries(
    payload: TelegramDeliveryRetryFilterRequest,
    db: AsyncSession = Depends(get_db),
) -> TelegramDeliveryBulkRetryResponse:
    filters = _filters_from_params(
        status_value=payload.status or "failed",
        integration_id=payload.integration_id,
        payment_account_id=payload.payment_account_id,
        provider_id=payload.provider_id,
        sender=payload.sender,
        amount_min=payload.amount_min,
        amount_max=payload.amount_max,
        date_from=payload.date_from,
        date_to=payload.date_to,
        search=payload.search,
        transaction_id=payload.transaction_id,
        retryable_only=True,
    )
    delivery_ids = await list_all_filtered_delivery_ids(db, filters, limit=payload.limit)
    result = await retry_deliveries_by_ids(delivery_ids)
    return TelegramDeliveryBulkRetryResponse(
        attempted=result["attempted"],
        succeeded=result["succeeded"],
        failed=result["failed"],
        skipped=result["skipped"],
        results=[TelegramDeliveryRetryResponse(**item) for item in result["results"]],
    )


@router.get("/{delivery_id}", response_model=TelegramDeliveryDetail)
async def get_delivery(delivery_id: int, db: AsyncSession = Depends(get_db)) -> TelegramDeliveryDetail:
    delivery = await get_telegram_delivery_detail(db, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telegram delivery not found.")
    return serialize_delivery_detail(delivery)


@router.post("/{delivery_id}/retry", response_model=TelegramDeliveryRetryResponse)
async def retry_delivery(delivery_id: int) -> TelegramDeliveryRetryResponse:
    result = await retry_telegram_delivery(delivery_id)
    if result.get("reason") == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telegram delivery not found.")
    if result.get("reason") == "already_sent":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot retry a sent delivery.")
    if result.get("reason") == "pending_owned":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot retry a pending delivery.")
    if result.get("reason") == "sending_in_progress":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Delivery is currently sending.")
    return TelegramDeliveryRetryResponse(**result)
