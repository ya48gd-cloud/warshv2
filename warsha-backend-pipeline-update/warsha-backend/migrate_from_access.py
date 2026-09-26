"""
ترحيل بيانات من ملف الأكسيس (F:\\End System Final\\End System Final.accdb) إلى قاعدة بيانات
SQLite جديدة منفصلة، بسكيما مطابقة لآخر إصدار من التطبيق (نفس الموديلات الحالية).
لا يلمس قاعدة البيانات الأصلية (heavy_erp.db) أبدًا — قاعدة جديدة باسم مختلف فقط.

تشغيل:
    cd backend
    DATABASE_URL سيُستبدل داخليًا — لا حاجة لتعيينه قبل التشغيل
    "C:\\WarshERP-runtime\\.venv\\Scripts\\python.exe" migrate_from_access.py
"""
import asyncio
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pyodbc
from decimal import Decimal, InvalidOperation
from datetime import date

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.core.database import Base
from app.models.models import (
    Tenant, User, MaterialCategory, Material, Customer, Supplier,
    PurchaseInvoice, PurchaseInvoiceLine, Treasury, TreasuryTransaction,
)
from app.api.auth import hash_password

NEW_DB_PATH = r"C:\WarshERP-runtime\data\heavy_erp_from_access.db"
ACCESS_PATH = r"F:\End System Final\End System Final.accdb"


def dec(v, default="0"):
    if v is None:
        return Decimal(default)
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return Decimal(default)


def to_date(v):
    if v is None:
        return None
    if hasattr(v, "date"):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def fetch_all(cur, table, id_col=None):
    """يقرأ كل الصفوف. لو فيه عمود مفتاح (id_col)، يقرأ صف-بصف ويتجاوز أي صف تالف
    بدل ما يفشل الجدول كله (ميزة مهمة لأن صفوف معطوبة قليلة لا تمنع الباقي)."""
    cur.execute(f"SELECT * FROM [{table}]")
    cols = [c[0] for c in cur.description]
    if not id_col:
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    cur.execute(f"SELECT [{id_col}] FROM [{table}]")
    ids = [r[0] for r in cur.fetchall()]
    rows = []
    skipped = 0
    for rid in ids:
        try:
            cur.execute(f"SELECT * FROM [{table}] WHERE [{id_col}] = ?", rid)
            row = cur.fetchone()
            if row is not None:
                rows.append(dict(zip(cols, row)))
        except Exception:
            skipped += 1
    if skipped:
        print(f"  [تحذير] تخطّيت {skipped} صف تالف من {table}")
    return rows


