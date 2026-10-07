"""
tenant_export.py — تصدير بيانات ورشة واحدة (tenant) كملف zip/JSON.

قواعد العزل (مهمة):
  * استعلامات Core (select(table)) مش بيغطيها فلتر الـ tenant التلقائي، فكل استعلام هنا
    بشرط tenant_id صريح. الجداول الابن اللي مالهاش tenant_id بتتفلتر عبر الأب
    (IN-subselect والـ subselect نفسه بشرط tenant_id الأب).
  * صف `tenants` بشرط id == tenant_id.
  * الأعمدة السرية (password/secret/token/hash...) بتتشال من الملف بالكامل.
  * الجداول بتتكتشف ديناميكياً من Base.metadata، فأي جدول جديد بـ tenant_id بيتصدّر تلقائياً،
    وأي جدول بلا tenant_id لازم يتضاف لـ CHILD_SCOPES أو GLOBAL_TABLES (اختبار الحراسة بيتأكد).
"""
import base64
import hashlib
import json
import os
import re
import zipfile
from datetime import date, datetime, time as dtime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base

FORMAT_VERSION = 1
APP_VERSION = "4.0.0"
BATCH = 1000

# جداول لا تُصدَّر: سجل العمليات نفسه + أحداث تحليلية (مش بيانات ورشة)
EXPORT_EXCLUDE_TABLES = frozenset({"backup_runs", "analytics_events"})
# جداول عامة مشتركة بلا tenant_id (مش بيانات ورشة)
GLOBAL_TABLES = frozenset({"global_catalog"})
# جدول ← (عمود الربط، الجدول الأب اللي عنده tenant_id)
CHILD_SCOPES: Dict[str, Tuple[str, str]] = {
    "accounting_entry_lines": ("entry_id", "accounting_entries"),
    "delivery_order_lines": ("order_id", "delivery_orders"),
    "sales_order_work_orders": ("customer_order_id", "customer_orders"),
}
# نفس نمط SENSITIVE_RE في سجل التدقيق (superset) — أي عمود بالاسم ده بيتشال
SECRET_COLUMN_RE = re.compile(
    r"(password|passwd|pwd|secret|token|api_key|hash|credential|authorization|private_key)", re.I)

NOTE_AR = "الملفات المرفوعة (الشعارات وملفات CAD والمرفقات) غير مضمنة — بيانات قاعدة البيانات فقط"


def is_secret_column(name: str) -> bool:
    return bool(SECRET_COLUMN_RE.search(name or ""))


def _json_default(o: Any):
    if isinstance(o, Decimal):
        return str(o)  # نحافظ على الدقة
    if isinstance(o, (datetime, date, dtime)):
        return o.isoformat()
    if isinstance(o, (bytes, bytearray, memoryview)):
        return {"__b64__": base64.b64encode(bytes(o)).decode("ascii")}
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_default, ensure_ascii=False)


def table_plan() -> List[dict]:
    """يكتشف الجداول من Base.metadata ويصنفها: exported / child / skipped."""
    plan = []
    for t in Base.metadata.sorted_tables:
        if t.name in EXPORT_EXCLUDE_TABLES or t.name in GLOBAL_TABLES:
            plan.append({"table": t, "mode": "skip"})
        elif t.name == "tenants":
            plan.append({"table": t, "mode": "tenant_row"})
        elif "tenant_id" in t.c:
            plan.append({"table": t, "mode": "tenant"})
        elif t.name in CHILD_SCOPES:
            plan.append({"table": t, "mode": "child"})
        else:
            # جدول بلا نطاق معروف: لا نصدّره أبداً (تفادي تسريب)
            plan.append({"table": t, "mode": "unscoped"})
    return plan


def redacted_columns() -> List[str]:
    """قائمة 'table.column' للأعمدة المحجوبة في الجداول المُصدَّرة."""
    out = []
    for p in table_plan():
        if p["mode"] in ("tenant", "tenant_row", "child"):
            for c in p["table"].c:
                if is_secret_column(c.name):
                    out.append(f"{p['table'].name}.{c.name}")
    return sorted(out)


