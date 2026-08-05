import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import require_admin
from app.api.telegram_integrations import serialize_integration
from app.core.encryption import decrypt_secret, encrypt_secret
from app.db.session import AsyncSessionLocal, get_db
from app.models.payment_account import PaymentAccount
from app.models.payment_account_telegram_route import PaymentAccountTelegramRoute
from app.models.payment_email import PaymentEmail
from app.models.provider import Provider
from app.models.telegram_integration import TelegramIntegration
from app.schemas.payment_account import (
    ConnectionTestResponse,
    PaymentAccountCreate,
    PaymentAccountResponse,
    PaymentAccountUpdate,
)
from app.schemas.telegram import (
    PaymentAccountDeliveryStats,
    PaymentAccountTelegramAssignmentUpdate,
    PaymentAccountTelegramIntegrationsRead,
    PaymentAccountTelegramIntegrationSummary,
)
from app.services.gmail_imap import test_gmail_connection
from app.services.telegram import count_routes_by_integration, lowest_telegram_integration_id
from app.services.telegram_assignments import (
    list_integrations_for_payment_account,
    replace_payment_account_telegram_integrations,
)
from app.services.telegram_delivery_ops import load_payment_account_delivery_stats

router = APIRouter(prefix="/payment-accounts", tags=["payment accounts"], dependencies=[Depends(require_admin)])


async def get_last_captured_email_times(db: AsyncSession) -> dict[int, object]:
    result = await db.execute(
        select(PaymentEmail.payment_account_id, func.max(PaymentEmail.received_at))
        .group_by(PaymentEmail.payment_account_id)
    )
    return {payment_account_id: captured_at for payment_account_id, captured_at in result.all()}


async def load_account_telegram_summaries(
    db: AsyncSession,
    account_ids: list[int],
) -> dict[int, list[PaymentAccountTelegramIntegrationSummary]]:
    if not account_ids:
        return {}

    result = await db.execute(
        select(PaymentAccountTelegramRoute.payment_account_id, TelegramIntegration)
        .join(
            TelegramIntegration,
            TelegramIntegration.id == PaymentAccountTelegramRoute.telegram_integration_id,
        )
        .where(PaymentAccountTelegramRoute.payment_account_id.in_(account_ids))
        .order_by(TelegramIntegration.id)
    )
    summaries: dict[int, list[PaymentAccountTelegramIntegrationSummary]] = {account_id: [] for account_id in account_ids}
    for account_id, integration in result.all():
        summaries.setdefault(account_id, []).append(
            PaymentAccountTelegramIntegrationSummary(
                id=integration.id,
                name=integration.name,
                enabled=integration.enabled,
                bot_username=integration.bot_username,
                group_id=integration.group_id,
            )
        )
    return summaries


def serialize_account(
    account: PaymentAccount,
    last_captured_email_at=None,
    telegram_integrations: list[PaymentAccountTelegramIntegrationSummary] | None = None,
    delivery_stats: PaymentAccountDeliveryStats | None = None,
) -> PaymentAccountResponse:
    integrations = telegram_integrations or []
    stats = delivery_stats
    if stats is not None:
        stats = PaymentAccountDeliveryStats(
            messages_today=stats.messages_today,
            telegram_destination_count=len(integrations),
            last_payment_at=stats.last_payment_at or account.last_email_at,
            last_telegram_delivery_at=stats.last_telegram_delivery_at,
        )
    return PaymentAccountResponse(
        id=account.id,
        provider_id=account.provider_id,
        provider_name=account.provider.name,
        friendly_name=account.friendly_name,
        receiver_tag=account.receiver_tag,
        gmail_address=account.gmail_address,
        enabled=account.enabled,
        listener_status=account.listener_status,
        last_checked_at=account.last_checked_at,
        last_email_at=account.last_email_at,
        last_captured_email_at=last_captured_email_at,
        has_app_password=bool(account.encrypted_app_password),
        created_at=account.created_at,
        updated_at=account.updated_at,
        telegram_integrations=integrations,
        telegram_integration_count=len(integrations),
        telegram_integration_ids=[item.id for item in integrations],
        delivery_stats=stats,
    )


