"""
خدمة سجل التدقيق (Audit Log).

- ثوابت الإجراءات وأسماء الكيانات بالعربي.
- أدوات التحويل الآمن للقيم (JSON-safe) وإخفاء الحقول الحساسة وحساب الـ diff.
- record_event(): تسجيل حدث صريح (login / logout ...) داخل transaction المستدعي.

ملاحظة: هذه الخدمة "قراءة فقط" بالنسبة للمخزون والمحاسبة — لا تلمس
inventory_ledger ولا auto_journal.
"""
import json
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional, Dict

from app.core import audit_context as ctx
from app.models.models import AuditLog

# ── الإجراءات ───────────────────────────────────────────────────
ACTIONS = (
    "create", "update", "delete",
    "login", "login_failed", "logout",
    "permission_change", "password_change",
    "purge",
)
ACTION_LABELS: Dict[str, str] = {
    "create": "إضافة",
    "update": "تعديل",
    "delete": "حذف",
    "login": "تسجيل دخول",
    "login_failed": "فشل تسجيل الدخول",
    "logout": "تسجيل خروج",
    "permission_change": "تغيير صلاحيات",
    "password_change": "تغيير كلمة المرور",
    "purge": "تنظيف السجل (مدة الاحتفاظ)",
}

# ── جداول مستثناة من الالتقاط (ضوضاء / نفس سجل التدقيق) ──────────
IGNORED_TABLES = frozenset({
    "audit_logs", "analytics_events", "stock_quants", "material_bin_stock",
    "mrp_suggestions", "material_price_history", "backup_runs",
    "einvoice_submissions",
})
GLOBAL_IGNORED_COLUMNS = frozenset({"created_at", "updated_at"})
TABLE_IGNORED_COLUMNS: Dict[str, frozenset] = {"users": frozenset({"last_login"})}

SENSITIVE_RE = re.compile(
    r"(password|passwd|pwd|secret|token|api_key|hash|credential|authorization|private_key)", re.I)
MASK = "***"
MAX_STR = 1000
MAX_CHANGES_BYTES = 16 * 1024

