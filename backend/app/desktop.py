import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select

from app.api.auth import hash_password
from app.core.database import AsyncSessionLocal, engine
from app.models.models import Base, User


async def init_db(seed_demo: bool = False) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if seed_demo:
        from seed import seed, wipe_all

        await wipe_all()
        await seed()
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        if result.scalar_one_or_none():
            return

        db.add(
            User(
                username="admin",
                password_hash=hash_password("admin123"),
                full_name="مدير النظام",
                role="admin",
                is_active=True,
            )
        )
        await db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Heavy ERP desktop database setup")
    parser.add_argument("--seed-demo", action="store_true", help="Reset and load demo data")
    args = parser.parse_args()

    db_url = str(engine.url)
    if db_url.startswith("sqlite"):
        db_path = db_url.rsplit("///", 1)[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    asyncio.run(init_db(seed_demo=args.seed_demo))


if __name__ == "__main__":
    main()
