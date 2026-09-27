"""
test_05_production.py — اختبارات الإنتاج
WorkOrder · حجز مواد · صرف · خردة · إتمام · جرد المخزون · bins
"""
import pytest
from datetime import date
from app.api.auth import create_token

PROD = "/api/v1/production"
ACC = "/api/v1/accounting"
INV = "/api/v1/inventory"
EQ = "/api/v1/equipment"
CAD = "/api/v1/cad"


async def _make_equipment(cli):
    r = await cli.post(EQ, json={"name_ar": "معدة إنتاج", "level": 1})
    return r.json()["id"]


async def _make_material(cli, qty=100):
    r = await cli.post(f"{INV}/materials", json={"name_ar": "مادة إنتاج", "unit": "كجم", "unit_cost": 5.0})
    mid = r.json()["id"]
    await cli.post(f"{INV}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": qty, "unit_cost": 5.0,
    })
    return mid


async def _make_work_order(cli, eq_id, qty=10):
    r = await cli.post(f"{ACC}/work-orders", json={
        "equipment_id": eq_id,
        "planned_cost": 1000.0,
        "start_date": str(date.today()),
    })
    return r.json()["id"]


# ════════════════ ALERTS ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_production_alerts(cli):
    r = await cli.get(f"{PROD}/alerts")
    assert r.status_code == 200


# ════════════════ RESERVE MATERIALS ══════════════════════════════════
@pytest.mark.asyncio
async def test_reserve_materials(cli):
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=50)
    wo_id = await _make_work_order(cli, eq_id)

    # Add material to BOM so reservation works
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 5.0, "unit_cost": 5.0})

    r = await cli.post(f"{PROD}/reserve/{wo_id}", params={"quantity": 1})
    assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_get_reservations(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_work_order(cli, eq_id)
    r = await cli.get(f"{PROD}/{wo_id}/reservations")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ════════════════ ISSUE MATERIAL ══════════════════════════════════════
@pytest.mark.asyncio
async def test_issue_material(cli):
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id,
        "material_id": mid,
        "qty": 10,
        "stage": "تجميع",
    })
    assert r.status_code in (200, 201)

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert mat.json()["stock_qty"] <= 90


# ════════════════ SCRAP ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_record_scrap(cli):
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/scrap", json={
        "work_order_id": wo_id,
        "material_id": mid,
        "qty": 2,
        "reason": "تالف",
        "stage": "لحام",
    })
    assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_scrap_report(cli):
    r = await cli.get(f"{PROD}/scrap/report")
    assert r.status_code == 200


# ════════════════ COMPLETE WO ════════════════════════════════════════
@pytest.mark.asyncio
async def test_complete_work_order(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_work_order(cli, eq_id)
    r = await cli.post(f"{PROD}/complete/{wo_id}")
    assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_wo_cost_breakdown(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_work_order(cli, eq_id)
    r = await cli.get(f"{PROD}/{wo_id}/cost-breakdown")
    assert r.status_code == 200


# ════════════════ BIN LOCATIONS ══════════════════════════════════════
@pytest.mark.asyncio
async def test_create_bin(cli):
    r = await cli.post(f"{PROD}/bins", json={
        "code": "BIN-001", "name_ar": "رف أ", "zone": "A",
    })
    assert r.status_code == 201
    assert r.json()["code"] == "BIN-001"


@pytest.mark.asyncio
async def test_list_bins(cli):
    await cli.post(f"{PROD}/bins", json={"code": "BIN-002", "name_ar": "رف ب"})
    r = await cli.get(f"{PROD}/bins")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_bin_stock(cli):
    r = await cli.post(f"{PROD}/bins", json={"code": "BIN-003", "name_ar": "رف ج"})
    bin_id = r.json()["id"]
    r2 = await cli.get(f"{PROD}/bins/{bin_id}/stock")
    assert r2.status_code == 200


# ════════════════ STOCK COUNT ════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_stock_count(cli):
    r = await cli.post(f"{PROD}/stock-count")
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_list_stock_counts(cli):
    r = await cli.get(f"{PROD}/stock-count")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_stock_count_scan_and_close(cli):
    # Create a fresh material
    r_mat = await cli.post(f"{INV}/materials", json={"name_ar": "مادة جرد", "unit": "كجم", "unit_cost": 1.0})
    mid = r_mat.json()["id"]

    r_sc = await cli.post(f"{PROD}/stock-count")
    count_id = r_sc.json()["id"]

    # Scan a material in this count
    r_scan = await cli.post(f"{PROD}/stock-count/{count_id}/scan", json={
        "material_id": mid, "actual_qty": 15,
    })
    assert r_scan.status_code in (200, 201)

    # Close count
    r_close = await cli.post(f"{PROD}/stock-count/{count_id}/close")
    assert r_close.status_code == 200


# ════════════════ MRP ════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mrp_suggestions(cli):
    r = await cli.get(f"{PROD}/mrp/suggestions")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_run_mrp(cli):
    r = await cli.post(f"{PROD}/mrp/run")
    assert r.status_code in (200, 201)


# ════════════════ AUTO RESERVE ═══════════════════════════════════════
@pytest.mark.asyncio
async def test_auto_reserve(cli):
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=50)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 2.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)
    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201)