ENTITY_LABELS: Dict[str, str] = {
    "accounting_entries": "قيد محاسبي", "asset_movements": "حركة أصل",
    "attendance": "حضور وانصراف", "bar_stock_items": "قطعة أسياخ/مواسير",
    "bin_locations": "موقع تخزين", "bom_lines": "بند قائمة مواد",
    "cad_files": "ملف CAD", "chart_of_accounts": "حساب (دليل الحسابات)",
    "cost_lines": "بند تكلفة", "credit_note_lines": "بند إشعار دائن",
    "credit_notes": "إشعار دائن", "customer_assets": "أصل عميل",
    "customer_locations": "موقع عميل", "customer_order_lines": "بند طلب عميل",
    "customer_orders": "طلب عميل", "customer_payment_allocations": "تخصيص دفعة عميل",
    "customer_payments": "دفعة عميل", "customers": "عميل",
    "delivery_orders": "إذن تسليم", "equipment": "معدة",
    "equipment_categories": "فئة معدات", "equipment_cost_lines": "بند تكلفة معدة",
    "equipment_dimensions": "أبعاد معدة", "ext_project_expenses": "مصروف مشروع خارجي",
    "ext_project_materials": "خامة مشروع خارجي", "ext_project_members": "عضو مشروع خارجي",
    "ext_project_payments": "دفعة مشروع خارجي", "ext_project_progress": "تقدم مشروع خارجي",
    "ext_project_tools": "عدة مشروع خارجي", "ext_project_trips": "رحلة مشروع خارجي",
    "ext_projects": "مشروع خارجي", "field_service_orders": "أمر خدمة ميدانية",
    "file_attachments": "مرفق", "fixed_asset_depreciations": "إهلاك أصل ثابت",
    "fixed_assets": "أصل ثابت", "invoice_lines": "بند فاتورة",
    "invoice_payment_schedules": "قسط فاتورة", "invoices": "فاتورة",
    "journal_entries": "قيد يومية", "material_categories": "فئة خامات",
    "material_reservations": "حجز خامة", "materials": "خامة",
    "payment_schedule_templates": "قالب أقساط", "payroll_lines": "بند رواتب",
    "payroll_runs": "مسير رواتب", "price_catalog_items": "صنف كتالوج الأسعار",
    "production_schedules": "جدول إنتاج", "project_labor_entries": "عمالة مشروع",
    "project_tool_withdrawals": "سحب عدة مشروع", "purchase_invoice_lines": "بند فاتورة مشتريات",
    "purchase_invoices": "فاتورة مشتريات", "purchase_order_lines": "بند أمر شراء",
    "purchase_orders": "أمر شراء", "quality_inspections": "فحص جودة",
    "quotation_lines": "بند عرض سعر", "quotations": "عرض سعر",
    "scrap_records": "سجل سكراب", "scrap_sale_lines": "بند بيع سكراب",
    "scrap_sales": "بيع سكراب", "service_assignments": "تكليف خدمة",
    "service_visits": "زيارة خدمة", "sheet_stock_items": "لوح صاج",
    "stock_batches": "دفعة مخزون", "stock_count_lines": "بند جرد",
    "stock_counts": "جرد", "stock_movements": "حركة مخزون",
    "supplier_payment_allocations": "تخصيص دفعة مورد", "supplier_payments": "دفعة مورد",
    "suppliers": "مورد", "supply_contract_lines": "بند عقد توريد",
    "supply_contracts": "عقد توريد", "treasuries": "خزينة",
    "treasury_categories": "تصنيف خزينة", "treasury_transactions": "حركة خزينة",
    "users": "مستخدم", "warehouses": "مخزن", "warranty_certificates": "شهادة ضمان",
    "work_completion_certs": "شهادة إنجاز", "work_completion_items": "بند شهادة إنجاز",
    "work_order_bom_lines": "بند مواد أمر عمل", "work_order_members": "عضو أمر عمل",
    "work_order_stages": "مرحلة أمر عمل", "work_orders": "أمر عمل",
    "worker_advances": "سلفة عامل", "workers": "عامل", "workshop_profile": "بيانات الورشة",
    "audit_settings": "إعدادات سجل التدقيق",
}


def entity_label(table: Optional[str]) -> Optional[str]:
    if not table:
        return None
    return ENTITY_LABELS.get(table, table)


# ── تحويل القيم ──────────────────────────────────────────────────
def _is_nan(v: Any) -> bool:
    return isinstance(v, float) and (math.isnan(v) or math.isinf(v))


