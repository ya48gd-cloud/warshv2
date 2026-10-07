"""
سياسة الاحتفاظ بسجل التدقيق (Retention).

- الافتراضي من البيئة AUDIT_DEFAULT_RETENTION_DAYS (730 يوم؛ 0 = احتفاظ دائم).
- كل ورشة تقدر تعمل override عبر audit_settings (0 أو 90..3650).
- purge_expired(): الحذف الوحيد المسموح لصفوف التدقيق، بـ Core delete وبشرط tenant_id صريح
  على كل statement (الـ jobs ملهاش ContextVar للـ tenant)، وبيسجّل صف 'purge' لكل ورشة اتمسح منها.
- حلقة يومية تبدأ من lifespan (لا تشتغل في الاختبارات لأن ASGITransport لا يشغّل lifespan).
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, distinct, insert, select

from app.core.database import AsyncSessionLocal
from app.models.models import AuditLog, AuditSetting
from app.services.audit import build_row, utcnow_naive

logger = logging.getLogger("warsha.audit")

MIN_DAYS = 90
MAX_DAYS = 3650
BATCH = 5000


def default_days() -> int:
    try:
        v = int(os.getenv("AUDIT_DEFAULT_RETENTION_DAYS", "730"))
    except ValueError:
        v = 730
    return v if v == 0 or v >= 1 else 730


def valid_retention(value: Optional[int]) -> bool:
    """None (إزالة الـ override) أو 0 (دائم) أو 90..3650."""
    return value is None or value == 0 or MIN_DAYS <= value <= MAX_DAYS


def effective_days(override: Optional[int]) -> int:
    return override if override is not None else default_days()


def settings_payload(override: Optional[int]) -> dict:
    return {
        "retention_days": override,
        "custom": override is not None,
        "days": effective_days(override),
        "default_days": default_days(),
        "min_days": MIN_DAYS,
        "max_days": MAX_DAYS,
    }


async def purge_expired(now: Optional[datetime] = None, session_factory=None) -> dict:
    """يمسح صفوف التدقيق الأقدم من مدة الاحتفاظ لكل ورشة. يرجّع {tenant_id: عدد المحذوف}."""
    now = now or utcnow_naive()
    factory = session_factory or AsyncSessionLocal
    t = AuditLog.__table__
    result: dict = {}
    async with factory() as session:
        overrides = {
            r.tenant_id: r.retention_days
            for r in (await session.execute(
                select(AuditSetting).execution_options(skip_tenant_filter=True))).scalars().all()
        }
        tenant_ids = [r[0] for r in (await session.execute(select(distinct(t.c.tenant_id)))).all()]
        for tid in tenant_ids:
            days = effective_days(overrides.get(tid))
            if days <= 0:
                continue
            cutoff = now - timedelta(days=days)
            deleted = 0
            while True:
                sub = (select(t.c.id)
                       .where(t.c.tenant_id == tid, t.c.created_at < cutoff)
                       .limit(BATCH))
                res = await session.execute(
                    delete(t).where(t.c.tenant_id == tid, t.c.id.in_(sub)))
                n = res.rowcount or 0
                await session.commit()
                deleted += n
                if n < BATCH:
                    break
            if deleted:
                await session.execute(insert(t), [build_row(
                    tenant_id=tid, action="purge", entity_type=None,
                    summary="تنظيف سجل التدقيق حسب مدة الاحتفاظ",
                    extra={"deleted": deleted, "cutoff": cutoff.isoformat(),
                           "retention_days": days},
                    use_context=False)])
                await session.commit()
                result[tid] = deleted
    return result


async def start_audit_retention_scheduler():
    """حلقة خلفية: تنظيف يومي. أي خطأ يُسجَّل ولا يوقف الحلقة."""
    await asyncio.sleep(60)
    while True:
        try:
            res = await purge_expired()
            if res:
                logger.info("audit retention purge: %s", res)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("audit retention purge failed", exc_info=True)
        await asyncio.sleep(86400)
