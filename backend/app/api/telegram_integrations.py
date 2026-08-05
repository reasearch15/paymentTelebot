from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import require_admin
from app.core.encryption import decrypt_secret, encrypt_secret
from app.db.session import get_db
from app.models.payment_account import PaymentAccount
from app.models.payment_account_telegram_route import PaymentAccountTelegramRoute
from app.models.telegram_delivery import TelegramDelivery
from app.models.telegram_integration import TelegramIntegration
from app.schemas.telegram import (
    PaymentAccountAssignmentItem,
    TelegramActionResponse,
    TelegramIntegrationAssignmentRead,
    TelegramIntegrationAssignmentUpdate,
    TelegramIntegrationCreate,
    TelegramIntegrationListItem,
    TelegramIntegrationRead,
    TelegramIntegrationUpdate,
)
from app.services.telegram import (
    count_routes_by_integration,
    find_duplicate_telegram_destination,
    lowest_telegram_integration_id,
    mask_bot_token,
    test_specific_telegram_integration,
)
from app.services.telegram_assignments import (
    list_routes_for_integration,
    replace_integration_payment_accounts,
)

router = APIRouter(
    prefix="/telegram-integrations",
    tags=["telegram-integrations"],
    dependencies=[Depends(require_admin)],
)


def serialize_integration(
    integration: TelegramIntegration,
    *,
    assigned_count: int = 0,
    legacy_default_id: int | None = None,
) -> TelegramIntegrationRead:
    token_mask = None
    if integration.bot_token_encrypted:
        try:
            token_mask = mask_bot_token(decrypt_secret(integration.bot_token_encrypted))
        except ValueError:
            token_mask = "****"
    return TelegramIntegrationRead(
        id=integration.id,
        name=integration.name,
        bot_token_masked=token_mask,
        has_bot_token=bool(integration.bot_token_encrypted),
        group_id=integration.group_id,
        bot_username=integration.bot_username,
        enabled=integration.enabled,
        last_checked_at=integration.last_checked_at,
        last_success_at=integration.last_success_at,
        last_error=integration.last_error,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
        assigned_payment_account_count=assigned_count,
        is_legacy_default=legacy_default_id is not None and integration.id == legacy_default_id,
    )


async def get_integration_or_404(db: AsyncSession, integration_id: int) -> TelegramIntegration:
    integration = await db.get(TelegramIntegration, integration_id)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telegram integration not found.")
    return integration


def serialize_action(success: bool, message: str, integration: TelegramIntegration) -> TelegramActionResponse:
    return TelegramActionResponse(
        success=success,
        message=message,
        last_checked_at=integration.last_checked_at,
        last_success_at=integration.last_success_at,
        last_error=integration.last_error,
        bot_username=integration.bot_username,
    )


@router.get("", response_model=list[TelegramIntegrationListItem])
async def list_telegram_integrations(db: AsyncSession = Depends(get_db)) -> list[TelegramIntegrationListItem]:
    integrations = list((await db.scalars(select(TelegramIntegration).order_by(TelegramIntegration.id))).all())
    counts = await count_routes_by_integration(db)
    legacy_id = integrations[0].id if integrations else None
    return [
        TelegramIntegrationListItem(
            **serialize_integration(
                integration,
                assigned_count=counts.get(integration.id, 0),
                legacy_default_id=legacy_id,
            ).model_dump()
        )
        for integration in integrations
    ]


@router.post("", response_model=TelegramIntegrationRead, status_code=status.HTTP_201_CREATED)
async def create_telegram_integration(
    payload: TelegramIntegrationCreate,
    db: AsyncSession = Depends(get_db),
) -> TelegramIntegrationRead:
    duplicate = await find_duplicate_telegram_destination(
        db,
        bot_token=payload.bot_token,
        group_id=payload.group_id,
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A Telegram integration with the same bot token and group ID already exists.",
        )

    integration = TelegramIntegration(
        name=payload.name,
        bot_token_encrypted=encrypt_secret(payload.bot_token),
        group_id=payload.group_id,
        enabled=payload.enabled,
    )
    db.add(integration)
    await db.commit()
    await db.refresh(integration)
    legacy_id = await lowest_telegram_integration_id(db)
    return serialize_integration(integration, assigned_count=0, legacy_default_id=legacy_id)


@router.get("/{integration_id}", response_model=TelegramIntegrationRead)
async def get_telegram_integration(
    integration_id: int,
    db: AsyncSession = Depends(get_db),
) -> TelegramIntegrationRead:
    integration = await get_integration_or_404(db, integration_id)
    counts = await count_routes_by_integration(db)
    legacy_id = await lowest_telegram_integration_id(db)
    return serialize_integration(
        integration,
        assigned_count=counts.get(integration.id, 0),
        legacy_default_id=legacy_id,
    )