# ════════════════ محطة 1: منع الصرف اليدوي من الرصيد المحجوز ═══════════
@pytest.mark.asyncio
async def test_manual_issue_blocked_by_reservation(cli):
    """رصيد 100، محجوز 5 لأمر إنتاج → محاولة صرف يدوي 98 (أكبر من المتاح الحر 95) يجب أن تُرفض."""
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 5.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201)

    r2 = await cli.post(f"{INV}/movements", json={
        "material_id": mid, "movement_type": "out", "qty": 98, "unit_cost": 5.0,
    })
    assert r2.status_code == 400
    body = r2.json()["detail"]
    assert "محجوز" in body

    # الرصيد ما اتأثرش لما الصرف اتمنع
    mat = await cli.get(f"{INV}/materials/{mid}")
    assert mat.json()["stock_qty"] == 100


@pytest.mark.asyncio
async def test_manual_issue_allowed_within_free_stock(cli):
    """رصيد 100، محجوز 5 لأمر إنتاج → صرف يدوي 95 (بالظبط = المتاح الحر) يجب أن ينجح."""
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 5.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201)

    r2 = await cli.post(f"{INV}/movements", json={
        "material_id": mid, "movement_type": "out", "qty": 95, "unit_cost": 5.0,
    })
    assert r2.status_code == 201

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert mat.json()["stock_qty"] == 5


@pytest.mark.asyncio
async def test_manual_issue_without_any_reservation_unaffected(cli):
    """مفيش حجوزات خالص على المادة → الصرف اليدوي يفضل شغال زي الأول بدون أي تغيير في السلوك."""
    mid = await _make_material(cli, qty=40)
    r = await cli.post(f"{INV}/movements", json={
        "material_id": mid, "movement_type": "out", "qty": 40, "unit_cost": 5.0,
    })
    assert r.status_code == 201
    mat = await cli.get(f"{INV}/materials/{mid}")
    assert mat.json()["stock_qty"] == 0


