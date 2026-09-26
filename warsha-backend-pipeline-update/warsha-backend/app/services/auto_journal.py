"""
Auto-Journal Service — قيود محاسبية تلقائية
يُستدعى من sales.py و purchases.py و workers.py
كل فاتورة/دفعة/راتب تُولّد قيداً مزدوجاً في COA تلقائياً
"""
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ChartOfAccount, AccountingEntry, AccountingEntryLine


# ── Helpers ────────────────────────────────────────────────────────────────

async def _get_account_id(db: AsyncSession, code: str, tenant_id: int) -> int | None:
    """جلب ID الحساب بالكود — مع fallback يمنع الكسر لو COA غير مزروع"""
    row = await db.scalar(
        select(ChartOfAccount.id).where(
            ChartOfAccount.code == code,
            ChartOfAccount.tenant_id == tenant_id,
            ChartOfAccount.is_active == True,
        )
    )
    return row


async def _post_entry(
    db: AsyncSession,
    tenant_id: int,
    entry_date: date,
    description: str,
    reference: str,
    source: str,
    source_id: int,
    lines: list[dict],          # [{"account_code": ..., "side": "debit"/"credit", "amount": Decimal}]
):
    """
    ينشئ AccountingEntry مزدوج.
    لو أي حساب غير موجود في COA → يتخطى بصمت (لا يكسر العملية الأصلية)
    """
    resolved = []
    for ln in lines:
        acc_id = ln.get("account_id")
        if acc_id is None:
            acc_id = await _get_account_id(db, ln["account_code"], tenant_id)
        if acc_id is None:
            return  # COA غير مزروع — تخطّ بدون كسر
        resolved.append({"account_id": acc_id, "side": ln["side"], "amount": ln["amount"]})

    # تحقق من التوازن
    debits  = sum(l["amount"] for l in resolved if l["side"] == "debit")
    credits = sum(l["amount"] for l in resolved if l["side"] == "credit")
    if abs(debits - credits) > Decimal("0.01"):
        return  # غير متوازن — تخطّ بدون كسر

    entry = AccountingEntry(
        tenant_id=tenant_id,
        entry_date=entry_date,
        reference=reference,
        description=description,
        source=source,
        source_id=source_id,
        posted=True,  # تلقائي = مرحّل فوراً
        period_year=entry_date.year,
        period_month=entry_date.month,
    )
    db.add(entry)
    await db.flush()

    for ln in resolved:
        db.add(AccountingEntryLine(
            entry_id=entry.id,
            account_id=ln["account_id"],
            side=ln["side"],
            amount=ln["amount"],
        ))
    # لا commit هنا — المستدعي يتحكم في الـ transaction


# ══════════════════════════════════════════════════════════════════════════
# SALES INVOICE
# مدين: حساب العملاء (1210)   دائن: إيرادات المبيعات (4100)
# ══════════════════════════════════════════════════════════════════════════

async def journal_sales_invoice_create(
    db: AsyncSession, tenant_id: int, invoice_id: int,
    invoice_code: str, tax_amount: Decimal, total: Decimal, inv_date: date,
):
    revenue = total - tax_amount
    lines = [
        {"account_code": "1210", "side": "debit",  "amount": total},
        {"account_code": "4100", "side": "credit", "amount": revenue},
    ]
    if tax_amount > 0:
        lines.append({"account_code": "2310", "side": "credit", "amount": tax_amount})
    await _post_entry(
        db, tenant_id, inv_date,
        f"فاتورة بيع: {invoice_code}",
        invoice_code, "sales_invoice", invoice_id,
        lines,
    )


async def journal_sales_invoice_payment(
    db: AsyncSession, tenant_id: int, invoice_id: int,
    invoice_code: str, amount: Decimal, pay_date: date,
):
    """استلام دفعة: مدين الصندوق (1110)، دائن العملاء (1210)"""
    await _post_entry(
        db, tenant_id, pay_date,
        f"استلام دفعة على فاتورة: {invoice_code}",
        invoice_code, "sales_payment", invoice_id,
        [
            {"account_code": "1110", "side": "debit",  "amount": amount},
            {"account_code": "1210", "side": "credit", "amount": amount},
        ]
    )


