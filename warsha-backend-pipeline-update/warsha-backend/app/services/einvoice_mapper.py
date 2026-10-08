"""
einvoice_mapper.py — تحويل Invoice / CreditNote إلى مستند ETA JSON (الإصدار 1.0).

*** غير مُجرَّب على بيئة ETA الفعلية *** — أسماء الحقول وأكواد الضريبة الفرعية مكتوبة من وصف الـ SDK العام.

  * دوال قراءة فقط: لا تكتب في الداتابيز ولا تتصل بالشبكة ولا توقّع.
  * كل الاستعلامات بـ tenant_id صريح (لا اعتماد على ContextVar لأن الـ worker بلا JWT).
  * معادلات الـ ERP (تُستنسخ كما هي ثم تُعمل مطابقة):
      الفاتورة (sales.calc_totals): subtotal = Σ qty*price؛ discount = subtotal*discount_pct/100؛
          tax = (subtotal-discount)*tax_pct/100؛ total = subtotal-discount+tax.
      الإشعار الدائن (credit_notes._validate_cn_input): tax = subtotal*tax_pct/100 (على الإجمالي قبل الخصم!)؛
          total = subtotal - discount_amt + tax. بينما ETA تحسب T1 على الصافي بعد الخصم، فعند وجود
          خصم + ضريبة معًا قد لا تتطابق الأرقام — حارس المطابقة يمنع الإرسال بدل إرسال أرقام مختلفة.
  * الخصم على مستوى المستند يُوزَّع على البنود بالتناسب والبند الأخير يمتص باقي التقريب (المجموع = الخصم تمامًا).
  * حارس المطابقة: إجمالي ETA المحسوب يجب أن يساوي إجمالي المستند في الـ ERP بفارق ≤ 0.01.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date as _date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    CreditNote, CustomerOrder, EInvoiceSettings, Invoice, InvoiceLine, Quotation, WorkshopProfile,
)

DOC_TYPE_INVOICE = "I"
DOC_TYPE_CREDIT = "C"
DOC_VERSION = "1.0"
TAX_TYPE_VAT = "T1"
RECONCILE_TOLERANCE = Decimal("0.01")
Q5 = Decimal("0.00001")

# الوحدة العربية -> unitType في ETA (EA قطعة، MTR متر، ...). مجهولة -> settings.default_unit_type
UNIT_MAP = {
    "قطعة": "EA", "قطعه": "EA", "عدد": "EA", "وحدة": "EA", "ea": "EA", "pcs": "EA", "pc": "EA",
    "متر": "MTR", "م": "MTR", "م.ط": "MTR", "متر طولي": "MTR", "mtr": "MTR", "m": "MTR",
    "م2": "MTK", "م²": "MTK", "متر مربع": "MTK", "mtk": "MTK",
    "م3": "MTQ", "م³": "MTQ", "متر مكعب": "MTQ", "mtq": "MTQ",
    "كجم": "KGM", "كغ": "KGM", "كيلو": "KGM", "كيلوجرام": "KGM", "kg": "KGM", "kgm": "KGM",
    "طن": "TNE", "tne": "TNE",
    "لتر": "LTR", "ltr": "LTR", "l": "LTR",
    "طقم": "SET", "set": "SET",
}


def D(x) -> Decimal:
    return Decimal(str(x if x is not None else 0))


def q5(x) -> Decimal:
    return D(x).quantize(Q5, rounding=ROUND_HALF_UP)


def num(x) -> float:
    """رقم JSON بلا ضجيج float: يُقرَّب لـ 5 خانات ثم float."""
    return float(q5(x))


def allocate_discount(total_discount: Decimal, sales_totals: List[Decimal]) -> List[Decimal]:
    """توزيع الخصم بالتناسب؛ البند الأخير يمتص الباقي فيتساوى المجموع مع الخصم تمامًا."""
    n = len(sales_totals)
    total_discount = q5(total_discount)
    base = sum(sales_totals, Decimal("0"))
    if n == 0 or total_discount <= 0 or base <= 0:
        return [Decimal("0")] * n
    out: List[Decimal] = []
    running = Decimal("0")
    for i, s in enumerate(sales_totals):
        d = (total_discount - running) if i == n - 1 else q5(total_discount * s / base)
        running += d
        out.append(d)
    return out


@dataclass
class MapResult:
    document: dict
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    obj: Any = None
    internal_id: str = ""


async def get_settings(db: AsyncSession, tid: int) -> Optional[EInvoiceSettings]:
    return (await db.execute(
        select(EInvoiceSettings).where(EInvoiceSettings.tenant_id == tid)
    )).scalars().first()


async def load_document(db: AsyncSession, tid: int, doc_type: str, doc_id: int):
    """يحمّل الفاتورة/الإشعار بفلتر tenant صريح، و404 لو مش موجود (كذلك لو تابع لورشة تانية)."""
    if doc_type == "invoice":
        q = (select(Invoice).options(selectinload(Invoice.lines), selectinload(Invoice.customer))
             .where(Invoice.id == doc_id, Invoice.tenant_id == tid))
    elif doc_type == "credit_note":
        q = (select(CreditNote).options(selectinload(CreditNote.lines), selectinload(CreditNote.customer))
             .where(CreditNote.id == doc_id, CreditNote.tenant_id == tid))
    else:
        raise HTTPException(404, "نوع المستند غير معروف")
    obj = (await db.execute(q.execution_options(populate_existing=True))).scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "المستند غير موجود")
    return obj


def map_unit(unit: Optional[str], default_unit: str) -> str:
    key = (unit or "").strip().lower()
    return UNIT_MAP.get(key) or UNIT_MAP.get((unit or "").strip()) or (default_unit or "EA")


def _digits(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")


def _item_code(settings: EInvoiceSettings, model_no: Optional[str], errors: List[str], idx: int) -> str:
    rin = _digits(settings.tax_registration_number)
    mn = (model_no or "").strip()
    if (settings.default_item_type or "EGS") == "GS1":
        digits = _digits(mn)
        if mn and digits == mn and len(digits) in (8, 13, 14):
            return digits
        errors.append(f"البند {idx}: كود GS1 يتطلب رقم موديل من 8 أو 13 أو 14 رقمًا")
        return mn
    code = re.sub(r"[^A-Za-z0-9\-_]", "", mn) or (settings.default_item_code or "GEN")
    return f"EG-{rin}-{code}"[:100]


def _issue_datetime(doc_date: _date, max_backdate_days: int, errors: List[str], warnings: List[str]) -> str:
    now = datetime.utcnow().replace(microsecond=0)
    utc_today = now.date()
    # أقصى فرق توقيت لمصر +3 ساعات — فاتورة بتاريخ "اليوم" بتوقيت القاهرة مقبولة
    local_today = (now + timedelta(hours=3)).date()
    if doc_date > local_today:
        errors.append("تاريخ المستند في المستقبل — لا تقبل مصلحة الضرائب مستندات بتاريخ لاحق")
        return now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if doc_date >= utc_today:
        # ETA ترفض وقتًا بعد "الآن" — نستخدم الآن ناقص دقيقة
        return (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    age = (utc_today - doc_date).days
    limit = int(max_backdate_days if max_backdate_days is not None else 7)
    if age > limit:
        warnings.append(f"تاريخ المستند أقدم من {limit} يوم — قد ترفضه مصلحة الضرائب (حد الإرسال المتأخر)")
    return datetime(doc_date.year, doc_date.month, doc_date.day, 0, 0, 0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _receiver(customer, total: Decimal, settings: EInvoiceSettings, errors: List[str], warnings: List[str]) -> dict:
    name = (getattr(customer, "name", None) or "").strip() or "عميل نقدي"
    tax_id = _digits(getattr(customer, "tax_id", None))
    raw_tax = (getattr(customer, "tax_id", None) or "").strip()
    addr_text = (getattr(customer, "address", None) or "").strip()
    address = {"country": "EG", "governate": "", "regionCity": "", "street": addr_text[:200], "buildingNumber": ""}
    if len(tax_id) == 9:
        rtype, rid = "B", tax_id
        if not addr_text:
            errors.append("عنوان العميل (منشأة) مطلوب — أدخل عنوان العميل")
        else:
            warnings.append("عنوان العميل نص حر: المحافظة/المدينة/رقم المبنى غير مفصّلة وقد ترفضها مصلحة الضرائب")
    elif len(tax_id) == 14:
        rtype, rid = "P", tax_id
    elif not raw_tax:
        rtype, rid = "P", ""
        if total >= D(settings.person_id_threshold if settings.person_id_threshold is not None else 50000):
            errors.append("الرقم القومي أو الضريبي للعميل مطلوب للفواتير التي تتجاوز حد الأفراد")
    else:
        rtype, rid = "P", ""
        errors.append("الرقم الضريبي للعميل غير صالح (9 أرقام للمنشأة أو 14 رقمًا قوميًا للفرد)")
    rec = {"type": rtype, "name": name}
    if rid:
        rec["id"] = rid
    if rtype == "B" or addr_text:
        rec["address"] = address
    return rec


async def _profile(db: AsyncSession, tid: int) -> Optional[WorkshopProfile]:
    return (await db.execute(
        select(WorkshopProfile).where(WorkshopProfile.tenant_id == tid).limit(1)
    )).scalars().first()


async def build_document(db: AsyncSession, tid: int, doc_type: str, doc_id: int,
                         settings: Optional[EInvoiceSettings] = None) -> MapResult:
    obj = await load_document(db, tid, doc_type, doc_id)
    if settings is None:
        settings = await get_settings(db, tid)
    errors: List[str] = []
    warnings: List[str] = []
    is_cn = doc_type == "credit_note"
    internal_id = obj.code

    if settings is None:
        return MapResult({}, ["إعدادات الفاتورة الإلكترونية غير مضبوطة"], [], obj, internal_id)

    # ── حالة المستند في الـ ERP ──
    if is_cn:
        if obj.status not in ("issued", "applied"):
            errors.append("لا يمكن إرسال إشعار دائن غير صادر (مسودة أو ملغي)")
    elif obj.status == "cancelled":
        errors.append("لا يمكن إرسال فاتورة ملغاة")

    # ── المُصدِر ──
    profile = await _profile(db, tid)
    rin = _digits(settings.tax_registration_number)
    if len(rin) != 9:
        errors.append("رقم التسجيل الضريبي للمُصدِر غير مضبوط (9 أرقام)")
    if not (settings.activity_code or "").strip():
        errors.append("كود النشاط (taxpayerActivityCode) غير مضبوط")
    issuer_name = (settings.issuer_name or (profile.name if profile else "") or "").strip()
    if not issuer_name:
        errors.append("اسم المُصدِر غير مضبوط")
    missing_addr = [lbl for lbl, v in (("المحافظة", settings.governate), ("المدينة/المنطقة", settings.region_city),
                                       ("الشارع", settings.street), ("رقم المبنى", settings.building_number))
                    if not (v or "").strip()]
    if missing_addr:
        errors.append("عنوان المُصدِر ناقص: " + "، ".join(missing_addr))
    issuer = {
        "address": {
            "branchID": (settings.branch_id or "0").strip() or "0", "country": "EG",
            "governate": (settings.governate or "").strip(), "regionCity": (settings.region_city or "").strip(),
            "street": (settings.street or "").strip(), "buildingNumber": (settings.building_number or "").strip(),
        },
        "type": "B", "id": rin, "name": issuer_name,
    }

    # ── البنود ──
    raw_lines = list(obj.lines or [])
    if not raw_lines:
        errors.append("المستند بلا بنود")
    inv_line_models: dict = {}
    if is_cn:
        ids = [l.invoice_line_id for l in raw_lines if l.invoice_line_id]
        if ids:
            rows = (await db.execute(
                select(InvoiceLine).where(InvoiceLine.id.in_(ids), InvoiceLine.tenant_id == tid)
            )).scalars().all()
            inv_line_models = {r.id: r.model_no for r in rows}

    sales_totals: List[Decimal] = []
    for i, l in enumerate(raw_lines, start=1):
        if D(l.qty) <= 0:
            errors.append(f"البند {i}: الكمية يجب أن تكون أكبر من صفر")
        if D(l.unit_price) < 0:
            errors.append(f"البند {i}: سعر الوحدة لا يمكن أن يكون سالبًا")
        sales_totals.append(q5(D(l.qty) * D(l.unit_price)))

    discount_total = D(obj.discount_amt)
    discounts = allocate_discount(discount_total, sales_totals)
    tax_pct = D(obj.tax_pct)
    zero = tax_pct == 0
    subtype = (settings.zero_vat_subtype if zero else settings.vat_subtype) or ("V003" if zero else "V009")
    if zero:
        warnings.append(f"الضريبة صفرية: استُخدم النوع الفرعي {subtype} — راجع المحاسب (غير مُتحقَّق منه)")

    lines_out: List[dict] = []
    tot_sales = tot_disc = tot_net = tot_tax = Decimal("0")
    for i, (l, sales, disc) in enumerate(zip(raw_lines, sales_totals, discounts), start=1):
        net = sales - disc
        tax = q5(net * tax_pct / 100)
        model_no = inv_line_models.get(getattr(l, "invoice_line_id", None)) if is_cn else getattr(l, "model_no", None)
        line = {
            "description": (l.description or "").strip()[:500] or f"بند {i}",
            "itemType": settings.default_item_type or "EGS",
            "itemCode": _item_code(settings, model_no, errors, i),
            "unitType": map_unit(l.unit, settings.default_unit_type),
            "quantity": num(l.qty),
            "internalCode": str(i),
            "salesTotal": num(sales),
            "total": num(net + tax),
            "valueDifference": 0,
            "totalTaxableFees": 0,
            "netTotal": num(net),
            "itemsDiscount": 0,
            "unitValue": {"currencySold": "EGP", "amountEGP": num(l.unit_price)},
        }
        if disc > 0:
            line["discount"] = {"rate": num(disc / sales * 100) if sales > 0 else 0, "amount": num(disc)}
        line["taxableItems"] = [{
            "taxType": TAX_TYPE_VAT, "amount": num(tax), "subType": subtype, "rate": num(tax_pct),
        }]
        lines_out.append(line)
        tot_sales += sales
        tot_disc += disc
        tot_net += net
        tot_tax += tax

    total_amount = tot_net + tot_tax

    # ── المطابقة مع أرقام الـ ERP ──
    if raw_lines and abs(q5(total_amount) - D(obj.total)) > RECONCILE_TOLERANCE:
        errors.append(
            f"إجماليات المستند لا تطابق حساب الضريبة (المحسوب {float(q5(total_amount)):,.2f} مقابل "
            f"{float(D(obj.total)):,.2f}) — راجع المستند"
        )
    elif raw_lines and abs(q5(tot_tax) - D(obj.tax_amount)) > RECONCILE_TOLERANCE:
        errors.append(
            f"ضريبة المستند لا تطابق الحساب (المحسوبة {float(q5(tot_tax)):,.2f} مقابل "
            f"{float(D(obj.tax_amount)):,.2f}) — راجع المستند"
        )

    # ── المستلم ──
    receiver = _receiver(obj.customer, D(obj.total), settings, errors, warnings)

    # ── التاريخ ──
    date_issued = _issue_datetime(obj.date, settings.max_backdate_days, errors, warnings)

    doc: dict = {
        "issuer": issuer,
        "receiver": receiver,
        "documentType": DOC_TYPE_CREDIT if is_cn else DOC_TYPE_INVOICE,
        "documentTypeVersion": DOC_VERSION,
        "dateTimeIssued": date_issued,
        "taxpayerActivityCode": (settings.activity_code or "").strip(),
        "internalID": internal_id,
    }

    # ── مراجع ──
    if is_cn:
        orig = None
        if obj.invoice_id:
            orig = (await db.execute(
                select(Invoice).where(Invoice.id == obj.invoice_id, Invoice.tenant_id == tid)
            )).scalars().first()
        if not orig:
            errors.append("الإشعار الدائن غير مرتبط بفاتورة — يلزم ربطه بفاتورة مقبولة لدى مصلحة الضرائب")
        elif orig.eta_status != "valid" or not orig.eta_uuid:
            errors.append("الفاتورة الأصلية لم تُقبل لدى مصلحة الضرائب بعد")
        else:
            doc["references"] = [orig.eta_uuid]
    else:
        po_ref = None
        if obj.customer_order_id:
            co = (await db.execute(
                select(CustomerOrder.code).where(CustomerOrder.id == obj.customer_order_id,
                                                 CustomerOrder.tenant_id == tid)
            )).scalar_one_or_none()
            po_ref = co
        if not po_ref and obj.quotation_id:
            po_ref = (await db.execute(
                select(Quotation.code).where(Quotation.id == obj.quotation_id, Quotation.tenant_id == tid)
            )).scalar_one_or_none()
        if po_ref:
            doc["purchaseOrderReference"] = str(po_ref)[:100]

    doc.update({
        "invoiceLines": lines_out,
        "totalDiscountAmount": num(tot_disc),
        "totalSalesAmount": num(tot_sales),
        "netAmount": num(tot_net),
        "taxTotals": [{"taxType": TAX_TYPE_VAT, "amount": num(tot_tax)}],
        "totalAmount": num(total_amount),
        "extraDiscountAmount": 0,
        "totalItemsDiscountAmount": 0,
    })
    return MapResult(doc, errors, warnings, obj, internal_id)