# ════════ محطة 26: صرف مادة عادية (bulk) لازم يزامن StockQuant مش يكسره ════
@pytest.mark.asyncio
async def test_issue_material_keeps_stock_quant_reconciled(cli):
    """
    قبل التصليح: issue_material() (المسار العادي — مادة مش لوح ولا سيخ) كان
    بيخصم من Material.stock_qty مباشرة من غير ما يزامن StockQuant في موقع
    RAW — يعني بعد أي صرف، SUM(StockQuant) كان يفضل أعلى من الحقيقة للأبد
    (فجوة زي محطة 15/25 بالظبط، بس في مسار الإنتاج). تقرير stock-check
    (/inventory/materials/{id}/stock-check) هو نفس الأداة اللي بتكتشف
    الفجوة دي، فبنستخدمه هنا كإثبات مباشر إن التصليح شغال.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 30, "stage": "تجميع",
    })
    assert r.status_code in (200, 201), r.text

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 70.0  # 100 - 30

    check = await cli.get(f"{INV}/materials/{mid}/stock-check")
    assert check.status_code == 200
    body = check.json()
    assert body["matches"] is True, f"StockQuant لازم يتفق مع stock_qty بعد الصرف: {body}"
    assert body["quant_total"] == 70.0


@pytest.mark.asyncio
async def test_complete_work_order_unissued_reservation_does_not_inflate_stock(cli):
    """
    قبل التصليح: auto_reserve_materials() حجز soft-lock بحت (لا يخصم من
    stock_qty خالص). complete_work_order() كان بيفترض غلط إن الحجز خصم
    فعليًا، فكان "يرجّع" الجزء غير المصروف بإضافته لـstock_qty — ده كان
    يصنع مخزون وهمي (رصيد المادة يزيد فوق حقيقته) في أي أمر إنتاج بيتقفل
    من غير ما يصرف كل المحجوز (شائع جدًا). بعد التصليح: إغلاق حجز غير
    مصروف لازم يترك stock_qty زي ما هو تمامًا.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 5.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201), r.text

    r2 = await cli.post(f"{PROD}/complete/{wo_id}")
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["returned_value"] == 0.0

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 100.0  # لم يتغير — لم يُصرف أي شيء فعليًا

    check = await cli.get(f"{INV}/materials/{mid}/stock-check")
    assert check.status_code == 200
    assert check.json()["matches"] is True


@pytest.mark.asyncio
async def test_scrap_while_reserved_actually_deducts_stock(cli):
    """
    قبل التصليح: تسجيل هالك على مادة محجوزة (قبل الصرف الفعلي) كان
    يتجاهل خصم stock_qty بافتراض غلط إن الحجز خصم فعليًا بالفعل — فمادة
    اتهلكت (وسُجّلت تكلفتها) كانت تفضل معدودة في المخزون للأبد.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 5.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201), r.text

    r2 = await cli.post(f"{PROD}/scrap", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 2, "reason": "قطع خاطئ",
    })
    assert r2.status_code in (200, 201), r2.text

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 98.0  # 100 - 2 اتخصمت فعليًا رغم وجود حجز نشِط

    check = await cli.get(f"{INV}/materials/{mid}/stock-check")
    assert check.status_code == 200
    assert check.json()["matches"] is True


# ════════ محطة 27: /production/reserve/{id} — حجز يدوي حقيقي (material_id/qty) ════
@pytest.mark.asyncio
async def test_manual_reserve_honors_material_id_and_qty(cli):
    """
    قبل التصليح: POST /production/reserve/{id} كان نسخة مطابقة تمامًا لـ
    /auto-reserve/{id} — بيتجاهل تمامًا أي material_id/qty في الجسم
    ويحجز كل بنود BOM بدلاً من ذلك. شاشة "الحجز اليدوي" في الواجهة كانت
    بترسل {material_id, qty} يتم تجاهلهما بالكامل.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=50)
    wo_id = await _make_work_order(cli, eq_id)
    # عمدًا من غير أي BOM — لو الـendpoint لسه بيسقط لـauto-reserve هيفشل بـ400
    # ("لا توجد بنود BOM") بدل ما يحجز المادة المحددة في الجسم

    r = await cli.post(f"{PROD}/reserve/{wo_id}", json={"material_id": mid, "qty": 7})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["material_id"] == mid
    assert body["reserved_qty"] == 7.0
    assert body["blocked"] is False

    res = await cli.get(f"{PROD}/{wo_id}/reservations")
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["material_id"] == mid
    assert float(rows[0]["reserved_qty"]) == 7.0