async def journal_sales_invoice_delete(
    db: AsyncSession, tenant_id: int, invoice_id: int,
    invoice_code: str, tax_amount: Decimal, total: Decimal, inv_date: date,
):
    """عكس قيد فاتورة البيع عند الحذف"""
    revenue = total - tax_amount
    lines = [
        {"account_code": "4100", "side": "debit",  "amount": revenue},
    ]
    if tax_amount > 0:
        lines.append({"account_code": "2310", "side": "debit", "amount": tax_amount})
    lines.append({"account_code": "1210", "side": "credit", "amount": total})
    await _post_entry(
        db, tenant_id, inv_date,
        f"عكس فاتورة بيع: {invoice_code}",
        invoice_code, "sales_invoice_reversal", invoice_id,
        lines,
    )


# ══════════════════════════════════════════════════════════════════════════
# CREDIT NOTE (إشعار دائن)
# مدين: إيرادات المبيعات (4100) + ضريبة المبيعات (2310) إن وُجدت   دائن: العملاء (1210)
# ══════════════════════════════════════════════════════════════════════════

async def journal_credit_note_issue(
    db: AsyncSession, tenant_id: int, cn_id: int,
    cn_code: str, tax_amount: Decimal, total: Decimal, cn_date: date,
):
    revenue = total - tax_amount
    lines = []
    if revenue > 0:
        lines.append({"account_code": "4100", "side": "debit", "amount": revenue})
    if tax_amount > 0:
        lines.append({"account_code": "2310", "side": "debit", "amount": tax_amount})
    lines.append({"account_code": "1210", "side": "credit", "amount": total})
    await _post_entry(
        db, tenant_id, cn_date,
        f"إشعار دائن: {cn_code}",
        cn_code, "credit_note", cn_id,
        lines,
    )


async def journal_credit_note_cancel(
    db: AsyncSession, tenant_id: int, cn_id: int,
    cn_code: str, tax_amount: Decimal, total: Decimal, cn_date: date,
):
    """عكس قيد إشعار الدائن عند الإلغاء — بدل من نعكسه لاحقاً عبر journal_reverse_entry"""
    revenue = total - tax_amount
    lines = [{"account_code": "1210", "side": "debit", "amount": total}]
    if revenue > 0:
        lines.append({"account_code": "4100", "side": "credit", "amount": revenue})
    if tax_amount > 0:
        lines.append({"account_code": "2310", "side": "credit", "amount": tax_amount})
    await _post_entry(
        db, tenant_id, cn_date,
        f"إلغاء إشعار دائن: {cn_code}",
        cn_code, "credit_note_cancel", cn_id,
        lines,
    )


# ══════════════════════════════════════════════════════════════════════════
# PURCHASE INVOICE
# مدين: المخزون (1310)   دائن: حساب الموردين (2110)
# ══════════════════════════════════════════════════════════════════════════

async def journal_purchase_invoice_create(
    db: AsyncSession, tenant_id: int, invoice_id: int,
    invoice_code: str, tax_amount: Decimal, total: Decimal, inv_date: date,
):
    cost = total - tax_amount
    lines = [
        {"account_code": "1310", "side": "debit",  "amount": cost},
    ]
    if tax_amount > 0:
        lines.append({"account_code": "1230", "side": "debit", "amount": tax_amount})
    lines.append({"account_code": "2110", "side": "credit", "amount": total})
    await _post_entry(
        db, tenant_id, inv_date,
        f"فاتورة شراء: {invoice_code}",
        invoice_code, "purchase_invoice", invoice_id,
        lines,
    )


