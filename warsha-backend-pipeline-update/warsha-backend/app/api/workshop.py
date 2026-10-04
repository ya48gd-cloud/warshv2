"""
Workshop Profile API — بيانات الورشة (مصدر وحيد للحقيقة)
- GET    /workshop/profile          ← قراءة بيانات الورشة
- PUT    /workshop/profile          ← تحديث البيانات
- POST   /workshop/profile/logo     ← رفع اللوجو (multipart)
- GET    /workshop/profile/logo     ← تحميل/عرض اللوجو
- DELETE /workshop/profile/logo     ← حذف اللوجو
"""
import base64
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.auth import require_any, require_admin, current_tenant_id
from app.models.models import WorkshopProfile

router = APIRouter(prefix="/workshop", tags=["workshop"], dependencies=[Depends(require_any)])

LOGO_DIR = Path(os.getenv("ERP_UPLOAD_DIR", "/app/uploads")) / "workshop"
# mkdir مؤجّلة للـ endpoints — لا نستدعيها عند import لأن الـ directory قد لا يكون متاحاً بعد

def _ensure_logo_dir():
    LOGO_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_LOGO_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/svg+xml"}
MAX_LOGO_SIZE = 5 * 1024 * 1024  # 5 MB
_EXT_BY_TYPE = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
                "image/webp": ".webp", "image/svg+xml": ".svg"}


class WorkshopProfileIn(BaseModel):
    name: str
    description: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    tax_id: Optional[str] = None
    commercial_reg: Optional[str] = None
    address: Optional[str] = None
    report_footer: Optional[str] = None
    currency: Optional[str] = "ج.م"


async def _get_or_create(db: AsyncSession) -> WorkshopProfile:
    """يرجّع بروفايل الورشة للـ tenant الحالي، ويُنشئه إن لم يوجد."""
    from sqlalchemy import select
    tid = current_tenant_id.get()
    if tid is None:
        raise HTTPException(401, "غير مسموح — يرجى تسجيل الدخول")
    res = await db.execute(select(WorkshopProfile).where(WorkshopProfile.tenant_id == tid))
    prof = res.scalar_one_or_none()
    if not prof:
        prof = WorkshopProfile(tenant_id=tid, name="ورشة جديدة")
        db.add(prof)
        await db.commit()
        await db.refresh(prof)
    return prof