@pytest.mark.asyncio
async def test_manual_reserve_does_not_touch_other_reservations(cli):
    """حجز يدوي لمادة B بعد ما اتحجزت مادة A من BOM لازم يسيب حجز A زي ما هو."""
    eq_id = await _make_equipment(cli)
    mid_a = await _make_material(cli, qty=50)
    mid_b = await _make_material(cli, qty=50)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid_a, "qty": 3.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)

    r1 = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r1.status_code in (200, 201), r1.text

    r2 = await cli.post(f"{PROD}/reserve/{wo_id}", json={"material_id": mid_b, "qty": 4})
    assert r2.status_code in (200, 201), r2.text

    res = await cli.get(f"{PROD}/{wo_id}/reservations")
    rows = {row["material_id"]: row for row in res.json()}
    assert len(rows) == 2
    assert float(rows[mid_a]["reserved_qty"]) == 3.0  # لم يتأثر بالحجز اليدوي للمادة الثانية
    assert float(rows[mid_b]["reserved_qty"]) == 4.0


@pytest.mark.asyncio
async def test_reserve_without_body_still_falls_back_to_auto_reserve(cli):
    """توافق خلفي: استدعاء /reserve/{id} من غير جسم يفضل يحجز كل بنود BOM زي القديم."""
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=50)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 5.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/reserve/{wo_id}")
    assert r.status_code in (200, 201), r.text
    assert r.json()["reserved"] == 1


@pytest.mark.asyncio
async def test_reserve_again_after_partial_issue_does_not_reset_issued_progress(cli):
    """
    قبل التصليح: إعادة استدعاء auto-reserve/reserve على أمر إنتاج بعد ما
    اتصرف منه جزء فعليًا كانت تمسح كل الحجوزات (بما فيها issued_qty
    المسجّلة) وتبنيها من الصفر — فمحاولة صرف باقي الكمية بعد كده كانت
    ممكن تصرف تاني فوق اللي اتصرف بالفعل (لأن الحجز الجديد ملوش أي أثر
    للصرف السابق). التصليح: حجز فيه صرف فعلي (issued_qty > 0) يتسيب زي
    ما هو ولا يتكرر.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 10.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)

    r1 = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r1.status_code in (200, 201), r1.text

    r2 = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 4, "stage": "قطع",
    })
    assert r2.status_code in (200, 201), r2.text

    # إعادة استدعاء auto-reserve على نفس الأمر (مثلاً بعد تعديل BOM) —
    # لازم يسيب حجز المادة اللي اتصرف منها زي ما هو (issued_qty=4 محفوظة)
    r3 = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r3.status_code in (200, 201), r3.text

    res = await cli.get(f"{PROD}/{wo_id}/reservations")
    rows = res.json()
    assert len(rows) == 1
    assert float(rows[0]["issued_qty"]) == 4.0  # لم تُمسح
    assert float(rows[0]["reserved_qty"]) == 10.0  # نفس الحجز الأصلي، لم يتكرر

    # ومحاولة صرف الباقي فقط (6) لازم تنجح، وأي محاولة تتجاوز المتبقي تُرفض
    r4 = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 7, "stage": "قطع",
    })
    assert r4.status_code == 400  # 7 > المتبقي المحجوز (10-4=6)


# ════════ CAD upload — تفعيل قائمة الأنواع المسموحة (كانت معرَّفة وغير مُفعَّلة) ════
@pytest.mark.asyncio
async def test_cad_upload_rejects_disallowed_mime_type(cli):
    """
    قبل التصليح: ALLOWED_TYPES كانت معرَّفة في cad.py لكن غير مُستخدمة
    خالص في upload_cad_file() — أي نوع ملف كان يُقبل بغض النظر عن القائمة.
    """
    eq_id = await _make_equipment(cli)
    files = {"file": ("malware.exe", b"MZ\x90\x00fake-binary", "application/x-msdownload")}
    r = await cli.post(f"{CAD}/upload/{eq_id}", files=files, data={"revision": "Rev1"})
    assert r.status_code == 400
    assert "نوع الملف" in r.json()["detail"]


@pytest.mark.asyncio
async def test_cad_upload_accepts_allowed_mime_type(cli):
    eq_id = await _make_equipment(cli)
    files = {"file": ("drawing.png", b"\x89PNG\r\n\x1a\nfakepngdata", "image/png")}
    r = await cli.post(f"{CAD}/upload/{eq_id}", files=files, data={"revision": "Rev1"})
    assert r.status_code in (200, 201), r.text

    listing = await cli.get(f"{CAD}/{eq_id}")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


# ════════ محطة 28: عزل tenant صريح في دوال الداشبورد/MRP (دفاع إضافي) ════
@pytest.mark.asyncio
async def test_scrap_inventory_respects_tenant_isolation(cli, cli2):
    """
    get_scrap_inventory() بيستخدم استعلام أعمدة/تجميع (select(col, func.sum))
    على ScrapRecord — أضفنا فلتر tenant_id صريح عليه كدفاع إضافي بعد بلاغ
    فريق آخر عن تسريب بيانات عبر المستأجرين من دوال داشبورد مشابهة
    (dashboard_new.py/reports.py). هالك مسجَّل لتينانت 1 لازم يفضل غير
    مرئي تمامًا من تينانت 2.
    """
    mid = await _make_material(cli, qty=50)
    r = await cli.post(f"{PROD}/scrap", json={"material_id": mid, "qty": 10, "reason": "تلف"})
    assert r.status_code in (200, 201), r.text

    inv1 = await cli.get(f"{PROD}/scrap/inventory")
    assert inv1.status_code == 200
    assert any(row["material_id"] == mid for row in inv1.json())

    inv2 = await cli2.get(f"{PROD}/scrap/inventory")
    assert inv2.status_code == 200
    assert all(row["material_id"] != mid for row in inv2.json())


@pytest.mark.asyncio
async def test_smart_alerts_respects_tenant_isolation(cli, cli2):
    """
    get_smart_alerts() بيتنادى من /dashboard/full (محطة 28) — نتأكد إن
    الحجز المحجوب (partial) والمواد الناقصة لتينانت 1 ما يظهروش خالص في
    تنبيهات تينانت 2.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=2)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 10.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)
    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201), r.text
    assert r.json()["blocked"] is True  # 2 متاح فقط من 10 مطلوبة → حجز جزئي

    alerts2 = await cli2.get(f"{PROD}/alerts")
    assert alerts2.status_code == 200
    wo_link = f"/workorders/{wo_id}"
    assert all(a.get("link") != wo_link for a in alerts2.json())


