"""
migrate_access_stock.py — تحديث الكميات الحالية للأصناف المُرحّلة من الأكسيس
بناءً على: الرصيد الافتتاحي + المشتريات + إنتاج وارد - استهلاك تصنيع

Run: PYTHONIOENCODING=utf-8 python migrate_access_stock.py
"""
import asyncio
from decimal import Decimal, InvalidOperation

import pyodbc
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.models import Material

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


async def main():
    cnxn = pyodbc.connect(f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};Dbq={ACCDB_PATH};")
    cur = cnxn.cursor()

    # ── 1. خريطة الوحدات: UNIT_ID -> (UOMM_NAME, UOMM_UNITS) ─────────────
    units = {}
    for row in cur.execute("SELECT UNIT_ID, UOMM_NAME, UOMM_UNITS, UOM_NAME, UOM_UNITS FROM UNITS_OF_MESSURE"):
        units[row.UNIT_ID] = {
            "uomm_name": (row.UOMM_NAME or "").strip(),
            "uomm_units": dec(row.UOMM_UNITS, "1"),
            "uom_name": (row.UOM_NAME or "").strip(),
        }

    # ── 2. بيانات الأصناف: ITEM_ID -> (ITEM_CODE, UOM_NAME, UNIT_ID, UOMM_UNITS, BEGINNING_BALANCE) ─
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
            # base unit أو غير معروف -> استخدم القيمة كما هي
            qty_base = qty
        info["qty"] += sign * qty_base

    # ── 3. المشتريات (تزيد المخزون) ──────────────────────────────────────
    for row in cur.execute("SELECT ITEM_ID, UOM_NAME, ITEM_QTY FROM PURCHASING_LINES"):
        add_qty(row.ITEM_ID, row.UOM_NAME, dec(row.ITEM_QTY), 1)

    # ── 4. إنتاج وارد (يزيد المخزون) ─────────────────────────────────────
    for row in cur.execute("SELECT ITEM_ID, UOM_NAME, ITEM_QTY FROM PRODUCTION_HEADER"):
        add_qty(row.ITEM_ID, row.UOM_NAME, dec(row.ITEM_QTY), 1)

    # ── 5. استهلاك تصنيع (يقلل المخزون) ──────────────────────────────────
    for row in cur.execute("SELECT ITEM_ID, UOM_NAME, ITEM_QTY FROM PRODUCTION_LINES"):
        add_qty(row.ITEM_ID, row.UOM_NAME, dec(row.ITEM_QTY), -1)

    cnxn.close()

    # ── 6. تحديث materials ───────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        n_updated, n_negative = 0, 0
        for info in items.values():
            mat = (await db.execute(select(Material).where(Material.code == info["code"]))).scalar_one_or_none()
            if not mat:
                continue
            qty = info["qty"]
            if qty < 0:
                n_negative += 1
                print(f"  ! كمية سالبة: {mat.code} ({mat.name_ar}) = {qty} -> سيتم ضبطها لـ 0")
                qty = Decimal("0")
            mat.stock_qty = qty
            n_updated += 1
        await db.commit()
        print(f"تم تحديث الكميات لـ {n_updated} صنف ({n_negative} كان لها قيمة سالبة وتم ضبطها لـ 0).")


if __name__ == "__main__":
    asyncio.run(main())