async def journal_purchase_invoice_payment(
    db: AsyncSession, tenant_id: int, invoice_id: int,
    invoice_code: str, amount: Decimal, pay_date: date,
    wht_amount: Decimal = Decimal("0"),
):
    """سداد دفعة للمورد: مدين المورد (2110)، دائن الصندوق (1110) بالصافي، ودائن ضريبة الخصم (2320) إن وُجدت"""
    lines = [
        {"account_code": "2110", "side": "debit",  "amount": amount},
    ]
    if wht_amount > 0:
        lines.append({"account_code": "2320", "side": "credit", "amount": wht_amount})
    lines.append({"account_code": "1110", "side": "credit", "amount": amount - wht_amount})
    await _post_entry(
        db, tenant_id, pay_date,
        f"سداد للمورد على فاتورة: {invoice_code}",
        invoice_code, "purchase_payment", invoice_id,
        lines,
    )


async def journal_purchase_invoice_delete(
    db: AsyncSession, tenant_id: int, invoice_id: int,
    invoice_code: str, tax_amount: Decimal, total: Decimal, inv_date: date,
):
    cost = total - tax_amount
    lines = [
        {"account_code": "2110", "side": "debit",  "amount": total},
        {"account_code": "1310", "side": "credit", "amount": cost},
    ]
    if tax_amount > 0:
        lines.append({"account_code": "1230", "side": "credit", "amount": tax_amount})
    await _post_entry(
        db, tenant_id, inv_date,
        f"عكس فاتورة شراء: {invoice_code}",
        invoice_code, "purchase_invoice_reversal", invoice_id,
        lines,
    )


# ══════════════════════════════════════════════════════════════════════════
# PAYROLL
# مدين: مصاريف رواتب (5200)   دائن: الصندوق (1110)
# ══════════════════════════════════════════════════════════════════════════

async def journal_payroll(
    db: AsyncSession, tenant_id: int, payroll_run_id: int,
    run_code: str, gross_amount: Decimal, net_amount: Decimal, pay_date: date,
    advances_settled: Decimal = Decimal("0"),
):
    """
    دائن الصندوق (1110) بالصافي المدفوع نقداً فقط.
    لو فيه سلف مُسوّاة (advances_settled > 0): القيد بيتمدد لـ3 سطور
    (مدين 5200 بالإجمالي المستحق، دائن 1220 بقيمة السلف المسوّاة، دائن 1110 بالصافي النقدي).
    """
    if advances_settled > 0:
        expense_amount = net_amount + advances_settled
        if expense_amount <= 0:
            return
        lines = [{"account_code": "5200", "side": "debit", "amount": expense_amount}]
        lines.append({"account_code": "1220", "side": "credit", "amount": advances_settled})
        if net_amount > 0:
            lines.append({"account_code": "1110", "side": "credit", "amount": net_amount})
        await _post_entry(
            db, tenant_id, pay_date,
            f"كشف رواتب: {run_code}",
            run_code, "payroll", payroll_run_id,
            lines,
        )
        return

    if net_amount <= 0:
        return
    await _post_entry(
        db, tenant_id, pay_date,
        f"كشف رواتب: {run_code}",
        run_code, "payroll", payroll_run_id,
        [
            {"account_code": "5200", "side": "debit",  "amount": net_amount},
            {"account_code": "1110", "side": "credit", "amount": net_amount},
        ]
    )


# ══════════════════════════════════════════════════════════════════════════
# WORKER ADVANCE (سلفة عامل)
# مدين: سلف عاملين (1220)   دائن: حساب الخزينة (الصندوق/البنك)
# ══════════════════════════════════════════════════════════════════════════

async def journal_worker_advance(
    db: AsyncSession, tenant_id: int, advance_id: int,
    worker_name: str, amount: Decimal, treasury_account_id: int, adv_date: date,
):
    if amount <= 0:
        return
    await _post_entry(
        db, tenant_id, adv_date,
        f"سلفة عامل: {worker_name}",
        f"ADV-{advance_id}", "worker_advance", advance_id,
        [
            {"account_code": "1220", "side": "debit",  "amount": amount},
            {"account_id": treasury_account_id, "side": "credit", "amount": amount},
        ]
    )