async def get_account_or_404(db: AsyncSession, account_id: int) -> PaymentAccount:
    result = await db.execute(
        select(PaymentAccount)
        .options(selectinload(PaymentAccount.provider))
        .where(PaymentAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment account not found.")
    return account


async def ensure_provider_exists(db: AsyncSession, provider_id: int) -> None:
    provider = await db.get(Provider, provider_id)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="provider_id does not exist.")


def normalize_receiver_tag(receiver_tag: str | None) -> str | None:
    normalized_receiver_tag = receiver_tag.strip() if receiver_tag else None
    return normalized_receiver_tag or None


async def ensure_receiver_tag_is_unique(
    db: AsyncSession,
    receiver_tag: str | None,
    exclude_account_id: int | None = None,
) -> str | None:
    normalized_receiver_tag = normalize_receiver_tag(receiver_tag)
    if normalized_receiver_tag is None:
        return None

    query = select(PaymentAccount.id).where(PaymentAccount.receiver_tag == normalized_receiver_tag)
    if exclude_account_id is not None:
        query = query.where(PaymentAccount.id != exclude_account_id)

    result = await db.execute(query)
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="receiver_tag must be unique.")
    return normalized_receiver_tag


def raise_account_conflict(exc: IntegrityError) -> None:
    message = str(exc.orig).lower()
    if "receiver_tag" in message:
        detail = "receiver_tag must be unique."
    elif "gmail_address" in message:
        detail = "gmail_address must be unique."
    else:
        detail = "Payment account violates a unique constraint."
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc


@router.get("", response_model=list[PaymentAccountResponse])
async def list_payment_accounts(db: AsyncSession = Depends(get_db)) -> list[PaymentAccountResponse]:
    result = await db.execute(
        select(PaymentAccount)
        .options(selectinload(PaymentAccount.provider))
        .order_by(PaymentAccount.friendly_name)
    )
    accounts = list(result.scalars().all())
    last_captured = await get_last_captured_email_times(db)
    telegram_map = await load_account_telegram_summaries(db, [account.id for account in accounts])
    stats_map = await load_payment_account_delivery_stats(db, [account.id for account in accounts])
    return [
        serialize_account(
            account,
            last_captured.get(account.id),
            telegram_map.get(account.id, []),
            delivery_stats=PaymentAccountDeliveryStats(**stats_map.get(account.id, {})),
        )
        for account in accounts
    ]


