"""
einvoice_service.py — تنسيق إرسال الفاتورة الإلكترونية (آلة الحالات في مكان واحد).

*** التكامل غير مُجرَّب على بيئة ETA الفعلية *** (يحتاج شهادة توقيع + client_id/secret + بيئة تجريبية).

الحالات: NULL (لم تُرسل) -> queued -> submitting -> submitted -> valid | invalid
         submitting -> failed (مؤقت، يُعاد من الـ worker)  |  rejected (رفض دائم: يحتاج تصحيح بيانات)
         valid -> cancelled (إلغاء لدى المصلحة، أدمن فقط).

مبادئ:
  * الإرسال لا يُنتج أي قيد محاسبي ولا حركة مخزون (الإيراد/الضريبة اتسجلوا وقت إصدار المستند أصلًا).
  * الحمولة الموقّعة تُحفظ (payload_json + sha256) وتُعمَّد (commit) قبل أي اتصال بالشبكة، والإعادة ترسل
    نفس الحمولة تمامًا ونفس internalID — فتكرار الإرسال لا يُنشئ مستندًا ثانيًا.
  * صف تسليم واحد لكل (tenant, doc_type, doc_id) (UNIQUE) + claim ذرّي بـ UPDATE ... WHERE status IN (...)
    وفحص rowcount == 1 => ضغطتين/طلبين متزامنين = اتصال HTTP واحد.
  * لا يُترك أي transaction مفتوح أثناء مكالمة HTTP.
  * كل الاستعلامات بـ tenant_id صريح (الـ worker بلا JWT فلا فلتر تلقائي).
"""
import json
import logging
from datetime import date as _date, datetime, timedelta
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select, update, func, or_, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import CreditNote, Customer, EInvoiceSettings, EInvoiceSubmission, Invoice
from app.services import eta_client, einvoice_mapper as mapper, secret_box
from app.services import einvoice_signer as signer_mod

logger = logging.getLogger("warsha.einvoice")

DISCLAIMER = "غير مُجرَّب على بيئة مصلحة الضرائب الفعلية — يحتاج شهادة توقيع وبيانات اعتماد وبيئة تجريبية"
ALL_STATUSES = ("queued", "submitting", "submitted", "valid", "invalid", "rejected", "failed", "cancelled")
RESUBMITTABLE = ("rejected", "invalid", "failed")
MAX_ATTEMPTS = 8
MAX_BACKOFF_SECONDS = 6 * 3600
STALE_LOCK = timedelta(minutes=10)
POLL_AFTER = timedelta(seconds=120)
HISTORY_CAP = 50
DUPLICATE_MARKERS = ("duplicate", "duplicated", "already", "مكرر", "موجود مسبق")


