"""
migrate_access_all.py — ترحيل شامل من قاعدة الأكسيس القديمة "End System Final.accdb"
إلى WarshERP، في سكريبت واحد.

يشمل:
  1. مجموعات الأصناف، العملاء، الموردين، الأصناف، كتالوج الأسعار
  2. تحديث الكميات الحالية للأصناف (رصيد افتتاحي + مشتريات + إنتاج - استهلاك)
  3. فواتير الشراء المحفوظة + تحديث أرصدة الموردين

ملاحظة: السكريبت idempotent (يمكن إعادة تشغيله بأمان، لن يكرر البيانات).

الإعداد قبل التشغيل:
  - ضع المسار الصحيح لملف الأكسيس في متغير ACCDB_PATH أدناه.
  - تأكد من أن backend/.env يشير إلى قاعدة بيانات WarshERP الصحيحة.

Run:
  PYTHONIOENCODING=utf-8 python migrate_access_all.py
"""
import asyncio
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pyodbc
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.models import (
    Tenant, MaterialCategory, Material, Customer, Supplier, PriceCatalogItem,
    PurchaseInvoice, PurchaseInvoiceLine,
)

# ⚠️ غيّر هذا المسار ليطابق موقع ملف الأكسيس عند العميل
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


def clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def to_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    return v


