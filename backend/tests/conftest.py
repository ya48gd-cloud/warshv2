"""
Shared pytest fixtures — SQLite in-memory, full isolation per test.
"""
import os
import uuid
import pytest
import pytest_asyncio
from decimal import Decimal
from datetime import date

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)

import app.core.database as _db_module
_db_module.engine = _engine
_db_module.AsyncSessionLocal = _SessionLocal

from httpx import AsyncClient, ASGITransport
from app.core.database import Base, get_db
from app.main import app
from app.models.models import (
    MaterialCategory, Material, User, Equipment, Worker, Customer
)
from app.api.auth import hash_password, create_token


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(autouse=True)
async def clean_tables():
    """Truncate all tables before each test for isolation."""
    yield
    async with _engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest_asyncio.fixture
async def db():
    async with _SessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client(db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ── Seed fixtures ──────────────────────────────────────────────

@pytest_asyncio.fixture
async def category(db):
    cat = MaterialCategory(name_ar="حديد", name_en="Steel")
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


@pytest_asyncio.fixture
async def material(db, category):
    mat = Material(
        code="MAT-001", name_ar="حديد مقطعي", name_en="Section Steel",
        unit="كجم", unit_cost=Decimal("50.00"),
        stock_qty=Decimal("100.000"), reorder_level=Decimal("20.000"),
        category_id=category.id,
    )
    db.add(mat)
    await db.commit()
    await db.refresh(mat)
    return mat


@pytest_asyncio.fixture
async def admin_user(db):
    user = User(
        username="admin", password_hash=hash_password("admin123"),
        full_name="مدير النظام", role="admin", is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def viewer_user(db):
    user = User(
        username="viewer", password_hash=hash_password("viewer123"),
        full_name="مشاهد", role="viewer", is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def production_user(db):
    user = User(
        username="production", password_hash=hash_password("prod123"),
        full_name="مشرف الإنتاج", role="production", is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_token(admin_user):
    return create_token(admin_user.id, admin_user.username, admin_user.role)


@pytest_asyncio.fixture
async def viewer_token(viewer_user):
    return create_token(viewer_user.id, viewer_user.username, viewer_user.role)


@pytest_asyncio.fixture
async def production_token(production_user):
    return create_token(production_user.id, production_user.username, production_user.role)


@pytest_asyncio.fixture
async def equipment(db):
    eq = Equipment(code="EQ-001", name_ar="خزان ضغط", name_en="Pressure Tank", is_active=True)
    db.add(eq)
    await db.commit()
    await db.refresh(eq)
    return eq


@pytest_asyncio.fixture
async def worker(db):
    w = Worker(
        code="WRK-001", name="أحمد محمد", job_title="ميكانيكي",
        base_weekly_wage=Decimal("1200.00"), daily_wage=Decimal("200.00"), is_active=True,
    )
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return w


@pytest_asyncio.fixture
async def customer(db):
    c = Customer(
        code="CUST-001", name="شركة النيل للتجارة", phone="01012345678",
        credit_limit=Decimal("50000.00"), balance=Decimal("0.00"),
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c
