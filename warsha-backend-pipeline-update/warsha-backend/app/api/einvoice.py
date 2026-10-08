"""
الفاتورة الإلكترونية (ETA) — /api/v1/einvoice

*** التكامل غير مُجرَّب على بيئة مصلحة الضرائب الفعلية — يحتاج شهادة توقيع وبيانات اعتماد وبيئة تجريبية. ***

الصلاحيات: القراءة require_any · الإرسال/المعاينة/إعادة الإرسال/تحديث الحالة/الطابور أدمن+محاسب ·
الإعدادات/اختبار الاتصال/الإلغاء/تشغيل الطابور أدمن فقط · المشاهد (viewer) قراءة فقط.
الورشة (tenant) تُؤخذ دائمًا من توكن المستخدم — لا تُقبل من الجسم ولا من المسار.
الأسرار (client_id / client_secret / signer_token) مشفّرة ولا تُعاد في أي استجابة.
"""
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_any, require_admin, require_admin_accountant
from app.core.database import get_db
from app.services import eta_client, einvoice_mapper as mapper, einvoice_service as svc, secret_box
from app.services import einvoice_signer as signer_mod
from app.services.einvoice_worker import run_einvoice_queue_once

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def _block_viewer_writes(request: Request, user: dict = Depends(require_any)) -> dict:
    """المشاهد (viewer) قراءة فقط — لا يصل لأي endpoint كتابة."""
    if request.method.upper() not in _READ_METHODS and user.get("role") == "viewer":
        raise HTTPException(403, "صلاحية 'viewer' (مشاهد) لا تسمح بالتعديل")
    return user


router = APIRouter(prefix="/einvoice", tags=["einvoice"],
                   dependencies=[Depends(require_any), Depends(_block_viewer_writes)])


def _tid(user: dict) -> int:
    try:
        return int(user["tenant_id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(401, "الجلسة غير صالحة — يرجى إعادة تسجيل الدخول")


def _fail(e: svc.EInvoiceError):
    """422 بشكل {detail:<نص>, errors:[..]}؛ غيره HTTPException عادي (detail نص)."""
    if e.errors is not None:
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail, "errors": e.errors})
    raise HTTPException(e.status_code, e.detail)


# ═════════════════════ الإعدادات ═════════════════════

class SettingsIn(BaseModel):
    enabled: Optional[bool] = None
    environment: Optional[str] = None
    tax_registration_number: Optional[str] = None
    activity_code: Optional[str] = None
    issuer_name: Optional[str] = None
    branch_id: Optional[str] = None
    governate: Optional[str] = None
    region_city: Optional[str] = None
    street: Optional[str] = None
    building_number: Optional[str] = None
    custom_id_base_url: Optional[str] = None
    custom_api_base_url: Optional[str] = None
    signer_mode: Optional[str] = None
    signer_url: Optional[str] = None
    default_item_type: Optional[str] = None
    default_item_code: Optional[str] = None
    default_unit_type: Optional[str] = None
    vat_subtype: Optional[str] = None
    zero_vat_subtype: Optional[str] = None
    person_id_threshold: Optional[Decimal] = None
    max_backdate_days: Optional[int] = None
    # write-only
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    signer_token: Optional[str] = None
    clear_client_secret: Optional[bool] = None
    clear_signer_token: Optional[bool] = None


def _mask(enc: Optional[str]) -> Optional[str]:
    if not enc:
        return None
    try:
        v = secret_box.decrypt(enc)
    except secret_box.SecretBoxError:
        return "••••"
    return "••••" + v[-4:] if len(v) > 4 else "••••"


def settings_view(s) -> dict:
    id_base, api_base = eta_client.resolve_base_urls(s.environment, s.custom_id_base_url, s.custom_api_base_url)
    miss = svc.missing_fields(s)
    return {
        "enabled": bool(s.enabled), "environment": s.environment,
        "tax_registration_number": s.tax_registration_number, "activity_code": s.activity_code,
        "issuer_name": s.issuer_name, "branch_id": s.branch_id, "governate": s.governate,
        "region_city": s.region_city, "street": s.street, "building_number": s.building_number,
        "has_client_id": bool(s.client_credential_id_enc), "client_id_masked": _mask(s.client_credential_id_enc),
        "has_client_secret": bool(s.client_secret_enc), "has_signer_token": bool(s.signer_token_enc),
        "signer_mode": s.signer_mode, "signer_url": s.signer_url,
        "custom_id_base_url": s.custom_id_base_url, "custom_api_base_url": s.custom_api_base_url,
        "effective_id_base_url": id_base, "effective_api_base_url": api_base,
        "default_item_type": s.default_item_type, "default_item_code": s.default_item_code,
        "default_unit_type": s.default_unit_type, "vat_subtype": s.vat_subtype,
        "zero_vat_subtype": s.zero_vat_subtype,
        "person_id_threshold": float(s.person_id_threshold if s.person_id_threshold is not None else 50000),
        "max_backdate_days": s.max_backdate_days if s.max_backdate_days is not None else 7,
        "last_connection_ok": s.last_connection_ok, "last_connection_at": svc.iso(s.last_connection_at),
        "last_connection_error": s.last_connection_error,
        "ready": bool(s.enabled and not miss), "missing": miss, "disclaimer": svc.DISCLAIMER,
    }