@router.post("", response_model=PaymentAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_payment_account(
    payload: PaymentAccountCreate,
    db: AsyncSession = Depends(get_db),
) -> PaymentAccountResponse:
    await ensure_provider_exists(db, payload.provider_id)
    receiver_tag = await ensure_receiver_tag_is_unique(db, payload.receiver_tag)
    account = PaymentAccount(
        provider_id=payload.provider_id,
        friendly_name=payload.friendly_name,
        receiver_tag=receiver_tag,
        gmail_address=str(payload.gmail_address).lower(),
        encrypted_app_password=encrypt_secret(payload.app_password),
    )
    db.add(account)

    try:
        await db.flush()
        if payload.telegram_integration_ids:
            await replace_payment_account_telegram_integrations(
                db,
                account.id,
                payload.telegram_integration_ids,
            )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise_account_conflict(exc)

    account = await get_account_or_404(db, account.id)
    telegram_map = await load_account_telegram_summaries(db, [account.id])
    return serialize_account(account, telegram_integrations=telegram_map.get(account.id, []))


@router.get("/{account_id}", response_model=PaymentAccountResponse)
async def get_payment_account(account_id: int, db: AsyncSession = Depends(get_db)) -> PaymentAccountResponse:
    account = await get_account_or_404(db, account_id)
    telegram_map = await load_account_telegram_summaries(db, [account.id])
    return serialize_account(account, telegram_integrations=telegram_map.get(account.id, []))


@router.patch("/{account_id}", response_model=PaymentAccountResponse)
async def update_payment_account(
    account_id: int,
    payload: PaymentAccountUpdate,
    db: AsyncSession = Depends(get_db),
) -> PaymentAccountResponse:
    account = await get_account_or_404(db, account_id)
    updates = payload.model_dump(exclude_unset=True)
    telegram_ids = updates.pop("telegram_integration_ids", None)

    if "provider_id" in updates:
        await ensure_provider_exists(db, updates["provider_id"])

    if "app_password" in updates:
        account.encrypted_app_password = encrypt_secret(updates.pop("app_password"))

    if "gmail_address" in updates and updates["gmail_address"] is not None:
        updates["gmail_address"] = str(updates["gmail_address"]).lower()

    if "receiver_tag" in updates:
        updates["receiver_tag"] = await ensure_receiver_tag_is_unique(db, updates["receiver_tag"], account.id)

    for field, value in updates.items():
        setattr(account, field, value)

    try:
        if telegram_ids is not None:
            await replace_payment_account_telegram_integrations(db, account.id, telegram_ids)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise_account_conflict(exc)

    account = await get_account_or_404(db, account.id)
    telegram_map = await load_account_telegram_summaries(db, [account.id])
    return serialize_account(account, telegram_integrations=telegram_map.get(account.id, []))


@router.post("/{account_id}/enable", response_model=PaymentAccountResponse)
async def enable_payment_account(account_id: int, db: AsyncSession = Depends(get_db)) -> PaymentAccountResponse:
    account = await get_account_or_404(db, account_id)
    account.enabled = True
    await db.commit()
    account = await get_account_or_404(db, account.id)
    telegram_map = await load_account_telegram_summaries(db, [account.id])
    return serialize_account(account, telegram_integrations=telegram_map.get(account.id, []))


@router.post("/{account_id}/disable", response_model=PaymentAccountResponse)
async def disable_payment_account(account_id: int, db: AsyncSession = Depends(get_db)) -> PaymentAccountResponse:
    account = await get_account_or_404(db, account_id)
    account.enabled = False
    await db.commit()
    account = await get_account_or_404(db, account.id)
    telegram_map = await load_account_telegram_summaries(db, [account.id])
    return serialize_account(account, telegram_integrations=telegram_map.get(account.id, []))


@router.post("/{account_id}/test-connection", response_model=ConnectionTestResponse)
async def test_payment_account_connection(
    account_id: int,
    db: AsyncSession = Depends(get_db),
) -> ConnectionTestResponse:
    account = await get_account_or_404(db, account_id)
    gmail_address = account.gmail_address
    app_password = decrypt_secret(account.encrypted_app_password)
    await db.close()

    result = await asyncio.to_thread(test_gmail_connection, gmail_address, app_password)

    async with AsyncSessionLocal() as update_db:
        account_to_update = await update_db.get(PaymentAccount, account_id)
        if account_to_update is not None:
            account_to_update.listener_status = "connected" if result.success else "error"
            account_to_update.last_checked_at = result.checked_at
            await update_db.commit()

    return ConnectionTestResponse(success=result.success, message=result.message, checked_at=result.checked_at)


@router.get("/{account_id}/telegram-integrations", response_model=PaymentAccountTelegramIntegrationsRead)
async def get_payment_account_telegram_integrations(
    account_id: int,
    db: AsyncSession = Depends(get_db),
) -> PaymentAccountTelegramIntegrationsRead:
    await get_account_or_404(db, account_id)
    integrations = await list_integrations_for_payment_account(db, account_id)
    counts = await count_routes_by_integration(db)
    legacy_id = await lowest_telegram_integration_id(db)
    return PaymentAccountTelegramIntegrationsRead(
        payment_account_id=account_id,
        telegram_integrations=[
            serialize_integration(
                integration,
                assigned_count=counts.get(integration.id, 0),
                legacy_default_id=legacy_id,
            )
            for integration in integrations
        ],
    )


@router.put("/{account_id}/telegram-integrations", response_model=PaymentAccountTelegramIntegrationsRead)
async def put_payment_account_telegram_integrations(
    account_id: int,
    payload: PaymentAccountTelegramAssignmentUpdate,
    db: AsyncSession = Depends(get_db),
) -> PaymentAccountTelegramIntegrationsRead:
    await get_account_or_404(db, account_id)
    try:
        integrations = await replace_payment_account_telegram_integrations(
            db,
            account_id,
            payload.telegram_integration_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    await db.commit()
    counts = await count_routes_by_integration(db)
    legacy_id = await lowest_telegram_integration_id(db)
    return PaymentAccountTelegramIntegrationsRead(
        payment_account_id=account_id,
        telegram_integrations=[
            serialize_integration(
                integration,
                assigned_count=counts.get(integration.id, 0),
                legacy_default_id=legacy_id,
            )
            for integration in integrations
        ],
    )