async def main():
    # ── 1) السكيما الجديدة بآخر إصدار ──
    engine = create_async_engine(f"sqlite+aiosqlite:///{NEW_DB_PATH}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ── 2) فتح الأكسيس ──
    conn_str = (
        r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
        f"DBQ={ACCESS_PATH};"
    )
    acc = pyodbc.connect(conn_str)
    cur = acc.cursor()

    report = {}

    async with AsyncSession(engine) as db:
        # ── tenant + admin أساسي ──
        tenant = Tenant(name="الورشة (مستورد من الأكسيس)", slug="from-access", plan="pro", is_active=True)
        db.add(tenant)
        await db.flush()
        tid = tenant.id

        db.add(User(tenant_id=tid, username="admin", password_hash=hash_password("admin123"),
                     full_name="مدير النظام", role="admin", is_active=True))

        cat = MaterialCategory(tenant_id=tid, name_ar="مستورد من الأكسيس", name_en="Imported from Access")
        db.add(cat)
        await db.flush()
        cat_id = cat.id

        treasury = Treasury(tenant_id=tid, name="الخزنة الرئيسية", kind="cash",
                             opening_balance=0, balance=0, is_active=True)
        db.add(treasury)
        await db.flush()
        treasury_id = treasury.id

        await db.commit()

        # ── SUPPLIERS ──
        sup_id_map = {}
        rows = fetch_all(cur, "SUPPLIERS", "SUPPLIER_ID")
        for i, r in enumerate(rows, start=1):
            s = Supplier(
                tenant_id=tid,
                code=r.get("SUPPLIER_CODE") or f"SUP-{i:04d}",
                name=r.get("SUPPLIER_NAME") or r.get("COMPANY_NAME") or f"مورد {i}",
                phone=r.get("MOBILE_1") or r.get("PHONE"),
                email=r.get("EMAIL"),
                address=r.get("ADDRESS"),
                balance=dec(r.get("BEGINNING_BALANCE")),
                notes=r.get("DESCRIPTION"),
                is_active=True,
            )
            db.add(s)
            await db.flush()
            sup_id_map[r["SUPPLIER_ID"]] = s.id
        await db.commit()
        report["suppliers"] = len(rows)

        # ── CUSTOMERS ──
        cust_id_map = {}
        rows = fetch_all(cur, "CUSTOMERS", "CUSTOMER_ID")
        for i, r in enumerate(rows, start=1):
            c = Customer(
                tenant_id=tid,
                code=r.get("CUSTOMER_CODE") or f"CUS-{i:04d}",
                name=r.get("CUSTOMER_NAME") or f"عميل {i}",
                phone=r.get("MOBILE_1") or r.get("PHONE"),
                address=r.get("ADDRESS"),
                credit_limit=dec(r.get("DEBIT_LIMIT")),
                balance=dec(r.get("BEGINNING_BALANCE")),
                notes=r.get("NOTES"),
            )
            db.add(c)
            await db.flush()
            cust_id_map[r["CUSTOMER_ID"]] = c.id
        await db.commit()
        report["customers"] = len(rows)

        # ── ITEMS → materials ──
        mat_id_map = {}
        rows = fetch_all(cur, "ITEMS", "ITEM_ID")
        for i, r in enumerate(rows, start=1):
            code = r.get("ITEM_CODE") or f"MAT-{i:04d}"
            m = Material(
                tenant_id=tid,
                code=str(code),
                name_ar=r.get("ITEM_NAME") or f"مادة {i}",
                name_en=r.get("ITEM_NAME_EN") or r.get("ITEM_NAME") or f"Material {i}",
                unit=r.get("UOM_NAME") or "قطعة",
                unit_cost=dec(r.get("PURSHASED_PRICE")),
                stock_qty=dec(r.get("BEGINNING_BALANCE")),
                category_id=cat_id,
                supplier=None,
                material_kind="general",
            )
            db.add(m)
            await db.flush()
            mat_id_map[r["ITEM_ID"]] = m.id
        await db.commit()
        report["materials"] = len(rows)

        # ── PURCHASE_HEADER + PURCHASING_LINES ──
        lines_by_invoice = {}
        for ln in fetch_all(cur, "PURCHASING_LINES", "LINE_ID"):
            lines_by_invoice.setdefault(ln["PURCHASING_INVOICE_ID"], []).append(ln)

        headers = fetch_all(cur, "PURCHASE_HEADER", "PURCHASING_INVOICE_ID")
        inv_count, line_count = 0, 0
        for i, h in enumerate(headers, start=1):
            d = to_date(h.get("INVOICE_DATE")) or date(2025, 1, 1)
            inv = PurchaseInvoice(
                tenant_id=tid,
                code=str(h.get("INVOICE_NUMBER") or f"PI-ACC-{i:04d}"),
                supplier_id=sup_id_map.get(h.get("SUPPLIER_ID")),
                date=d,
                status="received" if dec(h.get("REMAIN_AMOUNT")) <= 0 else "partial",
                subtotal=dec(h.get("TOTAL_AMOUNT")),
                tax_amount=dec(h.get("TOTAL_TAX")),
                discount_amt=dec(h.get("TOTAL_DISCOUNT")),
                total=dec(h.get("NET_AMOUNT")),
                paid_amount=dec(h.get("PAID_AMOUNT")),
                notes=h.get("NOTES") or h.get("COMMENTS"),
            )
            db.add(inv)
            await db.flush()
            inv_count += 1

            for ln in lines_by_invoice.get(h["PURCHASING_INVOICE_ID"], []):
                db.add(PurchaseInvoiceLine(
                    tenant_id=tid,
                    invoice_id=inv.id,
                    material_id=mat_id_map.get(ln.get("ITEM_ID")),
                    description=str(ln.get("ITEM_ID") or "بند"),
                    qty=dec(ln.get("ITEM_QTY"), "1"),
                    unit=ln.get("UOM_NAME") or "قطعة",
                    unit_cost=dec(ln.get("ITEM_PRICE")),
                    total_cost=dec(ln.get("NET_PRICE")),
                    update_stock=False,
                ))
                line_count += 1
            if i % 40 == 0:
                await db.commit()
        await db.commit()
        report["purchase_invoices"] = inv_count
        report["purchase_invoice_lines"] = line_count

        # ── TREASURY → treasury_transactions ──
        rows = fetch_all(cur, "TREASURY", "DOCUMENT_ID")
        txn_count = 0
        for i, r in enumerate(rows, start=1):
            amount = dec(r.get("AMOUNT"))
            if amount == 0:
                continue
            if r.get("SUPPLIER_ID"):
                direction = "out"
            elif r.get("CUSTOMER_ID"):
                direction = "in"
            else:
                direction = "in" if amount >= 0 else "out"
            db.add(TreasuryTransaction(
                tenant_id=tid,
                treasury_id=treasury_id,
                direction=direction,
                amount=abs(amount),
                txn_date=to_date(r.get("DOCUMENT_DATE")) or date(2025, 1, 1),
                description=r.get("DESCRIPTIONS") or "مستورد من الأكسيس",
                reference=str(r.get("DOCUMENT_NUMBER")) if r.get("DOCUMENT_NUMBER") else None,
                source="manual",
            ))
            txn_count += 1
            if i % 40 == 0:
                await db.commit()
        await db.commit()
        report["treasury_transactions"] = txn_count

    acc.close()
    await engine.dispose()

    print("تم الترحيل بنجاح إلى:", NEW_DB_PATH)
    for k, v in report.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