def _scoped_select(p: dict, tenant_id: int):
    t = p["table"]
    cols = [c for c in t.c if not is_secret_column(c.name)]
    stmt = select(*cols)
    mode = p["mode"]
    if mode == "tenant":
        stmt = stmt.where(t.c.tenant_id == tenant_id)
    elif mode == "tenant_row":
        stmt = stmt.where(t.c.id == tenant_id)
    elif mode == "child":
        fk_col, parent_name = CHILD_SCOPES[t.name]
        parent = Base.metadata.tables[parent_name]
        sub = select(parent.c.id).where(parent.c.tenant_id == tenant_id)
        stmt = stmt.where(t.c[fk_col].in_(sub))
    else:  # pragma: no cover
        raise ValueError("unscoped table")
    pk = [c for c in t.primary_key.columns]
    if pk:
        stmt = stmt.order_by(*pk)
    return stmt


async def _stream_rows(db: AsyncSession, stmt):
    result = await db.stream(stmt.execution_options(yield_per=BATCH, skip_tenant_filter=True))
    try:
        keys = list(result.keys())
        async for part in result.partitions(BATCH):
            for row in part:
                yield dict(zip(keys, row))
    finally:
        await result.close()


class _Sink:
    """يكتب جدولاً كمصفوفة JSON تدريجياً (zip entry أو جزء من ملف JSON واحد)."""

    def __init__(self, write):
        self._write = write
        self.count = 0
        self._first = True

    def start(self):
        self._write("[")

    def row(self, d: dict):
        self._write(("" if self._first else ",\n") + dumps(d))
        self._first = False
        self.count += 1

    def end(self):
        self._write("]")


async def export_tenant_to_file(db: AsyncSession, tenant_id: int, fmt: str, dest_path: str) -> dict:
    """يكتب تصدير الورشة في dest_path. يرجّع {tables, redacted, size, sha256, tenant}."""
    from app.models.models import Tenant
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise LookupError("tenant not found")
    plan = [p for p in table_plan() if p["mode"] in ("tenant", "tenant_row", "child")]
    unscoped = [p["table"].name for p in table_plan() if p["mode"] == "unscoped"]
    counts: Dict[str, int] = {}

    def _manifest() -> dict:
        return {
            "format_version": FORMAT_VERSION,
            "app_version": APP_VERSION,
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "tenant": {"id": tenant.id, "name": tenant.name, "slug": tenant.slug},
            "tables": counts,
            "redacted_columns": redacted_columns(),
            "scoped_via": {k: f"{v[1]}.{v[0]}" for k, v in CHILD_SCOPES.items()},
            "skipped_unscoped_tables": unscoped,
            "note": NOTE_AR,
        }

    if fmt == "zip":
        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for p in plan:
                name = p["table"].name
                with zf.open(f"{name}.json", "w", force_zip64=True) as raw:
                    sink = _Sink(lambda s, raw=raw: raw.write(s.encode("utf-8")))
                    sink.start()
                    async for d in _stream_rows(db, _scoped_select(p, tenant_id)):
                        sink.row(d)
                    sink.end()
                counts[name] = sink.count
            zf.writestr("manifest.json", json.dumps(_manifest(), ensure_ascii=False, indent=1))
    elif fmt == "json":
        with open(dest_path, "w", encoding="utf-8") as f:
            f.write('{"tables": {')
            for i, p in enumerate(plan):
                name = p["table"].name
                f.write(("," if i else "") + json.dumps(name) + ": ")
                sink = _Sink(f.write)
                sink.start()
                async for d in _stream_rows(db, _scoped_select(p, tenant_id)):
                    sink.row(d)
                sink.end()
                counts[name] = sink.count
            f.write('}, "manifest": ' + json.dumps(_manifest(), ensure_ascii=False) + "}")
    else:
        raise ValueError("bad format")

    h = hashlib.sha256()
    with open(dest_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {
        "tables": counts,
        "redacted": redacted_columns(),
        "size": os.path.getsize(dest_path),
        "sha256": h.hexdigest(),
    }