_TEXT_LIMITS = {"issuer_name": 200, "branch_id": 20, "governate": 100, "region_city": 100,
                "street": 200, "building_number": 50}


def _bad(msg: str):
    raise HTTPException(400, msg)


def _clean_text(name: str, v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    if len(v) > _TEXT_LIMITS[name]:
        _bad(f"الحقل '{name}' طويل جدًا (الحد {_TEXT_LIMITS[name]} حرفًا)")
    return v or None


def _validate_changes(body: dict, current) -> dict:
    """يتحقق من الحقول المُرسلة ويرجّع dict بالقيم المنظّفة (لم يُطبَّق شيء بعد)."""
    ch: dict = {}
    if "enabled" in body and body["enabled"] is not None:
        ch["enabled"] = bool(body["enabled"])
    if body.get("environment") is not None:
        if body["environment"] not in eta_client.VALID_ENVIRONMENTS:
            _bad("البيئة غير معروفة (preprod / prod / custom)")
        ch["environment"] = body["environment"]
    if "tax_registration_number" in body and body["tax_registration_number"] is not None:
        v = re.sub(r"[\s\-]", "", body["tax_registration_number"])
        if v and not re.fullmatch(r"\d{9}", v):
            _bad("رقم التسجيل الضريبي يجب أن يكون 9 أرقام")
        ch["tax_registration_number"] = v or None
    if "activity_code" in body and body["activity_code"] is not None:
        v = body["activity_code"].strip()
        if v and not re.fullmatch(r"\d{4,6}", v):
            _bad("كود النشاط يجب أن يكون من 4 إلى 6 أرقام")
        ch["activity_code"] = v or None
    for name in _TEXT_LIMITS:
        if name in body and body[name] is not None:
            ch[name] = _clean_text(name, body[name])
    if body.get("default_item_type") is not None:
        if body["default_item_type"] not in ("EGS", "GS1"):
            _bad("نوع الصنف الافتراضي يجب أن يكون EGS أو GS1")
        ch["default_item_type"] = body["default_item_type"]
    if body.get("default_item_code") is not None:
        v = body["default_item_code"].strip()
        if not re.fullmatch(r"[A-Za-z0-9\-_]{1,50}", v):
            _bad("كود الصنف الافتراضي: حروف إنجليزية وأرقام و - _ فقط (حتى 50)")
        ch["default_item_code"] = v
    if body.get("default_unit_type") is not None:
        v = body["default_unit_type"].strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,6}", v):
            _bad("كود الوحدة الافتراضي غير صالح (مثل EA)")
        ch["default_unit_type"] = v
    for name in ("vat_subtype", "zero_vat_subtype"):
        if body.get(name) is not None:
            v = body[name].strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{2,10}", v):
                _bad(f"النوع الفرعي للضريبة غير صالح ({name})")
            ch[name] = v
    if body.get("person_id_threshold") is not None:
        try:
            v = Decimal(str(body["person_id_threshold"]))
        except InvalidOperation:
            _bad("حد الأفراد غير صالح")
        if v < 0 or v > Decimal("1000000000"):
            _bad("حد الأفراد يجب أن يكون بين صفر ومليار")
        ch["person_id_threshold"] = v
    if body.get("max_backdate_days") is not None:
        if not (0 <= int(body["max_backdate_days"]) <= 365):
            _bad("أيام الإرسال المتأخر يجب أن تكون بين 0 و365")
        ch["max_backdate_days"] = int(body["max_backdate_days"])
    if body.get("signer_mode") is not None:
        if body["signer_mode"] not in signer_mod.SIGNER_MODES:
            _bad("وضع التوقيع غير معروف (none / mock / external)")
        ch["signer_mode"] = body["signer_mode"]
    for name in ("signer_url", "custom_id_base_url", "custom_api_base_url"):
        if name in body and body[name] is not None:
            v = body[name].strip()
            if v:
                if len(v) > 300:
                    _bad("الرابط طويل جدًا")
                try:
                    v = eta_client.validate_https_url(v)
                except eta_client.UnsafeUrlError as e:
                    _bad(f"الرابط غير مسموح: {e}")
            ch[name] = v or None
    # الأسرار (write-only): None = احتفظ، "" = مرفوض
    for src, dst in (("client_id", "client_credential_id_enc"), ("client_secret", "client_secret_enc"),
                     ("signer_token", "signer_token_enc")):
        if body.get(src) is not None:
            v = body[src]
            if not v.strip():
                _bad(f"لا يمكن حفظ قيمة فارغة لـ {src} — اتركه بدون إرسال للاحتفاظ بالقيمة الحالية")
            if len(v) > 2000:
                _bad(f"القيمة طويلة جدًا ({src})")
            try:
                ch[dst] = secret_box.encrypt(v.strip())
            except secret_box.SecretBoxError as e:
                _bad(str(e))
    if body.get("clear_client_secret"):
        if "client_secret_enc" in ch:
            _bad("لا يمكن إرسال client_secret مع clear_client_secret")
        ch["client_secret_enc"] = None
    if body.get("clear_signer_token"):
        if "signer_token_enc" in ch:
            _bad("لا يمكن إرسال signer_token مع clear_signer_token")
        ch["signer_token_enc"] = None
    return ch


