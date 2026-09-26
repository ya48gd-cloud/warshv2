"""
شهادة ضمان — Warranty Certificate API
"""
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import WarrantyCertificate, Customer, Invoice, WorkCompletionCert

router = APIRouter(
    prefix="/warranty-certs",
    tags=["warranty_certs"],
    dependencies=[Depends(require_any)],
)
print_router = APIRouter(prefix="/warranty-certs", tags=["warranty_print"], dependencies=[Depends(require_any)])


# ── Schemas ───────────────────────────────────────────────────────────

class WRIn(BaseModel):
    customer_id:     int
    invoice_id:      Optional[int] = None
    wcc_id:          Optional[int] = None
    date:            date
    product_name:    str
    model_no:        Optional[str] = None
    serial_no:       Optional[str] = None
    warranty_months: int = 12
    warranty_start:  date
    covered_items:   Optional[str] = None
    exclusions:      Optional[str] = None
    service_contact: Optional[str] = None
    notes:           Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────

async def _next_code(db: AsyncSession) -> str:
    count = await db.scalar(select(func.count()).select_from(WarrantyCertificate))
    n = (count or 0) + 1
    while True:
        code = f"WR-{n:04d}"
        exists = await db.scalar(select(WarrantyCertificate.id).where(WarrantyCertificate.code == code))
        if not exists:
            return code
        n += 1