# ══════════════════════════════════════════════════════════════════════════
# CUSTOMER ADVANCE PAYMENT (دفعة مقدمة من عميل)
# مدين: حساب الخزينة (الصندوق/البنك)   دائن: العملاء (1210)
# ══════════════════════════════════════════════════════════════════════════

async def journal_customer_advance(
    db: AsyncSession, tenant_id: int, order_id: int,
    customer_name: str, amount: Decimal, treasury_account_id: int, pay_date: date,
):
    if amount <= 0:
        return
    await _post_entry(
        db, tenant_id, pay_date,
        f"دفعة مقدمة من العميل: {customer_name}",
        f"ADVPAY-{order_id}", "customer_advance", order_id,
        [
            {"account_id": treasury_account_id, "side": "debit",  "amount": amount},
            {"account_code": "1210", "side": "credit", "amount": amount},
        ]
    )


# ══════════════════════════════════════════════════════════════════════════
# TREASURY MANUAL TRANSACTION (حركة خزينة يدوية)
# in: مدين حساب الخزينة / دائن حساب الفئة
# out: مدين حساب الفئة / دائن حساب الخزينة
# ══════════════════════════════════════════════════════════════════════════

async def journal_treasury_manual(
    db: AsyncSession, tenant_id: int, txn_id: int,
    description: str, amount: Decimal, direction: str,
    treasury_account_id: int, category_account_id: int, txn_date: date,
):
    if amount <= 0 or not treasury_account_id or not category_account_id:
        return
    if direction == "in":
        lines = [
            {"account_id": treasury_account_id, "side": "debit",  "amount": amount},
            {"account_id": category_account_id, "side": "credit", "amount": amount},
        ]
    else:
        lines = [
            {"account_id": category_account_id, "side": "debit",  "amount": amount},
            {"account_id": treasury_account_id, "side": "credit", "amount": amount},
        ]
    await _post_entry(
        db, tenant_id, txn_date,
        description,
        f"TRX-{txn_id}", "treasury_manual", txn_id,
        lines,
    )


async def journal_treasury_transfer(
    db: AsyncSession, tenant_id: int, txn_id: int,
    description: str, amount: Decimal,
    from_account_id: int, to_account_id: int, txn_date: date,
):
    """تحويل بين خزينتين بحسابين مختلفين في الحسابات (مثلاً صندوق -> بنك)"""
    if amount <= 0 or not from_account_id or not to_account_id or from_account_id == to_account_id:
        return
    await _post_entry(
        db, tenant_id, txn_date,
        description,
        f"TRX-{txn_id}", "treasury_transfer", txn_id,
        [
            {"account_id": to_account_id, "side": "debit",  "amount": amount},
            {"account_id": from_account_id, "side": "credit", "amount": amount},
        ]
    )


async def journal_reverse_entry(db: AsyncSession, tenant_id: int, source: str, source_id: int, reason: str):
    """
    يعكس آخر قيد مرحَّل بنفس (source, source_id) — مدين/دائن معكوسين، بدون حذف القيد الأصلي.
    يُستخدم عند ارتداد شيك كان معاه قيد محاسبي فعلاً.
    """
    entry = (await db.execute(
        select(AccountingEntry).where(
            AccountingEntry.tenant_id == tenant_id,
            AccountingEntry.source == source,
            AccountingEntry.source_id == source_id,
        ).order_by(AccountingEntry.id.desc())
    )).scalars().first()
    if entry is None:
        return
    lines = (await db.execute(
        select(AccountingEntryLine).where(AccountingEntryLine.entry_id == entry.id)
    )).scalars().all()
    if not lines:
        return
    reversed_lines = [
        {"account_id": ln.account_id,
         "side": "credit" if ln.side == "debit" else "debit",
         "amount": ln.amount}
        for ln in lines
    ]
    await _post_entry(
        db, tenant_id, entry.entry_date,
        f"عكس: {entry.description} ({reason})",
        entry.reference, f"{source}_reversal", source_id,
        reversed_lines,
    )


