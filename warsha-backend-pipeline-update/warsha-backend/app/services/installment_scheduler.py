"""
installment_scheduler.py — خدمة خلفية لتحديث حالة الأقساط تلقائياً

تشتغل كل ساعة وتحدث:
  - InvoicePaymentSchedule.status → 'overdue'  لو due_date < today و status = 'pending'
  - Invoice.status               → 'overdue'   لو كل أقساطها المفتوحة متأخرة
  - Invoice.status               → 'partial'   لو بعض أقساطها مسددة وبعضها متأخر

بدون هذا، الأقساط تظل 'pending' في الـ DB حتى لو اجتازت تاريخ الاستحقاق،
والـ get_invoice_due_status() يحتسبها صح لكن الـ status field نفسه يكون مضلل.
"""
import asyncio
import logging
from datetime import date

from sqlalchemy import select, and_, update as sa_update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.models import InvoicePaymentSchedule, Invoice

logger = logging.getLogger("installment_scheduler")


async def _run_once(db: AsyncSession) -> dict:
    """
    تحديثة واحدة — يمكن استدعاؤها من الـ test أو الـ scheduler.
    يرجع dict بإحصاءات ما تم تحديثه.
    """
    today = date.today()

    # عزل المستأجرين: لو اتنادت من طلب ورشة (POST /sales/admin/run-installment-scheduler)
    # الـ UPDATE الجماعي ماكانش بيتفلتر تلقائياً (فلتر الـ tenant على SELECT بس) فكان
    # أدمن ورشة يعدّل أقساط كل الورش. الـ background loop (بدون tenant) يفضل شامل.
    from app.core.database import current_tenant_id as _ctid_sched
    tid = _ctid_sched.get()

    # 1. حدّث status الأقساط التي تجاوزت تاريخ الاستحقاق
    upd = (
        sa_update(InvoicePaymentSchedule)
        .where(
            InvoicePaymentSchedule.due_date < today,
            InvoicePaymentSchedule.status == "pending",
        )
        .values(status="overdue")
    )
    if tid is not None:
        upd = upd.where(InvoicePaymentSchedule.tenant_id == tid)
    r1 = await db.execute(upd)
    updated_installments = r1.rowcount

    # 2. حدّث Invoice.status لكل فاتورة عندها أقساط مفتوحة
    # نفرغ الـ identity map عشان نضمن قراءة بيانات fresh من الـ DB
    await db.run_sync(lambda s: s.expire_all())

    inv_q = select(Invoice).where(
        Invoice.status.in_(["unpaid", "partial", "overdue"])
    )
    if tid is not None:
        inv_q = inv_q.where(Invoice.tenant_id == tid)
    invoices = (await db.execute(inv_q)).scalars().all()

    # expire الـ cache عشان paid_amount يتحمل من الـ DB مش من الـ session القديم
    for inv in invoices:
        await db.refresh(inv)

    updated_invoices = 0
    for inv in invoices:
        # نجلب حالة الأقساط
        sched_r = await db.execute(
            select(
                InvoicePaymentSchedule.status,
                func.count().label("cnt"),
            )
            .where(InvoicePaymentSchedule.invoice_id == inv.id)
            .group_by(InvoicePaymentSchedule.status)
        )
        status_counts = {row.status: row.cnt for row in sched_r}

        if not status_counts:
            # فاتورة بدون خطة أقساط — نحسب من due_date مباشرة
            if inv.due_date and inv.due_date < today and inv.status == "unpaid":
                inv.status = "overdue"
                updated_invoices += 1
            continue

        n_paid    = status_counts.get("paid", 0)
        n_overdue = status_counts.get("overdue", 0)
        n_partial = status_counts.get("partial", 0)
        n_pending = status_counts.get("pending", 0)
        n_total   = sum(status_counts.values())
        # اجلب paid_amount مباشرة من الـ DB عشان نتجنب cache الـ session
        fresh_paid = await db.scalar(
            select(Invoice.paid_amount).where(Invoice.id == inv.id)
        )
        has_payment = fresh_paid is not None and fresh_paid > 0
        new_status = inv.status
        if n_paid == n_total:
            new_status = "paid"
        elif has_payment:
            # أي دفعة = partial (بغض النظر عن باقي الأقساط)
            new_status = "partial"
        elif n_overdue > 0 and n_pending == 0 and n_partial == 0:
            # كل الأقساط المفتوحة متأخرة ومفيش أي دفع
            new_status = "overdue"
        elif n_pending > 0:
            new_status = "unpaid"

        if new_status != inv.status:
            inv.status = new_status
            updated_invoices += 1

    await db.commit()
    return {
        "updated_installments": updated_installments,
        "updated_invoices": updated_invoices,
        "run_date": str(today),
    }


async def run_installment_status_update() -> dict:
    """Entry point للـ scheduler — يفتح session مستقل."""
    async with AsyncSessionLocal() as db:
        try:
            result = await _run_once(db)
            if result["updated_installments"] or result["updated_invoices"]:
                logger.info(
                    "installment_scheduler: updated %d installments, %d invoices",
                    result["updated_installments"],
                    result["updated_invoices"],
                )
            return result
        except Exception as e:
            logger.error("installment_scheduler error: %s", e, exc_info=True)
            return {"error": str(e)}


async def start_installment_scheduler():
    """
    Loop لانهائي يشتغل كـ background task من lifespan.
    يشتغل فوراً عند الـ startup ثم كل ساعة.
    """
    await asyncio.sleep(5)   # انتظر الـ DB يكون جاهز
    while True:
        try:
            await run_installment_status_update()
        except Exception as e:
            logger.error("scheduler loop error: %s", e)
        await asyncio.sleep(3600)   # ساعة كاملة
