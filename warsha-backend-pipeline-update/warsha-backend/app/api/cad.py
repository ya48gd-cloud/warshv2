"""
CAD Files API
- POST /cad/upload/{equipment_id}  ← رفع ملف
- GET  /cad/{equipment_id}         ← قائمة ملفات المعدة
- GET  /cad/download/{file_id}     ← تنزيل ملف
- DELETE /cad/{file_id}            ← حذف ملف
"""
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
import os

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.auth import require_any, require_admin_production
from app.models.models import CADFile, Equipment

router = APIRouter(prefix="/cad", tags=["cad"], dependencies=[Depends(require_any)])

UPLOAD_DIR = Path(os.getenv("ERP_UPLOAD_DIR", "/app/uploads")) / "cad"
try:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
except (PermissionError, OSError):
    pass  # رفع الصلاحية للـ deploy فقط

# الأنواع المسموح بها
ALLOWED_TYPES = {
    "application/octet-stream",           # DWG, DXF, STEP, IGES
    "application/pdf",                    # PDF
    "image/png", "image/jpeg",            # صور
    "application/zip",                    # ملفات مضغوطة
    "application/x-zip-compressed",
    "application/vnd.ms-excel",           # XLS
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

MAX_SIZE = 50 * 1024 * 1024  # 50 MB


def _safe_stored_path(stored_name: str):
    """مسار الملف على الـdisk — مع ضمان إنه جوه UPLOAD_DIR (حماية من path traversal)."""
    if not stored_name or "/" in stored_name or "\\" in stored_name or stored_name in (".", ".."):
        return None
    p = (UPLOAD_DIR / stored_name).resolve()
    try:
        p.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        return None
    return p


@router.post("/upload/{equipment_id}", dependencies=[Depends(require_admin_production)])
async def upload_cad_file(
    equipment_id: int,
    file: UploadFile = File(...),
    revision: str = Form(default="Rev1"),
    notes: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    # تحقق من وجود المعدة
    eq = await db.get(Equipment, equipment_id)
    if not eq:
        raise HTTPException(404, f"Equipment {equipment_id} not found")

    # اقرأ الملف — بحد أقصى MAX_SIZE+1 بايت (بدل تحميل ملف ضخم كامل في الذاكرة)
    content = await file.read(MAX_SIZE + 1)
    file_size = len(content)
    if file_size == 0:
        raise HTTPException(400, "الملف فارغ")

    if file_size > MAX_SIZE:
        raise HTTPException(400, f"الملف أكبر من 50MB — الحجم: {file_size//1024//1024}MB")

    # تحقق من نوع الملف المسموح به — كان معرّف (ALLOWED_TYPES) لكن غير مُفعّل خالص،
    # فأي نوع ملف كان يُقبل بغض النظر عن القائمة البيضاء
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"نوع الملف غير مسموح به: {content_type}")

    # اسم فريد للتخزين
    ext = Path(file.filename).suffix.lower() if file.filename else ".bin"
    # الامتداد جاي من اسم ملف المستخدم — نقصره على حروف/أرقام فقط (بدون
    # فواصل مسار أو رموز) عشان stored_name يفضل آمن تمامًا على الـdisk
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", ext or ""):
        ext = ".bin"
    stored_name = f"eq{equipment_id}_{uuid.uuid4().hex}{ext}"
    stored_path = UPLOAD_DIR / stored_name

    # احفظ على الـ disk
    with open(stored_path, "wb") as f:
        f.write(content)

    # احفظ في الـ DB
    from app.core.database import current_tenant_id as _ctid_cad
    # اسم العرض: الاسم الأساسي فقط (بدون أي مسار) + بدون محارف تحكم، وبحد طول العمود
    _display_name = Path((file.filename or stored_name).replace("\\", "/")).name
    _display_name = re.sub(r'[\x00-\x1f\x7f"]', "_", _display_name)[:255] or stored_name
    cad = CADFile(
        tenant_id=_ctid_cad.get() or 1,
        equipment_id=equipment_id,
        filename=_display_name,
        stored_name=stored_name,
        file_size=file_size,
        mime_type=content_type[:100],
        revision=(revision.strip() or "Rev1")[:20],
        notes=notes.strip() or None,
    )
    db.add(cad)
    await db.commit()
    await db.refresh(cad)

    return {
        "id": cad.id,
        "equipment_id": cad.equipment_id,
        "filename": cad.filename,
        "file_size": cad.file_size,
        "revision": cad.revision,
        "uploaded_at": cad.uploaded_at.isoformat(),
        "message": "تم رفع الملف بنجاح",
    }


@router.get("/{equipment_id}")
async def list_cad_files(equipment_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CADFile)
        .where(CADFile.equipment_id == equipment_id)
        .order_by(CADFile.uploaded_at.desc())
    )
    files = result.scalars().all()
    return [
        {
            "id": f.id,
            "filename": f.filename,
            "file_size": f.file_size,
            "file_size_kb": round(f.file_size / 1024, 1) if f.file_size else 0,
            "mime_type": f.mime_type,
            "revision": f.revision,
            "notes": f.notes,
            "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
        }
        for f in files
    ]


@router.get("/download/{file_id}")
async def download_cad_file(file_id: int, db: AsyncSession = Depends(get_db)):
    cad = await db.get(CADFile, file_id)
    if not cad:
        raise HTTPException(404, "الملف غير موجود")

    stored_path = _safe_stored_path(cad.stored_name)
    if stored_path is None or not stored_path.exists():
        raise HTTPException(404, "الملف غير موجود على الـ server")

    # FileResponse(filename=...) بيبني Content-Disposition صح بنفسه (RFC 5987
    # لأسماء عربية/غير ASCII). الهيدر اليدوي القديم كان بيكسر أي اسم ملف عربي
    # (ترميز latin-1 → 500) ويسمح بحقن هيدر عبر علامات تنصيص في اسم الملف.
    return FileResponse(
        path=str(stored_path),
        filename=cad.filename,
        media_type=cad.mime_type or "application/octet-stream",
    )


@router.delete("/{file_id}", dependencies=[Depends(require_admin_production)])
async def delete_cad_file(file_id: int, db: AsyncSession = Depends(get_db)):
    cad = await db.get(CADFile, file_id)
    if not cad:
        raise HTTPException(404, "الملف غير موجود")

    # احذف من الـ disk
    stored_path = _safe_stored_path(cad.stored_name)
    if stored_path is not None and stored_path.exists():
        try:
            stored_path.unlink()
        except OSError:
            pass

    # احذف من الـ DB
    await db.delete(cad)
    await db.commit()
    return {"message": "تم حذف الملف", "id": file_id}
