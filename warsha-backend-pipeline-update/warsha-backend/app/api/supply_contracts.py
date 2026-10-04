"""
عقود التوريد — Supply Contracts API
إنشاء → موافقة → طباعة
يمكن إنشاؤه من عرض سعر أو مستقلاً
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.api.auth import require_any, block_viewer_writes
from app.models.models import (
    SupplyContract, SupplyContractLine, Customer, Quotation, QuotationLine,
    Material, Invoice, InvoiceLine,
    PaymentScheduleTemplate, InvoicePaymentSchedule,
)
from app.services.invoice_due import sync_invoice_due_date

router = APIRouter(
    prefix="/supply-contracts",
    tags=["supply_contracts"],
    dependencies=[Depends(require_any), Depends(block_viewer_writes)],
)
print_router = APIRouter(prefix="/supply-contracts", tags=["supply_contracts_print"], dependencies=[Depends(require_any)])


# ── Schemas ───────────────────────────────────────────────────────────

class ContractLineIn(BaseModel):
    item_type:   Optional[str] = None
    model_no:    Optional[str] = None
    description: str
    qty:         Decimal = Decimal("1")
    unit:        str = "قطعة"
    unit_price:  Decimal
    notes:       Optional[str] = None
    sort_order:  int = 0
    material_id: Optional[int] = None


class ContractIn(BaseModel):
    code:                 Optional[str]  = None
    customer_id:          int
    quotation_id:         Optional[int]  = None
    date:                 date
    valid_until:          Optional[date] = None
    payment_terms:        Optional[str]  = None
    delivery_terms:       Optional[str]  = None
    warranty_months:      int = 12
    warranty_description: Optional[str]  = None
    tax_pct:              Decimal = Decimal("0")
    discount_amt:         Decimal = Decimal("0")
    scope_of_work:        Optional[str]  = None
    special_terms:        Optional[str]  = None
    notes:                Optional[str]  = None
    lines:                List[ContractLineIn] = []


# ── Helpers ───────────────────────────────────────────────────────────

async def _tenant_contract_query(contract_id: int):
    """عقد توريد مقيّد بالـ tenant الحالي — يمنع أي tenant من قراءة/تعديل
    عقد تابع لـ tenant تاني عن طريق تخمين الـ id (كل استخدامات db.get
    القديمة هنا كانت بترجع أي عقد بغض النظر عن الـ tenant)."""
    from app.core.database import current_tenant_id as _ctid_tcq
    tid = _ctid_tcq.get()
    q = select(SupplyContract).where(SupplyContract.id == contract_id)
    if tid is not None:
        q = q.where(SupplyContract.tenant_id == tid)
    return q


async def _next_code(db: AsyncSession) -> str:
    count = await db.scalar(select(func.count()).select_from(SupplyContract))
    n = (count or 0) + 1
    while True:
        code = f"SC-{n:04d}"
        # عمود code عليه unique=True على مستوى الجدول كله (مش لكل tenant)،
        # بينما فلتر الـ tenant التلقائي كان بيخلّي الفحص ده يشوف عقود الـ
        # tenant الحالي بس → tenant تاني بيطلع له SC-0001 مكرر و IntegrityError
        # (500) عند الحفظ. نفحص عبر كل المستأجرين.
        exists = await _code_taken(db, code)
        if not exists:
            return code
        n += 1


async def _code_taken(db: AsyncSession, code: str) -> bool:
    return bool(await db.scalar(
        select(SupplyContract.id).where(SupplyContract.code == code)
        .execution_options(skip_tenant_filter=True)
    ))


def _contract_dict(c: SupplyContract) -> dict:
    status_ar = {
        "draft":     "مسودة",
        "active":    "ساري",
        "expired":   "منتهي",
        "cancelled": "ملغي",
    }
    return {
        "id": c.id,
        "code": c.code,
        "customer_id": c.customer_id,
        "customer_name": c.customer.name if c.customer else "",
        "quotation_id": c.quotation_id,
        "date": str(c.date),
        "valid_until": str(c.valid_until) if c.valid_until else None,
        "status": c.status,
        "status_ar": status_ar.get(c.status, c.status),
        "payment_terms": c.payment_terms,
        "delivery_terms": c.delivery_terms,
        "warranty_months": c.warranty_months,
        "warranty_description": c.warranty_description,
        "subtotal": float(c.subtotal),
        "tax_pct": float(c.tax_pct),
        "tax_amount": float(c.tax_amount),
        "discount_amt": float(c.discount_amt),
        "total": float(c.total),
        "scope_of_work": c.scope_of_work,
        "special_terms": c.special_terms,
        "notes": c.notes,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "delivery_confirmed": bool(c.delivery_confirmed),
        "delivery_date":  str(c.delivery_date)  if c.delivery_date  else None,
        "received_by":    c.received_by,
        "receiver_title": c.receiver_title,
        "delivery_notes": c.delivery_notes,
        "vehicle_plate":  c.vehicle_plate,
        "vehicle_type":   c.vehicle_type,
        "driver_name":    c.driver_name,
        "driver_license": c.driver_license,
        "driver_phone":   c.driver_phone,
        "lines": [
            {
                "id": l.id,
                "item_type": l.item_type,
                "model_no": l.model_no,
                "description": l.description,
                "qty": float(l.qty),
                "unit": l.unit,
                "unit_price": float(l.unit_price),
                "total_price": float(l.total_price),
                "notes": l.notes,
                "sort_order": l.sort_order,
                "material_id": l.material_id,
                "material_name": l.material.name_ar if getattr(l, 'material', None) and l.material else None,
            }
            for l in (c.lines or [])
        ],
    }


def _calc_totals(lines: list, tax_pct: Decimal, discount_amt: Decimal):
    subtotal  = sum(Decimal(str(l.qty)) * Decimal(str(l.unit_price)) for l in lines)
    tax_amount = subtotal * tax_pct / 100
    total     = subtotal - discount_amt + tax_amount
    return subtotal, tax_amount, total


# ══════════════════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════════════════

@router.get("")
async def list_contracts(
    status: Optional[str] = None,
    customer_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    from app.core.database import current_tenant_id as _ctid_lc
    tid = _ctid_lc.get()
    q = select(SupplyContract).options(
        selectinload(SupplyContract.customer),
        selectinload(SupplyContract.lines).selectinload(SupplyContractLine.material),
    ).order_by(SupplyContract.date.desc())
    if tid is not None:
        q = q.where(SupplyContract.tenant_id == tid)
    if status:
        q = q.where(SupplyContract.status == status)
    if customer_id:
        q = q.where(SupplyContract.customer_id == customer_id)
    res = await db.execute(q)
    return [_contract_dict(c) for c in res.scalars().all()]


@router.get("/next-code")
async def get_next_contract_code(db: AsyncSession = Depends(get_db)):
    return {"code": await _next_code(db)}


@router.get("/{contract_id}")
async def get_contract(contract_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        (await _tenant_contract_query(contract_id)).options(
            selectinload(SupplyContract.customer),
            selectinload(SupplyContract.lines).selectinload(SupplyContractLine.material),
        )
    )
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    return _contract_dict(c)


@router.post("", status_code=201)
async def create_contract(data: ContractIn, db: AsyncSession = Depends(get_db)):
    if not data.lines:
        raise HTTPException(400, "أضف بنداً واحداً على الأقل")
    for l in data.lines:
        if l.qty is None or l.qty <= 0:
            raise HTTPException(400, "كمية كل بند يجب أن تكون أكبر من صفر")
        if l.unit_price is not None and l.unit_price < 0:
            raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
    if data.date and data.valid_until and data.valid_until < data.date:
        raise HTTPException(400, "تاريخ صلاحية العقد يجب أن يكون بعد تاريخه")
    if data.tax_pct is not None and not (Decimal("0") <= data.tax_pct <= Decimal("100")):
        raise HTTPException(400, "نسبة الضريبة يجب أن تكون بين 0 و 100")
    if data.discount_amt is not None and data.discount_amt < 0:
        raise HTTPException(400, "الخصم لا يمكن أن يكون سالباً")
    if data.warranty_months is not None and data.warranty_months < 0:
        raise HTTPException(400, "مدة الضمان لا يمكن أن تكون سالبة")
    for l in data.lines:
        if not (l.description or "").strip():
            raise HTTPException(400, "وصف البند مطلوب")
    _gross = sum(Decimal(str(l.qty)) * Decimal(str(l.unit_price)) for l in data.lines)
    if data.discount_amt and data.discount_amt > _gross:
        raise HTTPException(400, "الخصم أكبر من إجمالي البنود")
    code = (data.code or "").strip() or await _next_code(db)
    if await _code_taken(db, code):
        raise HTTPException(400, f"كود {code} مستخدم من قبل")

    subtotal, tax_amount, total = _calc_totals(
        data.lines, Decimal(str(data.tax_pct)), Decimal(str(data.discount_amt))
    )
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get()

    cust_check = await db.get(Customer, data.customer_id)
    if not cust_check or (tid is not None and cust_check.tenant_id != tid):
        raise HTTPException(400, "العميل غير موجود")
    if data.quotation_id is not None:
        q_check = await db.get(Quotation, data.quotation_id)
        if not q_check or (tid is not None and q_check.tenant_id != tid):
            raise HTTPException(400, "عرض السعر غير موجود")
        if q_check.customer_id != data.customer_id:
            raise HTTPException(400, "عرض السعر يخص عميلاً آخر")
    # تحقق أن كل مادة مرتبطة ببند فعلاً تابعة لنفس الـ tenant — من غيره كان
    # ممكن نمرر material_id تخميني تابع لـ tenant تاني، وعند تأكيد التسليم
    # (confirm_delivery) يتصرف من مخزون مادة مش بتاعتنا خالص.
    for l in data.lines:
        if l.material_id:
            mat_check = await db.get(Material, l.material_id)
            if not mat_check or (tid is not None and mat_check.tenant_id != tid):
                raise HTTPException(400, "المادة غير موجودة")

    c = SupplyContract(
        tenant_id=tid,
        code=code,
        customer_id=data.customer_id,
        quotation_id=data.quotation_id,
        date=data.date,
        valid_until=data.valid_until,
        payment_terms=data.payment_terms,
        delivery_terms=data.delivery_terms,
        warranty_months=data.warranty_months,
        warranty_description=data.warranty_description,
        tax_pct=data.tax_pct,
        tax_amount=tax_amount,
        discount_amt=data.discount_amt,
        subtotal=subtotal,
        total=total,
        scope_of_work=data.scope_of_work,
        special_terms=data.special_terms,
        notes=data.notes,
        status="draft",
    )
    db.add(c)
    await db.flush()

    for i, ld in enumerate(data.lines):
        lp = Decimal(str(ld.qty)) * Decimal(str(ld.unit_price))
        db.add(SupplyContractLine(
            tenant_id=tid,
            contract_id=c.id,
            item_type=ld.item_type,
            model_no=ld.model_no,
            description=ld.description,
            qty=ld.qty,
            unit=ld.unit,
            unit_price=ld.unit_price,
            total_price=lp,
            notes=ld.notes,
            sort_order=ld.sort_order or i,
            material_id=ld.material_id,
        ))

    await db.commit()
    res = await db.execute(
        select(SupplyContract)
        .options(selectinload(SupplyContract.customer), selectinload(SupplyContract.lines).selectinload(SupplyContractLine.material))
        .where(SupplyContract.id == c.id)
    )
    return _contract_dict(res.scalar_one())


@router.post("/from-quotation/{quotation_id}", status_code=201)
async def contract_from_quotation(quotation_id: int, db: AsyncSession = Depends(get_db)):
    """تحويل عرض سعر إلى عقد توريد"""
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get()
    q_query = select(Quotation).options(
        selectinload(Quotation.lines), selectinload(Quotation.customer)
    ).where(Quotation.id == quotation_id)
    if tid is not None:
        q_query = q_query.where(Quotation.tenant_id == tid)
    res = await db.execute(q_query)
    q = res.scalar_one_or_none()
    if not q:
        raise HTTPException(404, "عرض السعر غير موجود")
    if q.status not in ("sent", "accepted", "draft"):
        raise HTTPException(400, f"لا يمكن تحويل عرض بحالة '{q.status}' إلى عقد")
    # منع التحويل المكرر لنفس العرض (ضغطتين على الزر = عقدين بنفس البنود)
    dup = await db.scalar(select(SupplyContract.code).where(
        SupplyContract.quotation_id == q.id, SupplyContract.status != "cancelled",
    ))
    if dup:
        raise HTTPException(409, f"عرض السعر محوّل بالفعل إلى العقد {dup}")

    code = await _next_code(db)

    c = SupplyContract(
        tenant_id=tid,
        code=code,
        customer_id=q.customer_id,
        quotation_id=q.id,
        date=date.today(),
        valid_until=q.valid_until,
        tax_pct=q.tax_pct,
        tax_amount=q.tax_amount,
        discount_amt=q.discount_amt,
        subtotal=q.subtotal,
        total=q.total,
        status="draft",
        notes=f"عقد محوّل من عرض سعر {q.code}",
    )
    db.add(c)
    await db.flush()

    for l in (q.lines or []):
        db.add(SupplyContractLine(
            tenant_id=tid,
            contract_id=c.id,
            item_type=l.item_type,
            model_no=l.model_no,
            description=l.description,
            qty=l.qty,
            unit=l.unit,
            unit_price=l.unit_price,
            total_price=l.total_price,
            notes=l.notes,
            sort_order=l.sort_order,
        ))

    await db.commit()
    res2 = await db.execute(
        select(SupplyContract)
        .options(selectinload(SupplyContract.customer), selectinload(SupplyContract.lines).selectinload(SupplyContractLine.material))
        .where(SupplyContract.id == c.id)
    )
    return _contract_dict(res2.scalar_one())


@router.patch("/{contract_id}/activate")
async def activate_contract(contract_id: int, db: AsyncSession = Depends(get_db)):
    c = (await db.execute(await _tenant_contract_query(contract_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    if c.status != "draft":
        raise HTTPException(400, "يمكن تفعيل العقود المسودة فقط")
    c.status = "active"
    await db.commit()
    return {"id": contract_id, "status": "active"}


@router.patch("/{contract_id}/cancel")
async def cancel_contract(contract_id: int, db: AsyncSession = Depends(get_db)):
    c = (await db.execute(await _tenant_contract_query(contract_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    # كان الإلغاء مسموحاً بلا أي شرط — حتى بعد تأكيد التسليم فعلياً، رغم إن
    # التسليم بيكون خلّف بالفعل فاتورة مبيعات حقيقية + رصيد عميل مرفوع +
    # حركة مخزون صادرة. إلغاء العقد في الحالة دي كان بيسيب حالة متضاربة
    # (عقد "ملغي" له أثر مالي/مخزني فعلي وقائم). امنع الإلغاء بعد التسليم،
    # بنفس منطق delete_contract اللي بيمنع حذف عقد "active" أصلاً.
    if c.delivery_confirmed:
        raise HTTPException(400, "لا يمكن إلغاء عقد تم تأكيد تسليمه بالفعل — تم إنشاء فاتورة وحركة مخزون مرتبطتين به")
    c.status = "cancelled"
    await db.commit()
    return {"id": contract_id, "status": "cancelled"}


class DeliveryIn(BaseModel):
    delivery_date:  Optional[date] = None
    received_by:    Optional[str]  = None
    receiver_title: Optional[str]  = None
    delivery_notes: Optional[str]  = None
    vehicle_plate:  Optional[str]  = None
    vehicle_type:   Optional[str]  = None
    driver_name:    Optional[str]  = None
    driver_license: Optional[str]  = None
    driver_phone:   Optional[str]  = None


# ══ خطة أقساط العقد ════════════════════════════════════════════════════
class ContractInstallmentIn(BaseModel):
    """قسط واحد في قالب خطة دفع العقد."""
    label:       Optional[str] = None   # "دفعة مقدمة" / "دفعة تصنيع"...
    pct:         Decimal                # نسبة مئوية من إجمالي العقد (0-100)
    days_offset: int = 0                # أيام من تاريخ تأكيد التسليم (0 = يوم التسليم)


class ContractScheduleIn(BaseModel):
    installments: List[ContractInstallmentIn]


def _contract_template_dict(t: PaymentScheduleTemplate) -> dict:
    return {
        "id": t.id, "installment_no": t.installment_no,
        "label": t.label, "pct": float(t.pct), "days_offset": t.days_offset,
    }


async def _materialize_contract_schedule(
    db: AsyncSession, contract: SupplyContract, invoice: Invoice, tenant_id: int,
) -> bool:
    """
    لو العقد عنده قالب خطة دفع، يُطبّقه على الفاتورة المُنشأة عند تأكيد التسليم.
    النسب تتحول لمبالغ حقيقية، والأيام تُحسب من تاريخ الفاتورة.
    يرجع True لو طبّق قالب، False لو مفيش.
    """
    tmpl_rows = (await db.execute(
        select(PaymentScheduleTemplate)
        .where(
            PaymentScheduleTemplate.source_type == "supply_contract",
            PaymentScheduleTemplate.source_id == contract.id,
        )
        .order_by(PaymentScheduleTemplate.installment_no)
    )).scalars().all()
    if not tmpl_rows:
        return False

    amounts = [
        (invoice.total * t.pct / Decimal("100")).quantize(Decimal("0.01"))
        for t in tmpl_rows
    ]
    # تصحيح فرق التقريب على آخر قسط
    diff = invoice.total - sum(amounts)
    if amounts:
        amounts[-1] = amounts[-1] + diff

    for t, amt in zip(tmpl_rows, amounts):
        db.add(InvoicePaymentSchedule(
            tenant_id=tenant_id,
            invoice_id=invoice.id,
            installment_no=t.installment_no,
            label=t.label,
            amount_due=amt,
            due_date=invoice.date + timedelta(days=t.days_offset),
        ))
    return True


async def _next_inv_code(db: AsyncSession) -> str:
    """توليد كود فاتورة فريد — مستقل عن sales.py لتجنب الاستيراد الدائري"""
    count = await db.scalar(select(func.count()).select_from(Invoice))
    n = (count or 0) + 1
    while True:
        code = f"INV-{n:04d}"
        exists = await db.scalar(select(Invoice.id).where(Invoice.code == code))
        if not exists:
            return code
        n += 1


@router.post("/{contract_id}/deliver")
async def confirm_delivery(contract_id: int, body: DeliveryIn, db: AsyncSession = Depends(get_db)):
    """تسجيل إقرار التسليم والاستلام + تحديث المخزون تلقائياً"""
    res = await db.execute(
        (await _tenant_contract_query(contract_id)).options(
            selectinload(SupplyContract.lines).selectinload(SupplyContractLine.material)
        )
    )
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    if c.status != "active":
        raise HTTPException(400, f"لا يمكن تأكيد التسليم — حالة العقد الحالية: {c.status}. يجب تفعيل العقد أولاً")
    if c.delivery_confirmed:
        raise HTTPException(400, "تم تأكيد التسليم مسبقاً")
    # حماية من التنفيذ المزدوج المتزامن (ضغطتين/طلبين في نفس اللحظة): نحجز
    # التسليم بتحديث ذرّي مشروط — الطلب التاني هيلاقي rowcount = 0. من غيره
    # الطلبين كانوا بيعدّوا الفحص اللي فوق ويصرفوا المخزون مرتين ويعملوا
    # فاتورتين وقيدين محاسبيين.
    from sqlalchemy import update as _sa_update, or_ as _or
    _claim = await db.execute(
        _sa_update(SupplyContract)
        .where(
            SupplyContract.id == c.id,
            _or(SupplyContract.delivery_confirmed.is_(None), SupplyContract.delivery_confirmed == False),  # noqa: E712
        )
        .values(delivery_confirmed=True)
        .execution_options(synchronize_session=False)
    )
    if _claim.rowcount == 0:
        raise HTTPException(400, "تم تأكيد التسليم مسبقاً")
    delivery_date = body.delivery_date or date.today()
    c.delivery_confirmed = True
    c.delivery_date      = delivery_date
    c.received_by        = body.received_by
    c.receiver_title     = body.receiver_title
    c.delivery_notes     = body.delivery_notes
    c.vehicle_plate      = body.vehicle_plate
    c.vehicle_type       = body.vehicle_type
    c.driver_name        = body.driver_name
    c.driver_license     = body.driver_license
    c.driver_phone       = body.driver_phone

    # ── تحديث المخزون تلقائياً لبنود مرتبطة بمواد ──────────────────
    # كانت الدالة دي بتبني StockMovement مباشرة وتضيفه بـ db.add() من غير
    # ما تمر على inventory_ledger.post_issue() — يعني Material.stock_qty
    # (والـ StockQuant المطابق) ميتحدّثش خالص عند تسليم عقد توريد لعميل:
    # المخزون فعلياً كان بيفضل زي ما هو حتى بعد "تسليم" الكمية، وكمان مفيش
    # أي فحص للمتاح (ممكن يتسلّم أكتر من الرصيد الموجود من غير أي تحذير).
    # نفس ما بيحصل في purchases.py/purchase_orders.py، لازم نمر من نفس
    # المصدر الموحّد لحركات الصادر.
    from app.services.inventory_ledger import post_issue
    movements_created = 0
    ref = f"SC-{c.code}"
    # مراجعة تكامل (integration review): post_issue بيسجّل حركة المخزون فقط —
    # لازم نراكم تكلفة البضاعة المصروفة هنا عشان نقيّد COGS فعلي (زي بالظبط
    # delivery_orders.py._release_fg_stock_for_line) بدل ما تتسجل إيرادات
    # الفاتورة (تحت) من غير أي تكلفة مقابلة أبداً في دفتر الأستاذ.
    cogs_amount = Decimal("0")
    for line in c.lines:
        if not line.material_id:
            continue
        mat = await db.get(Material, line.material_id)
        if not mat:
            continue
        if mat.tenant_id != c.tenant_id:
            raise HTTPException(400, "المادة غير موجودة")
        unit_cost = mat.unit_cost or Decimal("0")
        await post_issue(
            db, material=mat, qty=line.qty, unit_cost=unit_cost,
            movement_date=delivery_date,
            source_doc_type="supply_contract", source_doc_id=c.id,
            reference=ref,
            notes=f"تسليم أمر توريد {c.code} — {line.description}",
            destination="customer",
            destination_ref=str(c.customer_id),
            customer_id=c.customer_id,
        )
        movements_created += 1
        cogs_amount += Decimal(str(line.qty)) * unit_cost

    # ── إنشاء فاتورة مبيعات تلقائياً عند تأكيد التسليم ────────────
    inv_code = await _next_inv_code(db)
    # تحقق حد الائتمان
    from app.api.sales import _check_credit_limit
    await _check_credit_limit(db, c.customer_id, c.total)
    inv = Invoice(
        tenant_id    = c.tenant_id,
        code         = inv_code,
        customer_id  = c.customer_id,
        quotation_id = c.quotation_id,
        date         = delivery_date,
        tax_pct      = c.tax_pct,
        discount_pct = Decimal("0"),
        subtotal     = c.subtotal,
        discount_amt = c.discount_amt,
        tax_amount   = c.tax_amount,
        total        = c.total,
        notes        = f"فاتورة مُنشأة تلقائياً عند تأكيد تسليم أمر التوريد {c.code}",
    )
    db.add(inv)
    await db.flush()  # لاحتياج inv.id
    for i, line in enumerate(c.lines):
        db.add(InvoiceLine(
            tenant_id   = c.tenant_id,
            invoice_id  = inv.id,
            item_type   = line.item_type,
            model_no    = line.model_no,
            description = line.description,
            qty         = line.qty,
            unit        = line.unit,
            unit_price  = line.unit_price,
            total_price = line.total_price,
            notes       = line.notes,
            sort_order  = i,
        ))
    # رصيد العميل: موجب = مديون (العميل عليه مبلغ)
    cust = await db.get(Customer, c.customer_id)
    if cust:
        cust.balance = cust.balance + c.total

    # قيد محاسبي تلقائي
    from app.core.database import current_tenant_id as _ctid
    from app.services.auto_journal import journal_sales_invoice_create, journal_cogs
    from decimal import Decimal as _D
    _tid = _ctid.get() or c.tenant_id
    await journal_sales_invoice_create(
        db, _tid, inv.id, inv.code,
        # c.total شامل الضريبة (subtotal - discount + tax_amount) — كان بيتبعت
        # هنا 0 فالضريبة كلها كانت بتتقيد إيراد (4100) بدل التزام ضريبي (2310)،
        # بعكس فواتير المبيعات العادية (sales.py) اللي بتفصلها صح.
        _D(str(c.tax_amount or 0)),
        c.total,
        delivery_date,
    )
    # قيد COGS الفعلي (مدين 5100 / دائن 1310) بنفس تكلفة المواد المصروفة
    # فعليًا أعلاه — من غير ده، الإيراد بيتسجل من غير أي تكلفة مقابلة،
    # فتفضل الأرباح المُبلَّغ عنها لكل عقود التوريد مبالغ فيها بشكل دائم.
    await journal_cogs(db, _tid, c.id, c.code, cogs_amount, delivery_date)

    # ── خطة أقساط العقد ── لو العقد عنده قالب، طبّقه على الفاتورة الجديدة
    schedule_applied = await _materialize_contract_schedule(db, c, inv, _tid)
    if schedule_applied:
        # زامن invoice.due_date ليعكس أقرب قسط
        await db.flush()
        await sync_invoice_due_date(db, inv)

    await db.commit()
    return {
        "id": contract_id,
        "delivery_confirmed": True,
        "delivery_date": str(c.delivery_date),
        "inventory_movements": movements_created,
        "invoice_id": inv.id,
        "invoice_code": inv.code,
        "schedule_applied": schedule_applied,
    }


@router.delete("/{contract_id}", status_code=204)
async def delete_contract(contract_id: int, db: AsyncSession = Depends(get_db)):
    c = (await db.execute(await _tenant_contract_query(contract_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    if c.status == "active":
        raise HTTPException(400, "لا يمكن حذف أمر توريد ساري. أوقفه أولاً")
    lines = (await db.execute(
        select(SupplyContractLine).where(SupplyContractLine.contract_id == contract_id)
    )).scalars().all()
    for l in lines:
        await db.delete(l)
    # احذف قالب الأقساط المرتبط بالعقد (لو موجود)
    await db.execute(
        sa_delete(PaymentScheduleTemplate).where(
            PaymentScheduleTemplate.source_type == "supply_contract",
            PaymentScheduleTemplate.source_id == contract_id,
        )
    )

    # حذف مرفقات العقد (لو وُجدت عبر endpoint المرفقات العام /purchases/attach)
    # — كانت بتفضل يتيمة (سجل + ملف على القرص) بعد حذف العقد نفسه.
    from app.models.models import FileAttachment
    from app.api.purchases import UPLOAD_DIR as _SC_UPLOAD_DIR
    atts_res = await db.execute(
        select(FileAttachment).where(
            FileAttachment.entity_type == "supply_contract",
            FileAttachment.entity_id == contract_id,
        )
    )
    for att in atts_res.scalars().all():
        att_path = _SC_UPLOAD_DIR / att.stored_name
        if att_path.exists():
            att_path.unlink()
        await db.delete(att)

    await db.delete(c)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# PRINT — طباعة عقد التوريد
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
        f'<img src="{_esc(prof.logo_data)}" alt="logo" '
        'style="max-height:80px;max-width:170px;object-fit:contain;display:block;"/>'
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



def _contract_html(c: SupplyContract, company: dict, schedule_template: list = None) -> str:
    import json as _j

    cname    = _esc(company.get("name", ""))
    csub     = _esc(company.get("sub", ""))
    cphone   = _esc(company.get("phone", ""))
    caddr    = _esc(company.get("address", ""))
    legal_h  = _esc(company.get("legal_html", ""))
    logo_html = company.get("logo_html", "")

    cust_name  = _esc(c.customer.name  if c.customer else "—")
    cust_phone = _esc(c.customer.phone if c.customer else "")
    cust_addr  = _esc(c.customer.address if c.customer else "")
    cust_tax   = _esc(c.customer.tax_id if c.customer else "")

    code_v  = _esc(c.code)
    date_v  = _esc(str(c.date))
    valid_v = _esc(str(c.valid_until)) if c.valid_until else "غير محدد"
    pay_t   = _esc(c.payment_terms  or "—")
    del_t   = _esc(c.delivery_terms or "—")
    warr_v  = f"{c.warranty_months} شهر"
    scope_v = _esc(c.scope_of_work  or "")
    spec_t  = _esc(c.special_terms  or "")
    notes_v = _esc(c.notes or "")

    # delivery
    del_confirmed = bool(c.delivery_confirmed)
    del_date      = _esc(str(c.delivery_date)) if c.delivery_date else ""
    del_rcvr      = _esc(c.received_by    or "")
    del_title     = _esc(c.receiver_title or "")
    del_notes     = _esc(c.delivery_notes or "")
    veh_plate   = _esc(c.vehicle_plate   or "")
    veh_type    = _esc(c.vehicle_type    or "")
    drv_name    = _esc(c.driver_name     or "")
    drv_license = _esc(c.driver_license  or "")
    drv_phone   = _esc(c.driver_phone    or "")

    rows_html = ""
    for i, l in enumerate(c.lines or []):
        bg = "#fafafa" if i % 2 == 0 else "#fff"
        rows_html += (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:9px 12px;text-align:center;font-size:12px;color:#666'>{i+1}</td>"
            f"<td style='padding:9px 12px'><b>{_esc(l.description)}</b>"
            f"{'<br><small style=color:#888>' + _esc(l.model_no) + '</small>' if l.model_no else ''}</td>"
            f"<td style='padding:9px 12px;text-align:center'>{float(l.qty):.0f} {_esc(l.unit)}</td>"
            f"<td style='padding:9px 12px;text-align:left;font-variant-numeric:tabular-nums'>{float(l.unit_price):,.2f}</td>"
            f"<td style='padding:9px 12px;text-align:left;font-weight:700;font-variant-numeric:tabular-nums'>{float(l.total_price):,.2f}</td>"
            "</tr>"
        )

    subtotal = float(c.subtotal)
    tax_pct  = float(c.tax_pct)
    tax_amt  = float(c.tax_amount)
    disc_amt = float(c.discount_amt)
    total_v  = float(c.total)

    totals_rows = f"<tr><td>الاجمالي قبل الضريبة</td><td style='text-align:left;font-weight:600'>{subtotal:,.2f}</td></tr>"
    if disc_amt > 0:
        totals_rows += f"<tr><td>الخصم</td><td style='text-align:left;color:#C0392B'>- {disc_amt:,.2f}</td></tr>"
    if tax_pct > 0:
        totals_rows += f"<tr><td>ضريبة ({tax_pct:.0f}%)</td><td style='text-align:left;color:#555'>+ {tax_amt:,.2f}</td></tr>"

    logo_wrap = (
        f'<div style="background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:6px;">'
        f'{logo_html}</div>'
        if logo_html else ""
    )
    from_details = "".join(f'<div class="party-detail">{v}</div>' for v in [cphone, caddr] if v)
    cust_tax_line = f"الرقم الضريبي: {cust_tax}" if cust_tax else ""
    cust_details = "".join(f'<div class="party-detail">{v}</div>' for v in [cust_phone, cust_addr, cust_tax_line] if v)

    scope_section = (
        f'<div class="clause-box"><div class="clause-title">نطاق الأعمال / المواصفات</div>'
        f'<div class="clause-body">{scope_v}</div></div>'
        if scope_v else ""
    )

    # ── جدول السداد (من قالب الأقساط إن وُجد) ──
    schedule_section = ""
    if schedule_template:
        _accent = "#1a3c5e"
        s_rows = ""
        for t in schedule_template:
            day_label = (
                "يوم التسليم" if t.days_offset == 0
                else f"خلال {t.days_offset} يوم من التسليم"
            )
            est_amount = float(c.total * t.pct / Decimal("100"))
            s_rows += (
                f"<tr style='border-bottom:1px solid #f0f0f0'>"
                f"<td style='padding:8px 12px;text-align:center;font-weight:700'>{t.installment_no}</td>"
                f"<td style='padding:8px 12px'>{_esc(t.label or '')}</td>"
                f"<td style='padding:8px 12px;text-align:center'>{float(t.pct):.1f}%</td>"
                f"<td style='padding:8px 12px'>{day_label}</td>"
                f"<td style='padding:8px 12px;text-align:left;font-weight:700;color:{_accent}'>{est_amount:,.2f} ج.م</td>"
                f"</tr>"
            )
        schedule_section = f"""