async def step1_basic_data(db, cur, TID):
    print("\n=== 1) المجموعات / العملاء / الموردين / الأصناف / الأسعار ===")

    # ── مجموعات الأصناف -> material_categories ──────────────────
    existing_cats = (await db.execute(
        select(MaterialCategory).where(MaterialCategory.tenant_id == TID)
    )).scalars().all()
    cat_by_name = {c.name_ar: c for c in existing_cats}

    group_to_cat = {}
    n_new_cats = 0
    for row in cur.execute("SELECT GROUP_ID, GROUP_NAME FROM ITEMS_GROUPS"):
        name = clean(row.GROUP_NAME) or f"مجموعة {row.GROUP_ID}"
        cat = cat_by_name.get(name)
        if not cat:
            cat = MaterialCategory(tenant_id=TID, name_ar=name, name_en="")
            db.add(cat)
            await db.flush()
            cat_by_name[name] = cat
            n_new_cats += 1
        group_to_cat[row.GROUP_ID] = cat.id

    fallback_cat = cat_by_name.get("غير مصنف")
    if not fallback_cat:
        fallback_cat = MaterialCategory(tenant_id=TID, name_ar="غير مصنف", name_en="Uncategorized")
        db.add(fallback_cat)
        await db.flush()
        cat_by_name["غير مصنف"] = fallback_cat
        n_new_cats += 1

    await db.commit()
    print(f"  + فئات جديدة: {n_new_cats}")

    # ── العملاء -> customers ─────────────────────────────────────
    n_cust = 0
    for row in cur.execute(
        "SELECT CUSTOMER_CODE, CUSTOMER_NAME, MOBILE_1, ADDRESS, DEBIT_LIMIT FROM CUSTOMERS"
    ):
        name = clean(row.CUSTOMER_NAME)
        code = clean(row.CUSTOMER_CODE)
        if not name or not code:
            continue
        new_code = f"AC-C{code}"
        exists = (await db.execute(
            select(Customer).where(Customer.tenant_id == TID, Customer.code == new_code)
        )).scalar_one_or_none()
        if exists:
            continue
        db.add(Customer(
            tenant_id=TID, code=new_code, name=name,
            phone=clean(row.MOBILE_1), address=clean(row.ADDRESS),
            credit_limit=dec(row.DEBIT_LIMIT),
        ))
        n_cust += 1
    await db.commit()
    print(f"  + عملاء جديدة: {n_cust}")

    # ── الموردين -> suppliers ────────────────────────────────────
    n_sup = 0
    for row in cur.execute(
        "SELECT SUPPLIER_CODE, SUPPLIER_NAME, MOBILE_1, PHONE, ADDRESS FROM SUPPLIERS"
    ):
        name = clean(row.SUPPLIER_NAME)
        code = clean(row.SUPPLIER_CODE)
        if not name or not code:
            continue
        new_code = f"AC-S{code}"
        exists = (await db.execute(
            select(Supplier).where(Supplier.tenant_id == TID, Supplier.code == new_code)
        )).scalar_one_or_none()
        if exists:
            continue
        db.add(Supplier(
            tenant_id=TID, code=new_code, name=name,
            phone=clean(row.MOBILE_1) or clean(row.PHONE),
            address=clean(row.ADDRESS),
        ))
        n_sup += 1
    await db.commit()
    print(f"  + موردين جدد: {n_sup}")

    # ── الأصناف -> materials  +  كتالوج الأسعار ───────────────────
    n_mat, n_price = 0, 0
    for row in cur.execute(
        "SELECT ITEM_ID, ITEM_CODE, ITEM_NAME, ITEM_NAME_EN, UOM_NAME, "
        "PURSHASED_PRICE, BEGINNING_BALANCE, GROUP_ID, PRICE_UOMM FROM ITEMS"
    ):
        item_code = clean(row.ITEM_CODE)
        if not item_code:
            continue
        new_code = f"ITM-{item_code}"
        exists = (await db.execute(
            select(Material).where(Material.tenant_id == TID, Material.code == new_code)
        )).scalar_one_or_none()
        if exists:
            continue

        name_ar = clean(row.ITEM_NAME) or item_code
        unit = clean(row.UOM_NAME) or "قطعة"
        unit_cost = dec(row.PURSHASED_PRICE)
        stock_qty = dec(row.BEGINNING_BALANCE)
        category_id = group_to_cat.get(row.GROUP_ID, fallback_cat.id)

        mat = Material(
            tenant_id=TID, code=new_code, name_ar=name_ar, name_en=clean(row.ITEM_NAME_EN) or "",
            unit=unit, unit_cost=unit_cost, stock_qty=stock_qty,
            category_id=category_id, material_kind="general",
        )
        db.add(mat)
        await db.flush()
        n_mat += 1

        selling_price = dec(row.PRICE_UOMM, default="0")
        if selling_price > 0:
            margin_pct = Decimal("25")
            if unit_cost > 0:
                margin_pct = ((selling_price - unit_cost) / unit_cost * 100).quantize(Decimal("0.01"))
            cat = cat_by_name.get(
                next((k for k, v in cat_by_name.items() if v.id == category_id), "غير مصنف")
            )
            db.add(PriceCatalogItem(
                tenant_id=TID, code=new_code, name=name_ar,
                category=cat.name_ar if cat else None,
                unit=unit, cost_price=unit_cost, margin_pct=margin_pct,
                selling_price=selling_price, price_mode="manual",
                source_type="material", source_id=mat.id,
            ))
            n_price += 1

    await db.commit()
    print(f"  + أصناف جديدة: {n_mat}")
    print(f"  + بنود كتالوج أسعار جديدة: {n_price}")