# ════════════════ محطة 29: كل الطرق لـ"done" لازم تنتج FG صحيح ═══════════

async def _make_work_order_qty(cli, eq_id, qty):
    r = await cli.post(f"{ACC}/work-orders", json={
        "equipment_id": eq_id, "planned_cost": 1000.0,
        "start_date": str(date.today()), "quantity": qty,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _run_all_stages_to_done(cli, wo_id):
    """يعمل seed لمراحل الأمر الافتراضية، ثم start+complete لكل مرحلة بالترتيب."""
    seed = await cli.post(f"{PROD}/stages/{wo_id}/seed")
    assert seed.status_code in (200, 201), seed.text
    stages = (await cli.get(f"{PROD}/stages/{wo_id}")).json()["stages"]
    stages.sort(key=lambda s: s["stage_order"])
    return stages


@pytest.mark.asyncio
async def test_stage_driven_completion_creates_fg_stock_like_manual_complete(cli):
    """
    قبل التصليح: إنهاء آخر مرحلة من مراحل أمر الإنتاج (work_order_stages.py
    complete_stage) كان بيكتفي بتغيير wo.status = "done" مباشرة من غير ما
    يمر على complete_work_order() (services/production.py) — يعني معندوش
    أي رصيد FG-{code} حقيقي، ولا إغلاق حجوزات، ولا تكلفة عمالة، رغم إن
    الحالة بتظهر "done" في كل الشاشات. فوترة sales.py المباشرة لأمر
    الإنتاج (اللي بتعتمد كليًا على وجود FG-{code} بكمية كافية) كانت هترفض
    للأبد لأمر خلّص عن طريق المراحل مش زرار "إنهاء" اليدوي.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo = await _make_work_order_qty(cli, eq_id, qty=2)
    wo_id, wo_code = wo["id"], wo["code"]

    await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 10, "stage": "تجميع",
    })

    stages = await _run_all_stages_to_done(cli, wo_id)
    for st in stages:
        r = await cli.patch(f"{PROD}/stages/{st['id']}/start")
        assert r.status_code == 200, r.text
        r = await cli.patch(f"{PROD}/stages/{st['id']}/complete")
        assert r.status_code == 200, r.text

    wo_after = await cli.get(f"{ACC}/work-orders/{wo_id}")
    assert wo_after.status_code == 200
    assert wo_after.json()["status"] == "done"

    mats = (await cli.get(f"{INV}/materials")).json()
    fg = next((m for m in mats if m["code"] == f"FG-{wo_code}"), None)
    assert fg is not None, "لازم تتنشئ مادة FG حتى لو الإنهاء جه من مراحل الإنتاج مش الزرار اليدوي"
    assert float(fg["stock_qty"]) == 2.0
    assert float(fg["unit_cost"]) > 0

    # الحجوزات المفتوحة (لو أي) لازم تتقفل زي ما بيحصل في الإنهاء اليدوي
    reservations = (await cli.get(f"{PROD}/{wo_id}/reservations")).json()
    assert all(r["status"] == "closed" for r in reservations)


@pytest.mark.asyncio
async def test_skipping_last_stage_completes_work_order_too(cli):
    """
    نفس الفجوة لكن عبر مسار /skip: تخطّي آخر مرحلة متبقية (بدل إنهائها)
    لازم يُنهي أمر الإنتاج بالكامل برضه — قبل التصليح، skip_stage ماكانش
    بيعمل أي فحص لحالة باقي المراحل خالص، فأمر الإنتاج كان يفضل عالق في
    "in_progress" للأبد حتى لو كل مراحله خلصت/اتخطت.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo = await _make_work_order_qty(cli, eq_id, qty=1)
    wo_id, wo_code = wo["id"], wo["code"]

    await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 5, "stage": "قطع",
    })

    stages = await _run_all_stages_to_done(cli, wo_id)
    *earlier, last = stages
    for st in earlier:
        await cli.patch(f"{PROD}/stages/{st['id']}/start")
        r = await cli.patch(f"{PROD}/stages/{st['id']}/complete")
        assert r.status_code == 200, r.text

    await cli.patch(f"{PROD}/stages/{last['id']}/start")
    r = await cli.patch(f"{PROD}/stages/{last['id']}/skip")
    assert r.status_code == 200, r.text

    wo_after = await cli.get(f"{ACC}/work-orders/{wo_id}")
    assert wo_after.json()["status"] == "done"

    mats = (await cli.get(f"{INV}/materials")).json()
    fg = next((m for m in mats if m["code"] == f"FG-{wo_code}"), None)
    assert fg is not None
    assert float(fg["stock_qty"]) == 1.0