<div style="margin-top:18px;page-break-inside:avoid;">
  <div style="font-weight:800;font-size:13px;margin-bottom:8px;color:#1a1a1a;border-right:4px solid {_accent};padding-right:8px;">
    جدول السداد المتفق عليه
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:12px;direction:rtl;">
    <thead>
      <tr style="background:{_accent};color:#fff;">
        <th style="padding:9px 12px;text-align:center">قسط</th>
        <th style="padding:9px 12px">التسمية</th>
        <th style="padding:9px 12px;text-align:center">النسبة</th>
        <th style="padding:9px 12px">الاستحقاق</th>
        <th style="padding:9px 12px;text-align:left">المبلغ التقريبي</th>
      </tr>
    </thead>
    <tbody>{s_rows}</tbody>
  </table>
  <div style="font-size:11px;color:#888;margin-top:6px;">
    * المبالغ تقريبية محسوبة من إجمالي العقد. تتحول لمبالغ نهائية عند إصدار الفاتورة.
  </div>
</div>"""

    special_section = (
        f'<div class="clause-box"><div class="clause-title">شروط خاصة</div>'
        f'<div class="clause-body">{spec_t}</div></div>'
        if spec_t else ""
    )
    notes_section = (
        f'<div class="clause-box"><div class="clause-title">ملاحظات</div>'
        f'<div class="clause-body">{notes_v}</div></div>'
        if notes_v else ""
    )

    # إقرار التسليم والاستلام
    if del_confirmed:
        delivery_banner = f"""<div style="background:#F0FDF4;border:1.5px solid #86EFAC;border-radius:10px;padding:10px 16px;margin-bottom:16px;display:flex;align-items:center;gap:12px;">
          <span style="font-size:22px;">✅</span>
          <div><div style="font-size:13px;font-weight:800;color:#15803D;">تم التسليم والاستلام رسمياً</div>
          <div style="font-size:11px;color:#166534;">تاريخ التسليم: <b>{del_date}</b></div></div></div>"""
    else:
        delivery_banner = ""

    rcvr_name_display  = del_rcvr  if del_rcvr  else "___________________________"
    rcvr_title_display = del_title if del_title else "___________________________"
    delivery_notes_html = f'<div style="font-size:11px;color:#555;margin-top:6px;padding:8px;background:#F9FAFB;border-radius:6px;">ملاحظات الاستلام: {del_notes}</div>' if del_notes else ""

    footer_items = " | ".join(filter(None, [
        f"هاتف: {cphone}" if cphone else "",
        f"العنوان: {caddr}" if caddr else "",
        legal_h,
    ]))
    js_code = _j.dumps(code_v)

    status_map = {
        "draft":     ("مسودة",   "#888"),
        "active":    ("ساري",    "#1a7a3c"),
        "expired":   ("منتهي",   "#854F0B"),
        "cancelled": ("ملغي",    "#A32D2D"),
    }
    st_label, st_color = status_map.get(c.status, ("—", "#888"))
    delivered_badge = (
        '<span class="status-pill" style="color:#15803D;border-color:#86EFAC;margin-right:8px;">✅ مُسلَّم</span>'
        if del_confirmed else ""
    )

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<title>عقد توريد - {code_v}</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{{--navy:#0C2340;--navy-dk:#081828;--gold:#C8A550;--surface:#F8F7F4;--border:#E8E6E0;--text:#1A1A2E;--muted:#7A7870;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Cairo',Tahoma,Arial,sans-serif;font-size:13px;color:var(--text);background:#EDECEA;direction:rtl;line-height:1.7;}}
@page{{size:A4;margin:8mm 7mm;}}
@media print{{.no-print{{display:none!important;}}body{{-webkit-print-color-adjust:exact;print-color-adjust:exact;background:#fff;}}.page{{max-width:100%;box-shadow:none;border-radius:0;margin:0;}}}}
.page{{max-width:840px;margin:16px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 60px rgba(0,0,0,.13);}}
.doc-header{{background:linear-gradient(135deg,var(--navy-dk) 0%,var(--navy) 60%,#143558 100%);color:#fff;padding:24px 30px 20px;display:flex;justify-content:space-between;align-items:center;}}
.company-block{{display:flex;align-items:center;gap:18px;}}
.company-name{{font-size:21px;font-weight:800;line-height:1.2;}}
.company-sub{{font-size:11px;color:rgba(255,255,255,.5);margin-top:3px;}}
.company-phone{{font-size:12px;color:var(--gold);margin-top:6px;font-weight:600;}}
.doc-meta{{text-align:left;}}
.doc-type{{font-size:26px;font-weight:900;color:var(--gold);line-height:1;}}
.doc-badge{{display:inline-flex;align-items:center;gap:6px;margin-top:8px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 12px;}}
.badge-label{{font-size:11px;color:rgba(255,255,255,.5);}}
.badge-val{{font-size:12.5px;color:#fff;font-weight:700;}}
.gold-stripe{{height:4px;background:linear-gradient(90deg,var(--gold),rgba(200,165,80,.4),var(--gold));}}
.status-bar{{background:#0D2440;padding:8px 30px;display:flex;justify-content:space-between;align-items:center;}}
.status-pill{{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(255,255,255,.2);font-size:11px;font-weight:700;padding:3px 14px;border-radius:99px;}}
.body{{padding:24px 30px;}}
.parties{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:20px;}}
.party-box{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 18px;border-top:3px solid var(--gold);}}
.party-label{{font-size:10px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:7px;}}
.party-name{{font-size:15px;font-weight:800;margin-bottom:4px;}}
.party-detail{{font-size:11.5px;color:var(--muted);line-height:1.7;}}
.meta-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px;}}
.meta-box{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 14px;}}
.meta-label{{font-size:10px;font-weight:700;color:var(--muted);letter-spacing:.08em;margin-bottom:4px;}}
.meta-value{{font-size:13px;font-weight:600;}}
table.items-table{{width:100%;border-collapse:collapse;margin-bottom:20px;border-radius:12px;overflow:hidden;border:1px solid var(--border);}}
.items-table thead tr{{background:var(--navy);}}
.items-table thead th{{padding:10px 12px;font-size:11px;font-weight:700;color:rgba(255,255,255,.75);text-align:right;}}
.items-table thead th:last-child,.items-table thead th:nth-last-child(2){{text-align:left;}}
.items-table tbody td{{padding:9px 12px;border-bottom:1px solid var(--border);font-size:12.5px;}}
.items-table tbody tr:last-child td{{border-bottom:none;}}
.totals-wrap{{display:flex;justify-content:flex-end;margin-bottom:20px;}}
table.totals-tbl{{width:260px;border-collapse:collapse;}}
.totals-tbl td{{padding:6px 10px;font-size:12.5px;border-bottom:1px dashed var(--border);}}
.grand-row{{background:linear-gradient(135deg,var(--navy-dk),var(--navy));border-radius:10px;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;}}
.grand-label{{font-size:13px;font-weight:600;color:rgba(255,255,255,.65);}}
.grand-value{{font-size:22px;font-weight:900;color:var(--gold);font-variant-numeric:tabular-nums;}}
.intro-box{{background:#F0F4FF;border:1px solid #DBEAFE;border-radius:10px;padding:14px 18px;margin-bottom:18px;font-size:12.5px;line-height:1.9;}}
.clause-box{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:14px;}}
.clause-title{{font-size:11px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:8px;border-bottom:1px solid var(--border);padding-bottom:6px;}}
.clause-body{{font-size:13px;line-height:1.8;white-space:pre-wrap;}}
/* إقرار التسليم */
.delivery-section{{border:2px solid #0C2340;border-radius:12px;padding:18px 22px;margin:20px 0;}}
.delivery-title{{font-size:14px;font-weight:900;color:#0C2340;text-align:center;margin-bottom:14px;padding-bottom:10px;border-bottom:1.5px solid #E8E6E0;letter-spacing:.05em;}}
.delivery-meta{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px;}}
.delivery-field{{background:#F8F7F4;border:1px solid #E8E6E0;border-radius:8px;padding:8px 12px;}}
.delivery-field-label{{font-size:10px;font-weight:800;color:#7A7870;margin-bottom:3px;}}
.delivery-field-value{{font-size:13px;font-weight:700;}}
.sig-section{{border:2px solid var(--navy);border-radius:12px;padding:18px 22px;margin:20px 0;}}
.sig-section-title{{font-size:14px;font-weight:900;color:var(--navy);text-align:center;margin-bottom:14px;padding-bottom:10px;border-bottom:1.5px solid var(--border);letter-spacing:.05em;}}
.sig-row-2{{display:grid;grid-template-columns:1fr 1fr;gap:24px;}}
.sig-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-top:14px;}}
.sig-box{{border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center;}}
.sig-party{{font-size:12px;font-weight:800;margin-bottom:3px;}}
.sig-party-sub{{font-size:10px;color:var(--muted);margin-bottom:36px;}}
.sig-name-fill{{font-size:12px;font-weight:700;color:#0C2340;margin-bottom:2px;min-height:18px;}}
.sig-title-fill{{font-size:10px;color:#7A7870;margin-bottom:6px;min-height:15px;}}
.sig-line{{border-top:1px solid #CCC;margin:0 10px;padding-top:6px;font-size:10px;color:#aaa;}}
.doc-footer{{background:linear-gradient(90deg,var(--navy-dk),var(--navy));color:rgba(255,255,255,.6);padding:12px 30px;text-align:center;font-size:11.5px;}}
.action-bar{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);display:flex;gap:8px;z-index:99;background:rgba(8,24,40,.85);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:8px 12px;}}
.action-bar button{{padding:8px 18px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;border:none;font-family:'Cairo',sans-serif;}}
.btn-print{{background:var(--gold);color:#fff;}}
</style>
</head>
<body onload="setTimeout(()=>window.print(),400)">
<div class="page">
  <div class="doc-header">
    <div class="company-block">
      {logo_wrap}
      <div>
        <div class="company-name">{cname}</div>
        <div class="company-sub">{csub}</div>
        <div class="company-phone">{cphone}</div>
      </div>
    </div>
    <div class="doc-meta">
      <div class="doc-type">عقد توريد</div>
      <div class="doc-badge"><span class="badge-label">رقم:</span><span class="badge-val">{code_v}</span></div>
      <div class="doc-badge" style="margin-top:5px;"><span class="badge-label">تاريخ:</span><span class="badge-val">{date_v}</span></div>
    </div>
  </div>
  <div class="gold-stripe"></div>
  <div class="status-bar">
    <div style="display:flex;align-items:center;">
      <span class="status-pill" style="color:{st_color};border-color:{st_color}40;">
        <span style="width:6px;height:6px;background:currentColor;border-radius:50%;display:inline-block;"></span>
        {st_label}
      </span>
      {delivered_badge}
    </div>
    <span style="font-size:11px;color:rgba(255,255,255,.5)">الضمان: <b style="color:rgba(255,255,255,.7)">{warr_v}</b></span>
  </div>
  <div class="body">
    {delivery_banner}
    <div class="parties">
      <div class="party-box">
        <div class="party-label">الطرف الأول — المورد</div>
        <div class="party-name">{cname}</div>
        {from_details}
      </div>
      <div class="party-box">
        <div class="party-label">الطرف الثاني — العميل</div>
        <div class="party-name">{cust_name}</div>
        {cust_details}
      </div>
    </div>
    <div class="meta-grid">
      <div class="meta-box"><div class="meta-label">تاريخ العقد</div><div class="meta-value">{date_v}</div></div>
      <div class="meta-box"><div class="meta-label">الصلاحية حتى</div><div class="meta-value">{valid_v}</div></div>
      <div class="meta-box"><div class="meta-label">فترة الضمان</div><div class="meta-value">{warr_v}</div></div>
      <div class="meta-box"><div class="meta-label">شروط الدفع</div><div class="meta-value">{pay_t}</div></div>
      <div class="meta-box" style="grid-column:span 2"><div class="meta-label">شروط التسليم</div><div class="meta-value">{del_t}</div></div>
    </div>
    <div class="intro-box">
      اتفق الطرفان المُوقِّعان أدناه على توريد الأصناف والكميات الواردة في الجدول التالي،
      وفقاً للأسعار والشروط المُبيَّنة في هذا العقد، ويُعدّ توقيع الطرفين عليه بمثابة
      موافقة نهائية وملزمة على الأسعار والشروط المذكورة.
    </div>
    {scope_section}
    {schedule_section}
    <table class="items-table">
      <thead><tr>
        <th style="width:38px;text-align:center">#</th>
        <th>الصنف / الوصف</th>
        <th style="width:110px;text-align:center">الكمية</th>
        <th style="width:120px;text-align:left">سعر الوحدة</th>
        <th style="width:120px;text-align:left">الاجمالي</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    <div class="totals-wrap">
      <div>
        <table class="totals-tbl"><tbody>{totals_rows}</tbody></table>
        <div class="grand-row" style="margin-top:8px;">
          <span class="grand-label">اجمالي قيمة عقد التوريد</span>
          <span class="grand-value">{total_v:,.2f} ج.م</span>
        </div>
      </div>
    </div>
    {special_section}
    {notes_section}

    <!-- ══ توقيع الطرفين على العقد ══ -->
    <div class="sig-section">
      <div class="sig-section-title">توقيع الطرفين على بنود وأسعار وشروط هذا العقد</div>
      <div class="sig-row-2">
        <div class="sig-box">
          <div class="sig-party">المورد — الطرف الأول</div>
          <div class="sig-party-sub">{cname}</div>
          <div class="sig-name-fill"></div>
          <div class="sig-title-fill"></div>
          <div class="sig-line">الاسم والتوقيع والختم</div>
        </div>
        <div class="sig-box">
          <div class="sig-party">العميل — الطرف الثاني</div>
          <div class="sig-party-sub">{cust_name}</div>
          <div class="sig-name-fill"></div>
          <div class="sig-title-fill"></div>
          <div class="sig-line">الاسم والتوقيع والختم</div>
        </div>
      </div>
    </div>

    <!-- ══ إقرار التسليم والاستلام ══ -->
    <div class="delivery-section">
      <div class="delivery-title">إقرار التسليم والاستلام</div>
      <div class="delivery-meta">
        <div class="delivery-field">
          <div class="delivery-field-label">تاريخ التسليم الفعلي</div>
          <div class="delivery-field-value">{del_date if del_date else "_____ / _____ / _______"}</div>
        </div>
        <div class="delivery-field">
          <div class="delivery-field-label">اسم المستلم</div>
          <div class="delivery-field-value">{rcvr_name_display}</div>
        </div>
        <div class="delivery-field">
          <div class="delivery-field-label">وظيفة / صفة المستلم</div>
          <div class="delivery-field-value">{rcvr_title_display}</div>
        </div>
        <div class="delivery-field">
          <div class="delivery-field-label">رقم لوحة السيارة</div>
          <div class="delivery-field-value">{veh_plate if veh_plate else "___________________"}</div>
        </div>
        <div class="delivery-field">
          <div class="delivery-field-label">نوع السيارة</div>
          <div class="delivery-field-value">{veh_type if veh_type else "___________________"}</div>
        </div>
        <div class="delivery-field">
          <div class="delivery-field-label">اسم السائق</div>
          <div class="delivery-field-value">{drv_name if drv_name else "___________________"}</div>
        </div>
        <div class="delivery-field">
          <div class="delivery-field-label">رقم رخصة القيادة</div>
          <div class="delivery-field-value">{drv_license if drv_license else "___________________"}</div>
        </div>
        <div class="delivery-field">
          <div class="delivery-field-label">موبايل السائق</div>
          <div class="delivery-field-value">{drv_phone if drv_phone else "___________________"}</div>
        </div>
      </div>
      {delivery_notes_html}
      <div class="sig-row">
        <div class="sig-box">
          <div class="sig-party">المورد — الطرف الأول</div>
          <div class="sig-party-sub">{cname}</div>
          <div class="sig-name-fill"></div>
          <div class="sig-title-fill"></div>
          <div class="sig-line">الاسم والتوقيع والختم</div>
        </div>
        <div class="sig-box">
          <div class="sig-party">المستلم — الطرف الثاني</div>
          <div class="sig-party-sub">{cust_name}</div>
          <div class="sig-name-fill">{rcvr_name_display}</div>
          <div class="sig-title-fill">{rcvr_title_display}</div>
          <div class="sig-line">الاسم والتوقيع والختم</div>
        </div>
        <div class="sig-box">
          <div class="sig-party">شاهد / مُحرِّر العقد</div>
          <div class="sig-party-sub">المسؤول المختص</div>
          <div class="sig-name-fill"></div>
          <div class="sig-title-fill"></div>
          <div class="sig-line">الاسم والتوقيع والختم</div>
        </div>
      </div>
    </div>

  </div>
  <div class="doc-footer">{footer_items if footer_items else cname}</div>
</div>
<div class="action-bar no-print">
  <button class="btn-print" onclick="(function(){{document.title='عقد توريد '+{js_code};window.print();}})()">طباعة / PDF</button>
</div>
</body></html>"""