def _wr_dict(w: WarrantyCertificate) -> dict:
    return {
        "id": w.id,
        "code": w.code,
        "customer_id": w.customer_id,
        "customer_name": w.customer.name if w.customer else "",
        "invoice_id": w.invoice_id,
        "invoice_code": w.invoice.code if w.invoice else None,
        "wcc_id": w.wcc_id,
        "wcc_code": w.wcc.code if w.wcc else None,
        "date": str(w.date),
        "product_name": w.product_name,
        "model_no": w.model_no,
        "serial_no": w.serial_no,
        "warranty_months": w.warranty_months,
        "warranty_start": str(w.warranty_start),
        "warranty_end":   str(w.warranty_end),
        "covered_items": w.covered_items,
        "exclusions": w.exclusions,
        "service_contact": w.service_contact,
        "status": w.status,
        "notes": w.notes,
        "issued_at": w.issued_at.isoformat() if w.issued_at else None,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


# ══════════════════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════════════════

@router.get("")
async def list_certs(
    status: Optional[str] = None,
    customer_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(WarrantyCertificate).options(
        selectinload(WarrantyCertificate.customer),
        selectinload(WarrantyCertificate.invoice),
        selectinload(WarrantyCertificate.wcc),
    ).order_by(WarrantyCertificate.warranty_end.asc())
    if status:
        q = q.where(WarrantyCertificate.status == status)
    if customer_id:
        q = q.where(WarrantyCertificate.customer_id == customer_id)
    res = await db.execute(q)
    return [_wr_dict(w) for w in res.scalars().all()]


@router.get("/{wr_id}")
async def get_cert(wr_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(WarrantyCertificate)
        .options(
            selectinload(WarrantyCertificate.customer),
            selectinload(WarrantyCertificate.invoice),
            selectinload(WarrantyCertificate.wcc),
        )
        .where(WarrantyCertificate.id == wr_id)
    )
    w = res.scalar_one_or_none()
    if not w:
        raise HTTPException(404)
    return _wr_dict(w)


@router.post("", status_code=201)
async def create_cert(data: WRIn, db: AsyncSession = Depends(get_db)):
    if data.warranty_months is not None and data.warranty_months < 0:
        raise HTTPException(400, "مدة الضمان لا يمكن أن تكون سالبة")
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get()
    code = await _next_code(db)
    end = data.warranty_start.replace(
        year=data.warranty_start.year + data.warranty_months // 12,
        month=((data.warranty_start.month - 1 + data.warranty_months % 12) % 12) + 1,
    ) if data.warranty_months else data.warranty_start

    w = WarrantyCertificate(
        tenant_id=tid,
        code=code,
        customer_id=data.customer_id,
        invoice_id=data.invoice_id,
        wcc_id=data.wcc_id,
        date=data.date,
        product_name=data.product_name,
        model_no=data.model_no,
        serial_no=data.serial_no,
        warranty_months=data.warranty_months,
        warranty_start=data.warranty_start,
        warranty_end=end,
        covered_items=data.covered_items,
        exclusions=data.exclusions,
        service_contact=data.service_contact,
        notes=data.notes,
        status="draft",
    )
    db.add(w)
    await db.commit()
    res = await db.execute(
        select(WarrantyCertificate)
        .options(
            selectinload(WarrantyCertificate.customer),
            selectinload(WarrantyCertificate.invoice),
            selectinload(WarrantyCertificate.wcc),
        )
        .where(WarrantyCertificate.id == w.id)
    )
    return _wr_dict(res.scalar_one())


@router.patch("/{wr_id}/issue")
async def issue_cert(wr_id: int, db: AsyncSession = Depends(get_db)):
    w = await db.get(WarrantyCertificate, wr_id)
    if not w:
        raise HTTPException(404)
    if w.status != "draft":
        raise HTTPException(400, "يمكن إصدار المسودات فقط")
    w.status    = "issued"
    w.issued_at = datetime.utcnow()
    await db.commit()
    return {"id": wr_id, "status": "issued"}


@router.delete("/{wr_id}", status_code=204)
async def delete_cert(wr_id: int, db: AsyncSession = Depends(get_db)):
    w = await db.get(WarrantyCertificate, wr_id)
    if not w:
        raise HTTPException(404)
    if w.status == "issued":
        raise HTTPException(400, "لا يمكن حذف شهادة صادرة")
    await db.delete(w)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# PRINT
# ══════════════════════════════════════════════════════════════════════

def _esc(s) -> str:
    import html as _h
    return _h.escape(str(s or ""))


async def _company(db: AsyncSession) -> dict:
    from app.core.database import current_tenant_id
    from app.models.models import WorkshopProfile
    tid = current_tenant_id.get()
    q = select(WorkshopProfile)
    if tid is not None:
        q = q.where(WorkshopProfile.tenant_id == tid)
    res = await db.execute(q.limit(1))
    prof = res.scalar_one_or_none()
    if not prof:
        return {"name": "ورشة معدات الاعلاف", "sub": "", "phone": "",
                "address": "", "footer": "شكرا لتعاملكم معنا", "logo_html": "", "legal_html": ""}
    logo_html = (
        f'<img src="{prof.logo_data}" alt="logo" '
        'style="max-height:75px;max-width:160px;object-fit:contain;display:block;"/>'
        if prof.logo_data else ""
    )
    legal = []
    if prof.tax_id:         legal.append(f"ب.ض: {prof.tax_id}")
    if prof.commercial_reg: legal.append(f"س.ت: {prof.commercial_reg}")
    return {
        "name":       prof.name or "ورشة معدات الاعلاف",
        "sub":        prof.description or "",
        "phone":      prof.mobile or prof.phone or "",
        "address":    prof.address or "",
        "footer":     prof.report_footer or "شكرا لتعاملكم معنا",
        "logo_html":  logo_html,
        "legal_html": " | ".join(legal),
    }


def _wr_html(w: WarrantyCertificate, company: dict) -> str:
    import json as _j

    GREEN   = "#0A6E3F"
    GREEN_L = "#F0FDF4"
    NAVY    = "#0C2340"
    NAVY_DK = "#081828"

    cname    = _esc(company.get("name", ""))
    csub     = _esc(company.get("sub", ""))
    cphone   = _esc(company.get("phone", ""))
    caddr    = _esc(company.get("address", ""))
    cfooter  = _esc(company.get("footer", ""))
    legal_h  = company.get("legal_html", "")
    logo_html = company.get("logo_html", "")

    cust_name  = _esc(w.customer.name  if w.customer else "—")
    cust_phone = _esc(w.customer.phone if w.customer else "")
    cust_addr  = _esc(w.customer.address if w.customer else "")

    code_v    = _esc(w.code)
    date_v    = _esc(str(w.date))
    prod_v    = _esc(w.product_name)
    model_v   = _esc(w.model_no or "—")
    serial_v  = _esc(w.serial_no or "—")
    warr_mo   = w.warranty_months
    ws_v      = _esc(str(w.warranty_start))
    we_v      = _esc(str(w.warranty_end))
    covered_v = _esc(w.covered_items or "عيوب التصنيع والمواد والتشغيل")
    excl_v    = _esc(w.exclusions or "الإساءة في الاستخدام — الحوادث — التعديلات غير المرخصة")
    svc_v     = _esc(w.service_contact or cphone)
    notes_v   = _esc(w.notes or "")
    inv_ref   = _esc(w.invoice.code if w.invoice else "—")
    wcc_ref   = _esc(w.wcc.code     if w.wcc     else "—")
    js_code   = _j.dumps(code_v)

    logo_wrap = (
        f'<div style="background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);'
        f'border-radius:10px;padding:6px;">{logo_html}</div>'
        if logo_html else ""
    )
    from_details = "".join(f'<div class="p-detail">{v}</div>' for v in [cphone, caddr] if v)
    cust_details = "".join(f'<div class="p-detail">{v}</div>' for v in [cust_phone, cust_addr] if v)
    notes_box = (
        f'<div class="clause-box"><div class="clause-title">ملاحظات</div>'
        f'<div class="clause-body">{notes_v}</div></div>'
        if notes_v else ""
    )
    footer_items = " | ".join(filter(None, [
        f"هاتف: {cphone}" if cphone else "",
        f"العنوان: {caddr}" if caddr else "",
        legal_h,
    ]))
    st_label = "صادرة" if w.status == "issued" else "مسودة"
    st_color = "#1a7a3c" if w.status == "issued" else "#888"

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<title>شهادة ضمان - {code_v}</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{{--navy:{NAVY};--navy-dk:{NAVY_DK};--green:{GREEN};--green-l:{GREEN_L};--surface:#F8F7F4;--border:#E8E6E0;--text:#1A1A2E;--muted:#7A7870;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Cairo',Tahoma,Arial,sans-serif;font-size:13px;color:var(--text);background:#EDECEA;direction:rtl;line-height:1.65;}}
@page{{size:A4;margin:8mm 7mm;}}
@media print{{.no-print{{display:none!important;}}body{{-webkit-print-color-adjust:exact;print-color-adjust:exact;background:#fff;}}.page{{max-width:100%;box-shadow:none;border-radius:0;margin:0;}}}}
.page{{max-width:840px;margin:16px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 60px rgba(0,0,0,.13);}}
.doc-header{{background:linear-gradient(135deg,var(--navy-dk) 0%,var(--navy) 60%,#143558 100%);color:#fff;padding:24px 30px 20px;display:flex;justify-content:space-between;align-items:center;}}
.co-block{{display:flex;align-items:center;gap:18px;}}
.co-name{{font-size:20px;font-weight:800;line-height:1.2;}}
.co-sub{{font-size:11px;color:rgba(255,255,255,.5);margin-top:3px;}}
.co-phone{{font-size:12px;color:#4ade80;margin-top:6px;font-weight:600;}}
.doc-meta{{text-align:left;}}
.doc-type{{font-size:26px;font-weight:900;color:#4ade80;line-height:1;}}
.doc-type-sub{{font-size:11px;color:rgba(255,255,255,.5);margin-top:3px;}}
.doc-badge{{display:inline-flex;align-items:center;gap:6px;margin-top:8px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 12px;}}
.bl{{font-size:11px;color:rgba(255,255,255,.5);}}
.bv{{font-size:12.5px;color:#fff;font-weight:700;}}
.green-stripe{{height:4px;background:linear-gradient(90deg,{GREEN},rgba(10,110,63,.4),{GREEN});}}
.status-bar{{background:#0D2440;padding:8px 30px;display:flex;justify-content:space-between;align-items:center;}}
.status-pill{{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(255,255,255,.2);font-size:11px;font-weight:700;padding:3px 14px;border-radius:99px;}}
.body{{padding:24px 30px;}}
.parties{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px;}}
.party-box{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 18px;border-top:3px solid var(--green);}}
.p-label{{font-size:10px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:7px;}}
.p-name{{font-size:15px;font-weight:800;margin-bottom:4px;}}
.p-detail{{font-size:11.5px;color:var(--muted);line-height:1.7;}}
.product-box{{background:var(--green-l);border:1px solid rgba(10,110,63,.25);border-radius:12px;padding:16px 20px;margin-bottom:18px;}}
.pb-label{{font-size:10px;font-weight:800;color:var(--green);letter-spacing:.1em;margin-bottom:10px;}}
.pb-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}}
.pb-item label{{font-size:10px;color:var(--muted);display:block;margin-bottom:3px;}}
.pb-item span{{font-size:13px;font-weight:700;}}
.warranty-banner{{background:linear-gradient(135deg,#052015,#0a3a20);border-radius:14px;padding:20px 24px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;}}
.wb-period{{}}
.wb-label{{font-size:11px;color:rgba(255,255,255,.5);margin-bottom:4px;}}
.wb-months{{font-size:36px;font-weight:900;color:#4ade80;line-height:1;}}
.wb-unit{{font-size:14px;color:rgba(255,255,255,.5);}}
.wb-dates{{text-align:left;}}
.wb-date-row{{font-size:12px;color:rgba(255,255,255,.6);margin-bottom:4px;}}
.wb-date-val{{font-weight:700;color:#fff;}}
.clause-box{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 16px;margin-bottom:14px;}}
.clause-title{{font-size:10px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:6px;}}
.clause-body{{font-size:13px;line-height:1.8;}}
.service-box{{background:var(--navy);color:#fff;border-radius:10px;padding:12px 18px;margin-bottom:16px;display:flex;align-items:center;gap:12px;}}
.svc-icon{{font-size:20px;}}
.svc-text label{{font-size:10px;color:rgba(255,255,255,.5);display:block;}}
.svc-text span{{font-size:14px;font-weight:700;}}
.sig-row{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:20px;margin-bottom:20px;}}
.sig-box{{border-top:2px solid var(--border);padding-top:10px;text-align:center;}}
.sig-title{{font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.07em;margin-bottom:32px;}}
.sig-line{{border-top:1px solid #CCC;margin:0 16px;padding-top:6px;font-size:11px;color:#aaa;}}
.doc-footer{{background:linear-gradient(90deg,var(--navy-dk),var(--navy));color:rgba(255,255,255,.6);padding:12px 30px;text-align:center;font-size:11.5px;}}
.action-bar{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);display:flex;gap:8px;z-index:99;background:rgba(8,24,40,.85);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:8px 12px;}}
.action-bar button{{padding:8px 18px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;border:none;font-family:'Cairo',sans-serif;}}
.btn-print{{background:{GREEN};color:#fff;}}
</style>
</head>
<body onload="setTimeout(()=>window.print(),400)">
<div class="page">
  <div class="doc-header">
    <div class="co-block">
      {logo_wrap}
      <div>
        <div class="co-name">{cname}</div>
        <div class="co-sub">{csub}</div>
        <div class="co-phone">{cphone}</div>
      </div>
    </div>
    <div class="doc-meta">
      <div class="doc-type">شهادة ضمان</div>
      <div class="doc-type-sub">Warranty Certificate</div>
      <div class="doc-badge"><span class="bl">رقم:</span><span class="bv">{code_v}</span></div>
      <div class="doc-badge" style="margin-top:5px;"><span class="bl">التاريخ:</span><span class="bv">{date_v}</span></div>
    </div>
  </div>
  <div class="green-stripe"></div>
  <div class="status-bar">
    <span class="status-pill" style="color:{st_color};border-color:{st_color}40;">
      <span style="width:6px;height:6px;background:currentColor;border-radius:50%;display:inline-block;"></span>
      {st_label}
    </span>
    <span style="font-size:11px;color:rgba(255,255,255,.4)">
      المنتج: <b style="color:rgba(255,255,255,.7)">{prod_v}</b>
    </span>
  </div>
  <div class="body">
    <div class="parties">
      <div class="party-box">
        <div class="p-label">الشركة المانحة للضمان</div>
        <div class="p-name">{cname}</div>
        {from_details}
      </div>
      <div class="party-box">
        <div class="p-label">العميل المستفيد</div>
        <div class="p-name">{cust_name}</div>
        {cust_details}
      </div>
    </div>
    <!-- Product Info -->
    <div class="product-box">
      <div class="pb-label">بيانات المنتج / الجهاز</div>
      <div class="pb-grid">
        <div class="pb-item"><label>اسم المنتج</label><span>{prod_v}</span></div>
        <div class="pb-item"><label>موديل / رقم الموديل</label><span>{model_v}</span></div>
        <div class="pb-item"><label>الرقم التسلسلي</label><span>{serial_v}</span></div>
        <div class="pb-item"><label>الفاتورة المرجعية</label><span>{inv_ref}</span></div>
        <div class="pb-item"><label>شهادة الإتمام</label><span>{wcc_ref}</span></div>
      </div>
    </div>
    <!-- Warranty Banner -->
    <div class="warranty-banner">
      <div class="wb-period">
        <div class="wb-label">مدة الضمان</div>
        <div><span class="wb-months">{warr_mo}</span> <span class="wb-unit">شهر</span></div>
      </div>
      <div class="wb-dates">
        <div class="wb-date-row">تاريخ البداية: <span class="wb-date-val">{ws_v}</span></div>
        <div class="wb-date-row">تاريخ الانتهاء: <span class="wb-date-val">{we_v}</span></div>
      </div>
    </div>
    <!-- Covered Items -->
    <div class="clause-box">
      <div class="clause-title">✅ ما يشمله الضمان</div>
      <div class="clause-body">{covered_v}</div>
    </div>
    <!-- Exclusions -->
    <div class="clause-box">
      <div class="clause-title">❌ ما لا يشمله الضمان</div>
      <div class="clause-body">{excl_v}</div>
    </div>
    <!-- Service Contact -->
    <div class="service-box">
      <div class="svc-icon">📞</div>
      <div class="svc-text">
        <label>للصيانة والخدمة يرجى التواصل على</label>
        <span>{svc_v}</span>
      </div>
    </div>
    {notes_box}
    <div class="sig-row">
      <div class="sig-box"><div class="sig-title">ممثل الشركة المانحة للضمان</div><div class="sig-line">الاسم والتوقيع والختم</div></div>
      <div class="sig-box"><div class="sig-title">العميل — قبول شهادة الضمان</div><div class="sig-line">التوقيع والتاريخ</div></div>
    </div>
  </div>
  <div class="doc-footer">{footer_items if footer_items else cname}</div>
</div>
<div class="action-bar no-print">
  <button class="btn-print" onclick="(function(){{document.title='شهادة ضمان '+{js_code};window.print();}})()">طباعة / PDF</button>
</div>
</body></html>"""


@print_router.get("/{wr_id}/print", response_class=HTMLResponse)
async def print_cert(wr_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(WarrantyCertificate)
        .options(
            selectinload(WarrantyCertificate.customer),
            selectinload(WarrantyCertificate.invoice),
            selectinload(WarrantyCertificate.wcc),
        )
        .where(WarrantyCertificate.id == wr_id)
    )
    w = res.scalar_one_or_none()
    if not w:
        raise HTTPException(404)
    company = await _company(db)
    return _wr_html(w, company)