def json_safe(v: Any) -> Any:
    """يحوّل أي قيمة إلى JSON primitive آمن (يقبله Postgres و SQLite)."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return None if _is_nan(v) else v
    if isinstance(v, Decimal):
        if v.is_nan() or v.is_infinite():
            return None
        return format(v, "f")
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Enum):
        return json_safe(v.value)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"[بيانات ثنائية: {len(bytes(v))} بايت]"
    if isinstance(v, str):
        s = v.replace("\x00", "")
        if len(s) > MAX_STR or s.startswith("data:"):
            return f"[نص/بيانات كبيرة: {len(s)} حرف]"
        return s
    if isinstance(v, (list, tuple, set)):
        return [json_safe(x) for x in v][:50]
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in list(v.items())[:50]}
    return json_safe(str(v))


def is_sensitive(name: str) -> bool:
    return bool(SENSITIVE_RE.search(name or ""))


def values_equal(a: Any, b: Any) -> bool:
    """مقارنة رقمية (10 == 10.00) مع مقارنة عادية لباقي الأنواع."""
    if a is None or b is None:
        return a is None and b is None
    num = (int, float, Decimal)
    if (isinstance(a, num) and isinstance(b, num)
            and not isinstance(a, bool) and not isinstance(b, bool)):
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except Exception:
            return a == b
    try:
        return bool(a == b)
    except Exception:
        return False


def mask_or_safe(name: str, v: Any) -> Any:
    """يخفي الحقول الحساسة (القيمة غير الفارغة فقط)، وإلا يحوّل القيمة لآمنة."""
    if is_sensitive(name) and v is not None and v != "":
        return MASK
    return json_safe(v)


def cap_changes(changes: Dict[str, Any]) -> Dict[str, Any]:
    """يحدّ الحجم المسلسل لـ changes بـ 16KB مع '_truncated': n."""
    try:
        size = len(json.dumps(changes, ensure_ascii=False))
    except Exception:
        return {"_truncated": len(changes)}
    if size <= MAX_CHANGES_BYTES:
        return changes
    out: Dict[str, Any] = {}
    used = 0
    for k, v in changes.items():
        piece = len(json.dumps({k: v}, ensure_ascii=False))
        if used + piece > MAX_CHANGES_BYTES - 64:
            continue
        out[k] = v
        used += piece
    out["_truncated"] = len(changes) - len(out)
    return out


_REF_KEYS = ("invoice_number", "number", "order_number", "code", "reference", "name_ar",
             "name", "username", "title", "internal_code")


def pick_ref(values: Dict[str, Any]) -> Optional[str]:
    for k in _REF_KEYS:
        v = values.get(k)
        if v is not None and str(v).strip() != "":
            s = str(v).replace("\x00", "")
            if s.startswith("data:"):
                continue
            return s[:200]
    return None


def classify_action(table: str, op: str, changed_keys, actor_known: bool) -> str:
    """create/update/delete — مع تمييز تغيير صلاحيات/كلمة مرور المستخدمين.
    ترقية الهاش القديم عند الدخول (بدون فاعل) تبقى 'update' عادية."""
    if op == "update" and table == "users":
        keys = set(changed_keys)
        if keys & {"role", "is_active"}:
            return "permission_change"
        if "password_hash" in keys and actor_known:
            return "password_change"
    return op


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def trunc(v: Optional[str], n: int) -> Optional[str]:
    if v is None:
        return None
    return str(v).replace("\x00", "")[:n]


def build_row(*, tenant_id: int, action: str, entity_type: Optional[str] = None,
              entity_id: Optional[int] = None, entity_ref: Optional[str] = None,
              summary: Optional[str] = None, changes: Optional[dict] = None,
              extra: Optional[dict] = None, user_id: Optional[int] = None,
              user_name: Optional[str] = None, use_context: bool = True,
              ip: Optional[str] = None, path: Optional[str] = None,
              request_id: Optional[str] = None) -> Dict[str, Any]:
    """يبني dict جاهز لـ insert (كل الأعمدة موجودة بنفس المفاتيح)."""
    if use_context:
        if user_id is None:
            user_id = ctx.audit_user_id.get()
        if user_name is None:
            user_name = ctx.audit_user_name.get()
        ip = ip or ctx.audit_ip.get()
        path = path or ctx.audit_path.get()
        request_id = request_id or ctx.audit_request_id.get()
    return {
        "tenant_id": int(tenant_id),
        "created_at": utcnow_naive(),
        "user_id": user_id,
        "user_name": trunc(user_name, 100),
        "action": action,
        "entity_type": trunc(entity_type, 60),
        "entity_id": entity_id,
        "entity_ref": trunc(entity_ref, 200),
        "summary": trunc(summary, 300),
        "changes": changes,
        "extra": json_safe(extra) if extra is not None else None,
        "ip": trunc(ip, 64),
        "path": trunc(path, 200),
        "request_id": trunc(request_id, 36),
    }


def record_event(db, action: str, tenant_id: int, *, user_id: Optional[int] = None,
                 user_name: Optional[str] = None, entity_type: Optional[str] = None,
                 entity_id: Optional[int] = None, entity_ref: Optional[str] = None,
                 summary: Optional[str] = None, changes: Optional[dict] = None,
                 extra: Optional[dict] = None) -> AuditLog:
    """يضيف صف تدقيق صريح داخل transaction المستدعي (المستدعي مسؤول عن commit).
    الفاعل الافتراضي يُؤخذ من ContextVars؛ tenant_id صريح دائمًا."""
    row = build_row(tenant_id=tenant_id, action=action, entity_type=entity_type,
                    entity_id=entity_id, entity_ref=entity_ref, summary=summary,
                    changes=changes, extra=extra, user_id=user_id, user_name=user_name)
    obj = AuditLog(**row)
    db.add(obj)
    return obj
