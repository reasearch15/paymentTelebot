import asyncio

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.provider import Provider

DEFAULT_PROVIDERS = (
    ("Chime", "chime"),
    ("Cash App", "cash_app"),
    ("Venmo", "venmo"),
)


async def seed_default_providers() -> None:
    async with AsyncSessionLocal() as session:
        for name, parser_key in DEFAULT_PROVIDERS:
            result = await session.execute(select(Provider).where(Provider.parser_key == parser_key))
            if result.scalar_one_or_none() is None:
                session.add(Provider(name=name, parser_key=parser_key, enabled=True))
        await session.commit()


async def main() -> None:
    await seed_default_providers()
    print("Default providers are present.")


if __name__ == "__main__":
    asyncio.run(main())
