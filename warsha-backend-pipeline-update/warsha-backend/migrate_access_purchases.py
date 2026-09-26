"""
migrate_access_purchases.py — ترحيل فواتير الشراء من الأكسيس
PURCHASE_HEADER (110) + PURCHASING_LINES (338) -> purchase_invoices / purchase_invoice_lines
ثم تحديث رصيد كل مورد = إجمالي المتبقي (REMAIN_AMOUNT) من فواتيره.

Run: PYTHONIOENCODING=utf-8 python migrate_access_purchases.py
"""
import asyncio
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pyodbc
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.models import Tenant, Supplier, Material, PurchaseInvoice, PurchaseInvoiceLine

ACCDB_PATH = r"E:\warsha\End System Final\End System Final.accdb"


def dec(v, default="0"):
    if v is None:
        return Decimal(default)
    s = str(v).strip()
    if not s:
        return Decimal(default)
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal(default)


def to_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    return v


async def main():
    cnxn = pyodbc.connect(f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};Dbq={ACCDB_PATH};")
    cur = cnxn.cursor()

    async with AsyncSessionLocal() as db:
        tenant = (await db.execute(select(Tenant))).scalars().first()
        TID = tenant.id

        # ── خرائط الموردين والأصناف ─────────────────────────────────────
        suppliers = {}
        for s in (await db.execute(select(Supplier).where(Supplier.tenant_id == TID))).scalars().all():
            if s.code.startswith("AC-S"):
                suppliers[int(s.code[4:])] = s

        materials = {}
        for m in (await db.execute(select(Material).where(Material.tenant_id == TID))).scalars().all():
            if m.code.startswith("ITM-"):
                materials[m.code[4:]] = m

        # ITEM_ID (داخلي) -> ITEM_CODE
        item_code_by_id = {}
        for row in cur.execute("SELECT ITEM_ID, ITEM_CODE FROM ITEMS"):
            if row.ITEM_CODE is not None:
                item_code_by_id[row.ITEM_ID] = str(row.ITEM_CODE).strip()

        # ── خرائط بنود الشراء حسب رقم الفاتورة ───────────────────────────
        lines_by_invoice = {}
        for row in cur.execute(
            "SELECT PURCHASING_INVOICE_ID, ITEM_ID, UOM_NAME, ITEM_QTY, ITEM_PRICE, NET_PRICE FROM PURCHASING_LINES"
        ):
            lines_by_invoice.setdefault(row.PURCHASING_INVOICE_ID, []).append(row)

        existing_codes = {
            r[0] for r in (await db.execute(
                select(PurchaseInvoice.code).where(PurchaseInvoice.tenant_id == TID)
            )).all()
        }

        n_inv, n_line, skipped = 0, 0, 0
        remain_by_supplier = {}

        for row in cur.execute(
            "SELECT PURCHASING_INVOICE_ID, INVOICE_NUMBER, INVOICE_DATE, SUPPLIER_ID, "
            "TOTAL_AMOUNT, NET_AMOUNT, PAID_AMOUNT, REMAIN_AMOUNT, TOTAL_TAX, TOTAL_DISCOUNT, "
            "SAVED, NOTES FROM PURCHASE_HEADER"
        ):
            if not row.SAVED:
                skipped += 1
                continue

            sup = suppliers.get(row.SUPPLIER_ID)
            remain = dec(row.REMAIN_AMOUNT)
            if sup:
                remain_by_supplier[sup.id] = remain_by_supplier.get(sup.id, Decimal("0")) + remain

            code = f"AC-PINV-{row.INVOICE_NUMBER}"
            if code in existing_codes:
                continue

            total = dec(row.NET_AMOUNT) or dec(row.TOTAL_AMOUNT)
            inv = PurchaseInvoice(
                tenant_id=TID, code=code,
                supplier_id=sup.id if sup else None,
                supplier_name=sup.name if sup else None,
                date=to_date(row.INVOICE_DATE) or datetime.now().date(),
                subtotal=dec(row.TOTAL_AMOUNT),
                tax_amount=dec(row.TOTAL_TAX),
                discount_amt=dec(row.TOTAL_DISCOUNT),
                total=total,
                paid_amount=dec(row.PAID_AMOUNT),
                status="paid" if remain <= 0 and total > 0 else "received",
                notes=(str(row.NOTES).strip() if row.NOTES else None) or "مرحّلة من النظام القديم",
            )
            db.add(inv)
            await db.flush()
            n_inv += 1

            for ln in lines_by_invoice.get(row.PURCHASING_INVOICE_ID, []):
                mat = None
                item_code = item_code_by_id.get(ln.ITEM_ID)
                if item_code:
                    mat = materials.get(item_code)
                qty = dec(ln.ITEM_QTY)
                unit_cost = dec(ln.ITEM_PRICE)
                total_cost = dec(ln.NET_PRICE) or (qty * unit_cost)
                db.add(PurchaseInvoiceLine(
                    tenant_id=TID, invoice_id=inv.id,
                    material_id=mat.id if mat else None,
                    description=mat.name_ar if mat else f"صنف #{ln.ITEM_ID}",
                    qty=qty, unit=(ln.UOM_NAME or "قطعة").strip() or "قطعة",
                    unit_cost=unit_cost, total_cost=total_cost,
                    update_stock=False,
                ))
                n_line += 1

        # تحديث رصيد الموردين = إجمالي المتبقي من فواتيرهم
        for sup_id, remain in remain_by_supplier.items():
            sup = await db.get(Supplier, sup_id)
            if sup:
                sup.balance = remain

        await db.commit()
        print(f"فواتير شراء جديدة: {n_inv} ، بنود: {n_line} ، تم تجاهل (غير محفوظة): {skipped}")
        print(f"تم تحديث أرصدة {len(remain_by_supplier)} مورد")

    cnxn.close()


if __name__ == "__main__":
    asyncio.run(main())
