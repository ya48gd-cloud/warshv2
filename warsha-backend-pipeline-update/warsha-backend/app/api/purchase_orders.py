"""
Purchase Orders API — أوامر الشراء
إنشاء → موافقة → استلام جزئي/كلي → ربط بفاتورة الشراء
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
from app.services.analytics import log_event, Events
from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import (
    PurchaseOrder, PurchaseOrderLine, Supplier,
    Material, StockMovement, PurchaseInvoice, PurchaseInvoiceLine,
)

router = APIRouter(prefix="/purchase-orders", tags=["purchase_orders"], dependencies=[Depends(require_any)])
print_router = APIRouter(prefix="/purchase-orders", tags=["purchase_orders_print"], dependencies=[Depends(require_any)])


# ═══════════════════════════════════════════════════════════════
# محطة 19: استلام أمر الشراء بموقع فعلي (RAW أو IMPORTED)
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# محطة 19: استلام أمر الشراء بموقع فعلي (RAW أو IMPORTED)
# ═══════════════════════════════════════════════════════════════

from app.services.inventory_ledger import is_import_supplier as _is_import_supplier  # noqa: E402
# ملاحظة: نُقلت الدالة نفسها لـ inventory_ledger.py عشان purchases.py (فواتير
# الشراء اليدوية) تقدر تستخدمها هي كمان بدل ما تكرر نفس المنطق (بند 15 في
# الروادماب الأصلي). الاسم القديم _is_import_supplier فاضل هنا كـ alias
# عشان أي كود تاني في الملف ده يفضل شغال من غير تعديل.


# ── Schemas ────────────────────────────────────────────────────

class POLineIn(BaseModel):
    material_id: Optional[int] = None
    description: str
    ordered_qty: Decimal
    unit: str = "قطعة"
    unit_cost: Decimal


class POIn(BaseModel):
    code: Optional[str] = None
    supplier_id: Optional[int] = None
    supplier_name: Optional[str] = None
    order_date: date
    expected_date: Optional[date] = None
    tax_pct: Decimal = Decimal("0")
    discount_amt: Decimal = Decimal("0")
    notes: Optional[str] = None
    lines: List[POLineIn] = []


class ReceiveLineIn(BaseModel):
    line_id: int
    received_qty: Decimal
    update_cost: bool = True


class ReceiveIn(BaseModel):
    receive_date: date
    lines: List[ReceiveLineIn]
    create_invoice: bool = True


# ── Helpers ────────────────────────────────────────────────────

async def _tenant_po_query(po_id: int):
    """أمر شراء مقيّد بالـ tenant الحالي — يمنع أي tenant من قراءة/تعديل
    أمر شراء تابع لـ tenant تاني بمجرد تخمين الـ id (db.get(PurchaseOrder, id)
    القديمة كانت بترجع أي أمر بغض النظر عن الـ tenant)."""
    from app.core.database import current_tenant_id as _ctid_tpq
    tid = _ctid_tpq.get()
    q = select(PurchaseOrder).where(PurchaseOrder.id == po_id)
    if tid is not None:
        q = q.where(PurchaseOrder.tenant_id == tid)
    return q


async def _next_po_code(db: AsyncSession) -> str:
    count = await db.scalar(select(func.count()).select_from(PurchaseOrder))
    n = (count or 0) + 1
    while True:
        code = f"PO-{n:04d}"
        exists = await db.scalar(select(PurchaseOrder.id).where(PurchaseOrder.code == code))
        if not exists:
            return code
        n += 1


def po_dict(po: PurchaseOrder) -> dict:
    status_ar = {
        "draft": "مسودة", "approved": "موافق عليه",
        "partial": "مستلم جزئياً", "received": "مستلم كاملاً",
        "cancelled": "ملغي",
    }
    return {
        "id": po.id, "code": po.code,
        "supplier_id": po.supplier_id,
        "supplier_name": po.supplier_name or (po.supplier.name if po.supplier else ""),
        "order_date": str(po.order_date),
        "expected_date": str(po.expected_date) if po.expected_date else None,
        "status": po.status,
        "status_ar": status_ar.get(po.status, po.status),
        "subtotal": float(po.subtotal),
        "tax_pct": float(po.tax_pct),
        "tax_amount": float(po.tax_amount),
        "discount_amt": float(po.discount_amt),
        "total": float(po.total),
        "notes": po.notes,
        "approved_at": po.approved_at.isoformat() if po.approved_at else None,
        "created_at": po.created_at.isoformat() if po.created_at else None,
        "lines": [{
            "id": l.id,
            "material_id": l.material_id,
            "description": l.description,
            "ordered_qty": float(l.ordered_qty),
            "received_qty": float(l.received_qty),
            "remaining_qty": float(l.ordered_qty - l.received_qty),
            "unit": l.unit,
            "unit_cost": float(l.unit_cost),
            "total_cost": float(l.total_cost),
        } for l in po.lines] if po.lines else [],
    }


# ══════════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════════

@router.get("")
async def list_purchase_orders(
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    from app.core.database import current_tenant_id as _ctid_lpo
    tid = _ctid_lpo.get()
    q = select(PurchaseOrder).options(
        selectinload(PurchaseOrder.supplier),
        selectinload(PurchaseOrder.lines),
    )
    if tid is not None:
        q = q.where(PurchaseOrder.tenant_id == tid)
    if status:
        q = q.where(PurchaseOrder.status == status)
    if supplier_id:
        q = q.where(PurchaseOrder.supplier_id == supplier_id)
    res = await db.execute(q.order_by(PurchaseOrder.order_date.desc()))
    return [po_dict(po) for po in res.scalars().all()]


@router.get("/next-code")
async def get_next_po_code_ep(db: AsyncSession = Depends(get_db)):
    return {"code": await _next_po_code(db)}


@router.post("", status_code=201)
async def create_purchase_order(data: POIn, db: AsyncSession = Depends(get_db)):
    if not data.lines:
        raise HTTPException(400, "أضف بنداً واحداً على الأقل")
    for l in data.lines:
        if l.ordered_qty is None or l.ordered_qty <= 0:
            raise HTTPException(400, "كمية كل بند يجب أن تكون أكبر من صفر")
        if l.unit_cost is not None and l.unit_cost < 0:
            raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
    if data.order_date and data.expected_date and data.expected_date < data.order_date:
        raise HTTPException(400, "تاريخ التوريد المتوقع يجب أن يكون بعد تاريخ الأمر")
    from app.core.database import current_tenant_id as _ctid_po
    _tid_po = _ctid_po.get() or 1
    code = data.code or await _next_po_code(db)
    exists = await db.scalar(select(PurchaseOrder.id).where(PurchaseOrder.code == code))
    if exists:
        raise HTTPException(400, f"كود {code} مستخدم من قبل")
    if data.supplier_id:
        sup_check = await db.get(Supplier, data.supplier_id)
        if not sup_check or sup_check.tenant_id != _tid_po:
            raise HTTPException(400, "المورد غير موجود")
    # تحقق أن كل مادة مرتبطة ببند فعلاً تابعة لنفس الـ tenant — من غيره كان
    # ممكن نمرر material_id تخميني تابع لـ tenant تاني، فيتسجل في بند أمر
    # الشراء، وبعدين عند الاستلام (receive_purchase_order) يترفع مخزون/سعر
    # مادة مش بتاعتنا خالص (تسريب/عبث فعلي بين المستأجرين).
    for l in data.lines:
        if l.material_id:
            mat_check = await db.get(Material, l.material_id)
            if not mat_check or mat_check.tenant_id != _tid_po:
                raise HTTPException(400, "المادة غير موجودة")

    subtotal = sum(Decimal(str(l.ordered_qty)) * Decimal(str(l.unit_cost)) for l in data.lines)
    tax_amount = subtotal * Decimal(str(data.tax_pct)) / 100
    total = subtotal - Decimal(str(data.discount_amt)) + tax_amount

    po = PurchaseOrder(
        tenant_id=_tid_po,
        code=code,
        supplier_id=data.supplier_id,
        supplier_name=data.supplier_name,
        order_date=data.order_date,
        expected_date=data.expected_date,
        tax_pct=data.tax_pct,
        tax_amount=tax_amount,
        discount_amt=data.discount_amt,
        subtotal=subtotal,
        total=total,
        notes=data.notes,
        status="draft",
    )
    db.add(po)
    await db.flush()

    for ld in data.lines:
        line = PurchaseOrderLine(
            tenant_id=_tid_po,
            order_id=po.id,
            material_id=ld.material_id,
            description=ld.description,
            ordered_qty=ld.ordered_qty,
            unit=ld.unit,
            unit_cost=ld.unit_cost,
            total_cost=Decimal(str(ld.ordered_qty)) * Decimal(str(ld.unit_cost)),
        )
        db.add(line)

    await db.commit()
    await db.refresh(po)
    await log_event(Events.PURCHASE_ORDER_CREATED, tenant_id=_tid_po, entity_type="purchase_order",
                     entity_id=po.id, metadata={"total": float(po.total)}, source_path="/api/v1/purchase-orders")

    # reload with lines
    res = await db.execute(
        select(PurchaseOrder).options(
            selectinload(PurchaseOrder.supplier),
            selectinload(PurchaseOrder.lines),
        ).where(PurchaseOrder.id == po.id)
    )
    return po_dict(res.scalar_one())


@router.get("/{po_id}")
async def get_purchase_order(po_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        (await _tenant_po_query(po_id)).options(
            selectinload(PurchaseOrder.supplier),
            selectinload(PurchaseOrder.lines).selectinload(PurchaseOrderLine.material),
        )
    )
    po = res.scalar_one_or_none()
    if not po:
        raise HTTPException(404)
    return po_dict(po)


@router.put("/{po_id}")
async def update_purchase_order(po_id: int, data: POIn, db: AsyncSession = Depends(get_db)):
    """تعديل أمر شراء — مسموح فقط في حالة مسودة"""
    res = await db.execute(
        (await _tenant_po_query(po_id)).options(selectinload(PurchaseOrder.lines))
    )
    po = res.scalar_one_or_none()
    if not po:
        raise HTTPException(404)
    if po.status != "draft":
        raise HTTPException(400, "لا يمكن تعديل أمر الشراء بعد الموافقة عليه أو إلغائه")
    if not data.lines:
        raise HTTPException(400, "أضف بنداً واحداً على الأقل")
    for l in data.lines:
        if l.ordered_qty is None or l.ordered_qty <= 0:
            raise HTTPException(400, "كمية كل بند يجب أن تكون أكبر من صفر")
        if l.unit_cost is not None and l.unit_cost < 0:
            raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
    if data.order_date and data.expected_date and data.expected_date < data.order_date:
        raise HTTPException(400, "تاريخ التوريد المتوقع يجب أن يكون بعد تاريخ الأمر")

    from app.core.database import current_tenant_id as _ctid_upo
    _tid_upo = _ctid_upo.get() or 1
    if data.supplier_id:
        sup_check = await db.get(Supplier, data.supplier_id)
        if not sup_check or sup_check.tenant_id != _tid_upo:
            raise HTTPException(400, "المورد غير موجود")
    for l in data.lines:
        if l.material_id:
            mat_check = await db.get(Material, l.material_id)
            if not mat_check or mat_check.tenant_id != _tid_upo:
                raise HTTPException(400, "المادة غير موجودة")

    subtotal = sum(Decimal(str(l.ordered_qty)) * Decimal(str(l.unit_cost)) for l in data.lines)
    tax_amount = subtotal * Decimal(str(data.tax_pct)) / 100
    total = subtotal - Decimal(str(data.discount_amt)) + tax_amount

    po.supplier_id = data.supplier_id
    po.supplier_name = data.supplier_name
    po.order_date = data.order_date
    po.expected_date = data.expected_date
    po.tax_pct = data.tax_pct
    po.tax_amount = tax_amount
    po.discount_amt = data.discount_amt
    po.subtotal = subtotal
    po.total = total
    po.notes = data.notes

    for old_line in po.lines:
        await db.delete(old_line)
    await db.flush()

    from app.core.database import current_tenant_id as _ctid_pu
    _tid_pu = _ctid_pu.get() or 1
    for ld in data.lines:
        db.add(PurchaseOrderLine(
            tenant_id=_tid_pu,
            order_id=po.id,
            material_id=ld.material_id,
            description=ld.description,
            ordered_qty=ld.ordered_qty,
            unit=ld.unit,
            unit_cost=ld.unit_cost,
            total_cost=Decimal(str(ld.ordered_qty)) * Decimal(str(ld.unit_cost)),
        ))

    await db.commit()
    res2 = await db.execute(
        select(PurchaseOrder).options(
            selectinload(PurchaseOrder.supplier),
            selectinload(PurchaseOrder.lines),
        ).where(PurchaseOrder.id == po.id)
    )
    return po_dict(res2.scalar_one())


@router.patch("/{po_id}/approve")
async def approve_purchase_order(po_id: int, db: AsyncSession = Depends(get_db)):
    """موافقة على أمر الشراء — ينتقل من draft إلى approved"""
    po = (await db.execute(await _tenant_po_query(po_id))).scalar_one_or_none()
    if not po:
        raise HTTPException(404)
    if po.status != "draft":
        raise HTTPException(400, f"لا يمكن الموافقة على أمر بحالة '{po.status}'")
    po.status = "approved"
    po.approved_at = datetime.utcnow()
    await db.commit()
    return {"id": po_id, "status": "approved"}


@router.patch("/{po_id}/cancel")
async def cancel_purchase_order(po_id: int, db: AsyncSession = Depends(get_db)):
    po = (await db.execute(await _tenant_po_query(po_id))).scalar_one_or_none()
    if not po:
        raise HTTPException(404)
    # كان مسموح إلغاء أمر "partial" (مستلم جزئياً) من غير أي عكس لأثره —
    # الكمية اللي اتستلمت فعلاً رفعت المخزون وأنشأت فاتورة شراء ورفعت رصيد
    # المورد بالفعل، فإلغاء الأمر بعد كده كان بيسيب حالة متضاربة (أمر
    # "ملغي" له أثر فعلي في المخزون والحسابات). امنع الإلغاء في الحالتين
    # زي بعض، نفس ما بيحصل فعلياً مع "received".
    if po.status in ("received", "partial"):
        raise HTTPException(400, "لا يمكن إلغاء أمر تم استلام جزء منه بالفعل — الكمية المستلمة أثّرت على المخزون والحسابات")
    po.status = "cancelled"
    await db.commit()
    return {"id": po_id, "status": "cancelled"}


@router.post("/{po_id}/receive")
async def receive_purchase_order(po_id: int, data: ReceiveIn, db: AsyncSession = Depends(get_db)):
    """
    استلام (كلي أو جزئي) لأمر الشراء.
    يحدّث الكميات المستلمة، يرفع المخزون، وينشئ فاتورة شراء اختياريًا.
    """
    res = await db.execute(
        (await _tenant_po_query(po_id)).options(
            selectinload(PurchaseOrder.lines),
            selectinload(PurchaseOrder.supplier),
        )
    )
    po = res.scalar_one_or_none()
    if not po:
        raise HTTPException(404)
    if po.status == "cancelled":
        raise HTTPException(400, "الأمر ملغي")
    if po.status == "received":
        raise HTTPException(400, "الأمر مستلم بالكامل بالفعل")
    if po.status == "draft":
        # كان ممكن تستلم أمر شراء لسه "مسودة" — يعني تتخطى خطوة الموافقة
        # بالكامل (approve كان اختياري فعلياً رغم وجوده كخطوة في الـ workflow).
        # المفروض الاستلام ميتمش غير لأمر معتمد (approved) أو مستلم جزئياً
        # بالفعل (partial).
        raise HTTPException(400, "لا يمكن استلام أمر شراء لم تتم الموافقة عليه بعد")

    from app.core.database import current_tenant_id as _ctid_recv
    _tid_recv = _ctid_recv.get() or 1

    lines_map = {l.id: l for l in po.lines}
    invoice_lines_data = []
    total_received_value = Decimal("0")

    for rd in data.lines:
        line = lines_map.get(rd.line_id)
        if not line:
            raise HTTPException(404, f"البند {rd.line_id} غير موجود في الأمر")
        remaining = line.ordered_qty - line.received_qty
        if rd.received_qty <= 0:
            raise HTTPException(400, f"الكمية يجب أن تكون أكبر من صفر")
        if rd.received_qty > remaining:
            raise HTTPException(400, f"الكمية ({float(rd.received_qty)}) أكبر من المتبقي ({float(remaining)})")

        line.received_qty += rd.received_qty
        val = rd.received_qty * line.unit_cost
        total_received_value += val

        # رفع المخزون — بموقع فعلي حقيقي (RAW أو IMPORTED) مش بالاسم فقط
        if line.material_id:
            mat = await db.get(Material, line.material_id)
            if not mat:
                raise HTTPException(400, "المادة غير موجودة")
            if mat:
                from app.services.inventory_ledger import get_or_create_warehouse_bin, post_receipt
                wh_code = "IMPORTED" if _is_import_supplier(po.supplier) else "RAW"
                bin_loc = await get_or_create_warehouse_bin(db, _tid_recv, wh_code)
                await post_receipt(
                    db, material=mat, qty=rd.received_qty, unit_cost=line.unit_cost,
                    movement_date=data.receive_date,
                    source_doc_type="purchase_order", source_doc_id=po.id,
                    destination_location_id=bin_loc.id,
                    reference=po.code, notes=f"استلام أمر شراء: {po.code}",
                )
                if rd.update_cost:
                    mat.unit_cost = line.unit_cost  # سعر اليوم مباشرة، تجاوز المتوسط المرجّح
                mat.last_purchase_price = line.unit_cost

        invoice_lines_data.append({
            "material_id": line.material_id,
            "description": line.description,
            "qty": rd.received_qty,
            "unit": line.unit,
            "unit_cost": line.unit_cost,
            "total_cost": val,
        })

    # تحديث حالة الأمر
    all_received = all(l.ordered_qty <= l.received_qty for l in po.lines)
    po.status = "received" if all_received else "partial"

    invoice_id = None
    if data.create_invoice and invoice_lines_data:
        tax_amount = total_received_value * po.tax_pct / 100
        inv_total = total_received_value + tax_amount

        # توليد كود فاتورة
        pi_count = await db.scalar(select(func.count()).select_from(PurchaseInvoice))
        inv_code = f"PI-{po.code}-{(pi_count or 0) + 1:03d}"

        inv = PurchaseInvoice(
            tenant_id=_tid_recv,
            code=inv_code,
            supplier_id=po.supplier_id,
            supplier_name=po.supplier_name or (po.supplier.name if po.supplier else None),
            date=data.receive_date,
            tax_pct=po.tax_pct,
            tax_amount=tax_amount,
            discount_amt=Decimal("0"),
            subtotal=total_received_value,
            total=inv_total,
            notes=f"تم إنشاؤها تلقائياً من أمر الشراء: {po.code}",
            status="received",
        )
        db.add(inv)
        await db.flush()

        for ld in invoice_lines_data:
            db.add(PurchaseInvoiceLine(
                tenant_id=_tid_recv,
                invoice_id=inv.id,
                material_id=ld["material_id"],
                description=ld["description"],
                qty=ld["qty"],
                unit=ld["unit"],
                unit_cost=ld["unit_cost"],
                total_cost=ld["total_cost"],
                update_stock=False,  # already updated above
            ))
        invoice_id = inv.id

        # محطة 24: قيد محاسبي حقيقي للفاتورة المُنشأة تلقائيًا عند الاستلام —
        # قبل كده كان بيتم إنشاء PurchaseInvoice فعليًا (وبيرفع رصيد المورد)
        # من غير أي أثر في دفتر الحسابات خالص (لا مخزون مدين ولا ذمم دائنة).
        # inventory_ledger.post_receipt (محطة 19) بيسجل الحركة المخزنية فقط —
        # مفيش أي مكان تاني كان بيغطي الجانب المحاسبي لاستلام أمر شراء.
        from app.services.auto_journal import journal_purchase_invoice_create
        await journal_purchase_invoice_create(
            db, _tid_recv, inv.id, inv.code, tax_amount, inv_total, data.receive_date,
        )

        # تحديث رصيد المورد
        if po.supplier_id:
            sup = await db.get(Supplier, po.supplier_id)
            if sup:
                sup.balance += inv_total

    await db.commit()
    return {
        "po_id": po_id,
        "status": po.status,
        "invoice_id": invoice_id,
        "message": "تم الاستلام بنجاح",
    }


@router.delete("/{po_id}")
async def delete_purchase_order(po_id: int, db: AsyncSession = Depends(get_db)):
    po = (await db.execute(await _tenant_po_query(po_id))).scalar_one_or_none()
    if not po:
        raise HTTPException(404)
    if po.status not in ("draft", "cancelled"):
        raise HTTPException(400, "يمكن حذف أوامر المسودة والملغية فقط")
    lines = (await db.execute(select(PurchaseOrderLine).where(PurchaseOrderLine.order_id == po_id))).scalars().all()
    for l in lines:
        await db.delete(l)

    # حذف مرفقات أمر الشراء (لو وُجدت عبر endpoint المرفقات العام /purchases/attach)
    # — كانت بتفضل يتيمة (سجل + ملف على القرص) بعد حذف الأمر نفسه.
    from app.models.models import FileAttachment
    from app.api.purchases import UPLOAD_DIR as _PO_UPLOAD_DIR
    atts_res = await db.execute(
        select(FileAttachment).where(
            FileAttachment.entity_type == "purchase_order",
            FileAttachment.entity_id == po_id,
        )
    )
    for att in atts_res.scalars().all():
        att_path = _PO_UPLOAD_DIR / att.stored_name
        if att_path.exists():
            att_path.unlink()
        await db.delete(att)

    await db.delete(po)
    await db.commit()
    return {"message": "تم الحذف", "id": po_id}


@router.get("/dashboard/summary")
async def po_dashboard(db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_pod
    tid = _ctid_pod.get()

    def _scoped(q):
        return q.where(PurchaseOrder.tenant_id == tid) if tid is not None else q

    total = await db.scalar(_scoped(select(func.count()).select_from(PurchaseOrder)))
    draft = await db.scalar(_scoped(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.status == "draft")))
    approved = await db.scalar(_scoped(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.status == "approved")))
    partial = await db.scalar(_scoped(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.status == "partial")))
    total_value = await db.scalar(_scoped(select(func.coalesce(func.sum(PurchaseOrder.total), 0))))
    return {
        "total_orders": total or 0,
        "draft": draft or 0,
        "approved": approved or 0,
        "partial": partial or 0,
        "total_value": float(total_value or 0),
    }


# ══════════════════════════════════════════════════════════════
# PRINT — أمر التوريد وسند الاستلام
# ══════════════════════════════════════════════════════════════

def _po_esc(s) -> str:
    import html as _h
    return _h.escape(str(s or ""))


async def _po_company(db: AsyncSession) -> dict:
    from app.core.database import current_tenant_id
    from app.models.models import WorkshopProfile
    tid = current_tenant_id.get()
    q = select(WorkshopProfile)
    if tid is not None:
        q = q.where(WorkshopProfile.tenant_id == tid)
    res = await db.execute(q.limit(1))
    prof = res.scalar_one_or_none()
    if not prof:
        return {"name": "ورشة معدات الأعلاف", "sub": "", "phone": "",
                "address": "", "tax_id": "", "commercial_reg": "",
                "footer": "شكراً لتعاملكم معنا", "logo_html": ""}
    phone = prof.mobile or prof.phone or ""
    logo_html = (
        f'<img src="{prof.logo_data}" alt="logo" '
        'style="max-height:80px;max-width:170px;object-fit:contain;display:block;"/>'
        if prof.logo_data else ""
    )
    legal = []
    if prof.tax_id:         legal.append(f"ب.ض: {prof.tax_id}")
    if prof.commercial_reg: legal.append(f"س.ت: {prof.commercial_reg}")
    return {
        "name":    prof.name or "ورشة معدات الأعلاف",
        "sub":     prof.description or "",
        "phone":   phone,
        "address": prof.address or "",
        "footer":  prof.report_footer or "شكراً لتعاملكم معنا",
        "logo_html": logo_html,
        "legal_html": " | ".join(legal),
    }



def _po_print_html(po: PurchaseOrder, company: dict, doc_type: str = "po") -> str:
    import json as _json
    is_grn = doc_type == "grn"
    title  = "سند استلام بضاعة" if is_grn else "أمر توريد"
    accent = "#0F6E56" if is_grn else "#C8A550"

    status_map = {
        "draft":    ("مسودة",          "#888"),
        "approved": ("موافق عليه",     "#1a7a3c"),
        "partial":  ("مستلم جزئياً",  "#854F0B"),
        "received": ("مستلم كاملاً",  "#1a7a3c"),
        "cancelled":("ملغي",           "#A32D2D"),
    }
    st_label, st_color = status_map.get(po.status, ("—", "#888"))

    cname    = _po_esc(company.get("name", ""))
    csub     = _po_esc(company.get("sub", ""))
    cphone   = _po_esc(company.get("phone", ""))
    caddr    = _po_esc(company.get("address", ""))
    cfooter  = _po_esc(company.get("footer", ""))
    legal_h  = _po_esc(company.get("legal_html", ""))
    logo_html = company.get("logo_html", "")

    sup_name  = _po_esc(po.supplier_name or (po.supplier.name  if po.supplier else "—"))
    sup_phone = _po_esc(po.supplier.phone   if po.supplier else "")
    sup_addr  = _po_esc(po.supplier.address if po.supplier else "")
    sup_tax   = _po_esc(po.supplier.tax_id  if po.supplier else "")

    lines = list(po.lines or [])
    if is_grn:
        lines = [l for l in lines if float(l.received_qty) > 0]

    rows_html = ""
    for i, l in enumerate(lines):
        bg    = "#fafafa" if i % 2 == 0 else "#fff"
        qty   = float(l.received_qty if is_grn else l.ordered_qty)
        total = qty * float(l.unit_cost)
        grn_extra = (
            f"<td style='padding:9px 12px;text-align:center;color:#854F0B'>{float(l.ordered_qty):.3f}</td>"
            if is_grn else ""
        )
        rows_html += (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:9px 12px;text-align:center;color:#6b6a64;font-size:12px'>{i+1}</td>"
            f"<td style='padding:9px 12px;font-weight:600'>{_po_esc(l.description)}</td>"
            f"<td style='padding:9px 12px;text-align:center'>{qty:.3f} {_po_esc(l.unit)}</td>"
            f"{grn_extra}"
            f"<td style='padding:9px 12px;text-align:left;font-variant-numeric:tabular-nums'>{float(l.unit_cost):,.2f}</td>"
            f"<td style='padding:9px 12px;text-align:left;font-weight:700;font-variant-numeric:tabular-nums'>{total:,.2f}</td>"
            "</tr>"
        )

    subtotal = float(po.subtotal)
    tax_pct  = float(po.tax_pct)
    tax_amt  = float(po.tax_amount)
    disc_amt = float(po.discount_amt)
    total_v  = float(po.total)

    totals_rows = (
        f"<tr><td>الاجمالي قبل الضريبة</td>"
        f"<td style='text-align:left;font-weight:600'>{subtotal:,.2f}</td></tr>"
    )
    if disc_amt > 0:
        totals_rows += (
            f"<tr><td>الخصم</td>"
            f"<td style='text-align:left;color:#C0392B'>- {disc_amt:,.2f}</td></tr>"
        )
    if tax_pct > 0:
        totals_rows += (
            f"<tr><td>ضريبة ({tax_pct:.0f}%)</td>"
            f"<td style='text-align:left;color:#6b6a64'>+ {tax_amt:,.2f}</td></tr>"
        )

    po_code   = _po_esc(po.code)
    po_date   = _po_esc(str(po.order_date))
    exp_date  = _po_esc(str(po.expected_date)) if po.expected_date else ""
    notes_box = (
        f"<div style='background:#f9f9f7;border-radius:8px;padding:12px 15px;"
        f"font-size:12px;margin-top:16px'>"
        f"<b style='font-size:10px;color:#6b6a64;letter-spacing:.08em'>ملاحظات:</b><br>"
        f"{_po_esc(po.notes)}</div>"
        if po.notes else ""
    )
    approved_date = po.approved_at.strftime("%Y-%m-%d") if po.approved_at else ""
    js_code  = _json.dumps(po_code)
    js_title = _json.dumps(title)

    grn_extra_th = "<th style='width:90px;text-align:center'>المطلوب</th>" if is_grn else ""
    footer_items = " | ".join(filter(None, [
        f"هاتف: {cphone}" if cphone else "",
        f"العنوان: {caddr}" if caddr else "",
        legal_h,
    ]))

    sig1 = "معد الأمر"            if not is_grn else "معد السند"
    sig2 = "المدير المعتمد"       if not is_grn else "مدير المستودع"
    sig3 = "المورد — استلام نسخة" if not is_grn else "المورد — توقيع الاستلام"

    logo_wrap = (
        f'<div style="background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);'
        f'border-radius:10px;padding:6px;">{logo_html}</div>'
        if logo_html else ""
    )
    exp_badge = (
        f'<div class="doc-badge" style="margin-top:5px;">'
        f'<span class="doc-badge-label">{"تاريخ الاستلام" if is_grn else "التسليم المتوقع"}:</span>'
        f'<span class="doc-badge-val">{exp_date}</span></div>'
        if exp_date else ""
    )
    approved_bar = (
        f'<span style="font-size:11px;color:rgba(255,255,255,.4)">اعتمد في: '
        f'<b style="color:rgba(255,255,255,.7)">{approved_date}</b></span>'
        if approved_date else "<span></span>"
    )
    from_details = "".join(
        f'<div class="party-detail">{v}</div>'
        for v in [cphone, caddr] if v
    )
    sup_tax_line = f"الرقم الضريبي: {sup_tax}" if sup_tax else ""
    sup_details = "".join(
        f'<div class="party-detail">{v}</div>'
        for v in [sup_phone, sup_addr, sup_tax_line] if v
    )

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<title>{title} - {po_code}</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{{--navy:#0C2340;--navy-dk:#081828;--surface:#F8F7F4;--border:#E8E6E0;--text:#1A1A2E;--muted:#7A7870;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Cairo',Tahoma,Arial,sans-serif;font-size:13px;color:var(--text);background:#EDECEA;direction:rtl;line-height:1.65;}}
@page{{size:A4;margin:8mm 7mm;}}
@media print{{.no-print{{display:none!important;}}body{{-webkit-print-color-adjust:exact;print-color-adjust:exact;background:#fff;}}.page{{max-width:100%;box-shadow:none;border-radius:0;margin:0;}}}}
.page{{max-width:840px;margin:16px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 60px rgba(0,0,0,.13);}}
.doc-header{{background:linear-gradient(135deg,var(--navy-dk) 0%,var(--navy) 60%,#143558 100%);color:#fff;padding:24px 30px 20px;display:flex;justify-content:space-between;align-items:center;}}
.company-block{{display:flex;align-items:center;gap:18px;}}
.company-name{{font-size:21px;font-weight:800;line-height:1.2;}}
.company-tagline{{font-size:11px;color:rgba(255,255,255,.5);margin-top:3px;}}
.company-phone{{font-size:12px;color:{accent};margin-top:6px;font-weight:600;}}
.doc-meta{{text-align:left;}}
.doc-type{{font-size:30px;font-weight:900;color:{accent};line-height:1;letter-spacing:-1px;}}
.doc-badge{{display:inline-flex;align-items:center;gap:6px;margin-top:8px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 12px;}}
.doc-badge-label{{font-size:11px;color:rgba(255,255,255,.5);}}
.doc-badge-val{{font-size:12.5px;color:#fff;font-weight:700;}}
.accent-stripe{{height:4px;background:linear-gradient(90deg,{accent},rgba(200,165,80,.5),{accent});}}
.status-bar{{background:#0D2440;padding:8px 30px;display:flex;justify-content:space-between;align-items:center;}}
.status-pill{{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(255,255,255,.2);font-size:11px;font-weight:700;padding:3px 14px;border-radius:99px;}}
.status-dot{{width:6px;height:6px;background:currentColor;border-radius:50%;}}
.body{{padding:24px 30px;}}
.parties{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:22px;}}
.party-box{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 18px;border-top:3px solid {accent};}}
.party-label{{font-size:10px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:7px;}}
.party-name{{font-size:15px;font-weight:800;color:var(--text);margin-bottom:4px;}}
.party-detail{{font-size:11.5px;color:var(--muted);line-height:1.7;}}
table.items-table{{width:100%;border-collapse:collapse;margin-bottom:22px;border-radius:12px;overflow:hidden;border:1px solid var(--border);}}
.items-table thead tr{{background:var(--navy);}}
.items-table thead th{{padding:10px 13px;font-size:11px;font-weight:700;color:rgba(255,255,255,.75);letter-spacing:.06em;text-align:right;}}
.items-table thead th:last-child,.items-table thead th:nth-last-child(2){{text-align:left;}}
.items-table tbody td{{padding:10px 13px;border-bottom:1px solid var(--border);font-size:12.5px;vertical-align:top;}}
.items-table tbody tr:last-child td{{border-bottom:none;}}
.totals-wrap{{display:flex;justify-content:flex-end;margin-bottom:22px;}}
table.totals-tbl{{width:260px;border-collapse:collapse;}}
.totals-tbl td{{padding:6px 10px;font-size:12.5px;border-bottom:1px dashed var(--border);}}
.totals-tbl tr:last-child td{{border-bottom:none;}}
.grand-row{{background:linear-gradient(135deg,var(--navy-dk),var(--navy));border-radius:10px;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;margin-bottom:22px;}}
.grand-label{{font-size:13px;font-weight:600;color:rgba(255,255,255,.65);}}
.grand-value{{font-size:22px;font-weight:900;color:{accent};letter-spacing:-.5px;font-variant-numeric:tabular-nums;}}
.sig-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-top:24px;margin-bottom:22px;}}
.sig-box{{border-top:2px solid var(--border);padding-top:10px;text-align:center;}}
.sig-box-title{{font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.07em;margin-bottom:32px;}}
.sig-line{{border-top:1px solid #CCC;margin:0 16px;padding-top:6px;font-size:11px;color:#aaa;}}
.doc-footer{{background:linear-gradient(90deg,var(--navy-dk),var(--navy));color:rgba(255,255,255,.6);padding:12px 30px;text-align:center;font-size:11.5px;}}
.action-bar{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);display:flex;gap:8px;z-index:99;background:rgba(8,24,40,.85);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:8px 12px;}}
.action-bar button{{padding:8px 18px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;border:none;font-family:'Cairo',sans-serif;transition:opacity .15s;}}
.btn-print{{background:{accent};color:#fff;}}
</style>
</head>
<body onload="setTimeout(()=>window.print(),400)">
<div class="page">
  <div class="doc-header">
    <div class="company-block">
      {logo_wrap}
      <div>
        <div class="company-name">{cname}</div>
        <div class="company-tagline">{csub}</div>
        <div class="company-phone">{cphone}</div>
      </div>
    </div>
    <div class="doc-meta">
      <div class="doc-type">{title}</div>
      <div class="doc-badge"><span class="doc-badge-label">رقم:</span><span class="doc-badge-val">{po_code}</span></div>
      <div class="doc-badge" style="margin-top:5px;"><span class="doc-badge-label">التاريخ:</span><span class="doc-badge-val">{po_date}</span></div>
      {exp_badge}
    </div>
  </div>
  <div class="accent-stripe"></div>
  <div class="status-bar">
    <span class="status-pill" style="color:{st_color};border-color:{st_color}40;">
      <span class="status-dot"></span>{st_label}
    </span>
    {approved_bar}
  </div>
  <div class="body">
    <div class="parties">
      <div class="party-box">
        <div class="party-label">مصدر الأمر</div>
        <div class="party-name">{cname}</div>
        {from_details}
      </div>
      <div class="party-box">
        <div class="party-label">المورد</div>
        <div class="party-name">{sup_name}</div>
        {sup_details}
      </div>
    </div>
    <table class="items-table">
      <thead><tr>
        <th style="width:40px;text-align:center">#</th>
        <th>المادة / الوصف</th>
        <th style="width:120px;text-align:center">{"الكمية المستلمة" if is_grn else "الكمية"}</th>
        {grn_extra_th}
        <th style="width:120px;text-align:left">سعر الوحدة</th>
        <th style="width:120px;text-align:left">الاجمالي</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    <div class="totals-wrap">
      <div>
        <table class="totals-tbl"><tbody>{totals_rows}</tbody></table>
        <div class="grand-row" style="margin-top:8px;">
          <span class="grand-label">{"اجمالي قيمة الاستلام" if is_grn else "اجمالي أمر التوريد"}</span>
          <span class="grand-value">{total_v:,.2f} ج.م</span>
        </div>
      </div>
    </div>
    {notes_box}
    <div class="sig-row">
      <div class="sig-box"><div class="sig-box-title">{sig1}</div><div class="sig-line">التوقيع والاسم</div></div>
      <div class="sig-box"><div class="sig-box-title">{sig2}</div><div class="sig-line">التوقيع والختم</div></div>
      <div class="sig-box"><div class="sig-box-title">{sig3}</div><div class="sig-line">التوقيع والتاريخ</div></div>
    </div>
  </div>
  <div class="doc-footer">{footer_items if footer_items else cname}</div>
</div>
<div class="action-bar no-print">
  <button class="btn-print" onclick="(function(){{document.title={js_title}+' '+{js_code};window.print();}})()">طباعة / PDF</button>
</div>
</body></html>"""


@print_router.get("/{po_id}/print", response_class=HTMLResponse)
async def print_purchase_order(po_id: int, db: AsyncSession = Depends(get_db)):
    """طباعة أمر التوريد"""
    res = await db.execute(
        (await _tenant_po_query(po_id)).options(
            selectinload(PurchaseOrder.supplier), selectinload(PurchaseOrder.lines)
        )
    )
    po = res.scalar_one_or_none()
    if not po:
        raise HTTPException(404)
    company = await _po_company(db)
    return _po_print_html(po, company, "po")


@print_router.get("/{po_id}/grn/print", response_class=HTMLResponse)
async def print_grn(po_id: int, db: AsyncSession = Depends(get_db)):
    """طباعة سند استلام البضاعة (GRN)"""
    res = await db.execute(
        (await _tenant_po_query(po_id)).options(
            selectinload(PurchaseOrder.supplier), selectinload(PurchaseOrder.lines)
        )
    )
    po = res.scalar_one_or_none()
    if not po:
        raise HTTPException(404)
    if po.status not in ("partial", "received"):
        raise HTTPException(400, "لا يمكن طباعة سند استلام لأمر لم يستلم بعد")
    company = await _po_company(db)
    return _po_print_html(po, company, "grn")
