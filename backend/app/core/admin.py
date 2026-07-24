from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.app_setting import AppSetting

ADMIN_EMAIL_KEY = "admin.email"
ADMIN_PASSWORD_HASH_KEY = "admin.password_hash"


async def get_setting(session: AsyncSession, key: str) -> AppSetting | None:
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    return result.scalar_one_or_none()


async def get_admin_email(session: AsyncSession) -> str | None:
    setting = await get_setting(session, ADMIN_EMAIL_KEY)
    return setting.encrypted_value if setting else None


async def upsert_admin(session: AsyncSession, email: str, password: str) -> None:
    normalized_email = email.strip().lower()
    email_setting = await get_setting(session, ADMIN_EMAIL_KEY)
    password_setting = await get_setting(session, ADMIN_PASSWORD_HASH_KEY)

    if email_setting is None:
        session.add(AppSetting(key=ADMIN_EMAIL_KEY, encrypted_value=normalized_email))
    else:
        email_setting.encrypted_value = normalized_email

    hashed_password = hash_password(password)
    if password_setting is None:
        session.add(AppSetting(key=ADMIN_PASSWORD_HASH_KEY, encrypted_value=hashed_password))
    else:
        password_setting.encrypted_value = hashed_password

    await session.commit()


async def authenticate_admin(session: AsyncSession, email: str, password: str) -> str | None:
    email_setting = await get_setting(session, ADMIN_EMAIL_KEY)
    password_setting = await get_setting(session, ADMIN_PASSWORD_HASH_KEY)

    if email_setting is None or password_setting is None:
        return None

    normalized_email = email.strip().lower()
    if normalized_email != email_setting.encrypted_value:
        return None

    if not verify_password(password, password_setting.encrypted_value):
        return None

    return email_setting.encrypted_value