@router.get("/status")
async def einvoice_status(user: dict = Depends(require_any), db: AsyncSession = Depends(get_db)):
    """حالة مختصرة لكل الأدوار (لإظهار/إخفاء أزرار الإرسال) — بلا أسرار ولا أرقام ضريبية."""
    s = await mapper.get_settings(db, _tid(user))
    if s is None:
        return {"enabled": False, "environment": "preprod", "ready": False, "signer_mode": "none"}
    return {"enabled": bool(s.enabled), "environment": s.environment,
            "ready": svc.is_ready(s), "signer_mode": s.signer_mode}


@router.get("/settings")
async def get_settings(user: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    s = await svc.get_or_create_settings(db, _tid(user))
    await db.commit()
    return settings_view(s)


@router.put("/settings")
async def put_settings(data: SettingsIn, user: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    tid = _tid(user)
    body = data.model_dump(exclude_unset=True)
    body.pop("tenant_id", None)
    s = await svc.get_or_create_settings(db, tid)
    ch = _validate_changes(body, s)

    def final(name):
        return ch[name] if name in ch else getattr(s, name)

    env = final("environment")
    if (ch.get("custom_id_base_url") or ch.get("custom_api_base_url")) and env != "custom":
        _bad("الروابط المخصصة تُستخدم فقط مع البيئة 'custom'")
    if env != "custom" and ("environment" in ch):
        # الخروج من البيئة المخصصة ينظّف روابطها
        ch.setdefault("custom_id_base_url", None)
        ch.setdefault("custom_api_base_url", None)
    try:
        signer_mod.assert_signer_allowed(env, final("signer_mode") or "none")
    except signer_mod.SignerError as e:
        _bad(str(e))
    for k, v in ch.items():
        setattr(s, k, v)
    if s.enabled:
        miss = svc.missing_fields(s)
        if miss:
            _bad("لا يمكن تفعيل الفاتورة الإلكترونية — بيانات ناقصة: " + "، ".join(miss))
    await db.commit()
    await db.refresh(s)
    return settings_view(s)


@router.post("/settings/test-connection")
async def test_connection(user: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    tid = _tid(user)
    s = await svc.get_or_create_settings(db, tid)
    if not s.client_credential_id_enc or not s.client_secret_enc:
        raise HTTPException(400, "أدخل client_id و client_secret أولًا")
    try:
        client = svc.build_client(s, max_attempts=1)
    except svc.EInvoiceError as e:
        raise HTTPException(e.status_code, e.detail)
    ok, msg = True, "تم الاتصال بنجاح — حصلنا على توكن من خادم المصادقة (لم يُرسل أي مستند)"
    try:
        await client.get_token(force=True)
    except eta_client.EtaError as e:
        ok, msg = False, str(e)
    s.last_connection_ok = ok
    s.last_connection_at = svc.utcnow()
    s.last_connection_error = None if ok else msg[:500]
    await db.commit()
    return {"ok": ok, "message": msg}


# ═════════════════════ المستندات ═════════════════════

@router.get("/documents/{doc_type}/{doc_id}/preview")
async def preview_document(doc_type: str, doc_id: int, user: dict = Depends(require_admin_accountant),
                           db: AsyncSession = Depends(get_db)):
    """قراءة فقط: لا شبكة ولا توقيع ولا تغيير حالة."""
    tid = _tid(user)
    try:
        dt = svc.norm_doc_type(doc_type)
    except svc.EInvoiceError as e:
        return _fail(e)
    settings = await mapper.get_settings(db, tid)
    res = await mapper.build_document(db, tid, dt, doc_id, settings)
    errors, warnings = list(res.errors), list(res.warnings)
    if settings is None or not settings.enabled:
        errors.append("الفاتورة الإلكترونية غير مُفعّلة")
    else:
        miss = svc.missing_fields(settings)
        if miss:
            errors.append("إعدادات الفاتورة الإلكترونية ناقصة: " + "، ".join(miss))
    row = await svc.load_row(db, tid, dt, doc_id)
    if row is not None and row.status not in ("queued",) + svc.RESUBMITTABLE:
        warnings.append("سبق إرسال هذا المستند أو أُلغي لدى المصلحة — لا يمكن إرساله مرة أخرى")
    can_submit = (not errors) and (row is None or row.status in ("queued",) + svc.RESUBMITTABLE)
    return {"document": res.document, "errors": errors, "warnings": warnings, "can_submit": bool(can_submit)}


async def _respond(coro):
    try:
        body, code = await coro
    except svc.EInvoiceError as e:
        return _fail(e)
    return JSONResponse(status_code=code, content=body)


@router.post("/documents/{doc_type}/{doc_id}/submit")
async def submit_document(doc_type: str, doc_id: int, user: dict = Depends(require_admin_accountant),
                          db: AsyncSession = Depends(get_db)):
    try:
        dt = svc.norm_doc_type(doc_type)
    except svc.EInvoiceError as e:
        return _fail(e)
    return await _respond(svc.submit_document(db, _tid(user), dt, doc_id, user))


@router.post("/documents/{doc_type}/{doc_id}/resubmit")
async def resubmit_document(doc_type: str, doc_id: int, user: dict = Depends(require_admin_accountant),
                            db: AsyncSession = Depends(get_db)):
    try:
        dt = svc.norm_doc_type(doc_type)
    except svc.EInvoiceError as e:
        return _fail(e)
    return await _respond(svc.submit_document(db, _tid(user), dt, doc_id, user, resubmit=True))


@router.post("/documents/{doc_type}/{doc_id}/refresh-status")
async def refresh_document(doc_type: str, doc_id: int, user: dict = Depends(require_admin_accountant),
                           db: AsyncSession = Depends(get_db)):
    try:
        dt = svc.norm_doc_type(doc_type)
    except svc.EInvoiceError as e:
        return _fail(e)
    return await _respond(svc.refresh_status(db, _tid(user), dt, doc_id))


class CancelIn(BaseModel):
    reason: str


@router.post("/documents/{doc_type}/{doc_id}/cancel")
async def cancel_document(doc_type: str, doc_id: int, data: CancelIn, user: dict = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    try:
        dt = svc.norm_doc_type(doc_type)
        return await svc.cancel_document(db, _tid(user), dt, doc_id, data.reason)
    except svc.EInvoiceError as e:
        return _fail(e)


@router.get("/documents/{doc_type}/{doc_id}")
async def document_state(doc_type: str, doc_id: int, user: dict = Depends(require_any),
                         db: AsyncSession = Depends(get_db)):
    try:
        dt = svc.norm_doc_type(doc_type)
        return await svc.document_state(db, _tid(user), dt, doc_id)
    except svc.EInvoiceError as e:
        return _fail(e)


@router.get("/submissions")
async def list_submissions(status: Optional[str] = None, doc_type: Optional[str] = None, q: Optional[str] = None,
                           limit: int = 50, offset: int = 0, user: dict = Depends(require_admin_accountant),
                           db: AsyncSession = Depends(get_db)):
    try:
        return await svc.list_submissions(db, _tid(user), status=status, doc_type=doc_type, q=q,
                                          limit=limit, offset=offset)
    except svc.EInvoiceError as e:
        return _fail(e)


@router.post("/queue/run")
async def run_queue(user: dict = Depends(require_admin)):
    """تمريرة واحدة من الـ worker لورشة المستخدم فقط (حتى 50 صفًا)."""
    return await run_einvoice_queue_once(tenant_id=_tid(user), limit=50)