async def step2_stock_quantities(db, cur, TID):
    print("\n=== 2) تحديث الكميات الحالية للأصناف ===")

    # ── خريطة الوحدات: UNIT_ID -> (UOMM_NAME, UOMM_UNITS) ─────────────
    units = {}
    for row in cur.execute("SELECT UNIT_ID, UOMM_NAME, UOMM_UNITS, UOM_NAME, UOM_UNITS FROM UNITS_OF_MESSURE"):
        units[row.UNIT_ID] = {
            "uomm_name": (row.UOMM_NAME or "").strip(),
            "uomm_units": dec(row.UOMM_UNITS, "1"),
            "uom_name": (row.UOM_NAME or "").strip(),
        }

    # ── بيانات الأصناف: ITEM_ID -> معلومات الكمية ─────────────────────
    items = {}
    for row in cur.execute(
        "SELECT ITEM_ID, ITEM_CODE, UOM_NAME, UNIT_ID, UOMM_UNITS, BEGINNING_BALANCE FROM ITEMS"
    ):
        item_code = str(row.ITEM_CODE).strip() if row.ITEM_CODE is not None else ""
        if not item_code:
            continue
        u = units.get(row.UNIT_ID, {})
        items[row.ITEM_ID] = {
            "code": f"ITM-{item_code}",
            "base_uom": (row.UOM_NAME or "").strip(),
            "big_uom": u.get("uomm_name", ""),
            "big_to_base": dec(row.UOMM_UNITS, "1") or Decimal("1"),
            "qty": dec(row.BEGINNING_BALANCE),
        }

    def add_qty(item_id, line_uom, qty, sign):
        info = items.get(item_id)
        if not info or qty == 0:
            return
        line_uom = (line_uom or "").strip()
        if line_uom == info["big_uom"] and info["big_uom"]:
            qty_base = qty * info["big_to_base"]
        else:
            qty_base = qty
        info["qty"] += sign * qty_base

    # المشتريات (تزيد المخزون)
    for row in cur.execute("SELECT ITEM_ID, UOM_NAME, ITEM_QTY FROM PURCHASING_LINES"):
        add_qty(row.ITEM_ID, row.UOM_NAME, dec(row.ITEM_QTY), 1)

    # إنتاج وارد (يزيد المخزون)
    for row in cur.execute("SELECT ITEM_ID, UOM_NAME, ITEM_QTY FROM PRODUCTION_HEADER"):
        add_qty(row.ITEM_ID, row.UOM_NAME, dec(row.ITEM_QTY), 1)

    # استهلاك تصنيع (يقلل المخزون)
    for row in cur.execute("SELECT ITEM_ID, UOM_NAME, ITEM_QTY FROM PRODUCTION_LINES"):
        add_qty(row.ITEM_ID, row.UOM_NAME, dec(row.ITEM_QTY), -1)

    n_updated, n_negative = 0, 0
    for info in items.values():
        mat = (await db.execute(select(Material).where(Material.code == info["code"]))).scalar_one_or_none()
        if not mat:
            continue
        qty = info["qty"]
        if qty < 0:
            n_negative += 1
            qty = Decimal("0")
        mat.stock_qty = qty
        n_updated += 1
    await db.commit()
    print(f"  تم تحديث الكميات لـ {n_updated} صنف ({n_negative} كان لها قيمة سالبة وتم ضبطها لـ 0).")


async def step3_purchase_invoices(db, cur, TID):
    print("\n=== 3) فواتير الشراء وأرصدة الموردين ===")

    suppliers = {}
    for s in (await db.execute(select(Supplier).where(Supplier.tenant_id == TID))).scalars().all():
        if s.code.startswith("AC-S"):
            suppliers[int(s.code[4:])] = s

    materials = {}
    for m in (await db.execute(select(Material).where(Material.tenant_id == TID))).scalars().all():
        if m.code.startswith("ITM-"):
            materials[m.code[4:]] = m

    item_code_by_id = {}
    for row in cur.execute("SELECT ITEM_ID, ITEM_CODE FROM ITEMS"):
        if row.ITEM_CODE is not None:
            item_code_by_id[row.ITEM_ID] = str(row.ITEM_CODE).strip()

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

    for sup_id, remain in remain_by_supplier.items():
        sup = await db.get(Supplier, sup_id)
        if sup:
            sup.balance = remain

    await db.commit()
    print(f"  فواتير شراء جديدة: {n_inv} ، بنود: {n_line} ، تم تجاهل (غير محفوظة): {skipped}")
    print(f"  تم تحديث أرصدة {len(remain_by_supplier)} مورد")


async def main():
    cnxn = pyodbc.connect(f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};Dbq={ACCDB_PATH};")
    cur = cnxn.cursor()

    async with AsyncSessionLocal() as db:
        tenant = (await db.execute(select(Tenant))).scalars().first()
        TID = tenant.id
        print(f"Tenant: {tenant.name} (id={TID})")

        await step1_basic_data(db, cur, TID)
        await step2_stock_quantities(db, cur, TID)
        await step3_purchase_invoices(db, cur, TID)

    cnxn.close()
    print("\n✅ تم الترحيل بنجاح.")


if __name__ == "__main__":
    asyncio.run(main())
