"""
conftest.py — البنية التحتية المشتركة لكل الاختبارات
SQLite aiosqlite · override get_db · real JWT tokens · clean tables per test

CRITICAL: DATABASE_URL must be patched via os.environ BEFORE any app imports
so that app/core/config.py picks up the SQLite URL at settings init time.
"""
import os
import asyncio

# ── MUST happen BEFORE any app import ────────────────────────────────
# TEST_DB_PATH lets multiple pytest processes (e.g. several agents/CI shards
# running concurrently) point at their own SQLite file instead of colliding
# on the shared default — falls back to the historical fixed path unchanged.
_TEST_DB_PATH = os.getenv("TEST_DB_PATH", "/tmp/test_warsh.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"
os.environ.setdefault("ERP_UPLOAD_DIR", "/tmp/warsh_test_uploads")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text

# ── App & DB imports (after env patch) ──────────────────────────────
from app.main import app
from app.core.database import Base, get_db, current_tenant_id
from app.api.auth import create_token

# ── Test Database ────────────────────────────────────────────────────
TEST_DB_URL = os.environ["DATABASE_URL"]

test_engine = create_async_engine(
    TEST_DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)


# ── Override get_db ──────────────────────────────────────────────────
async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = override_get_db


# ── Event loop (session-scoped) ──────────────────────────────────────
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Create all tables once per test session ──────────────────────────
@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ── Truncate all tables before each test ────────────────────────────
@pytest_asyncio.fixture(autouse=True)
async def clean_db(create_tables):
    """Delete all rows from all tables in dependency order before each test.
    FK stays OFF throughout: fake JWT tokens use tenant_id=1/2 which must exist
    without triggering FK violations during teardown/setup.
    """
    async with TestSessionLocal() as session:
        await session.execute(text("PRAGMA foreign_keys = OFF"))
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(table.delete())
        await session.commit()

    # Seed: two tenants (matching fake JWT tokens) and one material category each
    async with TestSessionLocal() as session:
        await session.execute(text(
            "INSERT INTO tenants (id, name, slug, plan, is_active) VALUES "
            "(1, 'Tenant 1', 'tenant-1', 'free', 1), "
            "(2, 'Tenant 2', 'tenant-2', 'free', 1)"
        ))
        await session.execute(text(
            "INSERT INTO material_categories (id, tenant_id, name_ar, name_en) VALUES "
            "(1, 1, 'عام', 'General'), "
            "(2, 2, 'عام', 'General')"
        ))
        await session.commit()

    # محطة 10: المخازن الافتراضية الـ8 لكل tenant تجريبي — نفس اللي بيحصل
    # فعليًا عند إنشاء أي tenant حقيقي (desktop.py / signup-internal)
    from app.api.warehouses import seed_default_warehouses
    async with TestSessionLocal() as session:
        await seed_default_warehouses(session, 1)
        await seed_default_warehouses(session, 2)
        await session.commit()


# ── JWT Tokens ───────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def token_t1():
    return create_token(user_id=1, username="admin", role="admin", tenant_id=1)


@pytest.fixture(scope="session")
def token_t2():
    return create_token(user_id=2, username="admin2", role="admin", tenant_id=2)


@pytest.fixture(scope="session")
def headers_t1(token_t1):
    return {"Authorization": f"Bearer {token_t1}"}


@pytest.fixture(scope="session")
def headers_t2(token_t2):
    return {"Authorization": f"Bearer {token_t2}"}


# ── HTTP Clients ─────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


@pytest_asyncio.fixture
async def cli(headers_t1):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=headers_t1,
    ) as c:
        yield c


@pytest_asyncio.fixture
async def cli2(headers_t2):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=headers_t2,
    ) as c:
        yield c