def _to_dict(p: WorkshopProfile) -> dict:
    return {
        "id":             p.id,
        "name":           p.name,
        "description":    p.description,
        "phone":          p.phone,
        "mobile":         p.mobile,
        "email":          p.email,
        "tax_id":         p.tax_id,
        "commercial_reg": p.commercial_reg,
        "address":        p.address,
        "report_footer":  p.report_footer,
        "currency":       p.currency,
        "has_logo":       bool(p.logo_stored_name),
        # رابط ثابت يخدمه الباك؛ الفرونت يضيف عليه ?t=timestamp لكسر الكاش
        "logo_url":       "/api/v1/workshop/profile/logo" if p.logo_stored_name else None,
        # base64 data:URI — يُستخدم مباشرةً في الطباعة بدون مصادقة
        "logo_data":      p.logo_data or None,
        "updated_at":     p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/profile")
async def get_profile(db: AsyncSession = Depends(get_db)):
    prof = await _get_or_create(db)
    # ── auto-backfill logo_data لو فيه لوجو قديم بدون data:URI ──
    if prof.logo_stored_name and not prof.logo_data:
        try:
            import mimetypes
            path = LOGO_DIR / prof.logo_stored_name
            if path.exists():
                with open(path, "rb") as f:
                    content = f.read()
                mime, _ = mimetypes.guess_type(str(path))
                mime = mime or "image/png"
                prof.logo_data = f"data:{mime};base64,{base64.b64encode(content).decode()}"
                await db.commit()
                await db.refresh(prof)
        except Exception:
            pass  # لو فشل التحويل، بنكمل بدون logo_data
    return _to_dict(prof)


@router.put("/profile")
async def update_profile(
    data: WorkshopProfileIn,
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    # VALIDATION (fixed): اسم فاضي/قيم أطول من الأعمدة/currency=null (عمود NOT NULL)
    # كانت بتتقبل وتنتهي بـ 500 على Postgres أو بيانات فاسدة في الطباعة.
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(400, "اسم الورشة مطلوب")
    _limits = {"name": 200, "description": 300, "phone": 50, "mobile": 50,
               "email": 120, "tax_id": 50, "commercial_reg": 50, "currency": 10}
    payload = data.model_dump()
    payload["name"] = name
    for _k, _max in _limits.items():
        _v = payload.get(_k)
        if isinstance(_v, str) and len(_v) > _max:
            raise HTTPException(400, f"الحقل {_k} طويل جداً ({_max} حرف كحد أقصى)")
    if not (payload.get("currency") or "").strip():
        payload["currency"] = "ج.م"
    prof = await _get_or_create(db)
    for k, v in payload.items():
        setattr(prof, k, v)
    await db.commit()
    await db.refresh(prof)
    return _to_dict(prof)


@router.post("/profile/logo")
async def upload_logo(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    if file.content_type not in ALLOWED_LOGO_TYPES:
        raise HTTPException(400, "نوع الملف غير مدعوم — استخدم PNG أو JPG أو WEBP أو SVG")
    content = await file.read()
    if len(content) > MAX_LOGO_SIZE:
        raise HTTPException(400, "حجم اللوجو أكبر من 5 ميجابايت")

    _ensure_logo_dir()
    prof = await _get_or_create(db)

    # احذف اللوجو القديم لو موجود
    if prof.logo_stored_name:
        old = LOGO_DIR / prof.logo_stored_name
        if old.exists():
            try:
                old.unlink()
            except OSError:
                pass

    if not content:
        raise HTTPException(400, "الملف فارغ")
    # SECURITY (fixed): الامتداد كان بيتاخد من اسم الملف اللي بعته العميل (مثلاً
    # logo.html مع content-type=image/png) → FileResponse بيخدمه كـ text/html من نفس
    # الـ origin = stored XSS. دلوقتي الامتداد مشتق من النوع المسموح فقط.
    ext = _EXT_BY_TYPE.get(file.content_type, ".png")
    stored_name = f"logo_{uuid.uuid4().hex}{ext}"
    with open(LOGO_DIR / stored_name, "wb") as f:
        f.write(content)

    # تحويل اللوجو إلى base64 data:URI للتخزين في الداتابيز (للطباعة بدون مصادقة)
    mime = file.content_type or "image/png"
    b64 = base64.b64encode(content).decode("utf-8")
    logo_data_uri = f"data:{mime};base64,{b64}"

    prof.logo_stored_name = stored_name
    prof.logo_data = logo_data_uri
    await db.commit()
    await db.refresh(prof)
    return _to_dict(prof)


@router.get("/profile/logo")
async def get_logo(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    tid = current_tenant_id.get()
    if tid is None:
        raise HTTPException(401)
    res = await db.execute(select(WorkshopProfile).where(WorkshopProfile.tenant_id == tid))
    prof = res.scalar_one_or_none()
    if not prof or not prof.logo_stored_name:
        raise HTTPException(404, "لا يوجد لوجو")
    path = LOGO_DIR / prof.logo_stored_name
    # حماية من path traversal لو اسم مخزّن فاسد
    try:
        path.resolve().relative_to(LOGO_DIR.resolve())
    except ValueError:
        raise HTTPException(404, "ملف اللوجو غير موجود")
    if not path.exists():
        raise HTTPException(404, "ملف اللوجو غير موجود")
    # SVG ممكن يحتوي سكربت — نمنع تنفيذه لو اتفتح مباشرةً في المتصفح
    return FileResponse(path, headers={
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
    })


@router.delete("/profile/logo")
async def delete_logo(
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    prof = await _get_or_create(db)
    if prof.logo_stored_name:
        path = LOGO_DIR / prof.logo_stored_name
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
        prof.logo_stored_name = None
        prof.logo_data = None
        await db.commit()
    return {"message": "تم حذف اللوجو"}
