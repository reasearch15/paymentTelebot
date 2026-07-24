import asyncio

from app.core.admin import upsert_admin
from app.core.config import settings
from app.db.session import AsyncSessionLocal


async def main() -> None:
    if not settings.admin_email or not settings.admin_password:
        raise SystemExit("ADMIN_EMAIL and ADMIN_PASSWORD must be set.")

    async with AsyncSessionLocal() as session:
        await upsert_admin(session, settings.admin_email, settings.admin_password)

    print(f"Admin account configured for {settings.admin_email.strip().lower()}")


if __name__ == "__main__":
    asyncio.run(main())
