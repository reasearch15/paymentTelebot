from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.core.encryption import decrypt_secret
from app.db.session import get_db
from app.models.telegram_integration import TelegramIntegration
from app.schemas.telegram import TelegramActionResponse, TelegramSettingsResponse, TelegramSettingsUpdate
from app.services.telegram import (
    apply_telegram_settings,
    get_or_create_telegram_integration,
    mask_bot_token,
    test_telegram_integration,
)

router = APIRouter(prefix="/telegram", tags=["telegram"], dependencies=[Depends(require_admin)])


def serialize_settings(integration: TelegramIntegration) -> TelegramSettingsResponse:
    token_mask = None
    if integration.bot_token_encrypted:
        token_mask = mask_bot_token(decrypt_secret(integration.bot_token_encrypted))
    return TelegramSettingsResponse(
        bot_token_masked=token_mask,
        group_id=integration.group_id,
        enabled=integration.enabled,
        connected=integration.last_success_at is not None and integration.last_error is None,
        last_checked_at=integration.last_checked_at,
        last_success_at=integration.last_success_at,
        last_error=integration.last_error,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
    )


def serialize_action(success: bool, message: str, integration: TelegramIntegration) -> TelegramActionResponse:
    return TelegramActionResponse(
        success=success,
        message=message,
        last_checked_at=integration.last_checked_at,
        last_success_at=integration.last_success_at,
        last_error=integration.last_error,
    )


@router.get("/settings", response_model=TelegramSettingsResponse)
async def get_settings(db: AsyncSession = Depends(get_db)) -> TelegramSettingsResponse:
    integration = await get_or_create_telegram_integration(db)
    await db.commit()
    return serialize_settings(integration)


@router.put("/settings", response_model=TelegramSettingsResponse)
async def update_settings(
    payload: TelegramSettingsUpdate,
    db: AsyncSession = Depends(get_db),
) -> TelegramSettingsResponse:
    integration = await get_or_create_telegram_integration(db)
    try:
        apply_telegram_settings(integration, payload.bot_token, payload.group_id, payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(integration)
    return serialize_settings(integration)


@router.post("/test-connection", response_model=TelegramActionResponse)
async def test_connection(db: AsyncSession = Depends(get_db)) -> TelegramActionResponse:
    result = await test_telegram_integration(db, send_message=False)
    integration = await get_or_create_telegram_integration(db)
    return serialize_action(result.success, result.message, integration)


@router.post("/send-test-message", response_model=TelegramActionResponse)
async def send_test_message(db: AsyncSession = Depends(get_db)) -> TelegramActionResponse:
    result = await test_telegram_integration(db, send_message=True)
    integration = await get_or_create_telegram_integration(db)
    return serialize_action(result.success, result.message, integration)
