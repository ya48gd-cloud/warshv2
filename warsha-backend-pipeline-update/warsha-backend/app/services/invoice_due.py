"""
invoice_due.py — مصدر الحقيقة الوحيد لحالة استحقاق الفاتورة

المبدأ: نفس فلسفة inventory_ledger.py — أي كود يريد يعرف "هل الفاتورة دي متأخرة؟"
ينادي get_invoice_due_status() مش يقرأ Invoice.due_date مباشرة.

السبب: لو الفاتورة عندها خطة أقساط (InvoicePaymentSchedule)، الحقيقة الفعلية
في جدول الأقساط مش في invoice.due_date الثابت.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Invoice, InvoicePaymentSchedule


@dataclass
class InvoiceDueStatus:
    """ناتج الاستعلام عن حالة استحقاق فاتورة واحدة."""
    invoice_id:       int
    has_schedule:     bool          # عندها خطة أقساط؟
    is_overdue:       bool          # في تأخير فعلي؟
    days_overdue:     int           # أيام التأخير (0 لو مش متأخرة)
    overdue_amount:   Decimal       # المبلغ المتأخر فعليًا (من أقساط مستحقة)
    upcoming_amount:  Decimal       # المبلغ القادم (من أقساط لسه ماجاش استحقاقها)
    next_due_date:    Optional[date]  # أقرب تاريخ استحقاق لقسط مفتوح
    effective_due_date: date          # التاريخ الفعال لاستخدامه في Aging buckets


async def get_invoice_due_status(
    db: AsyncSession,
    invoice: Invoice,
    today: Optional[date] = None,
) -> InvoiceDueStatus:
    """
    احسب الحالة الفعلية لاستحقاق الفاتورة.

    - لو مفيش خطة أقساط: يعمل زي الكود القديم (يعتمد على invoice.due_date).
    - لو فيه خطة: يحسب من InvoicePaymentSchedule مباشرة.
    """
    if today is None:
        today = date.today()

    # اجلب أقساط الفاتورة (لو موجودة)
    result = await db.execute(
        select(InvoicePaymentSchedule)
        .where(
            InvoicePaymentSchedule.invoice_id == invoice.id,
            InvoicePaymentSchedule.status.in_(["pending", "partial", "overdue"]),
        )
        .order_by(InvoicePaymentSchedule.due_date, InvoicePaymentSchedule.installment_no)
    )
    open_installments = result.scalars().all()

    if not open_installments:
        # ─── لا توجد خطة أقساط (أو كل الأقساط متسددة) ───
        due = invoice.due_date or invoice.date
        days_overdue = (today - due).days if due else 0
        remaining = invoice.total - invoice.paid_amount
        # إصلاح: فاتورة متسددة بالكامل (remaining <= 0) بعد تاريخ استحقاقها كانت
        # بترجع is_overdue=True بمبلغ متأخر صفر/سالب — تنبيهات وهمية.
        if remaining <= 0:
            remaining = Decimal("0")
            days_overdue = min(days_overdue, 0)

        return InvoiceDueStatus(
            invoice_id=invoice.id,
            has_schedule=False,
            is_overdue=days_overdue > 0,
            days_overdue=max(0, days_overdue),
            overdue_amount=remaining if days_overdue > 0 else Decimal("0"),
            upcoming_amount=remaining if days_overdue <= 0 else Decimal("0"),
            next_due_date=due,
            effective_due_date=due or today,
        )

    # ─── توجد خطة أقساط — احسب من الأقساط نفسها ───
    overdue_installments  = [i for i in open_installments if i.due_date < today]
    upcoming_installments = [i for i in open_installments if i.due_date >= today]

    overdue_amount = sum(
        (ins.amount_due - ins.paid_amount) for ins in overdue_installments
    )
    upcoming_amount = sum(
        (ins.amount_due - ins.paid_amount) for ins in upcoming_installments
    )

    is_overdue = len(overdue_installments) > 0 and overdue_amount > Decimal("0")

    # أقدم قسط متأخر → يُحدد عمر التأخير
    if overdue_installments:
        oldest_overdue = overdue_installments[0]
        days_overdue   = (today - oldest_overdue.due_date).days
        effective_due  = oldest_overdue.due_date
    else:
        days_overdue  = 0
        effective_due = upcoming_installments[0].due_date if upcoming_installments else today

    # أقرب قسط مفتوح (متأخر أو قادم)
    next_due = open_installments[0].due_date if open_installments else None

    return InvoiceDueStatus(
        invoice_id=invoice.id,
        has_schedule=True,
        is_overdue=is_overdue,
        days_overdue=days_overdue,
        overdue_amount=Decimal(str(overdue_amount)),
        upcoming_amount=Decimal(str(upcoming_amount)),
        next_due_date=next_due,
        effective_due_date=effective_due,
    )


async def sync_invoice_due_date(db: AsyncSession, invoice: Invoice) -> None:
    """
    شبكة أمان: حدّث invoice.due_date ليعكس أقرب قسط مفتوح.
    يُستدعى بعد أي تعديل على جدول الأقساط.
    لا يُعوَّل عليه كمصدر حقيقة — get_invoice_due_status() هو الأصل.
    """
    result = await db.execute(
        select(InvoicePaymentSchedule.due_date)
        .where(
            InvoicePaymentSchedule.invoice_id == invoice.id,
            InvoicePaymentSchedule.status.in_(["pending", "partial", "overdue"]),
        )
        .order_by(InvoicePaymentSchedule.due_date)
        .limit(1)
    )
    earliest = result.scalar_one_or_none()
    if earliest and invoice.due_date != earliest:
        invoice.due_date = earliest
