"""
test_05_production.py — اختبارات الإنتاج
WorkOrder · حجز مواد · صرف · خردة · إتمام · جرد المخزون · bins
"""
import pytest
from datetime import date

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