class EInvoiceError(Exception):
    """خطأ منطقي يُحوَّل لاستجابة HTTP: (status_code, detail[, errors])."""

    def __init__(self, status_code: int, detail: str, errors: Optional[List[str]] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.errors = errors


def utcnow() -> datetime:
    return datetime.utcnow()


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def norm_doc_type(value: str) -> str:
    v = (value or "").strip().lower()
    if v == "invoice":
        return "invoice"
    if v in ("credit-note", "credit_note"):
        return "credit_note"
    raise EInvoiceError(404, "نوع المستند غير معروف")


# ═════════════════════ الإعدادات ═════════════════════

async def get_or_create_settings(db: AsyncSession, tid: int) -> EInvoiceSettings:
    s = await mapper.get_settings(db, tid)
    if s:
        return s
    s = EInvoiceSettings(tenant_id=tid, enabled=False, environment="preprod", signer_mode="none")
    db.add(s)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        s = await mapper.get_settings(db, tid)
        if not s:
            raise
    return s


def missing_fields(s: Optional[EInvoiceSettings]) -> List[str]:
    """حقول ناقصة بالعربية (فاضية = الإعدادات مكتملة)."""
    if s is None:
        return ["إعدادات الفاتورة الإلكترونية"]
    miss: List[str] = []
    rin = "".join(ch for ch in (s.tax_registration_number or "") if ch.isdigit())
    if len(rin) != 9:
        miss.append("رقم التسجيل الضريبي (9 أرقام)")
    if not (s.activity_code or "").strip():
        miss.append("كود النشاط")
    if not s.client_credential_id_enc:
        miss.append("client_id")
    if not s.client_secret_enc:
        miss.append("client_secret")
    mode = s.signer_mode or "none"
    if mode == "none":
        miss.append("التوقيع الرقمي (لم يُضبط)")
    elif mode == "external" and not s.signer_url:
        miss.append("رابط خدمة التوقيع")
    elif mode == "mock" and s.environment == "prod":
        miss.append("توقيع حقيقي (الإنتاج لا يقبل التوقيع الوهمي)")
    if s.environment == "custom" and not (s.custom_id_base_url and s.custom_api_base_url):
        miss.append("روابط البيئة المخصصة")
    return miss


def is_ready(s: Optional[EInvoiceSettings]) -> bool:
    return bool(s and s.enabled and not missing_fields(s))


def require_ready(s: Optional[EInvoiceSettings]) -> EInvoiceSettings:
    if s is None or not s.enabled:
        raise EInvoiceError(400, "الفاتورة الإلكترونية غير مُفعّلة")
    miss = missing_fields(s)
    if miss:
        raise EInvoiceError(400, "الفاتورة الإلكترونية غير مُفعّلة — إعدادات ناقصة: " + "، ".join(miss))
    return s


def build_client(s: EInvoiceSettings, *, max_attempts: int = 1, sleep=None) -> eta_client.EtaClient:
    try:
        cid = secret_box.decrypt(s.client_credential_id_enc)
        csec = secret_box.decrypt(s.client_secret_enc)
    except secret_box.SecretBoxError as e:
        raise EInvoiceError(400, str(e))
    id_base, api_base = eta_client.resolve_base_urls(s.environment, s.custom_id_base_url, s.custom_api_base_url)
    if not id_base or not api_base:
        raise EInvoiceError(400, "روابط بيئة مصلحة الضرائب غير مضبوطة")
    if s.environment == "custom":
        try:
            id_base = eta_client.validate_https_url(id_base)
            api_base = eta_client.validate_https_url(api_base)
        except eta_client.UnsafeUrlError as e:
            raise EInvoiceError(400, f"رابط البيئة المخصصة غير مسموح: {e}")
    return eta_client.EtaClient(
        tenant_id=s.tenant_id, environment=s.environment, id_base=id_base, api_base=api_base,
        client_id=cid, client_secret=csec, max_attempts=max_attempts, sleep=sleep,
    )


# ═════════════════════ صفوف التسليم ═════════════════════

async def load_row(db: AsyncSession, tid: int, doc_type: str, doc_id: int) -> Optional[EInvoiceSubmission]:
    return (await db.execute(
        select(EInvoiceSubmission).where(
            EInvoiceSubmission.tenant_id == tid, EInvoiceSubmission.doc_type == doc_type,
            EInvoiceSubmission.doc_id == doc_id,
        ).execution_options(populate_existing=True)
    )).scalars().first()


async def load_row_by_id(db: AsyncSession, tid: int, row_id: int) -> Optional[EInvoiceSubmission]:
    return (await db.execute(
        select(EInvoiceSubmission).where(
            EInvoiceSubmission.tenant_id == tid, EInvoiceSubmission.id == row_id,
        ).execution_options(populate_existing=True)
    )).scalars().first()


def push_history(row: EInvoiceSubmission, frm: Optional[str], to: str, note: str = "") -> None:
    hist = list(row.history or [])
    hist.append({"at": utcnow().isoformat(), "from": frm, "to": to, "note": (note or "")[:300]})
    row.history = hist[-HISTORY_CAP:]


def set_status(row: EInvoiceSubmission, to: str, note: str = "") -> None:
    if row.status != to:
        push_history(row, row.status, to, note)
        row.status = to


def mirror(doc, row: Optional[EInvoiceSubmission]) -> None:
    """مرآة حالة التسليم على الفاتورة/الإشعار (في نفس الـ transaction)."""
    if doc is None or row is None:
        return
    doc.eta_status = row.status
    doc.eta_uuid = row.eta_uuid
    doc.eta_submission_id = row.eta_submission_id
    doc.eta_long_id = row.eta_long_id
    doc.eta_internal_id = row.internal_id
    doc.eta_submitted_at = row.submitted_at
    doc.eta_errors = row.errors


def result_body(row: EInvoiceSubmission, idempotent: bool = False) -> dict:
    return {
        "status": row.status, "eta_status": row.status, "eta_uuid": row.eta_uuid,
        "eta_errors": row.errors or None, "attempts": row.attempts or 0,
        "next_attempt_at": iso(row.next_attempt_at), "last_error": row.last_error,
        "idempotent": idempotent,
    }


def http_status_for(row: EInvoiceSubmission) -> int:
    return 202 if row.status in ("queued", "failed") else 200


def _backoff_next(attempts: int) -> Optional[datetime]:
    if attempts >= MAX_ATTEMPTS:
        return None
    return utcnow() + timedelta(seconds=min((2 ** max(attempts, 1)) * 60, MAX_BACKOFF_SECONDS))


def _mark_failed(row: EInvoiceSubmission, msg: str, *, retry: bool = True) -> None:
    set_status(row, "failed", msg)
    row.last_error = (msg or "")[:1000]
    row.locked_at = None
    row.next_attempt_at = _backoff_next(row.attempts or 1) if retry else None


def _is_duplicate(errors: List[dict]) -> bool:
    for e in errors or []:
        text = f"{e.get('code', '')} {e.get('message', '')}".lower()
        if any(m in text for m in DUPLICATE_MARKERS):
            return True
    return False


def apply_summary(row: EInvoiceSubmission, s: dict) -> None:
    """يطبّق ملخص مستند من ETA على صف التسليم."""
    if s.get("uuid"):
        row.eta_uuid = s["uuid"]
    if s.get("longId"):
        row.eta_long_id = s["longId"]
    st = (s.get("status") or "").lower()
    row.locked_at = None
    if st == "valid":
        set_status(row, "valid", "قُبل لدى مصلحة الضرائب")
        row.validated_at = utcnow()
        row.errors = None
        row.last_error = None
        row.next_attempt_at = None
    elif st in ("invalid", "rejected"):
        set_status(row, "invalid", "رفضت مصلحة الضرائب المستند عند التحقق")
        row.errors = s.get("errors") or [{"code": "", "message": "فشل التحقق لدى مصلحة الضرائب", "target": ""}]
        row.next_attempt_at = None
    elif st == "cancelled":
        set_status(row, "cancelled", "ملغى لدى مصلحة الضرائب")
        row.next_attempt_at = None
    else:
        set_status(row, "submitted", "قيد المعالجة لدى المصلحة")
        row.next_attempt_at = None


async def _doc_or_none(db: AsyncSession, tid: int, doc_type: str, doc_id: int):
    model = Invoice if doc_type == "invoice" else CreditNote
    return (await db.execute(
        select(model).where(model.id == doc_id, model.tenant_id == tid)
        .execution_options(populate_existing=True)
    )).scalars().first()


async def reap_stale_row(db: AsyncSession, row: EInvoiceSubmission) -> bool:
    """صف 'submitting' عالق (القفل أقدم من 10 دقايق) -> failed. لا commit هنا."""
    if row.status == "submitting" and row.locked_at and utcnow() - row.locked_at > STALE_LOCK:
        _mark_failed(row, "انتهت مهلة المعالجة (القفل قديم) — سيُعاد المحاولة")
        mirror(await _doc_or_none(db, row.tenant_id, row.doc_type, row.doc_id), row)
        return True
    return False


# ═════════════════ سلامة الحمولة المخزّنة مقابل بيانات الـ ERP الحالية ═════════════════

def _norm_for_compare(obj):
    """تطبيع قيمة JSON للمقارنة: الأرقام كنص عشري ثابت (لا فرق بين 100 و 100.0) وترتيب المفاتيح غير مهم."""
    if isinstance(obj, dict):
        return {k: _norm_for_compare(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm_for_compare(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float)):
        return signer_mod.fmt_number(obj)
    return obj


def _comparable(document: dict) -> str:
    """محتوى المستند بدون التوقيع وبدون dateTimeIssued (يتغير مع كل بناء ويُقارَن بقاعدة مستقلة)."""
    body = {k: v for k, v in (document or {}).items() if k not in ("signatures", "dateTimeIssued")}
    return json.dumps(_norm_for_compare(body), sort_keys=True, ensure_ascii=False)


def _issue_date_current(stored: str, doc_date: Optional[_date]) -> bool:
    """هل dateTimeIssued المخزّن ما زال متسقًا مع تاريخ المستند الحالي؟
    * مستند بتاريخ سابق: الوقت منتصف الليل لتاريخه => يجب أن يتطابق التاريخ تمامًا.
    * مستند بتاريخ اليوم: الوقت هو (الآن - دقيقة) لحظة البناء => يكفي أن يقع تاريخ المستند بين يوم
      الإرسال الأول ويومين بعده (فرق التوقيت مع القاهرة)؛ فلا يفشل إعادة محاولة عبرت منتصف الليل."""
    try:
        sd = datetime.strptime((stored or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    if doc_date is None:
        return False
    if (stored or "").endswith("T00:00:00Z"):
        return sd == doc_date
    return sd <= doc_date <= sd + timedelta(days=2)


async def assert_payload_current(db: AsyncSession, tid: int, row: EInvoiceSubmission,
                                 settings: EInvoiceSettings) -> None:
    """يضمن ألا تُرسل حمولة قديمة: يعيد بناء المستند من بيانات الـ ERP الحالية ويقارنه بالمخزّن.
    يرفض (EInvoiceError 400، بلا أي اتصال بالشبكة) لو المستند حُذف أو أُلغي/لم يعد صالحًا أو تغيّر محتواه
    منذ بناء الحمولة (مثلًا تعديل فاتورة في حالة failed قبل إعادة المحاولة التلقائية).
    تُستدعى بعد claim (الحالة submitting = مقفولة ضد التعديل) فلا سباق بين الفحص والإرسال."""
    doc = await _doc_or_none(db, tid, row.doc_type, row.doc_id)
    if doc is None:
        raise EInvoiceError(400, "المستند لم يعد موجودًا في النظام — لن يُرسل لمصلحة الضرائب")
    try:
        res = await mapper.build_document(db, tid, row.doc_type, row.doc_id, settings)
    except HTTPException:
        raise EInvoiceError(400, "المستند لم يعد موجودًا في النظام — لن يُرسل لمصلحة الضرائب")
    if res.errors:
        raise EInvoiceError(
            400, "المستند لم يعد صالحًا للإرسال (ربما أُلغي أو تغيّر بعد المحاولة السابقة): " + "؛ ".join(res.errors))
    payload = row.payload_json or {}
    if _comparable(payload) != _comparable(res.document) or             not _issue_date_current(payload.get("dateTimeIssued", ""), getattr(res.obj, "date", None)):
        raise EInvoiceError(
            400, "تغيّرت بيانات المستند بعد بناء الحمولة المحفوظة — لم تُرسل النسخة القديمة. "
                 "استخدم إعادة الإرسال لبناء المستند من البيانات الحالية")


# ═════════════════════ الإرسال ═════════════════════

async def _sign(settings: EInvoiceSettings, document: dict) -> Tuple[dict, str]:
    try:
        signer = signer_mod.get_signer(settings)
        canonical = signer_mod.serialize_for_signing(document)
        sig = await signer.sign(document, canonical)
    except signer_mod.SignerError as e:
        raise EInvoiceError(400, str(e))
    payload = {**document, "signatures": sig["signatures"]}
    return payload, signer_mod.document_hash(canonical)


async def submit_document(db: AsyncSession, tid: int, doc_type: str, doc_id: int, user: Optional[dict] = None,
                          *, resubmit: bool = False, sleep=None, max_attempts: int = 1) -> Tuple[dict, int]:
    """يبني ويتحقق ويوقّع ويحفظ (commit) ثم يحاول إرسالًا فوريًا واحدًا. يرجّع (body, http_status)."""
    settings = require_ready(await mapper.get_settings(db, tid))
    await mapper.load_document(db, tid, doc_type, doc_id)  # 404 لو مش موجود/ورشة تانية
    row = await load_row(db, tid, doc_type, doc_id)
    if row is not None and await reap_stale_row(db, row):
        await db.commit()
        row = await load_row(db, tid, doc_type, doc_id)
    st = row.status if row else None

    if not resubmit:
        if st in ("submitted", "valid"):
            return result_body(row, idempotent=True), 200
        if st == "submitting":
            raise EInvoiceError(409, "جاري الإرسال بالفعل")
        if st in RESUBMITTABLE:
            raise EInvoiceError(400, "سبق إرسال هذا المستند — استخدم إعادة الإرسال")
        if st == "cancelled":
            raise EInvoiceError(400, "المستند ملغى لدى مصلحة الضرائب ولا يمكن إعادة إرساله")
    else:
        if st is None:
            raise EInvoiceError(400, "لم يُرسل هذا المستند بعد — استخدم الإرسال")
        if st == "cancelled":
            raise EInvoiceError(400, "المستند ملغى لدى مصلحة الضرائب ولا يمكن إعادة إرساله")
        if st == "submitting" or st == "queued":
            raise EInvoiceError(409, "جاري الإرسال بالفعل")
        if st not in RESUBMITTABLE:
            raise EInvoiceError(400, "لا يمكن إعادة إرسال مستند مقبول")

    uid = None
    try:
        uid = int((user or {}).get("sub"))
    except (TypeError, ValueError):
        uid = None

    need_build = resubmit or row is None or not row.payload_json
    if need_build:
        res = await mapper.build_document(db, tid, doc_type, doc_id, settings)
        if res.errors:
            raise EInvoiceError(422, "؛ ".join(res.errors), list(res.errors))
        payload, sha = await _sign(settings, res.document)
        now = utcnow()
        if row is None:
            row = EInvoiceSubmission(
                tenant_id=tid, doc_type=doc_type, doc_id=doc_id, status="queued", internal_id=res.internal_id,
                payload_json=payload, payload_sha256=sha, signer_mode_used=settings.signer_mode,
                environment_used=settings.environment, attempts=0, next_attempt_at=now,
                created_by_user_id=uid, history=[],
            )
            push_history(row, None, "queued", "تم التحقق والتوقيع وإضافة المستند للطابور")
            db.add(row)
            try:
                await db.flush()
                mirror(res.obj, row)
                await db.commit()
            except IntegrityError:
                # طلب متزامن سبقنا في إنشاء الصف — نكمل على الصف الموجود (claim ذرّي يضمن إرسالًا واحدًا)
                await db.rollback()
        else:
            hist = list(row.history or [])
            hist.append({"at": now.isoformat(), "from": row.status, "to": "queued",
                         "note": "إعادة إرسال بعد إعادة بناء المستند من البيانات الحالية"})
            upd = await db.execute(
                update(EInvoiceSubmission).where(
                    EInvoiceSubmission.id == row.id, EInvoiceSubmission.tenant_id == tid,
                    EInvoiceSubmission.status.in_(RESUBMITTABLE),
                ).values(
                    status="queued", payload_json=payload, payload_sha256=sha, internal_id=res.internal_id,
                    signer_mode_used=settings.signer_mode, environment_used=settings.environment,
                    attempts=0, next_attempt_at=now, locked_at=None, last_error=None, errors=None,
                    eta_uuid=None, eta_long_id=None, eta_submission_id=None, submitted_at=None,
                    validated_at=None, history=hist[-HISTORY_CAP:],
                ).execution_options(synchronize_session=False)
            )
            if upd.rowcount != 1:
                await db.rollback()
                raise EInvoiceError(409, "تغيّرت حالة المستند أثناء الطلب — حدّث الصفحة")
            fresh = await load_row(db, tid, doc_type, doc_id)
            mirror(res.obj, fresh)
            await db.commit()
        row = await load_row(db, tid, doc_type, doc_id)
        if row is None:
            raise EInvoiceError(409, "تعذّر إنشاء صف الإرسال — أعد المحاولة")

    outcome = await attempt_row(db, tid, row.id, sleep=sleep, max_attempts=max_attempts,
                                verify_current=not need_build)
    row = await load_row(db, tid, doc_type, doc_id)
    if outcome == "not_claimed":
        if row.status in ("submitted", "valid"):
            return result_body(row, idempotent=True), 200
        raise EInvoiceError(409, "جاري الإرسال بالفعل")
    return result_body(row), http_status_for(row)


async def attempt_row(db: AsyncSession, tid: int, row_id: int, *, sleep=None, max_attempts: int = 1,
                      verify_current: bool = True) -> str:
    """محاولة إرسال واحدة لصف (queued/failed). مشتركة بين الطلب العادي والـ worker.
    تُرجع: 'sent' | 'skipped' | 'not_claimed' | 'error'. لا تُبقي transaction مفتوحًا أثناء HTTP.
    verify_current: يعيد بناء المستند ويقارنه بالحمولة المخزّنة قبل الإرسال (يُعطَّل فقط لو بُنيت الحمولة
    في نفس الطلب قبل لحظات)."""
    settings = await mapper.get_settings(db, tid)
    row = await load_row_by_id(db, tid, row_id)
    if row is None:
        return "skipped"
    if not is_ready(settings):
        return "skipped"

    # claim ذرّي (يحمل tenant_id)
    now = utcnow()
    claim = await db.execute(
        update(EInvoiceSubmission).where(
            EInvoiceSubmission.id == row_id, EInvoiceSubmission.tenant_id == tid,
            EInvoiceSubmission.status.in_(("queued", "failed")),
        ).values(
            status="submitting", locked_at=now, last_attempt_at=now,
            attempts=EInvoiceSubmission.attempts + 1,
        ).execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        await db.rollback()
        return "not_claimed"
    row = await load_row_by_id(db, tid, row_id)
    push_history(row, "queued" if (row.attempts or 0) <= 1 else "failed", "submitting", f"محاولة #{row.attempts}")
    doc = await _doc_or_none(db, tid, row.doc_type, row.doc_id)
    mirror(doc, row)
    await db.commit()  # لا transaction مفتوح أثناء HTTP

    payload = row.payload_json or {}
    # ── حراس قبل الشبكة ──
    try:
        if signer_mod.document_hash(signer_mod.serialize_for_signing(payload)) != row.payload_sha256:
            raise EInvoiceError(400, "فشل التحقق من سلامة الحمولة المخزّنة — أعد الإرسال لإعادة البناء")
        if verify_current:
            await assert_payload_current(db, tid, row, settings)
        if signer_mod.has_mock_signature(payload) and settings.environment == "prod":
            raise EInvoiceError(400, signer_mod.PROD_SIGNER_ERROR)
        client = build_client(settings, max_attempts=max_attempts, sleep=sleep)
        if client.is_production_api() and signer_mod.has_mock_signature(payload):
            raise EInvoiceError(400, signer_mod.PROD_SIGNER_ERROR)
    except EInvoiceError as e:
        row = await load_row_by_id(db, tid, row_id)
        _mark_failed(row, e.detail, retry=False)
        mirror(await _doc_or_none(db, tid, row.doc_type, row.doc_id), row)
        await db.commit()
        return "error"

    # ── مكالمة ETA ──
    kind, out = "ok", None
    try:
        out = await client.submit([payload])
    except eta_client.EtaPermanentError as e:
        kind, out = "permanent", e
    except eta_client.EtaAuthError as e:
        kind, out = "auth", e
    except eta_client.EtaTransientError as e:
        kind, out = "transient", e
    except Exception as e:  # noqa: BLE001 — لا نسرّب تفاصيل غير متوقعة
        logger.error("einvoice submit unexpected error: %s", type(e).__name__)
        kind, out = "transient", eta_client.EtaTransientError("خطأ غير متوقع أثناء الاتصال بمصلحة الضرائب")

    row = await load_row_by_id(db, tid, row_id)
    doc = await _doc_or_none(db, tid, row.doc_type, row.doc_id)
    if kind in ("transient", "auth"):
        _mark_failed(row, str(out))
    elif kind == "permanent":
        errs = out.errors or [{"code": "", "message": str(out), "target": ""}]
        if _is_duplicate(errs):
            await _reconcile_duplicate(db, row, client)
        else:
            _set_rejected(row, errs, str(out))
    else:
        mine = [a for a in out["accepted"] if a.get("internalId") in (None, row.internal_id)] or out["accepted"]
        rej = [r for r in out["rejected"] if r.get("internalId") in (None, row.internal_id)] or out["rejected"]
        if mine:
            a = mine[0]
            row.eta_uuid = a.get("uuid")
            row.eta_long_id = a.get("longId")
            row.eta_submission_id = out.get("submissionId")
            row.submitted_at = utcnow()
            row.errors = None
            row.last_error = None
            row.locked_at = None
            row.next_attempt_at = None
            set_status(row, "submitted", "قبلت المصلحة المستند للمعالجة")
            mirror(doc, row)
            await db.commit()  # نثبّت 'submitted' قبل مكالمة الاستعلام
            await _poll_once(db, tid, row_id, client)
            return "sent"
        elif rej:
            errs = rej[0].get("errors") or [{"code": "", "message": "رفضت المصلحة المستند", "target": ""}]
            if _is_duplicate(errs):
                await _reconcile_duplicate(db, row, client)
            else:
                _set_rejected(row, errs, errs[0].get("message") or "")
        else:
            _mark_failed(row, "ردّ غير متوقع من مصلحة الضرائب (لا مقبول ولا مرفوض)")
    mirror(doc, row)
    await db.commit()
    return "sent" if row.status in ("submitted", "valid", "rejected", "invalid") else "error"


def _set_rejected(row: EInvoiceSubmission, errs: List[dict], note: str) -> None:
    set_status(row, "rejected", note)
    row.errors = errs
    row.last_error = (note or "")[:1000]
    row.locked_at = None
    row.next_attempt_at = None


async def _reconcile_duplicate(db: AsyncSession, row: EInvoiceSubmission, client: eta_client.EtaClient) -> None:
    """رفض 'مستند مكرر' = غالبًا الرد ضاع بعد القبول. نسوّي بالاستعلام بدل إنشاء مستند ثاني.
    كود الخطأ الفعلي غير مُتحقَّق منه (مطابقة نصية) فنرجع لـ failed يدوي لو ما قدرناش نطابق."""
    try:
        if row.eta_submission_id:
            found = await client.get_submission(row.eta_submission_id)
        else:
            found = await client.search_by_internal_id(row.internal_id)
    except eta_client.EtaError as e:
        _mark_failed(row, f"المستند مكرر لدى المصلحة وتعذّرت المطابقة: {e}", retry=False)
        return
    match = next((s for s in found if s.get("internalId") == row.internal_id), None) or \
        (found[0] if len(found) == 1 else None)
    if not match or not match.get("uuid"):
        _mark_failed(row, "المستند مكرر لدى المصلحة ولم تتم مطابقته — استخدم تحديث الحالة", retry=False)
        return
    if not row.submitted_at:
        row.submitted_at = utcnow()
    if match.get("submissionUUID") and not row.eta_submission_id:
        row.eta_submission_id = match["submissionUUID"]
    apply_summary(row, match)
    row.last_error = None


async def _poll_once(db: AsyncSession, tid: int, row_id: int, client: eta_client.EtaClient) -> None:
    """استعلام واحد عن حالة submitted. أخطاء الشبكة لا تغيّر الحالة (تبقى submitted)."""
    row = await load_row_by_id(db, tid, row_id)
    if row is None or row.status != "submitted" or not row.eta_submission_id:
        return
    doc = await _doc_or_none(db, tid, row.doc_type, row.doc_id)
    try:
        found = await client.get_submission(row.eta_submission_id)
    except eta_client.EtaError as e:
        row.last_error = str(e)[:1000]
        row.last_attempt_at = utcnow()
        await db.commit()
        return
    match = next((s for s in found if s.get("uuid") == row.eta_uuid), None) or \
        next((s for s in found if s.get("internalId") == row.internal_id), None)
    row.last_attempt_at = utcnow()
    if match:
        apply_summary(row, match)
    mirror(doc, row)
    await db.commit()


async def poll_row(db: AsyncSession, tid: int, row_id: int, *, sleep=None, max_attempts: int = 1) -> str:
    settings = await mapper.get_settings(db, tid)
    if not is_ready(settings):
        return "skipped"
    try:
        client = build_client(settings, max_attempts=max_attempts, sleep=sleep)
    except EInvoiceError:
        return "skipped"
    await _poll_once(db, tid, row_id, client)
    return "polled"


async def refresh_status(db: AsyncSession, tid: int, doc_type: str, doc_id: int,
                         *, sleep=None) -> Tuple[dict, int]:
    settings = require_ready(await mapper.get_settings(db, tid))
    await mapper.load_document(db, tid, doc_type, doc_id)
    row = await load_row(db, tid, doc_type, doc_id)
    if row is not None and await reap_stale_row(db, row):
        await db.commit()
        row = await load_row(db, tid, doc_type, doc_id)
    if row is None or row.status not in ("queued", "submitting", "submitted", "failed"):
        raise EInvoiceError(400, "لا يوجد ما يُحدَّث لهذا المستند")
    client = build_client(settings, max_attempts=1, sleep=sleep)
    doc = await _doc_or_none(db, tid, doc_type, doc_id)
    try:
        if row.eta_submission_id:
            found = await client.get_submission(row.eta_submission_id)
        else:
            found = await client.search_by_internal_id(row.internal_id)
    except eta_client.EtaError as e:
        row.last_error = str(e)[:1000]
        await db.commit()
        return result_body(row), 202
    match = next((s for s in found if s.get("uuid") and s.get("uuid") == row.eta_uuid), None) or \
        next((s for s in found if s.get("internalId") == row.internal_id), None)
    if match and match.get("uuid"):
        if not row.submitted_at:
            row.submitted_at = utcnow()
        if match.get("submissionUUID") and not row.eta_submission_id:
            row.eta_submission_id = match["submissionUUID"]
        apply_summary(row, match)
        row.last_error = None
    row.last_attempt_at = utcnow()
    mirror(doc, row)
    await db.commit()
    return result_body(row), 200


async def cancel_document(db: AsyncSession, tid: int, doc_type: str, doc_id: int, reason: str,
                          *, sleep=None) -> dict:
    reason = (reason or "").strip()
    if not (3 <= len(reason) <= 200):
        raise EInvoiceError(400, "سبب الإلغاء مطلوب (من 3 إلى 200 حرفًا)")
    settings = require_ready(await mapper.get_settings(db, tid))
    await mapper.load_document(db, tid, doc_type, doc_id)
    row = await load_row(db, tid, doc_type, doc_id)
    if row is None or row.status != "valid" or not row.eta_uuid:
        raise EInvoiceError(400, "يمكن إلغاء المستندات المقبولة فقط")
    client = build_client(settings, max_attempts=1, sleep=sleep)
    try:
        await client.cancel(row.eta_uuid, reason)
    except eta_client.EtaPermanentError as e:
        raise EInvoiceError(400, f"رفضت مصلحة الضرائب طلب الإلغاء: {e}")
    except eta_client.EtaError as e:
        raise EInvoiceError(502, f"تعذّر إلغاء المستند لدى مصلحة الضرائب: {e}")
    row = await load_row(db, tid, doc_type, doc_id)
    set_status(row, "cancelled", f"إلغاء لدى المصلحة: {reason}")
    row.next_attempt_at = None
    mirror(await _doc_or_none(db, tid, doc_type, doc_id), row)
    await db.commit()
    return {
        "status": "cancelled",
        "message": "تم إلغاء المستند لدى مصلحة الضرائب. لم يُعكس المستند داخل النظام تلقائيًا — "
                   "أصدر إشعارًا دائنًا أو اتبع الإجراء المعتاد.",
    }


# ═════════════════════ القراءة ═════════════════════

def share_url_for(row: EInvoiceSubmission) -> Optional[str]:
    portal = eta_client.portal_base(row.environment_used or "")
    if row.status != "valid" or not (portal and row.eta_uuid and row.eta_long_id):
        return None
    return f"{portal}/documents/{row.eta_uuid}/share/{row.eta_long_id}"


async def document_state(db: AsyncSession, tid: int, doc_type: str, doc_id: int) -> dict:
    await mapper.load_document(db, tid, doc_type, doc_id)  # 404
    row = await load_row(db, tid, doc_type, doc_id)
    if row is None:
        return {"status": None}
    url = share_url_for(row)
    return {
        "status": row.status, "eta_uuid": row.eta_uuid, "eta_long_id": row.eta_long_id,
        "eta_submission_id": row.eta_submission_id, "submitted_at": iso(row.submitted_at),
        "validated_at": iso(row.validated_at), "attempts": row.attempts or 0,
        "next_attempt_at": iso(row.next_attempt_at), "errors": row.errors or None,
        "last_error": row.last_error, "history": row.history or [],
        "qr_text": url, "share_url": url,
    }


async def list_submissions(db: AsyncSession, tid: int, *, status: Optional[str] = None,
                           doc_type: Optional[str] = None, q: Optional[str] = None,
                           limit: int = 50, offset: int = 0) -> dict:
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    S = EInvoiceSubmission
    conds = [S.tenant_id == tid]
    if status:
        if status not in ALL_STATUSES:
            raise EInvoiceError(400, "حالة غير معروفة")
        conds.append(S.status == status)
    if doc_type:
        conds.append(S.doc_type == norm_doc_type(doc_type))
    if q and q.strip():
        like = f"%{q.strip()}%"
        inv_ids = select(Invoice.id).join(Customer, Customer.id == Invoice.customer_id).where(
            Invoice.tenant_id == tid, or_(Invoice.code.ilike(like), Customer.name.ilike(like)))
        cn_ids = select(CreditNote.id).join(Customer, Customer.id == CreditNote.customer_id).where(
            CreditNote.tenant_id == tid, or_(CreditNote.code.ilike(like), Customer.name.ilike(like)))
        conds.append(or_(
            and_(S.doc_type == "invoice", S.doc_id.in_(inv_ids)),
            and_(S.doc_type == "credit_note", S.doc_id.in_(cn_ids)),
            S.internal_id.ilike(like),
        ))
    total = await db.scalar(select(func.count()).select_from(S).where(*conds)) or 0
    rows = (await db.execute(
        select(S).where(*conds).order_by(S.id.desc()).limit(limit).offset(offset)
    )).scalars().all()

    inv_map, cn_map = {}, {}
    inv_ids_l = [r.doc_id for r in rows if r.doc_type == "invoice"]
    cn_ids_l = [r.doc_id for r in rows if r.doc_type == "credit_note"]
    if inv_ids_l:
        for inv, cname in (await db.execute(
            select(Invoice, Customer.name).join(Customer, Customer.id == Invoice.customer_id)
            .where(Invoice.tenant_id == tid, Invoice.id.in_(inv_ids_l))
        )).all():
            inv_map[inv.id] = (inv.code, cname, inv.total)
    if cn_ids_l:
        for cn, cname in (await db.execute(
            select(CreditNote, Customer.name).join(Customer, Customer.id == CreditNote.customer_id)
            .where(CreditNote.tenant_id == tid, CreditNote.id.in_(cn_ids_l))
        )).all():
            cn_map[cn.id] = (cn.code, cname, cn.total)

    items = []
    for r in rows:
        code, cname, tot = (inv_map if r.doc_type == "invoice" else cn_map).get(r.doc_id, (r.internal_id, "", 0))
        items.append({
            "doc_type": r.doc_type, "doc_id": r.doc_id, "code": code, "customer_name": cname,
            "total": float(tot or 0), "status": r.status, "attempts": r.attempts or 0,
            "next_attempt_at": iso(r.next_attempt_at), "last_attempt_at": iso(r.last_attempt_at),
            "submitted_at": iso(r.submitted_at), "errors": r.errors or None, "last_error": r.last_error,
        })
    summary = {s: 0 for s in ALL_STATUSES}
    for st, cnt in (await db.execute(
        select(S.status, func.count()).where(S.tenant_id == tid).group_by(S.status)
    )).all():
        if st in summary:
            summary[st] = cnt
    return {"summary": summary, "total": total, "items": items}
