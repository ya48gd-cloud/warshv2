"""
شهادة إتمام أعمال — Work Completion Certificate API
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import (
    WorkCompletionCert, WorkCompletionItem, Customer, Invoice, SupplyContract,
)

router = APIRouter(
    prefix="/work-completion-certs",
    tags=["work_completion_certs"],
    dependencies=[Depends(require_any)],
)
print_router = APIRouter(prefix="/work-completion-certs", tags=["wcc_print"], dependencies=[Depends(require_any)])


# ── Schemas ───────────────────────────────────────────────────────────

class WCCItemIn(BaseModel):
    description: str
    qty:         Decimal = Decimal("1")
    unit:        str = "وحدة"
    unit_price:  Decimal = Decimal("0")
    sort_order:  int = 0


class WCCIn(BaseModel):
    customer_id:      int
    invoice_id:       Optional[int] = None
    contract_id:      Optional[int] = None
    date:             date
    project_name:     str
    project_location: Optional[str] = None
    start_date:       Optional[date] = None
    end_date:         Optional[date] = None
    contract_value:   Decimal = Decimal("0")
    warranty_months:  int = 12
    scope_summary:    Optional[str] = None
    notes:            Optional[str] = None
    items:            List[WCCItemIn] = []


# ── Helpers ───────────────────────────────────────────────────────────

async def _next_code(db: AsyncSession) -> str:
    count = await db.scalar(select(func.count()).select_from(WorkCompletionCert))
    n = (count or 0) + 1
    while True:
        code = f"WCC-{n:04d}"
        exists = await db.scalar(select(WorkCompletionCert.id).where(WorkCompletionCert.code == code))
        if not exists:
            return code
        n += 1


def _wcc_dict(w: WorkCompletionCert) -> dict:
    return {
        "id": w.id,
        "code": w.code,
        "customer_id": w.customer_id,
        "customer_name": w.customer.name if w.customer else "",
        "invoice_id": w.invoice_id,
        "invoice_code": w.invoice.code if w.invoice else None,
        "contract_id": w.contract_id,
        "contract_code": w.contract.code if w.contract else None,
        "date": str(w.date),
        "project_name": w.project_name,
        "project_location": w.project_location,
        "start_date": str(w.start_date) if w.start_date else None,
        "end_date": str(w.end_date) if w.end_date else None,
        "contract_value": float(w.contract_value),
        "warranty_months": w.warranty_months,
        "status": w.status,
        "scope_summary": w.scope_summary,
        "notes": w.notes,
        "issued_at": w.issued_at.isoformat() if w.issued_at else None,
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "items": [
            {
                "id": it.id,
                "description": it.description,
                "qty": float(it.qty),
                "unit": it.unit,
                "unit_price": float(it.unit_price),
                "total_price": float(it.total_price),
                "sort_order": it.sort_order,
            }
            for it in (w.items or [])
        ],
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
    q = select(WorkCompletionCert).options(
        selectinload(WorkCompletionCert.customer),
        selectinload(WorkCompletionCert.invoice),
        selectinload(WorkCompletionCert.contract),
        selectinload(WorkCompletionCert.items),
    ).order_by(WorkCompletionCert.date.desc())
    if status:
        q = q.where(WorkCompletionCert.status == status)
    if customer_id:
        q = q.where(WorkCompletionCert.customer_id == customer_id)
    res = await db.execute(q)
    return [_wcc_dict(w) for w in res.scalars().all()]


@router.get("/{wcc_id}")
async def get_cert(wcc_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(WorkCompletionCert)
        .options(
            selectinload(WorkCompletionCert.customer),
            selectinload(WorkCompletionCert.invoice),
            selectinload(WorkCompletionCert.contract),
            selectinload(WorkCompletionCert.items),
        )
        .where(WorkCompletionCert.id == wcc_id)
    )
    w = res.scalar_one_or_none()
    if not w:
        raise HTTPException(404)
    return _wcc_dict(w)


@router.post("", status_code=201)
async def create_cert(data: WCCIn, db: AsyncSession = Depends(get_db)):
    if data.start_date and data.end_date and data.end_date < data.start_date:
        raise HTTPException(400, "تاريخ انتهاء العمل يجب أن يكون بعد تاريخ بدايته")
    if data.warranty_months is not None and data.warranty_months < 0:
        raise HTTPException(400, "مدة الضمان لا يمكن أن تكون سالبة")
    for it in data.items:
        if it.qty is None or it.qty <= 0:
            raise HTTPException(400, "كمية كل بند يجب أن تكون أكبر من صفر")
        if it.unit_price is not None and it.unit_price < 0:
            raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
    # إصلاح ثغرة تحقق: مرجع عميل/فاتورة/عقد غير موجودة (أو تابعة لـ tenant
    # تاني) كان يُقبل بصمت.
    if not await db.get(Customer, data.customer_id):
        raise HTTPException(404, "العميل غير موجود")
    if data.invoice_id is not None and not await db.get(Invoice, data.invoice_id):
        raise HTTPException(404, "الفاتورة المرجعية غير موجودة")
    if data.contract_id is not None and not await db.get(SupplyContract, data.contract_id):
        raise HTTPException(404, "العقد المرجعي غير موجود")
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get()
    code = await _next_code(db)

    w = WorkCompletionCert(
        tenant_id=tid,
        code=code,
        customer_id=data.customer_id,
        invoice_id=data.invoice_id,
        contract_id=data.contract_id,
        date=data.date,
        project_name=data.project_name,
        project_location=data.project_location,
        start_date=data.start_date,
        end_date=data.end_date,
        contract_value=data.contract_value,
        warranty_months=data.warranty_months,
        scope_summary=data.scope_summary,
        notes=data.notes,
        status="draft",
    )
    db.add(w)
    await db.flush()

    for i, it in enumerate(data.items):
        tp = Decimal(str(it.qty)) * Decimal(str(it.unit_price))
        db.add(WorkCompletionItem(
            tenant_id=tid,
            cert_id=w.id,
            description=it.description,
            qty=it.qty,
            unit=it.unit,
            unit_price=it.unit_price,
            total_price=tp,
            sort_order=it.sort_order or i,
        ))

    await db.commit()
    res = await db.execute(
        select(WorkCompletionCert)
        .options(
            selectinload(WorkCompletionCert.customer),
            selectinload(WorkCompletionCert.invoice),
            selectinload(WorkCompletionCert.contract),
            selectinload(WorkCompletionCert.items),
        )
        .where(WorkCompletionCert.id == w.id)
    )
    return _wcc_dict(res.scalar_one())


@router.post("/from-contract/{contract_id}", status_code=201)
async def cert_from_contract(contract_id: int, db: AsyncSession = Depends(get_db)):
    """إنشاء شهادة إتمام من عقد توريد"""
    res = await db.execute(
        select(SupplyContract)
        .options(selectinload(SupplyContract.lines), selectinload(SupplyContract.customer))
        .where(SupplyContract.id == contract_id)
    )
    sc = res.scalar_one_or_none()
    if not sc:
        raise HTTPException(404, "العقد غير موجود")

    from app.core.database import current_tenant_id
    tid = current_tenant_id.get()
    code = await _next_code(db)

    w = WorkCompletionCert(
        tenant_id=tid,
        code=code,
        customer_id=sc.customer_id,
        contract_id=sc.id,
        date=date.today(),
        project_name=sc.scope_of_work or f"مشروع عقد {sc.code}",
        contract_value=sc.total,
        warranty_months=sc.warranty_months or 12,
        scope_summary=sc.scope_of_work,
        status="draft",
    )
    db.add(w)
    await db.flush()

    for i, l in enumerate(sc.lines or []):
        tp = Decimal(str(l.qty)) * Decimal(str(l.unit_price))
        db.add(WorkCompletionItem(
            tenant_id=tid,
            cert_id=w.id,
            description=l.description,
            qty=l.qty,
            unit=l.unit or "وحدة",
            unit_price=l.unit_price,
            total_price=tp,
            sort_order=i,
        ))

    await db.commit()
    res2 = await db.execute(
        select(WorkCompletionCert)
        .options(
            selectinload(WorkCompletionCert.customer),
            selectinload(WorkCompletionCert.invoice),
            selectinload(WorkCompletionCert.contract),
            selectinload(WorkCompletionCert.items),
        )
        .where(WorkCompletionCert.id == w.id)
    )
    return _wcc_dict(res2.scalar_one())


@router.patch("/{wcc_id}/issue")
async def issue_cert(wcc_id: int, db: AsyncSession = Depends(get_db)):
    w = await db.get(WorkCompletionCert, wcc_id)
    if not w:
        raise HTTPException(404)
    if w.status != "draft":
        raise HTTPException(400, "يمكن إصدار المسودات فقط")
    w.status    = "issued"
    w.issued_at = datetime.utcnow()
    await db.commit()
    return {"id": wcc_id, "status": "issued"}


@router.delete("/{wcc_id}", status_code=204)
async def delete_cert(wcc_id: int, db: AsyncSession = Depends(get_db)):
    w = await db.get(WorkCompletionCert, wcc_id)
    if not w:
        raise HTTPException(404)
    if w.status == "issued":
        raise HTTPException(400, "لا يمكن حذف شهادة صادرة")
    items = (await db.execute(
        select(WorkCompletionItem).where(WorkCompletionItem.cert_id == wcc_id)
    )).scalars().all()
    for it in items:
        await db.delete(it)
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
        "name":      prof.name or "ورشة معدات الاعلاف",
        "sub":       prof.description or "",
        "phone":     prof.mobile or prof.phone or "",
        "address":   prof.address or "",
        "footer":    prof.report_footer or "شكرا لتعاملكم معنا",
        "logo_html": logo_html,
        "legal_html": " | ".join(legal),
    }


def _wcc_html(w: WorkCompletionCert, company: dict) -> str:
    import json as _j

    GOLD   = "#B8892A"
    GOLD_L = "#FFFBF0"
    NAVY   = "#0C2340"
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
    proj_name = _esc(w.project_name)
    proj_loc  = _esc(w.project_location or "")
    sd        = _esc(str(w.start_date) if w.start_date else "—")
    ed        = _esc(str(w.end_date)   if w.end_date   else "—")
    cv        = float(w.contract_value)
    warr      = w.warranty_months
    scope_v   = _esc(w.scope_summary or "")
    notes_v   = _esc(w.notes or "")
    inv_ref   = _esc(w.invoice.code  if w.invoice  else "—")
    con_ref   = _esc(w.contract.code if w.contract else "—")
    js_code   = _j.dumps(code_v)

    rows_html = ""
    for i, it in enumerate(w.items or []):
        bg = "#fafafa" if i % 2 == 0 else "#fff"
        tp = float(it.total_price)
        rows_html += (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:9px 12px;text-align:center;color:#888;font-size:12px'>{i+1}</td>"
            f"<td style='padding:9px 12px;font-weight:600'>{_esc(it.description)}</td>"
            f"<td style='padding:9px 12px;text-align:center'>{float(it.qty):.2f} {_esc(it.unit)}</td>"
            f"<td style='padding:9px 12px;text-align:left;font-variant-numeric:tabular-nums'>{float(it.unit_price):,.2f}</td>"
            f"<td style='padding:9px 12px;text-align:left;font-weight:700;color:{GOLD};font-variant-numeric:tabular-nums'>{tp:,.2f}</td>"
            "</tr>"
        )

    logo_wrap = (
        f'<div style="background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);'
        f'border-radius:10px;padding:6px;">{logo_html}</div>'
        if logo_html else ""
    )
    from_details = "".join(f'<div class="p-detail">{v}</div>' for v in [cphone, caddr] if v)
    cust_details = "".join(f'<div class="p-detail">{v}</div>' for v in [cust_phone, cust_addr] if v)
    scope_box = (
        f'<div class="clause-box"><div class="clause-title">نطاق الأعمال المنجزة</div>'
        f'<div class="clause-body">{scope_v}</div></div>'
        if scope_v else ""
    )
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
<title>شهادة إتمام أعمال - {code_v}</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{{--navy:{NAVY};--navy-dk:{NAVY_DK};--gold:{GOLD};--gold-l:{GOLD_L};--surface:#F8F7F4;--border:#E8E6E0;--text:#1A1A2E;--muted:#7A7870;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Cairo',Tahoma,Arial,sans-serif;font-size:13px;color:var(--text);background:#EDECEA;direction:rtl;line-height:1.65;}}
@page{{size:A4;margin:8mm 7mm;}}
@media print{{.no-print{{display:none!important;}}body{{-webkit-print-color-adjust:exact;print-color-adjust:exact;background:#fff;}}.page{{max-width:100%;box-shadow:none;border-radius:0;margin:0;}}}}
.page{{max-width:840px;margin:16px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 60px rgba(0,0,0,.13);}}
.doc-header{{background:linear-gradient(135deg,var(--navy-dk) 0%,var(--navy) 60%,#143558 100%);color:#fff;padding:24px 30px 20px;display:flex;justify-content:space-between;align-items:center;}}
.co-block{{display:flex;align-items:center;gap:18px;}}
.co-name{{font-size:20px;font-weight:800;line-height:1.2;}}
.co-sub{{font-size:11px;color:rgba(255,255,255,.5);margin-top:3px;}}
.co-phone{{font-size:12px;color:var(--gold);margin-top:6px;font-weight:600;}}
.doc-meta{{text-align:left;}}
.doc-type{{font-size:26px;font-weight:900;color:var(--gold);line-height:1;}}
.doc-type-sub{{font-size:11px;color:rgba(255,255,255,.5);margin-top:3px;}}
.doc-badge{{display:inline-flex;align-items:center;gap:6px;margin-top:8px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 12px;}}
.bl{{font-size:11px;color:rgba(255,255,255,.5);}}
.bv{{font-size:12.5px;color:#fff;font-weight:700;}}
.gold-stripe{{height:4px;background:linear-gradient(90deg,var(--gold),rgba(184,137,42,.4),var(--gold));}}
.status-bar{{background:#0D2440;padding:8px 30px;display:flex;justify-content:space-between;align-items:center;}}
.status-pill{{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(255,255,255,.2);font-size:11px;font-weight:700;padding:3px 14px;border-radius:99px;}}
.body{{padding:24px 30px;}}
.parties{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px;}}
.party-box{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 18px;border-top:3px solid var(--gold);}}
.p-label{{font-size:10px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:7px;}}
.p-name{{font-size:15px;font-weight:800;margin-bottom:4px;}}
.p-detail{{font-size:11.5px;color:var(--muted);line-height:1.7;}}
.meta-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:18px;}}
.meta-item{{background:var(--gold-l);border:1px solid rgba(184,137,42,.25);border-radius:10px;padding:10px 14px;}}
.meta-label{{font-size:10px;font-weight:800;color:var(--gold);letter-spacing:.07em;margin-bottom:4px;}}
.meta-val{{font-size:13px;font-weight:700;}}
table.items{{width:100%;border-collapse:collapse;margin-bottom:20px;border-radius:12px;overflow:hidden;border:1px solid var(--border);}}
.items thead tr{{background:var(--navy);}}
.items thead th{{padding:10px 12px;font-size:11px;font-weight:700;color:rgba(255,255,255,.75);text-align:right;}}
.items thead th:last-child,.items thead th:nth-last-child(2){{text-align:left;}}
.items tbody td{{padding:9px 12px;border-bottom:1px solid var(--border);font-size:12.5px;}}
.items tbody tr:last-child td{{border-bottom:none;}}
.grand-row{{background:linear-gradient(135deg,#3d2600,{GOLD});border-radius:10px;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;}}
.g-label{{font-size:13px;font-weight:600;color:rgba(255,255,255,.75);}}
.g-value{{font-size:22px;font-weight:900;color:#fff;font-variant-numeric:tabular-nums;}}
.clause-box{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 16px;margin-bottom:14px;}}
.clause-title{{font-size:10px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:6px;}}
.clause-body{{font-size:13px;line-height:1.8;}}
.warranty-box{{background:linear-gradient(135deg,#052015,#0a3a20);border-radius:12px;padding:14px 20px;margin-bottom:20px;display:flex;align-items:center;gap:16px;}}
.w-icon{{font-size:28px;}}
.w-text{{color:#fff;}}
.w-title{{font-size:13px;font-weight:700;color:rgba(255,255,255,.7);}}
.w-val{{font-size:18px;font-weight:900;color:#4ade80;}}
.sig-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-top:20px;margin-bottom:20px;}}
.sig-box{{border-top:2px solid var(--border);padding-top:10px;text-align:center;}}
.sig-title{{font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.07em;margin-bottom:32px;}}
.sig-line{{border-top:1px solid #CCC;margin:0 16px;padding-top:6px;font-size:11px;color:#aaa;}}
.doc-footer{{background:linear-gradient(90deg,var(--navy-dk),var(--navy));color:rgba(255,255,255,.6);padding:12px 30px;text-align:center;font-size:11.5px;}}
.action-bar{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);display:flex;gap:8px;z-index:99;background:rgba(8,24,40,.85);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:8px 12px;}}
.action-bar button{{padding:8px 18px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;border:none;font-family:'Cairo',sans-serif;}}
.btn-print{{background:var(--gold);color:#fff;}}
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
      <div class="doc-type">شهادة إتمام أعمال</div>
      <div class="doc-type-sub">Work Completion Certificate</div>
      <div class="doc-badge"><span class="bl">رقم:</span><span class="bv">{code_v}</span></div>
      <div class="doc-badge" style="margin-top:5px;"><span class="bl">التاريخ:</span><span class="bv">{date_v}</span></div>
    </div>
  </div>
  <div class="gold-stripe"></div>
  <div class="status-bar">
    <span class="status-pill" style="color:{st_color};border-color:{st_color}40;">
      <span style="width:6px;height:6px;background:currentColor;border-radius:50%;display:inline-block;"></span>
      {st_label}
    </span>
    <span style="font-size:11px;color:rgba(255,255,255,.4)">
      المشروع: <b style="color:rgba(255,255,255,.7)">{proj_name}</b>
    </span>
  </div>
  <div class="body">
    <div class="parties">
      <div class="party-box">
        <div class="p-label">المقاول / المورد</div>
        <div class="p-name">{cname}</div>
        {from_details}
      </div>
      <div class="party-box">
        <div class="p-label">العميل / صاحب العمل</div>
        <div class="p-name">{cust_name}</div>
        {cust_details}
      </div>
    </div>
    <!-- Meta Grid -->
    <div class="meta-grid">
      <div class="meta-item">
        <div class="meta-label">اسم المشروع</div>
        <div class="meta-val">{proj_name}</div>
      </div>
      <div class="meta-item">
        <div class="meta-label">موقع التنفيذ</div>
        <div class="meta-val">{proj_loc or "—"}</div>
      </div>
      <div class="meta-item">
        <div class="meta-label">قيمة العقد</div>
        <div class="meta-val">{cv:,.2f} ج.م</div>
      </div>
      <div class="meta-item">
        <div class="meta-label">تاريخ البدء</div>
        <div class="meta-val">{sd}</div>
      </div>
      <div class="meta-item">
        <div class="meta-label">تاريخ الانتهاء</div>
        <div class="meta-val">{ed}</div>
      </div>
      <div class="meta-item">
        <div class="meta-label">رقم الفاتورة / العقد</div>
        <div class="meta-val">{inv_ref} / {con_ref}</div>
      </div>
    </div>
    {scope_box}
    {'<table class="items"><thead><tr><th style="width:38px;text-align:center">#</th><th>البند / الوصف</th><th style="width:110px;text-align:center">الكمية</th><th style="width:120px;text-align:left">سعر الوحدة</th><th style="width:120px;text-align:left">الإجمالي</th></tr></thead><tbody>' + rows_html + '</tbody></table>' if rows_html else ""}
    {'<div class="grand-row"><span class="g-label">إجمالي قيمة الأعمال المنجزة</span><span class="g-value">' + f"{sum(float(it.total_price) for it in w.items):,.2f} ج.م" + '</span></div>' if w.items else ""}
    <!-- Warranty Box -->
    <div class="warranty-box">
      <div class="w-icon">🛡️</div>
      <div class="w-text">
        <div class="w-title">فترة الضمان</div>
        <div class="w-val">{warr} شهر{"اً" if warr != 2 else ""}</div>
      </div>
      <div style="margin-right:auto;text-align:left;color:rgba(255,255,255,.5);font-size:11px;">
        تبدأ من تاريخ التسليم وتشمل عيوب التشغيل والتصنيع
      </div>
    </div>
    {notes_box}
    <p style="font-size:12px;color:#6B7280;margin-bottom:16px;line-height:1.8;">
      نشهد نحن الموقعون أدناه بأن الأعمال الواردة في هذه الشهادة قد أُنجزت بالكامل
      وفق المواصفات والمتطلبات المتفق عليها، وأن الموقع/المنتج تم تسليمه وقبوله في حالة جيدة.
    </p>
    <div class="sig-row">
      <div class="sig-box"><div class="sig-title">ممثل المورد / المقاول</div><div class="sig-line">الاسم والتوقيع والختم</div></div>
      <div class="sig-box"><div class="sig-title">مهندس الإشراف</div><div class="sig-line">التوقيع والتاريخ</div></div>
      <div class="sig-box"><div class="sig-title">العميل — القبول والاستلام</div><div class="sig-line">التوقيع والختم والتاريخ</div></div>
    </div>
  </div>
  <div class="doc-footer">{footer_items if footer_items else cname}</div>
</div>
<div class="action-bar no-print">
  <button class="btn-print" onclick="(function(){{document.title='شهادة إتمام '+{js_code};window.print();}})()">طباعة / PDF</button>
</div>
</body></html>"""


@print_router.get("/{wcc_id}/print", response_class=HTMLResponse)
async def print_cert(wcc_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(WorkCompletionCert)
        .options(
            selectinload(WorkCompletionCert.customer),
            selectinload(WorkCompletionCert.invoice),
            selectinload(WorkCompletionCert.contract),
            selectinload(WorkCompletionCert.items),
        )
        .where(WorkCompletionCert.id == wcc_id)
    )
    w = res.scalar_one_or_none()
    if not w:
        raise HTTPException(404)
    company = await _company(db)
    return _wcc_html(w, company)
