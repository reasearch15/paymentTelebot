"""Atomic Gmail ↔ Telegram route assignment helpers.

Assignment changes never dispatch Telegram notifications and never create historical
telegram_deliveries rows. Only future transactions use the committed route set.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment_account import PaymentAccount
from app.models.payment_account_telegram_route import PaymentAccountTelegramRoute
from app.models.telegram_integration import TelegramIntegration


async def replace_integration_payment_accounts(
    session: AsyncSession,
    telegram_integration_id: int,
    payment_account_ids: list[int],
) -> list[PaymentAccount]:
    desired = sorted({int(account_id) for account_id in payment_account_ids})
    if desired:
        found = list(
            (
                await session.scalars(select(PaymentAccount).where(PaymentAccount.id.in_(desired)))
            ).all()
        )
        if len(found) != len(desired):
            missing = sorted(set(desired) - {account.id for account in found})
            raise ValueError(f"Unknown payment_account_ids: {missing}")

    existing = list(
        (
            await session.scalars(
                select(PaymentAccountTelegramRoute).where(
                    PaymentAccountTelegramRoute.telegram_integration_id == telegram_integration_id
                )
            )
        ).all()
    )
    existing_ids = {route.payment_account_id for route in existing}
    desired_set = set(desired)

    to_remove = existing_ids - desired_set
    to_add = desired_set - existing_ids

    if to_remove:
        await session.execute(
            delete(PaymentAccountTelegramRoute).where(
                PaymentAccountTelegramRoute.telegram_integration_id == telegram_integration_id,
                PaymentAccountTelegramRoute.payment_account_id.in_(to_remove),
            )
        )

    for account_id in sorted(to_add):
        session.add(
            PaymentAccountTelegramRoute(
                payment_account_id=account_id,
                telegram_integration_id=telegram_integration_id,
            )
        )

    await session.flush()
    if not desired:
        return []
    result = await session.scalars(
        select(PaymentAccount)
        .where(PaymentAccount.id.in_(desired))
        .order_by(PaymentAccount.friendly_name, PaymentAccount.id)
    )
    return list(result.all())


async def replace_payment_account_telegram_integrations(
    session: AsyncSession,
    payment_account_id: int,
    telegram_integration_ids: list[int],
) -> list[TelegramIntegration]:
    desired = sorted({int(integration_id) for integration_id in telegram_integration_ids})
    if desired:
        found = list(
            (
                await session.scalars(select(TelegramIntegration).where(TelegramIntegration.id.in_(desired)))
            ).all()
        )
        if len(found) != len(desired):
            missing = sorted(set(desired) - {integration.id for integration in found})
            raise ValueError(f"Unknown telegram_integration_ids: {missing}")

    existing = list(
        (
            await session.scalars(
                select(PaymentAccountTelegramRoute).where(
                    PaymentAccountTelegramRoute.payment_account_id == payment_account_id
                )
            )
        ).all()
    )
    existing_ids = {route.telegram_integration_id for route in existing}
    desired_set = set(desired)

    to_remove = existing_ids - desired_set
    to_add = desired_set - existing_ids

    if to_remove:
        await session.execute(
            delete(PaymentAccountTelegramRoute).where(
                PaymentAccountTelegramRoute.payment_account_id == payment_account_id,
                PaymentAccountTelegramRoute.telegram_integration_id.in_(to_remove),
            )
        )

    for integration_id in sorted(to_add):
        session.add(
            PaymentAccountTelegramRoute(
                payment_account_id=payment_account_id,
                telegram_integration_id=integration_id,
            )
        )

    await session.flush()
    if not desired:
        return []
    result = await session.scalars(
        select(TelegramIntegration)
        .where(TelegramIntegration.id.in_(desired))
        .order_by(TelegramIntegration.id)
    )
    return list(result.all())


async def list_routes_for_integration(
    session: AsyncSession,
    telegram_integration_id: int,
) -> list[PaymentAccount]:
    result = await session.scalars(
        select(PaymentAccount)
        .join(
            PaymentAccountTelegramRoute,
            PaymentAccountTelegramRoute.payment_account_id == PaymentAccount.id,
        )
        .where(PaymentAccountTelegramRoute.telegram_integration_id == telegram_integration_id)
        .order_by(PaymentAccount.friendly_name, PaymentAccount.id)
    )
    return list(result.all())


async def list_integrations_for_payment_account(
    session: AsyncSession,
    payment_account_id: int,
) -> list[TelegramIntegration]:
    result = await session.scalars(
        select(TelegramIntegration)
        .join(
            PaymentAccountTelegramRoute,
            PaymentAccountTelegramRoute.telegram_integration_id == TelegramIntegration.id,
        )
        .where(PaymentAccountTelegramRoute.payment_account_id == payment_account_id)
        .order_by(TelegramIntegration.id)
    )
    return list(result.all())
