from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from contextvars import ContextVar
from typing import Optional
from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ── Multi-tenancy: tenant_id propagation through ContextVar ──────────
# يُملأ من middleware عند كل request بعد التحقق من JWT.
# يقرأه get_db (لـ Postgres RLS) و tenant_events.py (للحقن التلقائي عند insert).
current_tenant_id: ContextVar[Optional[int]] = ContextVar("current_tenant_id", default=None)

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            tid = current_tenant_id.get()
            if tid is not None and not _is_sqlite:
                # بوستجريس فقط: متغير الجلسة لـ RLS policies
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :t, false)").bindparams(t=str(tid))
                )
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
