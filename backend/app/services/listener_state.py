from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin import get_setting
from app.models.app_setting import AppSetting

HEARTBEAT_KEY = "gmail_listener.heartbeat"
HEARTBEAT_AT_KEY = "gmail_listener.heartbeat_at"
LAST_UID_PREFIX = "gmail_listener.last_uid"


def last_uid_key(payment_account_id: int, mailbox: str) -> str:
    return f"{LAST_UID_PREFIX}.{payment_account_id}.{mailbox}"


async def upsert_setting(session: AsyncSession, key: str, value: str) -> None:
    setting = await get_setting(session, key)
    if setting is None:
        session.add(AppSetting(key=key, encrypted_value=value))
    else:
        setting.encrypted_value = value


async def get_last_uid(session: AsyncSession, payment_account_id: int, mailbox: str = "INBOX") -> int:
    setting = await get_setting(session, last_uid_key(payment_account_id, mailbox))
    if setting is None:
        return 0
    try:
        return int(setting.encrypted_value)
    except ValueError:
        return 0


async def set_last_uid(session: AsyncSession, payment_account_id: int, gmail_uid: int, mailbox: str = "INBOX") -> None:
    current_uid = await get_last_uid(session, payment_account_id, mailbox)
    await upsert_setting(session, last_uid_key(payment_account_id, mailbox), str(max(current_uid, gmail_uid)))


async def write_heartbeat(session: AsyncSession, status: str = "alive") -> datetime:
    now = datetime.now(UTC)
    await upsert_setting(session, HEARTBEAT_KEY, status)
    await upsert_setting(session, HEARTBEAT_AT_KEY, now.isoformat())
    return now


async def read_heartbeat(session: AsyncSession) -> tuple[str | None, datetime | None]:
    heartbeat = await get_setting(session, HEARTBEAT_KEY)
    heartbeat_at = await get_setting(session, HEARTBEAT_AT_KEY)

    parsed_at: datetime | None = None
    if heartbeat_at is not None:
        try:
            parsed_at = datetime.fromisoformat(heartbeat_at.encrypted_value)
        except ValueError:
            parsed_at = None

    return heartbeat.encrypted_value if heartbeat else None, parsed_at