@pytest.mark.asyncio
async def test_double_complete_does_not_duplicate_fg_stock(cli):
    """
    قبل التصليح: complete_work_order() معندهاش أي حماية ضد الاستدعاء
    مرتين (نقرة مزدوجة، retry بعد timeout). الاستدعاء الثاني كان
    بيستقبل FG-{code} تاني (post_receipt بيضيف رصيد من غير فحص) فوق
    اللي موجود بالفعل — مخزون تام صنع وهمي يتضاعف بلا أي إنتاج فعلي.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo = await _make_work_order_qty(cli, eq_id, qty=3)
    wo_id, wo_code = wo["id"], wo["code"]

    await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 10, "stage": "تجميع",
    })

    r1 = await cli.post(f"{PROD}/complete/{wo_id}")
    assert r1.status_code in (200, 201), r1.text

    mats = (await cli.get(f"{INV}/materials")).json()
    fg = next(m for m in mats if m["code"] == f"FG-{wo_code}")
    assert float(fg["stock_qty"]) == 3.0

    r2 = await cli.post(f"{PROD}/complete/{wo_id}")
    assert r2.status_code == 400, r2.text

    mats_after = (await cli.get(f"{INV}/materials")).json()
    fg_after = next(m for m in mats_after if m["code"] == f"FG-{wo_code}")
    assert float(fg_after["stock_qty"]) == 3.0, "لازم يفضل رصيد FG زي ما هو — مش يتضاعف باستدعاء ثاني"


@pytest.mark.asyncio
async def test_issue_material_rejects_zero_and_negative_qty(cli):
    """
    قبل التصليح: صرف كمية سالبة كان بيمر من غير أي فحص — بيزوّد
    stock_qty فعليًا (بدل ما يخصم منه) وينقص wo.actual_cost (تكلفة سالبة)،
    عكس معنى "صرف مواد" تمامًا.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": -5, "stage": "تجميع",
    })
    assert r.status_code == 400, r.text

    r2 = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 0, "stage": "تجميع",
    })
    assert r2.status_code == 400, r2.text

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 100.0  # لم يتأثر خالص


