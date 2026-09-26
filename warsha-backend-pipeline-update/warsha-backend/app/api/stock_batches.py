"""
Stock Batches API — تتبع الدُفعات/الشحنات (Lot Tracking)
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import StockBatch, Material, Supplier
from app.services.inventory_ledger import post_receipt, post_issue

router = APIRouter(prefix="/inventory/batches", tags=["stock_batches"], dependencies=[Depends(require_any)])


class BatchIn(BaseModel):
    material_id: int
    lot_number: str
    supplier_id: Optional[int] = None
    purchase_invoice_id: Optional[int] = None
    received_date: date
    qty_received: Decimal
    unit_cost: Decimal
    expiry_date: Optional[date] = None
    notes: Optional[str] = None


class BatchIssueIn(BaseModel):
    qty: Decimal
    reference: Optional[str] = None


def batch_dict(b: StockBatch) -> dict:
    return {
        "id": b.id,
        "material_id": b.material_id,
        "material_code": b.material.code if b.material else None,
        "material_name": b.material.name_ar if b.material else None,
        "material_unit": b.material.unit if b.material else None,
        "lot_number": b.lot_number,
        "supplier_id": b.supplier_id,
        "supplier_name": b.supplier.name if b.supplier else None,
        "purchase_invoice_id": b.purchase_invoice_id,
        "received_date": str(b.received_date),
        "qty_received": float(b.qty_received),
        "qty_remaining": float(b.qty_remaining),
        "qty_consumed": float(b.qty_received - b.qty_remaining),
        "unit_cost": float(b.unit_cost),
        "total_value": float(b.qty_remaining * b.unit_cost),
        "expiry_date": str(b.expiry_date) if b.expiry_date else None,
        "status": b.status,
        "is_expired": bool(b.expiry_date and b.expiry_date < date.today()),
        "notes": b.notes,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


@router.get("")
async def list_batches(
    material_id: Optional[int] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(StockBatch).options(
        selectinload(StockBatch.material),
        selectinload(StockBatch.supplier),
    )
    if material_id:
        q = q.where(StockBatch.material_id == material_id)
    if status:
        q = q.where(StockBatch.status == status)
    else:
        q = q.where(StockBatch.status != "consumed")
    res = await db.execute(q.order_by(StockBatch.received_date.desc()))
    return [batch_dict(b) for b in res.scalars().all()]


@router.post("", status_code=201)
async def create_batch(data: BatchIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1

    # تحقق من عدم تكرار lot_number لنفس المادة
    exists = await db.scalar(
        select(func.count()).select_from(StockBatch).where(
            StockBatch.material_id == data.material_id,
            StockBatch.lot_number == data.lot_number,
        )
    )
    if exists:
        raise HTTPException(400, f"رقم الدُفعة '{data.lot_number}' موجود بالفعل لهذه المادة")

    mat = await db.get(Material, data.material_id)
    if not mat:
        raise HTTPException(404, "المادة غير موجودة")
    if data.unit_cost is not None and data.unit_cost < 0:
        raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
    if data.received_date and data.expiry_date and data.expiry_date < data.received_date:
        raise HTTPException(400, "تاريخ الصلاحية يجب أن يكون بعد تاريخ الاستلام")

    batch = StockBatch(
        tenant_id=tid,
        **data.model_dump(),
        qty_remaining=data.qty_received,
        status="active",
    )
    db.add(batch)
    await db.flush()  # نحتاج batch.id عشان نربط الحركة بيه كمصدر

    # محطة 8: كل دُفعة جديدة لازم تحدّث رصيد المادة العام وتنشئ حركة مخزون
    # فعلية — قبل كده كانت الدُفعة معزولة تمامًا عن stock_qty والحركات.
    await post_receipt(
        db, material=mat, qty=data.qty_received, unit_cost=data.unit_cost,
        movement_date=data.received_date,
        source_doc_type="stock_batch", source_doc_id=batch.id,
        reference=f"دُفعة {data.lot_number}",
        lot_id=batch.id,
    )

    await db.commit()
    await db.refresh(batch)

    res = await db.execute(
        select(StockBatch).options(
            selectinload(StockBatch.material), selectinload(StockBatch.supplier)
        ).where(StockBatch.id == batch.id)
    )
    return batch_dict(res.scalar_one())


@router.get("/{batch_id}")
async def get_batch(batch_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(StockBatch).options(
            selectinload(StockBatch.material), selectinload(StockBatch.supplier)
        ).where(StockBatch.id == batch_id)
    )
    b = res.scalar_one_or_none()
    if not b:
        raise HTTPException(404)
    return batch_dict(b)


@router.post("/{batch_id}/issue")
async def issue_from_batch(batch_id: int, data: BatchIssueIn, db: AsyncSession = Depends(get_db)):
    """صرف كمية من دُفعة محددة"""
    b = await db.get(StockBatch, batch_id)
    if not b:
        raise HTTPException(404)
    if b.status != "active":
        raise HTTPException(400, f"الدُفعة بحالة '{b.status}' — لا يمكن الصرف منها")
    if data.qty > b.qty_remaining:
        raise HTTPException(400, f"الكمية المطلوبة ({float(data.qty)}) أكبر من المتبقي ({float(b.qty_remaining)})")

    mat = await db.get(Material, b.material_id)
    if not mat:
        raise HTTPException(404, "المادة غير موجودة")

    # محطة 8: الصرف من دُفعة لازم يمر بنفس فحص المتاح (رصيد - محجوز) وينشئ
    # حركة مخزون فعلية، بدل ما ينقص qty_remaining بس من غير أي أثر على
    # stock_qty العام للمادة أو أي حركة قابلة للتتبع.
    await post_issue(
        db, material=mat, qty=data.qty, unit_cost=b.unit_cost,
        source_doc_type="stock_batch_issue", source_doc_id=b.id,
        reference=data.reference or f"صرف من دُفعة {b.lot_number}",
        lot_id=b.id,
    )

    b.qty_remaining -= data.qty
    if b.qty_remaining <= 0:
        b.status = "consumed"
        b.qty_remaining = Decimal("0")

    await db.commit()
    return {
        "batch_id": batch_id,
        "lot_number": b.lot_number,
        "issued_qty": float(data.qty),
        "remaining_qty": float(b.qty_remaining),
        "status": b.status,
    }


@router.get("/material/{material_id}/traceability")
async def material_traceability(material_id: int, db: AsyncSession = Depends(get_db)):
    """تتبع كل دُفعات مادة معينة"""
    mat = await db.get(Material, material_id)
    if not mat:
        raise HTTPException(404)

    res = await db.execute(
        select(StockBatch).options(
            selectinload(StockBatch.supplier)
        ).where(StockBatch.material_id == material_id)
        .order_by(StockBatch.received_date.desc())
    )
    batches = res.scalars().all()

    total_received = sum(b.qty_received for b in batches)
    total_remaining = sum(b.qty_remaining for b in batches if b.status == "active")
    total_consumed = sum(b.qty_received - b.qty_remaining for b in batches)

    return {
        "material_id": material_id,
        "material_code": mat.code,
        "material_name": mat.name_ar,
        "system_stock": float(mat.stock_qty),
        "total_received_batches": float(total_received),
        "total_remaining_batches": float(total_remaining),
        "total_consumed": float(total_consumed),
        "batches": [batch_dict(b) for b in batches],
    }


@router.get("/dashboard/expiry-alerts")
async def expiry_alerts(days_ahead: int = 30, db: AsyncSession = Depends(get_db)):
    """تنبيهات المواد قريبة الانتهاء"""
    from datetime import timedelta
    alert_date = date.today() + timedelta(days=days_ahead)
    res = await db.execute(
        select(StockBatch).options(
            selectinload(StockBatch.material), selectinload(StockBatch.supplier)
        ).where(
            StockBatch.expiry_date.is_not(None),
            StockBatch.expiry_date <= alert_date,
            StockBatch.status == "active",
        ).order_by(StockBatch.expiry_date)
    )
    batches = res.scalars().all()
    return {
        "alert_threshold_days": days_ahead,
        "count": len(batches),
        "batches": [batch_dict(b) for b in batches],
    }
