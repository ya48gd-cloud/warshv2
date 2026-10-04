"""
إشعار دائن — Credit Notes API
إنشاء من فاتورة أو مستقل → إصدار → طباعة
يُخفّض رصيد العميل عند الإصدار
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import (
    CreditNote, CreditNoteLine, Customer, Invoice, InvoiceLine, WorkOrder, Material,
)

from fastapi import Request as _Request

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def _block_viewer_writes(request: _Request, user: dict = Depends(require_any)) -> dict:
    """إصلاح ثغرة صلاحيات: مستخدم "مشاهد" (viewer) كان يقدر ينشئ/يصدر/يلغي
    إشعارات دائنة (بتأثر على رصيد العميل والمخزون ودفتر الأستاذ) بطلب API مباشر."""
    if request.method.upper() not in _READ_METHODS and user.get("role") == "viewer":
        raise HTTPException(403, "صلاحية 'viewer' (مشاهد) لا تسمح بالتعديل")
    return user


router = APIRouter(
    prefix="/credit-notes",
    tags=["credit_notes"],
    dependencies=[Depends(require_any), Depends(_block_viewer_writes)],
)
print_router = APIRouter(prefix="/credit-notes", tags=["credit_notes_print"], dependencies=[Depends(require_any)])


# ── Schemas ───────────────────────────────────────────────────────────

class CNLineIn(BaseModel):
    invoice_line_id: Optional[int] = None
    description:  str
    qty:          Decimal = Decimal("1")
    unit:         str = "قطعة"
    unit_price:   Decimal
    sort_order:   int = 0


class CNIn(BaseModel):
    customer_id:  int
    invoice_id:   Optional[int] = None
    date:         date
    reason:       Optional[str] = None
    return_type:  str = "partial"
    # مرتجع بضاعة فعلي (Sales Return) — يرجّع رصيد المخزون ويعكس تكلفة
    # البضاعة المباعة عند الإصدار، بخلاف إشعار دائن تسوية سعر/خطأ فاتورة.
    is_return:    bool = False
    tax_pct:      Decimal = Decimal("0")
    discount_amt: Decimal = Decimal("0")
    notes:        Optional[str] = None
    lines:        List[CNLineIn] = []


# ── Helpers ───────────────────────────────────────────────────────────

_VALID_RETURN_TYPES = {"full", "partial"}


def _validate_cn_input(data: "CNIn") -> tuple:
    """تحقق مدخلات الإشعار ويرجّع (subtotal, tax_amount, total).
    قبل الإصلاح: نسبة ضريبة سالبة/فوق 100، خصم سالب أو أكبر من المجموع،
    أو return_type عشوائي كانوا بيعدّوا — وإجمالي صفر/سالب كان بيتسجّل
    ويزوّد رصيد العميل بدل ما يخفّضه عند الإصدار."""
    if not data.lines:
        raise HTTPException(400, "أضف بنداً واحداً على الأقل")
    if data.return_type not in _VALID_RETURN_TYPES:
        raise HTTPException(400, f"نوع المرتجع غير صالح. القيم المسموحة: {sorted(_VALID_RETURN_TYPES)}")
    for l in data.lines:
        if l.qty is None or l.qty <= 0:
            raise HTTPException(400, "كمية كل بند يجب أن تكون أكبر من صفر")
        if l.unit_price is None or l.unit_price < 0:
            raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
        if l.invoice_line_id is not None and data.invoice_id is None:
            raise HTTPException(400, "لا يمكن ربط بند ببند فاتورة من غير تحديد الفاتورة المرجعية")
    tax_pct = Decimal(str(data.tax_pct or 0))
    disc = Decimal(str(data.discount_amt or 0))
    if tax_pct < 0 or tax_pct > 100:
        raise HTTPException(400, "نسبة الضريبة يجب أن تكون بين 0 و100")
    subtotal = sum((Decimal(str(l.qty)) * Decimal(str(l.unit_price)) for l in data.lines), Decimal("0"))
    if disc < 0 or disc > subtotal:
        raise HTTPException(400, "الخصم يجب أن يكون بين صفر ومجموع البنود")
    tax_amount = subtotal * tax_pct / 100
    total = subtotal - disc + tax_amount
    if total <= 0:
        raise HTTPException(400, "إجمالي الإشعار الدائن يجب أن يكون أكبر من صفر")
    return subtotal, tax_amount, total


async def _check_invoice_credit_room(
    db: AsyncSession, invoice_id: int, lines, total: Decimal, exclude_cn_id: Optional[int] = None,
) -> None:
    """إصلاح ثغرة تحقق (over-return): مفيش أي حد كان بيمنع إصدار إشعارات
    دائنة على نفس الفاتورة بمجموع أكبر من قيمتها، أو إرجاع كمية أكبر من
    المباعة فعلاً في بند الفاتورة. بنقارن بالإشعارات *الصادرة* بس (المسودات
    ممكن تتلغي/تتحذف)، وبنعيد الفحص وقت الإصدار نفسه."""
    inv = await db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "الفاتورة المرجعية غير موجودة")
    q = select(func.coalesce(func.sum(CreditNote.total), 0)).where(
        CreditNote.invoice_id == invoice_id, CreditNote.status == "issued")
    if exclude_cn_id is not None:
        q = q.where(CreditNote.id != exclude_cn_id)
    issued_total = Decimal(str(await db.scalar(q) or 0))
    if issued_total + Decimal(str(total)) > Decimal(str(inv.total)) + Decimal("0.01"):
        raise HTTPException(
            400,
            f"إجمالي الإشعارات الدائنة على الفاتورة {inv.code} ({float(issued_total + Decimal(str(total))):,.2f}) "
            f"سيتجاوز قيمة الفاتورة ({float(inv.total):,.2f})",
        )
    per_line: dict = {}
    for l in lines or []:
        if l.invoice_line_id is None:
            continue
        per_line[l.invoice_line_id] = per_line.get(l.invoice_line_id, Decimal("0")) + Decimal(str(l.qty))
    for lid, qty in per_line.items():
        il = await db.get(InvoiceLine, lid)
        if not il or il.invoice_id != invoice_id:
            raise HTTPException(400, f"بند الفاتورة #{lid} لا يخص الفاتورة المرجعية")
        lq = (select(func.coalesce(func.sum(CreditNoteLine.qty), 0))
              .join(CreditNote, CreditNote.id == CreditNoteLine.credit_note_id)
              .where(CreditNoteLine.invoice_line_id == lid, CreditNote.status == "issued"))
        if exclude_cn_id is not None:
            lq = lq.where(CreditNote.id != exclude_cn_id)
        already = Decimal(str(await db.scalar(lq) or 0))
        if already + qty > Decimal(str(il.qty)):
            raise HTTPException(
                400,
                f"الكمية المرتجعة للبند '{il.description}' ({float(already + qty):g}) "
                f"أكبر من الكمية المباعة ({float(il.qty):g})",
            )


async def _fg_net_sold_qty(db: AsyncSession, fg_mat_id: int) -> Decimal:
    """صافي الكمية اللي خرجت فعلاً من مخزون FG لمادة معيّنة بسبب بيع
    (فاتورة أمر إنتاج / أمر تسليم) ولسه مارجعتش بمرتجع — ده الحد الأقصى
    اللي ينفع يرجع للمخزون بمرتجع جديد (منع مخزون وهمي)."""
    from app.models.models import StockMovement as _SM

    async def _sum(mtype, types):
        v = await db.scalar(
            select(func.coalesce(func.sum(_SM.qty), 0)).where(
                _SM.material_id == fg_mat_id, _SM.movement_type == mtype,
                _SM.source_doc_type.in_(types),
            )
        )
        return Decimal(str(v or 0))

    sold = await _sum("out", ("sales_invoice", "delivery", "credit_note_cancel"))
    back = await _sum("in", ("credit_note_return", "sales_invoice_delete"))
    return max(sold - back, Decimal("0"))


async def _restock_qty_for_wo_invoice(db: AsyncSession, cn: CreditNote, inv: Invoice) -> Decimal:
    """
    لو الفاتورة صادرة مباشرة من أمر إنتاج (work_order_to_invoice)، بترجّع
    الكمية اللي المفروض ترجع لمخزون تام الصنع (FG-{wo.code}) — مجموع كميات
    بنود الإشعار المرتبطة ببند الفاتورة، بحد أقصى الكمية الأصلية المنتَجة.
    لأي حالة تانية (فاتورة مش من أمر إنتاج، أو مفيش رصيد FG أصلاً) بترجّع صفر —
    مفيش رابط مخزون معروف للمنتج المُباع فنسيبه من غير أي أثر مخزني (سلوك
    موثّق، مش خطأ).
    """
    if not inv.work_order_id:
        return Decimal("0")
    wo = await db.get(WorkOrder, inv.work_order_id)
    if not wo:
        return Decimal("0")
    qty = sum((Decimal(str(l.qty)) for l in (cn.lines or []) if l.invoice_line_id), Decimal("0"))
    if qty <= 0:
        return Decimal("0")
    return min(qty, wo.quantity or qty)




async def _next_code(db: AsyncSession) -> str:
    count = await db.scalar(select(func.count()).select_from(CreditNote))
    n = (count or 0) + 1
    while True:
        code = f"CN-{n:04d}"
        # إصلاح: CreditNote.code عليه UNIQUE على مستوى الجدول كله (مش لكل tenant)،
        # والفحص كان بيتفلتر بالـ tenant تلقائيًا — فورشة تانية بتولّد CN-0001
        # الموجود عند ورشة أولى وتقع في IntegrityError (500). نفحص عبر كل الورش.
        exists = await db.scalar(
            select(CreditNote.id).where(CreditNote.code == code)
            .execution_options(skip_tenant_filter=True)
        )
        if not exists:
            return code
        n += 1


def _cn_dict(c: CreditNote) -> dict:
    status_ar = {"draft": "مسودة", "issued": "صادر", "applied": "مُطبَّق"}
    return {
        "id": c.id,
        "code": c.code,
        "customer_id": c.customer_id,
        "customer_name": c.customer.name if c.customer else "",
        "invoice_id": c.invoice_id,
        "invoice_code": c.invoice.code if c.invoice else None,
        "date": str(c.date),
        "reason": c.reason,
        "return_type": c.return_type,
        "is_return": bool(c.is_return),
        "subtotal": float(c.subtotal),
        "tax_pct": float(c.tax_pct),
        "tax_amount": float(c.tax_amount),
        "discount_amt": float(c.discount_amt),
        "total": float(c.total),
        "status": c.status,
        "status_ar": status_ar.get(c.status, c.status),
        "notes": c.notes,
        "issued_at": c.issued_at.isoformat() if c.issued_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "lines": [
            {
                "id": l.id,
                "invoice_line_id": l.invoice_line_id,
                "description": l.description,
                "qty": float(l.qty),
                "unit": l.unit,
                "unit_price": float(l.unit_price),
                "total_price": float(l.total_price),
                "sort_order": l.sort_order,
            }
            for l in (c.lines or [])
        ],
    }


# ══════════════════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════════════════

@router.get("")
async def list_credit_notes(
    status: Optional[str] = None,
    customer_id: Optional[int] = None,
    invoice_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(CreditNote).options(
        selectinload(CreditNote.customer),
        selectinload(CreditNote.invoice),
        selectinload(CreditNote.lines),
    ).order_by(CreditNote.date.desc())
    if status:
        q = q.where(CreditNote.status == status)
    if customer_id:
        q = q.where(CreditNote.customer_id == customer_id)
    if invoice_id:
        q = q.where(CreditNote.invoice_id == invoice_id)
    res = await db.execute(q)
    return [_cn_dict(c) for c in res.scalars().all()]


@router.get("/{cn_id}")
async def get_credit_note(cn_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(CreditNote)
        .options(
            selectinload(CreditNote.customer),
            selectinload(CreditNote.invoice),
            selectinload(CreditNote.lines),
        )
        .where(CreditNote.id == cn_id)
    )
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    return _cn_dict(c)


@router.post("", status_code=201)
async def create_credit_note(data: CNIn, db: AsyncSession = Depends(get_db)):
    _validate_cn_input(data)
    # إصلاح ثغرة تحقق: إنشاء إشعار دائن لعميل رقمه غير موجود (أو تابع لـ
    # tenant تاني) كان ينجح بصمت — الإشعار يتسجل بمرجع عميل يتيم، ورصيده
    # لا يُخفَّض فعليًا عند الإصدار لأنه ببساطة مش موجود.
    if not await db.get(Customer, data.customer_id):
        raise HTTPException(404, "العميل غير موجود")
    if data.invoice_id is not None:
        inv_check = await db.get(Invoice, data.invoice_id)
        if not inv_check:
            raise HTTPException(404, "الفاتورة المرجعية غير موجودة")
        if inv_check.customer_id != data.customer_id:
            raise HTTPException(400, "الفاتورة المرجعية لا تخص هذا العميل")
        _st, _tx, _tot = _validate_cn_input(data)
        await _check_invoice_credit_room(db, data.invoice_id, data.lines, _tot)
    code = await _next_code(db)
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get()

    subtotal = sum(
        Decimal(str(l.qty)) * Decimal(str(l.unit_price))
        for l in data.lines
    )
    tax_amount = subtotal * Decimal(str(data.tax_pct)) / 100
    total = subtotal - Decimal(str(data.discount_amt)) + tax_amount

    cn = CreditNote(
        tenant_id=tid,
        code=code,
        customer_id=data.customer_id,
        invoice_id=data.invoice_id,
        date=data.date,
        reason=data.reason,
        return_type=data.return_type,
        is_return=data.is_return,
        tax_pct=data.tax_pct,
        tax_amount=tax_amount,
        discount_amt=data.discount_amt,
        subtotal=subtotal,
        total=total,
        notes=data.notes,
        status="draft",
    )
    db.add(cn)
    await db.flush()

    for i, ld in enumerate(data.lines):
        lp = Decimal(str(ld.qty)) * Decimal(str(ld.unit_price))
        db.add(CreditNoteLine(
            tenant_id=tid,
            credit_note_id=cn.id,
            invoice_line_id=ld.invoice_line_id,
            description=ld.description,
            qty=ld.qty,
            unit=ld.unit,
            unit_price=ld.unit_price,
            total_price=lp,
            sort_order=ld.sort_order or i,
        ))

    await db.commit()
    res = await db.execute(
        select(CreditNote)
        .options(
            selectinload(CreditNote.customer),
            selectinload(CreditNote.invoice),
            selectinload(CreditNote.lines),
        )
        .where(CreditNote.id == cn.id)
    )
    return _cn_dict(res.scalar_one())


@router.put("/{cn_id}")
async def update_credit_note(cn_id: int, data: CNIn, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(CreditNote).options(selectinload(CreditNote.lines))
        .where(CreditNote.id == cn_id)
    )
    cn = res.scalar_one_or_none()
    if not cn:
        raise HTTPException(404)
    if cn.status != "draft":
        raise HTTPException(400, "لا يمكن تعديل إشعار دائن بعد إصداره أو إلغائه")
    _validate_cn_input(data)
    if not await db.get(Customer, data.customer_id):
        raise HTTPException(404, "العميل غير موجود")
    if data.invoice_id is not None:
        inv_check = await db.get(Invoice, data.invoice_id)
        if not inv_check:
            raise HTTPException(404, "الفاتورة المرجعية غير موجودة")
        if inv_check.customer_id != data.customer_id:
            raise HTTPException(400, "الفاتورة المرجعية لا تخص هذا العميل")
        _st, _tx, _tot = _validate_cn_input(data)
        await _check_invoice_credit_room(db, data.invoice_id, data.lines, _tot, exclude_cn_id=cn_id)

    subtotal = sum(Decimal(str(l.qty)) * Decimal(str(l.unit_price)) for l in data.lines)
    tax_amount = subtotal * Decimal(str(data.tax_pct)) / 100
    total = subtotal - Decimal(str(data.discount_amt)) + tax_amount

    cn.customer_id = data.customer_id
    cn.invoice_id = data.invoice_id
    cn.date = data.date
    cn.reason = data.reason
    cn.return_type = data.return_type
    cn.is_return = data.is_return
    cn.tax_pct = data.tax_pct
    cn.tax_amount = tax_amount
    cn.discount_amt = data.discount_amt
    cn.subtotal = subtotal
    cn.total = total
    cn.notes = data.notes

    for old_line in cn.lines:
        await db.delete(old_line)
    await db.flush()

    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1
    for i, ld in enumerate(data.lines):
        lp = Decimal(str(ld.qty)) * Decimal(str(ld.unit_price))
        db.add(CreditNoteLine(
            tenant_id=tid,
            credit_note_id=cn.id,
            invoice_line_id=ld.invoice_line_id,
            description=ld.description,
            qty=ld.qty,
            unit=ld.unit,
            unit_price=ld.unit_price,
            total_price=lp,
            sort_order=ld.sort_order or i,
        ))

    await db.commit()
    res2 = await db.execute(
        select(CreditNote)
        .options(selectinload(CreditNote.customer), selectinload(CreditNote.invoice), selectinload(CreditNote.lines))
        .where(CreditNote.id == cn.id)
    )
    return _cn_dict(res2.scalar_one())


@router.post("/from-invoice/{invoice_id}", status_code=201)
async def credit_note_from_invoice(invoice_id: int, db: AsyncSession = Depends(get_db)):
    """إنشاء إشعار دائن من فاتورة — مرتجع كامل"""
    res = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.lines), selectinload(Invoice.customer))
        .where(Invoice.id == invoice_id)
    )
    inv = res.scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "الفاتورة غير موجودة")
    if inv.status == "cancelled":
        raise HTTPException(400, "لا يمكن إنشاء إشعار لفاتورة ملغية")

    # Check no full CN already issued for this invoice
    existing = await db.scalar(
        select(func.count()).select_from(CreditNote).where(
            CreditNote.invoice_id == invoice_id,
            CreditNote.return_type == "full",
            CreditNote.status == "issued",
        )
    )
    if (existing or 0) > 0:
        raise HTTPException(400, "يوجد إشعار دائن كامل مُصدر لهذه الفاتورة مسبقاً")
    # إصلاح ثغرة over-return: مرتجع كامل بعد مرتجعات جزئية صادرة كان بيرجّع
    # قيمة الفاتورة كلها تاني (إجمالي المرتجعات > قيمة الفاتورة)، ومسودة
    # "كاملة" تانية كانت بتتعمل عادي فوق مسودة موجودة.
    other_cn = await db.scalar(
        select(CreditNote.code).where(
            CreditNote.invoice_id == invoice_id,
            CreditNote.status.in_(("draft", "issued")),
        ).limit(1)
    )
    if other_cn:
        raise HTTPException(
            400,
            f"يوجد إشعار دائن آخر ({other_cn}) على هذه الفاتورة — لا يمكن إنشاء مرتجع كامل. "
            f"أنشئ مرتجعاً جزئياً بالكمية المتبقية بدلاً من ذلك.",
        )

    from app.core.database import current_tenant_id
    tid = current_tenant_id.get()
    code = await _next_code(db)

    cn = CreditNote(
        tenant_id=tid,
        code=code,
        customer_id=inv.customer_id,
        invoice_id=inv.id,
        date=date.today(),
        return_type="full",
        is_return=True,
        reason=f"مرتجع كامل للفاتورة {inv.code}",
        tax_pct=inv.tax_pct,
        tax_amount=inv.tax_amount,
        discount_amt=inv.discount_amt,
        subtotal=inv.subtotal,
        total=inv.total,
        status="draft",
    )
    db.add(cn)
    await db.flush()

    for i, l in enumerate(inv.lines or []):
        db.add(CreditNoteLine(
            tenant_id=tid,
            credit_note_id=cn.id,
            invoice_line_id=l.id,
            description=l.description,
            qty=l.qty,
            unit=l.unit,
            unit_price=l.unit_price,
            total_price=l.total_price,
            sort_order=i,
        ))

    await db.commit()
    res2 = await db.execute(
        select(CreditNote)
        .options(
            selectinload(CreditNote.customer),
            selectinload(CreditNote.invoice),
            selectinload(CreditNote.lines),
        )
        .where(CreditNote.id == cn.id)
    )
    return _cn_dict(res2.scalar_one())


@router.patch("/{cn_id}/issue")
async def issue_credit_note(cn_id: int, db: AsyncSession = Depends(get_db)):
    """إصدار الإشعار — يُخفّض رصيد العميل، ولو مرتجع بضاعة فعلي (is_return)
    يرجّع رصيد المخزون ويعكس تكلفة البضاعة المباعة أيضاً."""
    res = await db.execute(
        select(CreditNote)
        .options(selectinload(CreditNote.customer), selectinload(CreditNote.lines))
        .where(CreditNote.id == cn_id)
    )
    cn = res.scalar_one_or_none()
    if not cn:
        raise HTTPException(404)
    if cn.status != "draft":
        raise HTTPException(400, "يمكن إصدار المسودات فقط")
    if not cn.lines or Decimal(str(cn.total or 0)) <= 0:
        raise HTTPException(400, "لا يمكن إصدار إشعار دائن بدون بنود أو بإجمالي صفر")
    if cn.invoice_id:
        # إعادة فحص الحد وقت الإصدار (مسودتين ممكن يتعملوا قبل ما أي واحدة تتصدر)
        await _check_invoice_credit_room(db, cn.invoice_id, cn.lines, Decimal(str(cn.total)),
                                         exclude_cn_id=cn.id)

    # خفّض رصيد العميل
    cust = await db.get(Customer, cn.customer_id)
    if cust:
        cust.balance = (cust.balance or Decimal("0")) - Decimal(str(cn.total))

    # إصلاح ثغرة تكامل: الإشعار الدائن المرتبط بفاتورة كان يخفّض رصيد العميل
    # الإجمالي بس، من غير ما يلمس الفاتورة نفسها — فتفضل الفاتورة "غير مدفوعة"
    # بكامل قيمتها الأصلية في كل التقارير (remaining/status) رغم إن العميل
    # رجّع البضاعة فعليًا وخُصم المبلغ من رصيده. نطبّق المبلغ على paid_amount
    # بنفس أسلوب تسجيل أي دفعة عادية، بحد أقصى المتبقي على الفاتورة.
    applied_to_invoice = Decimal("0")
    if cn.invoice_id:
        inv = await db.get(Invoice, cn.invoice_id)
        if inv:
            remaining = inv.total - inv.paid_amount
            if remaining > 0:
                applied_to_invoice = min(Decimal(str(cn.total)), remaining)
                inv.paid_amount = inv.paid_amount + applied_to_invoice
                if inv.paid_amount >= inv.total:
                    inv.status = "paid"
                elif inv.paid_amount > 0:
                    inv.status = "partial"

    cn.status    = "issued"
    cn.issued_at = datetime.utcnow()

    from app.core.database import current_tenant_id as _ctid_cni
    from app.services.auto_journal import journal_credit_note_issue, journal_cogs_reversal
    await journal_credit_note_issue(
        db, _ctid_cni.get() or 1, cn.id, cn.code,
        Decimal(str(cn.tax_amount or 0)), Decimal(str(cn.total)), cn.date,
    )

    # مرتجع بضاعة فعلي: رجّع الكمية لمخزون تام الصنع واعكس قيد التكلفة —
    # نفس النمط المستخدم في work_order_to_invoice/delivery_orders عند البيع،
    # بس بالعكس. لو الفاتورة مش صادرة من أمر إنتاج (مفيش FG معروف) بيرجع صفر
    # ومفيش أي أثر مخزني — سلوك موثّق ومقصود، مش خطأ.
    restocked_qty = Decimal("0")
    if cn.is_return and cn.invoice_id:
        inv = await db.get(Invoice, cn.invoice_id)
        if inv and inv.work_order_id:
            restock_qty = await _restock_qty_for_wo_invoice(db, cn, inv)
            if restock_qty > 0:
                from app.services.inventory_ledger import get_or_create_warehouse_bin, post_receipt
                wo = await db.get(WorkOrder, inv.work_order_id)
                fg_mat = (await db.execute(
                    select(Material).where(
                        Material.tenant_id == (_ctid_cni.get() or 1),
                        Material.code == f"FG-{wo.code}",
                    )
                )).scalar_one_or_none()
                if fg_mat:
                    # إصلاح ثغرة (مخزون وهمي): الكمية المرتجعة كانت بتدخل FG حتى لو
                    # البضاعة عمرها ما خرجت من المخزون أصلاً (مثلاً فاتورة يدوية
                    # مربوطة بأمر إنتاج من غير خصم FG). نحدّها بصافي المُباع فعلاً.
                    restock_qty = min(restock_qty, await _fg_net_sold_qty(db, fg_mat.id))
                if fg_mat and restock_qty > 0:
                    unit_cost = fg_mat.unit_cost or Decimal("0")
                    fg_bin = await get_or_create_warehouse_bin(db, _ctid_cni.get() or 1, "FG")
                    await post_receipt(
                        db, material=fg_mat, qty=restock_qty, unit_cost=unit_cost,
                        source_doc_type="credit_note_return", source_doc_id=cn.id,
                        destination_location_id=fg_bin.id,
                        reference=cn.code, notes=f"مرتجع بضاعة — إشعار دائن {cn.code}",
                    )
                    restocked_qty = restock_qty
                    await journal_cogs_reversal(
                        db, _ctid_cni.get() or 1, cn.id, cn.code,
                        restock_qty * unit_cost, cn.date,
                    )

    await db.commit()
    return {"id": cn_id, "status": "issued", "total": float(cn.total),
            "applied_to_invoice": float(applied_to_invoice),
            "restocked_qty": float(restocked_qty)}


@router.patch("/{cn_id}/cancel")
async def cancel_credit_note(cn_id: int, db: AsyncSession = Depends(get_db)):
    """إلغاء الإشعار — يُعيد رصيد العميل إن كان صادراً، ولو مرتجع بضاعة فعلي
    (is_return) يسحب الكمية اللي رجعت المخزون تاني ويعكس قيد عكس التكلفة."""
    res = await db.execute(
        select(CreditNote)
        .options(selectinload(CreditNote.customer), selectinload(CreditNote.lines))
        .where(CreditNote.id == cn_id)
    )
    cn = res.scalar_one_or_none()
    if not cn:
        raise HTTPException(404)
    if cn.status == "applied":
        raise HTTPException(400, "لا يمكن إلغاء إشعار مُطبَّق")

    # إعادة الرصيد إن كان الإشعار صادراً
    if cn.status == "issued":
        cust = await db.get(Customer, cn.customer_id)
        if cust:
            cust.balance = (cust.balance or Decimal("0")) + Decimal(str(cn.total))

        # عكس ما طُبِّق على الفاتورة المرتبطة عند الإصدار (راجع issue_credit_note)
        if cn.invoice_id:
            inv = await db.get(Invoice, cn.invoice_id)
            if inv:
                # إصلاح: الإصدار بيطبّق على الفاتورة min(total, المتبقي) بس — لو
                # الفاتورة كانت متسددة كاش بالكامل مفيش حاجة اتطبّقت، والإلغاء كان
                # بيخصم قيمة الإشعار كلها من paid_amount (يمسح دفعات كاش حقيقية).
                # الجزء المطبّق من إشعارات = paid_amount - مجموع تخصيصات الدفعات.
                from app.models.models import CustomerPaymentAllocation as _CPA
                cash_alloc = Decimal(str(await db.scalar(
                    select(func.coalesce(func.sum(_CPA.amount), 0)).where(_CPA.invoice_id == inv.id)
                ) or 0))
                non_cash_paid = max(Decimal(str(inv.paid_amount)) - cash_alloc, Decimal("0"))
                reverse_amt = min(Decimal(str(cn.total)), non_cash_paid)
                if reverse_amt > 0:
                    inv.paid_amount = inv.paid_amount - reverse_amt
                    if inv.paid_amount >= inv.total:
                        inv.status = "paid"
                    elif inv.paid_amount > 0:
                        inv.status = "partial"
                    else:
                        inv.status = "unpaid"

        from app.core.database import current_tenant_id as _ctid_cnc
        from app.services.auto_journal import journal_credit_note_cancel, journal_cogs
        await journal_credit_note_cancel(
            db, _ctid_cnc.get() or 1, cn.id, cn.code,
            Decimal(str(cn.tax_amount or 0)), Decimal(str(cn.total)), cn.date,
        )

        # عكس المرتجع الفعلي (لو كان is_return وارجع مخزون عند الإصدار):
        # اسحب نفس الكمية تاني من FG واعكس قيد عكس-التكلفة (يعني رجّع COGS
        # الأصلي) — بالظبط عكس ما حصل في issue_credit_note.
        if cn.is_return and cn.invoice_id:
            inv = await db.get(Invoice, cn.invoice_id)
            if inv and inv.work_order_id:
                # نسحب بالظبط الكمية اللي رجعت فعلاً بالإشعار ده وقت الإصدار (من
                # سجل الحركات)، مش إعادة حساب من البنود — ممكن تكون اتحدّت وقتها.
                from app.models.models import StockMovement as _SM
                restock_qty = Decimal(str(await db.scalar(
                    select(func.coalesce(func.sum(_SM.qty), 0)).where(
                        _SM.source_doc_type == "credit_note_return", _SM.source_doc_id == cn.id,
                        _SM.movement_type == "in",
                    )
                ) or 0))
                if restock_qty > 0:
                    from app.services.inventory_ledger import get_or_create_warehouse_bin, post_issue
                    wo = await db.get(WorkOrder, inv.work_order_id)
                    fg_mat = (await db.execute(
                        select(Material).where(
                            Material.tenant_id == (_ctid_cnc.get() or 1),
                            Material.code == f"FG-{wo.code}",
                        )
                    )).scalar_one_or_none()
                    if not fg_mat or (fg_mat.stock_qty or Decimal("0")) < restock_qty:
                        # كان بيتخطى السحب بصمت ويكمل الإلغاء — فيفضل المخزون زايد
                        # بكمية المرتجع رغم إن الإشعار اتلغى وقيد التكلفة اترجع.
                        raise HTTPException(
                            400,
                            f"لا يمكن إلغاء الإشعار — رصيد تام الصنع الحالي "
                            f"({float(fg_mat.stock_qty or 0) if fg_mat else 0:g}) أقل من الكمية "
                            f"المرتجعة ({float(restock_qty):g}) اللي لازم تتسحب تاني",
                        )
                    if fg_mat:
                        unit_cost = fg_mat.unit_cost or Decimal("0")
                        fg_bin = await get_or_create_warehouse_bin(db, _ctid_cnc.get() or 1, "FG")
                        await post_issue(
                            db, material=fg_mat, qty=restock_qty, unit_cost=unit_cost,
                            source_doc_type="credit_note_cancel", source_doc_id=cn.id,
                            source_location_id=fg_bin.id,
                            reference=cn.code, notes=f"إلغاء مرتجع بضاعة — إشعار دائن {cn.code}",
                        )
                        await journal_cogs(
                            db, _ctid_cnc.get() or 1, cn.id, cn.code,
                            restock_qty * unit_cost, cn.date,
                        )

    cn.status = "cancelled"
    await db.commit()
    return {"id": cn_id, "status": "cancelled"}


@router.delete("/{cn_id}", status_code=204)
async def delete_credit_note(cn_id: int, db: AsyncSession = Depends(get_db)):
    cn = await db.get(CreditNote, cn_id)
    if not cn:
        raise HTTPException(404)
    if cn.status not in ("draft", "cancelled"):
        raise HTTPException(400, "يمكن حذف المسودات والملغية فقط")
    lines = (await db.execute(
        select(CreditNoteLine).where(CreditNoteLine.credit_note_id == cn_id)
    )).scalars().all()
    for l in lines:
        await db.delete(l)
    await db.delete(cn)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# PRINT — طباعة الإشعار الدائن
# ══════════════════════════════════════════════════════════════════════

def _esc(s) -> str:
    import html as _h
    return _h.escape(str(s or ""))


async def _company(db: AsyncSession) -> dict:
    from app.core.database import current_tenant_id
    from app.models.models import WorkshopProfile
    tid = current_tenant_id.get()
    q = select(WorkshopProfile)
    if tid is not None:
        q = q.where(WorkshopProfile.tenant_id == tid)
    res = await db.execute(q.limit(1))
    prof = res.scalar_one_or_none()
    if not prof:
        return {"name": "ورشة معدات الاعلاف", "sub": "", "phone": "",
                "address": "", "footer": "شكرا لتعاملكم معنا", "logo_html": "", "legal_html": ""}
    logo_html = (
        f'<img src="{prof.logo_data}" alt="logo" '
        'style="max-height:75px;max-width:160px;object-fit:contain;display:block;"/>'
        if prof.logo_data else ""
    )
    legal = []
    if prof.tax_id:         legal.append(f"ب.ض: {prof.tax_id}")
    if prof.commercial_reg: legal.append(f"س.ت: {prof.commercial_reg}")
    return {
        "name":      prof.name or "ورشة معدات الاعلاف",
        "sub":       prof.description or "",
        "phone":     prof.mobile or prof.phone or "",
        "address":   prof.address or "",
        "footer":    prof.report_footer or "شكرا لتعاملكم معنا",
        "logo_html": logo_html,
        "legal_html": " | ".join(legal),
    }


def _cn_html(cn: CreditNote, company: dict) -> str:
    import json as _j

    RED   = "#A32D2D"
    RED_L = "#FDF0F0"

    cname    = _esc(company.get("name", ""))
    csub     = _esc(company.get("sub", ""))
    cphone   = _esc(company.get("phone", ""))
    caddr    = _esc(company.get("address", ""))
    cfooter  = _esc(company.get("footer", ""))
    legal_h  = _esc(company.get("legal_html", ""))
    logo_html = company.get("logo_html", "")

    cust_name  = _esc(cn.customer.name  if cn.customer else "—")
    cust_phone = _esc(cn.customer.phone if cn.customer else "")
    cust_addr  = _esc(cn.customer.address if cn.customer else "")

    code_v  = _esc(cn.code)
    date_v  = _esc(str(cn.date))
    inv_ref = _esc(cn.invoice.code if cn.invoice else "—")
    reason_v = _esc(cn.reason or "مرتجع بضاعة")
    notes_v  = _esc(cn.notes or "")

    rows_html = ""
    for i, l in enumerate(cn.lines or []):
        bg = "#fafafa" if i % 2 == 0 else "#fff"
        tp = float(l.total_price)
        rows_html += (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:9px 12px;text-align:center;color:#888;font-size:12px'>{i+1}</td>"
            f"<td style='padding:9px 12px;font-weight:600'>{_esc(l.description)}</td>"
            f"<td style='padding:9px 12px;text-align:center'>{float(l.qty):.2f} {_esc(l.unit)}</td>"
            f"<td style='padding:9px 12px;text-align:left;font-variant-numeric:tabular-nums'>{float(l.unit_price):,.2f}</td>"
            f"<td style='padding:9px 12px;text-align:left;font-weight:700;color:{RED};font-variant-numeric:tabular-nums'>{tp:,.2f}</td>"
            "</tr>"
        )

    subtotal = float(cn.subtotal)
    tax_pct  = float(cn.tax_pct)
    tax_amt  = float(cn.tax_amount)
    disc_amt = float(cn.discount_amt)
    total_v  = float(cn.total)

    totals_rows = f"<tr><td>المجموع قبل الضريبة</td><td style='text-align:left;font-weight:600'>{subtotal:,.2f}</td></tr>"
    if disc_amt > 0:
        totals_rows += f"<tr><td>الخصم</td><td style='text-align:left;color:#555'>- {disc_amt:,.2f}</td></tr>"
    if tax_pct > 0:
        totals_rows += f"<tr><td>ضريبة ({tax_pct:.0f}%)</td><td style='text-align:left;color:#555'>+ {tax_amt:,.2f}</td></tr>"

    status_map = {
        "draft":     ("مسودة",   "#888"),
        "issued":    ("صادر",    "#1a7a3c"),
        "cancelled": ("ملغي",    RED),
    }
    st_label, st_color = status_map.get(cn.status, ("—", "#888"))
    issued_bar = (
        f'<span style="font-size:11px;color:rgba(255,255,255,.4)">صدر في: '
        f'<b style="color:rgba(255,255,255,.7)">{cn.issued_at.strftime("%Y-%m-%d") if cn.issued_at else "—"}</b></span>'
        if cn.issued_at else "<span></span>"
    )

    logo_wrap = (
        f'<div style="background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);'
        f'border-radius:10px;padding:6px;">{logo_html}</div>'
        if logo_html else ""
    )
    from_details = "".join(f'<div class="p-detail">{v}</div>' for v in [cphone, caddr] if v)
    cust_details = "".join(f'<div class="p-detail">{v}</div>' for v in [cust_phone, cust_addr] if v)
    notes_box = (
        f'<div class="clause-box"><div class="clause-title">ملاحظات</div>'
        f'<div class="clause-body">{notes_v}</div></div>'
        if notes_v else ""
    )
    footer_items = " | ".join(filter(None, [
        f"هاتف: {cphone}" if cphone else "",
        f"العنوان: {caddr}" if caddr else "",
        legal_h,
    ]))
    # الكود بيتحط جوه onclick="..." — json.dumps بيرجّع "..." اللي كانت بتقفل
    # الـ attribute وتكسر زرار الطباعة؛ نهرّبه كـ HTML attribute.
    js_code = _esc(_j.dumps(cn.code or ""))

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<title>إشعار دائن - {code_v}</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{{--navy:#0C2340;--navy-dk:#081828;--red:{RED};--red-l:{RED_L};--surface:#F8F7F4;--border:#E8E6E0;--text:#1A1A2E;--muted:#7A7870;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Cairo',Tahoma,Arial,sans-serif;font-size:13px;color:var(--text);background:#EDECEA;direction:rtl;line-height:1.65;}}
@page{{size:A4;margin:8mm 7mm;}}
@media print{{.no-print{{display:none!important;}}body{{-webkit-print-color-adjust:exact;print-color-adjust:exact;background:#fff;}}.page{{max-width:100%;box-shadow:none;border-radius:0;margin:0;}}}}
.page{{max-width:840px;margin:16px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 60px rgba(0,0,0,.13);}}
.doc-header{{background:linear-gradient(135deg,var(--navy-dk) 0%,var(--navy) 60%,#143558 100%);color:#fff;padding:24px 30px 20px;display:flex;justify-content:space-between;align-items:center;}}
.co-block{{display:flex;align-items:center;gap:18px;}}
.co-name{{font-size:20px;font-weight:800;line-height:1.2;}}
.co-sub{{font-size:11px;color:rgba(255,255,255,.5);margin-top:3px;}}
.co-phone{{font-size:12px;color:var(--red);margin-top:6px;font-weight:600;}}
.doc-meta{{text-align:left;}}
.doc-type{{font-size:28px;font-weight:900;color:var(--red);line-height:1;}}
.doc-badge{{display:inline-flex;align-items:center;gap:6px;margin-top:8px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 12px;}}
.bl{{font-size:11px;color:rgba(255,255,255,.5);}}
.bv{{font-size:12.5px;color:#fff;font-weight:700;}}
.red-stripe{{height:4px;background:linear-gradient(90deg,var(--red),rgba(163,45,45,.4),var(--red));}}
.status-bar{{background:#0D2440;padding:8px 30px;display:flex;justify-content:space-between;align-items:center;}}
.status-pill{{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(255,255,255,.2);font-size:11px;font-weight:700;padding:3px 14px;border-radius:99px;}}
.body{{padding:24px 30px;}}
.parties{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px;}}
.party-box{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 18px;border-top:3px solid var(--red);}}
.p-label{{font-size:10px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:7px;}}
.p-name{{font-size:15px;font-weight:800;margin-bottom:4px;}}
.p-detail{{font-size:11.5px;color:var(--muted);line-height:1.7;}}
.ref-bar{{background:var(--red-l);border:1px solid var(--red)30;border-radius:10px;padding:10px 18px;margin-bottom:18px;display:flex;gap:30px;align-items:center;}}
.ref-item{{font-size:12px;}}
.ref-label{{font-weight:700;color:var(--red);margin-left:6px;}}
table.items{{width:100%;border-collapse:collapse;margin-bottom:20px;border-radius:12px;overflow:hidden;border:1px solid var(--border);}}
.items thead tr{{background:var(--navy);}}
.items thead th{{padding:10px 12px;font-size:11px;font-weight:700;color:rgba(255,255,255,.75);text-align:right;}}
.items thead th:last-child,.items thead th:nth-last-child(2){{text-align:left;}}
.items tbody td{{padding:9px 12px;border-bottom:1px solid var(--border);font-size:12.5px;}}
.items tbody tr:last-child td{{border-bottom:none;}}
.totals-wrap{{display:flex;justify-content:flex-end;margin-bottom:20px;}}
table.totals-tbl{{width:260px;border-collapse:collapse;}}
.totals-tbl td{{padding:6px 10px;font-size:12.5px;border-bottom:1px dashed var(--border);}}
.grand-row{{background:linear-gradient(135deg,#5c0a0a,var(--red));border-radius:10px;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;}}
.g-label{{font-size:13px;font-weight:600;color:rgba(255,255,255,.75);}}
.g-value{{font-size:22px;font-weight:900;color:#fff;font-variant-numeric:tabular-nums;}}
.clause-box{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 16px;margin-bottom:14px;}}
.clause-title{{font-size:10px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:6px;}}
.clause-body{{font-size:13px;line-height:1.8;}}
.sig-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-top:20px;margin-bottom:20px;}}
.sig-box{{border-top:2px solid var(--border);padding-top:10px;text-align:center;}}
.sig-title{{font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.07em;margin-bottom:32px;}}
.sig-line{{border-top:1px solid #CCC;margin:0 16px;padding-top:6px;font-size:11px;color:#aaa;}}
.doc-footer{{background:linear-gradient(90deg,var(--navy-dk),var(--navy));color:rgba(255,255,255,.6);padding:12px 30px;text-align:center;font-size:11.5px;}}
.action-bar{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);display:flex;gap:8px;z-index:99;background:rgba(8,24,40,.85);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:8px 12px;}}
.action-bar button{{padding:8px 18px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;border:none;font-family:'Cairo',sans-serif;}}
.btn-print{{background:var(--red);color:#fff;}}
</style>
</head>
<body onload="setTimeout(()=>window.print(),400)">
<div class="page">
  <div class="doc-header">
    <div class="co-block">
      {logo_wrap}
      <div>
        <div class="co-name">{cname}</div>
        <div class="co-sub">{csub}</div>
        <div class="co-phone">{cphone}</div>
      </div>
    </div>
    <div class="doc-meta">
      <div class="doc-type">إشعار دائن</div>
      <div class="doc-badge"><span class="bl">رقم:</span><span class="bv">{code_v}</span></div>
      <div class="doc-badge" style="margin-top:5px;"><span class="bl">التاريخ:</span><span class="bv">{date_v}</span></div>
    </div>
  </div>
  <div class="red-stripe"></div>
  <div class="status-bar">
    <span class="status-pill" style="color:{st_color};border-color:{st_color}40;">
      <span style="width:6px;height:6px;background:currentColor;border-radius:50%;display:inline-block;"></span>
      {st_label}
    </span>
    {issued_bar}
  </div>
  <div class="body">
    <div class="parties">
      <div class="party-box">
        <div class="p-label">المُصدِر</div>
        <div class="p-name">{cname}</div>
        {from_details}
      </div>
      <div class="party-box">
        <div class="p-label">العميل</div>
        <div class="p-name">{cust_name}</div>
        {cust_details}
      </div>
    </div>
    <div class="ref-bar">
      <div class="ref-item"><span class="ref-label">سبب الإشعار:</span>{reason_v}</div>
      <div class="ref-item"><span class="ref-label">الفاتورة المرجعية:</span>{inv_ref}</div>
      <div class="ref-item"><span class="ref-label">نوع المرتجع:</span>{"مرتجع كامل" if cn.return_type == "full" else "مرتجع جزئي"}</div>
    </div>
    <table class="items">
      <thead><tr>
        <th style="width:38px;text-align:center">#</th>
        <th>الصنف / الوصف</th>
        <th style="width:110px;text-align:center">الكمية</th>
        <th style="width:120px;text-align:left">سعر الوحدة</th>
        <th style="width:120px;text-align:left">الإجمالي</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    <div class="totals-wrap">
      <div>
        <table class="totals-tbl"><tbody>{totals_rows}</tbody></table>
        <div class="grand-row" style="margin-top:8px;">
          <span class="g-label">إجمالي الإشعار الدائن</span>
          <span class="g-value">{total_v:,.2f} ج.م</span>
        </div>
      </div>
    </div>
    {notes_box}
    <div class="sig-row">
      <div class="sig-box"><div class="sig-title">محرر الإشعار</div><div class="sig-line">الاسم والتوقيع</div></div>
      <div class="sig-box"><div class="sig-title">مدير المبيعات</div><div class="sig-line">التوقيع والختم</div></div>
      <div class="sig-box"><div class="sig-title">العميل — استلام الإشعار</div><div class="sig-line">التوقيع والتاريخ</div></div>
    </div>
  </div>
  <div class="doc-footer">{footer_items if footer_items else cname}</div>
</div>
<div class="action-bar no-print">
  <button class="btn-print" onclick="(function(){{document.title='إشعار دائن '+{js_code};window.print();}})()">طباعة / PDF</button>
</div>
</body></html>"""


@print_router.get("/{cn_id}/print", response_class=HTMLResponse)
async def print_credit_note(cn_id: int, db: AsyncSession = Depends(get_db)):
    """طباعة الإشعار الدائن"""
    res = await db.execute(
        select(CreditNote)
        .options(
            selectinload(CreditNote.customer),
            selectinload(CreditNote.invoice),
            selectinload(CreditNote.lines),
        )
        .where(CreditNote.id == cn_id)
    )
    cn = res.scalar_one_or_none()
    if not cn:
        raise HTTPException(404)
    company = await _company(db)
    return _cn_html(cn, company)