# ══════════════════════════════════════════════════════════════════════════
# WORK ORDER COST (مواد صُرفت للإنتاج)
# مدين: WIP (1320)   دائن: مخزون خام (1310)
# ══════════════════════════════════════════════════════════════════════════

async def journal_material_issue(
    db: AsyncSession, tenant_id: int, work_order_id: int,
    wo_code: str, amount: Decimal, issue_date: date,
):
    if amount <= 0:
        return
    await _post_entry(
        db, tenant_id, issue_date,
        f"صرف مواد لأمر إنتاج: {wo_code}",
        wo_code, "material_issue", work_order_id,
        [
            {"account_code": "1320", "side": "debit",  "amount": amount},
            {"account_code": "1310", "side": "credit", "amount": amount},
        ]
    )


async def journal_labor_cost(
    db: AsyncSession, tenant_id: int, work_order_id: int,
    wo_code: str, amount: Decimal, cost_date: date,
):
    """رسملة تكلفة العمالة الفعلية في WIP — مدين WIP (1320)، دائن تكلفة العمالة المباشرة (5200)"""
    if amount <= 0:
        return
    await _post_entry(
        db, tenant_id, cost_date,
        f"تكلفة عمالة لأمر إنتاج: {wo_code}",
        wo_code, "labor_cost", work_order_id,
        [
            {"account_code": "1320", "side": "debit",  "amount": amount},
            {"account_code": "5200", "side": "credit", "amount": amount},
        ]
    )


async def journal_wo_complete(
    db: AsyncSession, tenant_id: int, work_order_id: int,
    wo_code: str, actual_cost: Decimal, complete_date: date,
):
    """إغلاق WO: تحويل WIP → مخزون تام الصنع"""
    if actual_cost <= 0:
        return
    await _post_entry(
        db, tenant_id, complete_date,
        f"إغلاق أمر إنتاج: {wo_code}",
        wo_code, "work_order_complete", work_order_id,
        [
            {"account_code": "1330", "side": "debit",  "amount": actual_cost},
            {"account_code": "1320", "side": "credit", "amount": actual_cost},
        ]
    )


# ══════════════════════════════════════════════════════════════════════════
# SCRAP (هالك)
# مدين: مصاريف هالك وإتلاف (5600)   دائن: مخزون خام (1310)
# ══════════════════════════════════════════════════════════════════════════

async def journal_scrap(
    db: AsyncSession, tenant_id: int, scrap_id: int,
    wo_code: str, amount: Decimal, scrap_date: date,
):
    if amount <= 0:
        return
    await _post_entry(
        db, tenant_id, scrap_date,
        f"هالك في أمر إنتاج: {wo_code}",
        wo_code, "scrap", scrap_id,
        [
            {"account_code": "5600", "side": "debit",  "amount": amount},
            {"account_code": "1310", "side": "credit", "amount": amount},
        ]
    )


# ══════════════════════════════════════════════════════════════════════════
# SCRAP SALE (بيع الهالك)
# مدين: الصندوق (1110)   دائن: إيرادات بيع الهالك (4400)
# ══════════════════════════════════════════════════════════════════════════

async def journal_scrap_sale(
    db: AsyncSession, tenant_id: int, sale_id: int,
    sale_code: str, amount: Decimal, sale_date: date,
):
    if amount <= 0:
        return
    await _post_entry(
        db, tenant_id, sale_date,
        f"بيع هالك: {sale_code}",
        sale_code, "scrap_sale", sale_id,
        [
            {"account_code": "1110", "side": "debit",  "amount": amount},
            {"account_code": "4400", "side": "credit", "amount": amount},
        ]
    )