@router.patch("/{integration_id}", response_model=TelegramIntegrationRead)
async def update_telegram_integration(
    integration_id: int,
    payload: TelegramIntegrationUpdate,
    db: AsyncSession = Depends(get_db),
) -> TelegramIntegrationRead:
    integration = await get_integration_or_404(db, integration_id)
    updates = payload.model_dump(exclude_unset=True)

    next_token = updates.pop("bot_token", None)
    if "name" in updates:
        integration.name = updates["name"]
    if "group_id" in updates:
        integration.group_id = updates["group_id"]
    if "enabled" in updates:
        integration.enabled = updates["enabled"]

    token_for_duplicate = None
    if next_token:
        token_for_duplicate = next_token
        integration.bot_token_encrypted = encrypt_secret(next_token)
    elif integration.bot_token_encrypted and "group_id" in payload.model_dump(exclude_unset=True):
        try:
            token_for_duplicate = decrypt_secret(integration.bot_token_encrypted)
        except ValueError:
            token_for_duplicate = None

    if token_for_duplicate and integration.group_id:
        duplicate = await find_duplicate_telegram_destination(
            db,
            bot_token=token_for_duplicate,
            group_id=integration.group_id,
            exclude_integration_id=integration.id,
        )
        if duplicate is not None:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A Telegram integration with the same bot token and group ID already exists.",
            )

    await db.commit()
    await db.refresh(integration)
    counts = await count_routes_by_integration(db)
    legacy_id = await lowest_telegram_integration_id(db)
    return serialize_integration(
        integration,
        assigned_count=counts.get(integration.id, 0),
        legacy_default_id=legacy_id,
    )


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_telegram_integration(
    integration_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    integration = await get_integration_or_404(db, integration_id)

    route_count = int(
        (
            await db.scalar(
                select(func.count(PaymentAccountTelegramRoute.id)).where(
                    PaymentAccountTelegramRoute.telegram_integration_id == integration_id
                )
            )
        )
        or 0
    )
    if route_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot delete a Telegram integration while Gmail accounts are assigned. "
                "Remove assignments or disable it instead."
            ),
        )

    delivery_count = int(
        (
            await db.scalar(
                select(func.count(TelegramDelivery.id)).where(
                    TelegramDelivery.telegram_integration_id == integration_id
                )
            )
        )
        or 0
    )
    if delivery_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a Telegram integration with delivery history. Disable it instead.",
        )

    await db.delete(integration)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{integration_id}/test-connection", response_model=TelegramActionResponse)
async def test_telegram_integration_connection(
    integration_id: int,
    db: AsyncSession = Depends(get_db),
) -> TelegramActionResponse:
    integration = await get_integration_or_404(db, integration_id)
    result = await test_specific_telegram_integration(db, integration, send_message=False)
    await db.refresh(integration)
    return serialize_action(result.success, result.message, integration)


@router.post("/{integration_id}/send-test-message", response_model=TelegramActionResponse)
async def send_telegram_integration_test_message(
    integration_id: int,
    db: AsyncSession = Depends(get_db),
) -> TelegramActionResponse:
    integration = await get_integration_or_404(db, integration_id)
    result = await test_specific_telegram_integration(db, integration, send_message=True)
    await db.refresh(integration)
    return serialize_action(result.success, result.message, integration)


@router.get("/{integration_id}/payment-accounts", response_model=TelegramIntegrationAssignmentRead)
async def get_telegram_integration_payment_accounts(
    integration_id: int,
    db: AsyncSession = Depends(get_db),
) -> TelegramIntegrationAssignmentRead:
    await get_integration_or_404(db, integration_id)
    accounts = await list_routes_for_integration(db, integration_id)
    if accounts:
        result = await db.execute(
            select(PaymentAccount)
            .options(selectinload(PaymentAccount.provider))
            .where(PaymentAccount.id.in_([account.id for account in accounts]))
            .order_by(PaymentAccount.friendly_name, PaymentAccount.id)
        )
        accounts = list(result.scalars().all())
    return TelegramIntegrationAssignmentRead(
        telegram_integration_id=integration_id,
        payment_accounts=[
            PaymentAccountAssignmentItem(
                id=account.id,
                friendly_name=account.friendly_name,
                gmail_address=account.gmail_address,
                provider_name=account.provider.name,
                enabled=account.enabled,
            )
            for account in accounts
        ],
    )


@router.put("/{integration_id}/payment-accounts", response_model=TelegramIntegrationAssignmentRead)
async def put_telegram_integration_payment_accounts(
    integration_id: int,
    payload: TelegramIntegrationAssignmentUpdate,
    db: AsyncSession = Depends(get_db),
) -> TelegramIntegrationAssignmentRead:
    await get_integration_or_404(db, integration_id)
    try:
        accounts = await replace_integration_payment_accounts(
            db,
            integration_id,
            payload.payment_account_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    await db.commit()

    if accounts:
        result = await db.execute(
            select(PaymentAccount)
            .options(selectinload(PaymentAccount.provider))
            .where(PaymentAccount.id.in_([account.id for account in accounts]))
            .order_by(PaymentAccount.friendly_name, PaymentAccount.id)
        )
        accounts = list(result.scalars().all())

    return TelegramIntegrationAssignmentRead(
        telegram_integration_id=integration_id,
        payment_accounts=[
            PaymentAccountAssignmentItem(
                id=account.id,
                friendly_name=account.friendly_name,
                gmail_address=account.gmail_address,
                provider_name=account.provider.name,
                enabled=account.enabled,
            )
            for account in accounts
        ],
    )