@print_router.get("/{contract_id}/print", response_class=HTMLResponse)
async def print_contract(contract_id: int, db: AsyncSession = Depends(get_db)):
    """طباعة أمر التوريد مع جدول السداد لو موجود"""
    res = await db.execute(
        (await _tenant_contract_query(contract_id)).options(
            selectinload(SupplyContract.customer),
            selectinload(SupplyContract.lines).selectinload(SupplyContractLine.material),
        )
    )
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(404)

    # جلب قالب الأقساط لإظهاره في الطباعة
    tmpl_rows = (await db.execute(
        select(PaymentScheduleTemplate)
        .where(
            PaymentScheduleTemplate.source_type == "supply_contract",
            PaymentScheduleTemplate.source_id == contract_id,
        )
        .order_by(PaymentScheduleTemplate.installment_no)
    )).scalars().all()

    company = await _company(db)
    return _contract_html(c, company, schedule_template=tmpl_rows)


# ── وصل التسليم (بدون أسعار) ──────────────────────────────────────────────────
def _delivery_slip_html(c: SupplyContract, company: dict) -> str:
    cname  = _esc(company.get("name", ""))
    csub   = _esc(company.get("sub", ""))
    cphone = _esc(company.get("phone", ""))
    caddr  = _esc(company.get("address", ""))
    logo_html = company.get("logo_html", "")

    logo_wrap = (
        f'<div style="background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:6px;">'
        f'{logo_html}</div>'
        if logo_html else ""
    )

    cust_name  = _esc(c.customer.name  if c.customer else "—")
    cust_phone = _esc(c.customer.phone if c.customer else "")
    cust_addr  = _esc(c.customer.address if c.customer else "")

    del_date    = _esc(str(c.delivery_date)) if c.delivery_date else "—"
    del_rcvr    = _esc(c.received_by    or "")
    del_title   = _esc(c.receiver_title or "")
    del_notes   = _esc(c.delivery_notes or "")
    veh_plate   = _esc(c.vehicle_plate   or "—")
    veh_type    = _esc(c.vehicle_type    or "—")
    drv_name    = _esc(c.driver_name     or "—")
    drv_license = _esc(c.driver_license  or "—")
    drv_phone   = _esc(c.driver_phone    or "—")

    rows_html = ""
    for i, l in enumerate(c.lines or []):
        bg = "#fafafa" if i % 2 == 0 else "#fff"
        rows_html += (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:9px 12px;text-align:center;font-size:12px;color:#666'>{i+1}</td>"
            f"<td style='padding:9px 12px'><b>{_esc(l.description)}</b>"
            f"{'<br><small style=color:#888>' + _esc(l.model_no) + '</small>' if l.model_no else ''}</td>"
            f"<td style='padding:9px 12px;text-align:center'>{float(l.qty):.0f}</td>"
            f"<td style='padding:9px 12px;text-align:center'>{_esc(l.unit)}</td>"
            "</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<title>وصل تسليم — {_esc(c.code)}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@300;400;500;600;700&display=swap');
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'IBM Plex Sans Arabic',sans-serif;background:#fff;color:#1a1a2e;font-size:13px;direction:rtl}}
  @page{{size:A4;margin:15mm 12mm}}
  @media print{{body{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}}}
  .header{{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 60%,#0f3460 100%);color:#fff;padding:20px 24px;border-radius:12px;display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}}
  .header-left h1{{font-size:22px;font-weight:700;letter-spacing:.5px}}
  .header-left p{{font-size:11px;opacity:.75;margin-top:3px}}
  .title-badge{{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.25);border-radius:8px;padding:8px 18px;text-align:center}}
  .title-badge .doc-type{{font-size:18px;font-weight:700}}
  .title-badge .doc-code{{font-size:12px;opacity:.8;margin-top:2px}}
  .info-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px}}
  .info-box{{border:1px solid #e0e0e0;border-radius:10px;padding:14px 16px}}
  .info-box h3{{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;border-bottom:1px solid #f0f0f0;padding-bottom:6px}}
  .info-row{{display:flex;justify-content:space-between;margin-bottom:5px;font-size:12.5px}}
  .info-row span:first-child{{color:#666}}
  .info-row span:last-child{{font-weight:500}}
  .vehicle-box{{border:2px solid #0f3460;border-radius:10px;padding:14px 16px;margin-bottom:16px;background:#f8f9ff}}
  .vehicle-box h3{{font-size:12px;color:#0f3460;font-weight:700;margin-bottom:10px;border-bottom:1px solid #dde3f0;padding-bottom:6px}}
  .vehicle-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}
  .v-item{{background:#fff;border:1px solid #dde3f0;border-radius:6px;padding:8px 10px}}
  .v-item .label{{font-size:10px;color:#888;margin-bottom:3px}}
  .v-item .value{{font-size:13px;font-weight:600;color:#0f3460}}
  table{{width:100%;border-collapse:collapse;margin-bottom:16px}}
  th{{background:#0f3460;color:#fff;padding:10px 12px;font-size:12px;font-weight:600}}
  td{{border-bottom:1px solid #f0f0f0;font-size:13px}}
  .sig-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-top:20px}}
  .sig-box{{border:1px solid #ddd;border-radius:10px;padding:14px;text-align:center}}
  .sig-box .sig-title{{font-size:12px;font-weight:700;color:#333;margin-bottom:6px}}
  .sig-box .sig-name{{font-size:11px;color:#666;margin-bottom:40px}}
  .sig-box .sig-line{{border-top:1px solid #333;margin:0 10px;padding-top:6px;font-size:10px;color:#888}}
  .notes-box{{background:#fffbf0;border:1px solid #f0e0a0;border-radius:8px;padding:12px 14px;margin-bottom:16px;font-size:12.5px}}
  .notes-box .label{{font-weight:600;color:#b8860b;margin-bottom:4px;font-size:11px}}
  .no-print{{display:block;text-align:center;margin:20px 0}}
  @media print{{.no-print{{display:none}}}}
</style>
</head>
<body>

<button class="no-print" onclick="window.print()"
  style="padding:10px 30px;background:#0f3460;color:#fff;border:none;border-radius:8px;font-size:14px;cursor:pointer;font-family:inherit">
  🖨 طباعة
</button>

<div class="header">
  <div class="header-left">
    <h1>{cname}</h1>
    <p>{csub}</p>
    <p>{cphone} {'•' if caddr else ''} {caddr}</p>
  </div>
  {logo_wrap}
  <div class="title-badge">
    <div class="doc-type">وصل تسليم بضاعة</div>
    <div class="doc-code">{_esc(c.code)}</div>
  </div>
</div>

<div class="info-grid">
  <div class="info-box">
    <h3>بيانات العميل / المستلم</h3>
    <div class="info-row"><span>الاسم</span><span>{cust_name}</span></div>
    {"<div class='info-row'><span>الهاتف</span><span>" + cust_phone + "</span></div>" if cust_phone else ""}
    {"<div class='info-row'><span>العنوان</span><span>" + cust_addr + "</span></div>" if cust_addr else ""}
    {"<div class='info-row'><span>المستلم</span><span>" + del_rcvr + "</span></div>" if del_rcvr else ""}
    {"<div class='info-row'><span>الصفة</span><span>" + del_title + "</span></div>" if del_title else ""}
  </div>
  <div class="info-box">
    <h3>بيانات التسليم</h3>
    <div class="info-row"><span>رقم الأمر</span><span>{_esc(c.code)}</span></div>
    <div class="info-row"><span>تاريخ التسليم</span><span>{del_date}</span></div>
    <div class="info-row"><span>الحالة</span><span style="color:{'#27ae60' if c.delivery_confirmed else '#e67e22'};font-weight:700">{'✅ مُسلَّم' if c.delivery_confirmed else '⏳ قيد التسليم'}</span></div>
  </div>
</div>

<div class="vehicle-box">
  <h3>🚛 بيانات السيارة والسائق</h3>
  <div class="vehicle-grid">
    <div class="v-item"><div class="label">رقم اللوحة</div><div class="value">{veh_plate}</div></div>
    <div class="v-item"><div class="label">نوع السيارة</div><div class="value">{veh_type}</div></div>
    <div class="v-item"><div class="label">اسم السائق</div><div class="value">{drv_name}</div></div>
    <div class="v-item"><div class="label">رقم الرخصة</div><div class="value">{drv_license}</div></div>
    <div class="v-item"><div class="label">رقم الموبايل</div><div class="value">{drv_phone}</div></div>
  </div>
</div>

<table>
  <thead>
    <tr>
      <th style="width:40px">#</th>
      <th>الوصف / البند</th>
      <th style="width:80px">الكمية</th>
      <th style="width:80px">الوحدة</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>

<div style="margin-top:32px;display:flex;gap:32px;flex-wrap:wrap">
  <div style="flex:1;min-width:220px;background:#f8f9fa;border-radius:10px;padding:16px">
    <h4 style="margin:0 0 12px;font-size:13px;color:#555">بيانات المستلِم</h4>
    <div style="font-size:13px;line-height:2">
      <div><b>اسم المستلم:</b> {del_rcvr}</div>
      <div><b>المسمى الوظيفي:</b> {del_title}</div>
      <div><b>تاريخ الاستلام:</b> {del_date}</div>
      {f'<div><b>ملاحظات:</b> {del_notes}</div>' if del_notes else ''}
    </div>
  </div>
  <div style="flex:1;min-width:220px;background:#f8f9fa;border-radius:10px;padding:16px">
    <h4 style="margin:0 0 12px;font-size:13px;color:#555">بيانات المركبة والسائق</h4>
    <div style="font-size:13px;line-height:2">
      <div><b>لوحة المركبة:</b> {veh_plate}</div>
      <div><b>نوع المركبة:</b> {veh_type}</div>
      <div><b>اسم السائق:</b> {drv_name}</div>
      <div><b>رقم الرخصة:</b> {drv_license}</div>
      <div><b>هاتف السائق:</b> {drv_phone}</div>
    </div>
  </div>
</div>

<div style="margin-top:40px;display:flex;gap:40px;justify-content:space-around;border-top:1px solid #ddd;padding-top:24px">
  <div style="text-align:center">
    <div style="font-size:12px;color:#777;margin-bottom:40px">توقيع المسلِّم</div>
    <div style="border-top:1px solid #333;width:160px;padding-top:6px;font-size:12px">الاسم والتوقيع</div>
  </div>
  <div style="text-align:center">
    <div style="font-size:12px;color:#777;margin-bottom:40px">توقيع المستلِم</div>
    <div style="border-top:1px solid #333;width:160px;padding-top:6px;font-size:12px">الاسم والتوقيع</div>
  </div>
</div>

</div>
</body>
</html>"""


# ══ خطة أقساط العقد — endpoints ══════════════════════════════════════

@router.post("/{contract_id}/payment-schedule-template", status_code=201)
async def save_contract_schedule_template(
    contract_id: int,
    data: ContractScheduleIn,
    replace: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """
    حفظ خطة أقساط على العقد (كنسب % وإزاحة أيام من تاريخ التسليم).
    replace=true → احذف القديم أولاً.
    الشرط: مجموع النسب = 100%.
    """
    c = (await db.execute(await _tenant_contract_query(contract_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "العقد غير موجود")
    if c.delivery_confirmed:
        # القالب بيتحوّل لأقساط فعلية على الفاتورة لحظة تأكيد التسليم — تعديله
        # بعد كده ما بيأثرش على الفاتورة وبيخلّي الطباعة متضاربة معاها.
        raise HTTPException(400, "لا يمكن تعديل خطة الأقساط بعد تأكيد التسليم — عدّل أقساط الفاتورة مباشرة")
    if not data.installments:
        raise HTTPException(400, "أضف قسطاً واحداً على الأقل")
    for ins in data.installments:
        if ins.pct is None or ins.pct <= 0:
            raise HTTPException(400, "نسبة كل قسط يجب أن تكون أكبر من صفر")
        if ins.days_offset is not None and ins.days_offset < 0:
            raise HTTPException(400, "أيام الاستحقاق لا يمكن أن تكون سالبة")

    # تحقق مجموع النسب
    total_pct = sum(ins.pct for ins in data.installments)
    if abs(total_pct - Decimal("100")) > Decimal("0.01"):
        raise HTTPException(400, f"مجموع النسب ({float(total_pct):.2f}%) يجب أن يساوي 100%")

    if replace:
        await db.execute(
            sa_delete(PaymentScheduleTemplate).where(
                PaymentScheduleTemplate.source_type == "supply_contract",
                PaymentScheduleTemplate.source_id == contract_id,
            )
        )

    from app.core.database import current_tenant_id as _ctid_sc
    _tid_sc = _ctid_sc.get() or 1

    rows = []
    for i, ins in enumerate(data.installments, start=1):
        # تحقق عدم تكرار رقم القسط لو مش replace
        if not replace:
            exists = await db.scalar(
                select(PaymentScheduleTemplate.id).where(
                    PaymentScheduleTemplate.source_type == "supply_contract",
                    PaymentScheduleTemplate.source_id == contract_id,
                    PaymentScheduleTemplate.installment_no == i,
                )
            )
            if exists:
                raise HTTPException(
                    400,
                    f"القسط رقم {i} موجود بالفعل — استخدم replace=true لاستبدال الخطة كاملاً",
                )
        t = PaymentScheduleTemplate(
            tenant_id=_tid_sc,
            source_type="supply_contract",
            source_id=contract_id,
            installment_no=i,
            label=ins.label,
            pct=ins.pct,
            days_offset=ins.days_offset,
        )
        db.add(t)
        rows.append(t)

    await db.commit()
    for t in rows:
        await db.refresh(t)
    return [_contract_template_dict(t) for t in rows]


@router.get("/{contract_id}/payment-schedule-template")
async def get_contract_schedule_template(
    contract_id: int, db: AsyncSession = Depends(get_db)
):
    """اجلب قالب خطة الأقساط الحالي للعقد."""
    c = (await db.execute(await _tenant_contract_query(contract_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "العقد غير موجود")
    rows = (await db.execute(
        select(PaymentScheduleTemplate)
        .where(
            PaymentScheduleTemplate.source_type == "supply_contract",
            PaymentScheduleTemplate.source_id == contract_id,
        )
        .order_by(PaymentScheduleTemplate.installment_no)
    )).scalars().all()
    return {
        "contract_id": contract_id,
        "total_pct": float(sum(t.pct for t in rows)),
        "installments": [_contract_template_dict(t) for t in rows],
    }


@router.delete("/{contract_id}/payment-schedule-template", status_code=204)
async def delete_contract_schedule_template(
    contract_id: int, db: AsyncSession = Depends(get_db)
):
    """احذف قالب خطة الأقساط من العقد (لو مش محتاجه)."""
    # كانت بتحذف مباشرة من غير التحقق إن العقد أصلاً موجود/تابع لنفس الـ
    # tenant — أي tenant كان يقدر يحذف قالب أقساط عقد تابع لـ tenant تاني
    # بمجرد تخمين contract_id (تعديل بيانات بين مستأجرين مختلفين).
    c = (await db.execute(await _tenant_contract_query(contract_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "العقد غير موجود")
    await db.execute(
        sa_delete(PaymentScheduleTemplate).where(
            PaymentScheduleTemplate.source_type == "supply_contract",
            PaymentScheduleTemplate.source_id == contract_id,
        )
    )
    await db.commit()


@print_router.get("/{contract_id}/delivery-slip", response_class=HTMLResponse)
async def print_delivery_slip(contract_id: int, db: AsyncSession = Depends(get_db)):
    """طباعة وصل التسليم بدون أسعار"""
    res = await db.execute(
        (await _tenant_contract_query(contract_id)).options(
            selectinload(SupplyContract.customer),
            selectinload(SupplyContract.lines).selectinload(SupplyContractLine.material),
        )
    )
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    company = await _company(db)
    return _delivery_slip_html(c, company)