# ══════════════════════════════════════════════════════════════════════════
# محطة 22: بند تكلفة يدوي على أمر إنتاج (Cost Line) — رسملة في WIP (1320)
# قبل كده كان بند التكلفة اليدوي (POST /accounting/cost-lines) بيترحّل لجدول
# JournalEntry القديم بس، وده غير مرئي خالص في ميزان المراجعة/قوائم الدخل
# (اللي بتقرأ من AccountingEntry الجديد فقط). الدالة دي بتوحّد المسار مع
# باقي القيود التلقائية في النظام.
# ══════════════════════════════════════════════════════════════════════════

_COST_LINE_CREDIT_ACCOUNT = {
    "material": "1310",  # مخزون خام
    "labor":    "2210",  # الأجور المستحقة
    "overhead": "5300",  # المصروفات الصناعية الغير مباشرة
}


async def journal_cost_line(
    db: AsyncSession, tenant_id: int, work_order_id: int,
    wo_code: str, cost_type: str, amount: Decimal, entry_date: date,
):
    if amount <= 0:
        return
    credit_code = _COST_LINE_CREDIT_ACCOUNT.get(cost_type, "1310")
    await _post_entry(
        db, tenant_id, entry_date,
        f"بند تكلفة ({cost_type}) لأمر إنتاج: {wo_code}",
        wo_code, "cost_line", work_order_id,
        [
            {"account_code": "1320",      "side": "debit",  "amount": amount},
            {"account_code": credit_code, "side": "credit", "amount": amount},
        ]
    )


async def journal_cost_line_delete(
    db: AsyncSession, tenant_id: int, work_order_id: int,
    wo_code: str, cost_type: str, amount: Decimal, entry_date: date,
):
    """
    عكس قيد بند تكلفة محدد عند حذفه — مدين/دائن معكوسين لنفس البند بالظبط
    (مش journal_reverse_entry العام، لأن أمر الإنتاج ممكن يكون عليه أكتر من
    بند تكلفة، وده كان هيعكس آخر قيد بالغلط بدل البند اللي اتحذف فعليًا).
    """
    if amount <= 0:
        return
    credit_code = _COST_LINE_CREDIT_ACCOUNT.get(cost_type, "1310")
    await _post_entry(
        db, tenant_id, entry_date,
        f"عكس بند تكلفة ({cost_type}) لأمر إنتاج: {wo_code}",
        wo_code, "cost_line_reversal", work_order_id,
        [
            {"account_code": "1320",      "side": "credit", "amount": amount},
            {"account_code": credit_code, "side": "debit",  "amount": amount},
        ]
    )


# ══════════════════════════════════════════════════════════════════════════
# محطة 22 (الروادماب الأصلي): قيد COGS عند التسليم الفعلي
# مدين: تكلفة البضاعة المباعة (5100)   دائن: مخزون تام الصنع (1330)
#
# ملاحظة اختيار الحساب: 5100 كانت موجودة في دليل الحسابات من الأول باسم
# "تكلفة المواد المباشرة" ومش مُستخدَمة في أي قيد فعلي في أي مكان تاني —
# بنعيد استخدامها هنا كحساب الـCOGS الفعلي (المفهومين متقاربين جدًا في
# محاسبة التصنيع بالأمر: تكلفة FG المتراكمة، من مواد وعمالة وتحميل، هي
# نفسها اللي بتتحول لـCOGS وقت البيع) بدل ما نضيف حساب جديد ومكرر معناه.
# ══════════════════════════════════════════════════════════════════════════

async def journal_cogs(
    db: AsyncSession, tenant_id: int, delivery_order_id: int,
    order_code: str, amount: Decimal, delivery_date: date,
):
    if amount <= 0:
        return
    await _post_entry(
        db, tenant_id, delivery_date,
        f"تكلفة بضاعة مباعة — تسليم: {order_code}",
        order_code, "cogs", delivery_order_id,
        [
            {"account_code": "5100", "side": "debit",  "amount": amount},
            {"account_code": "1330", "side": "credit", "amount": amount},
        ]
    )