@pytest.mark.asyncio
async def test_record_scrap_rejects_zero_and_negative_qty(cli):
    """
    قبل التصليح: هالك بكمية سالبة كان بيمر من غير فحص —
    max(0, stock_qty - (سالبة)) بيزوّد stock_qty بدل ما يخصمه، عكس معنى
    "هالك" تمامًا.
    """
    mid = await _make_material(cli, qty=50)

    r = await cli.post(f"{PROD}/scrap", json={"material_id": mid, "qty": -3, "reason": "تلف"})
    assert r.status_code == 400, r.text

    r2 = await cli.post(f"{PROD}/scrap", json={"material_id": mid, "qty": 0, "reason": "تلف"})
    assert r2.status_code == 400, r2.text

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 50.0  # لم يتأثر خالص


@pytest.mark.asyncio
async def test_team_hours_cannot_be_edited_after_work_order_done(cli):
    """
    بعد إنهاء أمر الإنتاج، actual_cost وFG.unit_cost مقفولين على القيمة
    المحسوبة وقت الإنهاء (تكلفة العمالة اتضافت مرة واحدة وللأبد). تعديل
    ساعات فريق العمل بعد كده كان بيوهم الواجهة بتحديث تكلفة من غير أي أثر
    حقيقي — دلوقتي لازم يترفض بوضوح.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo_id = await _make_work_order(cli, eq_id)

    worker = await cli.post("/api/v1/workers", json={"name": "فني 1", "daily_wage": 100})
    assert worker.status_code in (200, 201), worker.text
    wid = worker.json()["id"]

    add = await cli.post(f"{PROD}/work-orders/{wo_id}/team", json={
        "worker_id": wid, "hourly_rate": 10, "planned_hours": 5,
    })
    assert add.status_code == 201, add.text
    mid_member = add.json()["id"]

    await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 5, "stage": "تجميع",
    })
    r = await cli.post(f"{PROD}/complete/{wo_id}")
    assert r.status_code in (200, 201), r.text

    r2 = await cli.patch(
        f"{PROD}/work-orders/{wo_id}/team/{mid_member}/hours",
        params={"actual_hours": 8},
    )
    assert r2.status_code == 400, r2.text

    r3 = await cli.post(f"{PROD}/work-orders/{wo_id}/team", json={
        "worker_id": wid, "hourly_rate": 20, "planned_hours": 5,
    })
    assert r3.status_code == 400, r3.text


# ════════════════ محطة 30: viewer ممنوع من الكتابة في وحدة الإنتاج ══════

@pytest.mark.asyncio
async def test_viewer_role_forbidden_from_production_write_endpoints(client):
    """
    قبل التصليح: production.py وproduction_v2.py وwork_order_stages.py
    وcad.py كانت كلها معتمدة على require_any فقط على مستوى الراوتر —
    وrequire_any بيسمح صراحة بدور "viewer" (راجع تعريفه في auth.py). لكن
    users.py بيوثّق ROLE_WRITE["viewer"] == [] أي إن viewer مفروض يكون
    قراءة فقط في كل النظام. بما إن مفيش أي endpoint هنا كان بيستخدم
    require_admin_production (المعرّفة أصلاً في auth.py بالظبط لكده)، كان
    "viewer" يقدر فعليًا يصرف مواد، يسجّل هالك، ينهي أوامر إنتاج، يرفع/
    يحذف ملفات CAD... كل حاجة، رغم توثيقه كقراءة فقط. القراءة (GET) لازم
    تفضل مسموحة لـviewer، لكن أي endpoint كتابة (POST/PUT/PATCH/DELETE)
    لازم يترفض بـ403.
    """
    viewer_token = create_token(user_id=999, username="viewer1", role="viewer", tenant_id=1)
    viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

    # القراءة لسه لازم تفضل مسموحة لـviewer في كل الملفات
    for read_url in (
        f"{PROD}/alerts",
        f"{PROD}/pipeline",
        f"{PROD}/stages/dashboard/overview",
        "/api/v1/production/v2/schedules",
        "/api/v1/production/v2/quality",
    ):
        r = await client.get(read_url, headers=viewer_headers)
        assert r.status_code == 200, f"{read_url} -> {r.status_code}: {r.text}"

    # الكتابة لازم تترفض بـ403 (بغض النظر عن صحة البيانات — الفحص بيحصل
    # قبل ما يوصل لمنطق الـendpoint خالص)
    write_calls = [
        ("POST", f"{PROD}/scrap", {"material_id": 1, "qty": 1, "reason": "x"}),
        ("POST", f"{PROD}/issue", {"work_order_id": 1, "material_id": 1, "qty": 1, "stage": "x"}),
        ("POST", f"{PROD}/complete/1", None),
        ("POST", f"{PROD}/auto-reserve/1", None),
        ("POST", f"{PROD}/reserve/1", None),
        ("DELETE", f"{PROD}/reservations/1", None),
        ("POST", f"{PROD}/bins", {"code": "X1"}),
        ("POST", f"{PROD}/stock-count", None),
        ("POST", f"{PROD}/work-orders/1/team", {"worker_id": 1, "hourly_rate": 10}),
        ("PATCH", f"{PROD}/work-orders/1/team/1/hours", None),
        ("DELETE", f"{PROD}/work-orders/1/team/1", None),
        ("POST", f"{PROD}/stages/1/seed", None),
        ("POST", f"{PROD}/stages/1", {"stage_name": "x"}),
        ("PATCH", "/api/v1/production/stages/1/start", None),
        ("PATCH", "/api/v1/production/stages/1/complete", None),
        ("PATCH", "/api/v1/production/stages/1/skip", None),
        ("PUT", "/api/v1/production/stages/1", {"notes": "x"}),
        ("DELETE", "/api/v1/production/stages/1", None),
        ("POST", "/api/v1/production/v2/schedules", {
            "work_order_id": 1, "scheduled_start": str(date.today()), "scheduled_end": str(date.today()),
        }),
        ("PATCH", "/api/v1/production/v2/schedules/1", None),
        ("POST", "/api/v1/production/v2/quality", {
            "inspection_date": str(date.today()), "qty_inspected": 1, "qty_passed": 1,
        }),
        ("DELETE", "/api/v1/production/v2/quality/1", None),
        ("POST", "/api/v1/production/v2/link-order", {"customer_order_id": 1, "work_order_id": 1}),
        ("DELETE", f"{CAD}/1", None),
    ]
    for method, url, payload in write_calls:
        r = await client.request(method, url, json=payload, headers=viewer_headers)
        assert r.status_code == 403, f"{method} {url} -> {r.status_code} (expected 403): {r.text}"

    # صلاحية "production" (أو admin) لازم تفضل تقدر تكتب طبيعي (لا حماية زايدة عن اللازم)
    prod_token = create_token(user_id=998, username="prod1", role="production", tenant_id=1)
    prod_headers = {"Authorization": f"Bearer {prod_token}"}
    r_ok = await client.get(f"{PROD}/alerts", headers=prod_headers)
    assert r_ok.status_code == 200
    r_ok2 = await client.post(f"{PROD}/bins", json={"code": "PB1"}, headers=prod_headers)
    assert r_ok2.status_code == 201, r_ok2.text
