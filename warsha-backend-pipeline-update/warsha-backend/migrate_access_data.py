"""
migrate_access_data.py — ترحيل البيانات الأساسية من قاعدة الأكسيس القديمة
"End System Final.accdb" إلى WarshERP (SQLite الحالية).

يرحّل:
  - مجموعات الأصناف (ITEMS_GROUPS)   -> material_categories
  - العملاء (CUSTOMERS)              -> customers
  - الموردين (SUPPLIERS)             -> suppliers
  - الأصناف (ITEMS)                  -> materials
  - أسعار البيع (ITEMS.PRICE_UOMM)   -> price_catalog_items (سعر واحد لكل صنف، بدون جملة/تجزئة)

Run: PYTHONIOENCODING=utf-8 python migrate_access_data.py
"""
import asyncio
from decimal import Decimal, InvalidOperation

import pyodbc
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.models import (
    Tenant, MaterialCategory, Material, Customer, Supplier, PriceCatalogItem,
)

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


async def main():
    cnxn = pyodbc.connect(f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};Dbq={ACCDB_PATH};")
    cur = cnxn.cursor()

    async with AsyncSessionLocal() as db:
        tenant = (await db.execute(select(Tenant))).scalars().first()
        TID = tenant.id
        print(f"Tenant: {tenant.name} (id={TID})")

        # ── 1. مجموعات الأصناف -> material_categories ──────────────────
        existing_cats = (await db.execute(
            select(MaterialCategory).where(MaterialCategory.tenant_id == TID)
        )).scalars().all()
        cat_by_name = {c.name_ar: c for c in existing_cats}

        group_to_cat = {}
        for row in cur.execute("SELECT GROUP_ID, GROUP_NAME FROM ITEMS_GROUPS"):
            name = clean(row.GROUP_NAME) or f"مجموعة {row.GROUP_ID}"
            cat = cat_by_name.get(name)
            if not cat:
                cat = MaterialCategory(tenant_id=TID, name_ar=name, name_en="")
                db.add(cat)
                await db.flush()
                cat_by_name[name] = cat
                print(f"  + فئة جديدة: {name} (id={cat.id})")
            group_to_cat[row.GROUP_ID] = cat.id

        fallback_cat = cat_by_name.get("غير مصنف")
        if not fallback_cat:
            fallback_cat = MaterialCategory(tenant_id=TID, name_ar="غير مصنف", name_en="Uncategorized")
            db.add(fallback_cat)
            await db.flush()
            cat_by_name["غير مصنف"] = fallback_cat
            print(f"  + فئة جديدة: غير مصنف (id={fallback_cat.id})")

        await db.commit()

        # ── 2. العملاء -> customers ─────────────────────────────────────
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

        # ── 3. الموردين -> suppliers ────────────────────────────────────
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

        # ── 4. الأصناف -> materials  +  كتالوج الأسعار ───────────────────
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

    cnxn.close()
    print("تم الترحيل بنجاح.")


if __name__ == "__main__":
    asyncio.run(main())
