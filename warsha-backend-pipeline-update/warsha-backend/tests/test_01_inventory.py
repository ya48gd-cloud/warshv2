"""
test_01_inventory.py — اختبارات المخزون
مواد · فئات · حركات · تنبيهات نقص · حذف
"""
import pytest
from datetime import date

BASE = "/api/v1/inventory"


# ════════════════ CATEGORIES ════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_category(cli):
    r = await cli.post(f"{BASE}/categories", json={"name_ar": "فئة تجريبية", "name_en": "Test Cat"})
    assert r.status_code == 201
    assert r.json()["name_ar"] == "فئة تجريبية"


@pytest.mark.asyncio
async def test_list_categories(cli):
    await cli.post(f"{BASE}/categories", json={"name_ar": "فئة1", "name_en": "Cat1"})
    r = await cli.get(f"{BASE}/categories")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_delete_category(cli):
    r = await cli.post(f"{BASE}/categories", json={"name_ar": "للحذف", "name_en": "Delete"})
    cid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/categories/{cid}")
    assert r2.status_code == 204


# ════════════════ MATERIALS ══════════════════════════════════════════
def _mat_payload(**kw):
    # category_id=1 matches the seeded category for tenant-1 in conftest.clean_db
    return {
        "name_ar": "حديد تجريبي",
        "name_en": "Test Iron",
        "unit": "كجم",
        "unit_cost": 10.0,
        "category_id": 1,
        **kw,
    }


@pytest.mark.asyncio
async def test_create_material(cli):
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    assert r.status_code == 201
    d = r.json()
    assert d["name_ar"] == "حديد تجريبي"
    assert d["stock_qty"] == 0


@pytest.mark.asyncio
async def test_get_material(cli):
    r = await cli.post(f"{BASE}/materials", json=_mat_payload(name_ar="ألومنيوم"))
    mid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/materials/{mid}")
    assert r2.status_code == 200
    assert r2.json()["id"] == mid


@pytest.mark.asyncio
async def test_list_materials(cli):
    await cli.post(f"{BASE}/materials", json=_mat_payload())
    r = await cli.get(f"{BASE}/materials")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_update_material(cli):
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]
    r2 = await cli.put(f"{BASE}/materials/{mid}", json=_mat_payload(name_ar="محدَّث", unit_cost=20.0))
    assert r2.status_code == 200
    assert r2.json()["unit_cost"] == 20.0


@pytest.mark.asyncio
async def test_get_nonexistent_material(cli):
    r = await cli.get(f"{BASE}/materials/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_material(cli):
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/materials/{mid}")
    assert r2.status_code == 204


@pytest.mark.asyncio
async def test_next_code(cli):
    r = await cli.get(f"{BASE}/materials/next-code")
    assert r.status_code == 200
    assert "code" in r.json()


# ════════════════ MOVEMENTS ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_record_inbound_movement(cli):
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]
    r = await cli.post(f"{BASE}/movements", json={
        "material_id": mid,
        "movement_type": "in",
        "qty": 50,
        "unit_cost": 10.0,
        "reference": "REF-001",
        "movement_date": str(date.today()),
    })
    assert r.status_code == 201
    # stock should update
    mat = await cli.get(f"{BASE}/materials/{mid}")
    assert mat.json()["stock_qty"] == 50


@pytest.mark.asyncio
async def test_record_outbound_movement(cli):
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]
    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 100, "unit_cost": 10.0,
        "movement_date": str(date.today()),
    })
    r = await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "out", "qty": 30, "unit_cost": 10.0,
        "movement_date": str(date.today()),
    })
    assert r.status_code == 201
    mat = await cli.get(f"{BASE}/materials/{mid}")
    assert mat.json()["stock_qty"] == 70


@pytest.mark.asyncio
async def test_outbound_exceeds_stock(cli):
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]
    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 10, "unit_cost": 10.0,
        "movement_date": str(date.today()),
    })
    r = await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "out", "qty": 999, "unit_cost": 10.0,
        "movement_date": str(date.today()),
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_list_movements(cli):
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]
    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 5, "unit_cost": 10.0,
        "movement_date": str(date.today()),
    })
    r = await cli.get(f"{BASE}/movements/{mid}")
    assert r.status_code == 200
    assert len(r.json()) >= 1


# ════════════════ محطة 2: عكس الحركة بدل حذفها فعليًا ═══════════════════
@pytest.mark.asyncio
async def test_reverse_inbound_movement_keeps_audit_trail(cli):
    """عكس حركة وارد ينشئ حركة صادر مقابلة، ويُبقي الحركة الأصلية في الجدول بحالة reversed."""
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]
    mov = await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 50, "unit_cost": 10.0,
        "movement_date": str(date.today()),
    })
    mov_id = mov.json()["id"]

    r = await cli.delete(f"{BASE}/movements/{mov_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["original_movement_id"] == mov_id
    assert "reversal_movement_id" in body

    # الرصيد رجع صفر
    mat = await cli.get(f"{BASE}/materials/{mid}")
    assert mat.json()["stock_qty"] == 0

    # الحركة الأصلية لسه موجودة في الجدول (مش اتمسحت) بحالة reversed
    movements = (await cli.get(f"{BASE}/movements/{mid}")).json()
    assert len(movements) == 2  # الأصلية + العكسية
    original = next(m for m in movements if m["id"] == mov_id)
    assert original["state"] == "reversed"
    assert original["reversed_movement_id"] == body["reversal_movement_id"]


@pytest.mark.asyncio
async def test_reverse_outbound_movement_restores_stock(cli):
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]
    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 100, "unit_cost": 10.0,
        "movement_date": str(date.today()),
    })
    out_mov = await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "out", "qty": 30, "unit_cost": 10.0,
        "movement_date": str(date.today()),
    })
    out_id = out_mov.json()["id"]

    r = await cli.delete(f"{BASE}/movements/{out_id}")
    assert r.status_code == 200

    mat = await cli.get(f"{BASE}/materials/{mid}")
    assert mat.json()["stock_qty"] == 100


@pytest.mark.asyncio
async def test_cannot_reverse_already_reversed_movement(cli):
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]
    mov = await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 20, "unit_cost": 10.0,
        "movement_date": str(date.today()),
    })
    mov_id = mov.json()["id"]

    r1 = await cli.delete(f"{BASE}/movements/{mov_id}")
    assert r1.status_code == 200
    r2 = await cli.delete(f"{BASE}/movements/{mov_id}")
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_cannot_reverse_a_reversal_movement(cli):
    """الحركة العكسية نفسها ما ينفعش تتعكس تاني (منع دورة عكس-عكس-عكس بلا نهاية)."""
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]
    mov = await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 20, "unit_cost": 10.0,
        "movement_date": str(date.today()),
    })
    mov_id = mov.json()["id"]

    r1 = await cli.delete(f"{BASE}/movements/{mov_id}")
    reversal_id = r1.json()["reversal_movement_id"]

    r2 = await cli.delete(f"{BASE}/movements/{reversal_id}")
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_reverse_nonexistent_movement(cli):
    r = await cli.delete(f"{BASE}/movements/999999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_material_price(cli):
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]
    r = await cli.put(f"{BASE}/materials/{mid}/price", params={"new_price": 99.5})
    assert r.status_code == 200


# ════════════════ LOW-STOCK ALERTS ═══════════════════════════════════
@pytest.mark.asyncio
async def test_low_stock_alerts(cli):
    r = await cli.get(f"{BASE}/alerts/low-stock")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ════════════════ محطة 3: الخدمة المركزية للمخزون (traceability) ═════
@pytest.mark.asyncio
async def test_manual_movement_has_no_source_doc(cli):
    """حركة يدوية من شاشة المخزون تتسجل بـ source_doc_type=None (مفيش مستند مصدر)."""
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]
    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 10, "unit_cost": 5.0,
        "movement_date": str(date.today()),
    })
    r = await cli.get(f"{BASE}/movements/{mid}")
    mov = r.json()[0]
    assert mov["source_doc_type"] is None
    assert mov["source_doc_id"] is None
    assert mov["movement_no"] is not None


@pytest.mark.asyncio
async def test_movement_no_sequential_per_tenant(cli):
    """movement_no تسلسلي وفريد (كل حركة جديدة رقمها أكبر من اللي قبلها) داخل نفس الـ tenant."""
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]

    r1 = await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 10, "unit_cost": 5.0,
        "movement_date": str(date.today()),
    })
    r2 = await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 5, "unit_cost": 5.0,
        "movement_date": str(date.today()),
    })

    mvs = (await cli.get(f"{BASE}/movements/{mid}")).json()
    nos = sorted(m["movement_no"] for m in mvs)
    assert nos[1] == nos[0] + 1


@pytest.mark.asyncio
async def test_movement_no_isolated_per_tenant(cli, cli2):
    """أرقام الحركات مستقلة بين المستأجرين — tenant2 يبدأ ترقيمه لوحده من غير تأثر بـ tenant1."""
    m1 = (await cli.post(f"{BASE}/materials", json=_mat_payload())).json()["id"]
    m2 = (await cli2.post(f"{BASE}/materials", json=_mat_payload())).json()["id"]

    await cli.post(f"{BASE}/movements", json={
        "material_id": m1, "movement_type": "in", "qty": 10, "unit_cost": 5.0,
        "movement_date": str(date.today()),
    })
    r2 = await cli2.post(f"{BASE}/movements", json={
        "material_id": m2, "movement_type": "in", "qty": 10, "unit_cost": 5.0,
        "movement_date": str(date.today()),
    })
    assert r2.status_code == 201
    # لكل tenant قائمة حركاته بس، والترقيم بيبدأ من جديد له (1) بغض النظر عن tenant1
    mv2 = (await cli2.get(f"{BASE}/movements/{m2}")).json()
    assert mv2[0]["movement_no"] == 1


@pytest.mark.asyncio
async def test_reversal_movement_has_source_doc_type_reversal(cli):
    """الحركة العكسية (من محطة 2) بتتسجل بـ source_doc_type='reversal' وتشاور على الحركة الأصلية."""
    mr = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = mr.json()["id"]
    mov = await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 20, "unit_cost": 10.0,
        "movement_date": str(date.today()),
    })
    mov_id = mov.json()["id"]

    r = await cli.delete(f"{BASE}/movements/{mov_id}")
    reversal_id = r.json()["reversal_movement_id"]

    mvs = (await cli.get(f"{BASE}/movements/{mid}")).json()
    reversal = next(m for m in mvs if m["id"] == reversal_id)
    assert reversal["source_doc_type"] == "reversal"
    assert reversal["source_doc_id"] == mov_id


# ════════════════ محطة 9: سياسات الصنف ════════════════════════════════
@pytest.mark.asyncio
async def test_material_default_policies(cli):
    """مادة اتعملت بدون تحديد سياسات → تاخد القيم الافتراضية، وتشتغل زي الأول."""
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    assert r.status_code == 201
    m = r.json()
    assert m["stock_item_type"] == "raw_material"
    assert m["tracking"] == "none"
    assert m["valuation_method"] == "moving_average"
    assert m["removal_strategy"] == "fifo"


@pytest.mark.asyncio
async def test_material_explicit_policies_saved(cli):
    """مادة بسياسات محددة صراحةً → تتحفظ وترجع صح."""
    r = await cli.post(f"{BASE}/materials", json=_mat_payload(
        stock_item_type="spare_part", tracking="lot",
        valuation_method="standard", removal_strategy="fefo",
    ))
    assert r.status_code == 201
    mid = r.json()["id"]
    m2 = (await cli.get(f"{BASE}/materials/{mid}")).json()
    assert m2["stock_item_type"] == "spare_part"
    assert m2["tracking"] == "lot"
    assert m2["valuation_method"] == "standard"
    assert m2["removal_strategy"] == "fefo"


@pytest.mark.asyncio
async def test_material_update_preserves_policy_when_omitted(cli):
    """تعديل مادة من غير ما تبعت حقول السياسات → القيم القديمة تفضل زي ما هي."""
    r = await cli.post(f"{BASE}/materials", json=_mat_payload(tracking="serial"))
    mid = r.json()["id"]
    await cli.put(f"{BASE}/materials/{mid}", json=_mat_payload(name_ar="اسم جديد"))
    m2 = (await cli.get(f"{BASE}/materials/{mid}")).json()
    assert m2["tracking"] == "serial"   # لسه زي ما كان، مترجعش لـ "none"


@pytest.mark.asyncio
async def test_moving_average_pricing_on_receipt(cli):
    """
    محطة 9 + طلب تحديث السعر: استلام كميتين بسعرين مختلفين مع سياسة
    moving_average (الافتراضية) → السعر بيتحسب كمتوسط مرجّح، مش استبدال مباشر.
    """
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]

    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 10, "unit_cost": 100.0,
    })
    m1 = (await cli.get(f"{BASE}/materials/{mid}")).json()
    assert m1["unit_cost"] == 100.0   # أول استلام: السعر = سعر الوارد مباشرة

    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 10, "unit_cost": 200.0,
    })
    m2 = (await cli.get(f"{BASE}/materials/{mid}")).json()
    # المتوسط المرجّح = (10×100 + 10×200) / 20 = 150، مش 200 (استبدال مباشر)
    assert m2["unit_cost"] == 150.0


@pytest.mark.asyncio
async def test_standard_valuation_keeps_price_fixed_on_receipt(cli):
    """سياسة standard: السعر ثابت ومبيتغيرش تلقائيًا من استلام حركة وارد جديدة."""
    r = await cli.post(f"{BASE}/materials", json=_mat_payload(
        valuation_method="standard", unit_cost=50.0,
    ))
    mid = r.json()["id"]

    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 10, "unit_cost": 999.0,
    })
    m2 = (await cli.get(f"{BASE}/materials/{mid}")).json()
    assert m2["unit_cost"] == 50.0   # فضل زي ما هو، اتجاهل سعر الحركة


@pytest.mark.asyncio
async def test_last_price_valuation_takes_latest_receipt_directly(cli):
    """
    سياسة last_price: السعر بياخد آخر سعر استلام مباشرة من غير أي متوسط —
    مناسب للموردين اللي بيغيّروا سعرهم كل مرة وعايز سعر اليوم بالظبط.
    """
    r = await cli.post(f"{BASE}/materials", json=_mat_payload(valuation_method="last_price"))
    mid = r.json()["id"]

    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 10, "unit_cost": 100.0,
    })
    m1 = (await cli.get(f"{BASE}/materials/{mid}")).json()
    assert m1["unit_cost"] == 100.0

    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 10, "unit_cost": 200.0,
    })
    m2 = (await cli.get(f"{BASE}/materials/{mid}")).json()
    # آخر سعر استلام مباشرة = 200، مش متوسط (150)
    assert m2["unit_cost"] == 200.0

    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 5, "unit_cost": 80.0,
    })
    m3 = (await cli.get(f"{BASE}/materials/{mid}")).json()
    assert m3["unit_cost"] == 80.0   # حتى لو نازل عن السابق، برضه آخر سعر مباشرة


# ════════════════ محطة 11: StockLocation + StockQuant ════════════════
@pytest.mark.asyncio
async def test_quant_matches_stock_qty_after_receipt(cli):
    """استلام حركة وارد → SUM(StockQuant.qty) للمادة = Material.stock_qty بالظبط."""
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]
    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 30, "unit_cost": 10.0,
    })
    check = (await cli.get(f"{BASE}/materials/{mid}/stock-check")).json()
    assert check["stock_qty"] == 30.0
    assert check["quant_total"] == 30.0
    assert check["matches"] is True


@pytest.mark.asyncio
async def test_quant_matches_stock_qty_after_issue(cli):
    """صرف جزء من الرصيد → الاتنين (stock_qty و quant_total) يفضلوا متطابقين."""
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]
    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 30, "unit_cost": 10.0,
    })
    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "out", "qty": 12, "unit_cost": 10.0,
    })
    check = (await cli.get(f"{BASE}/materials/{mid}/stock-check")).json()
    assert check["stock_qty"] == 18.0
    assert check["quant_total"] == 18.0
    assert check["matches"] is True


@pytest.mark.asyncio
async def test_quant_matches_after_multiple_mixed_movements(cli):
    """سلسلة حركات وارد/صادر متعددة → التطابق يفضل صحيح طول الوقت، مش بالصدفة أول مرة بس."""
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]
    for mtype, qty in [("in", 50), ("out", 10), ("in", 20), ("out", 15), ("in", 5)]:
        await cli.post(f"{BASE}/movements", json={
            "material_id": mid, "movement_type": mtype, "qty": qty, "unit_cost": 10.0,
        })
    check = (await cli.get(f"{BASE}/materials/{mid}/stock-check")).json()
    expected = 50 - 10 + 20 - 15 + 5
    assert check["stock_qty"] == float(expected)
    assert check["quant_total"] == float(expected)
    assert check["matches"] is True


@pytest.mark.asyncio
async def test_reversal_keeps_quant_in_sync(cli):
    """عكس حركة (محطة 2) لازم يزامن الـ StockQuant برضه، مش بس stock_qty."""
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]
    mov = await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 40, "unit_cost": 10.0,
    })
    mov_id = mov.json()["id"]
    check_before = (await cli.get(f"{BASE}/materials/{mid}/stock-check")).json()
    assert check_before["matches"] is True and check_before["stock_qty"] == 40.0

    await cli.delete(f"{BASE}/movements/{mov_id}")

    check_after = (await cli.get(f"{BASE}/materials/{mid}/stock-check")).json()
    assert check_after["stock_qty"] == 0.0
    assert check_after["quant_total"] == 0.0   # لولا الإصلاح كان هيفضل 40 (Quant متأخر عن الرصيد)
    assert check_after["matches"] is True


@pytest.mark.asyncio
async def test_batch_receipt_and_issue_keep_quant_reconciled(cli):
    """تكامل مع محطة 8: استلام وصرف عن طريق StockBatch لازم يفضل الـ quant متطابق بعده كمان."""
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]
    batch = await cli.post("/api/v1/inventory/batches", json={
        "material_id": mid, "lot_number": "S11-LOT", "received_date": str(date.today()),
        "qty_received": 25, "unit_cost": 10.0,
    })
    batch_id = batch.json()["id"]
    check1 = (await cli.get(f"{BASE}/materials/{mid}/stock-check")).json()
    assert check1["matches"] is True and check1["stock_qty"] == 25.0

    await cli.post(f"/api/v1/inventory/batches/{batch_id}/issue", json={"qty": 10})
    check2 = (await cli.get(f"{BASE}/materials/{mid}/stock-check")).json()
    assert check2["matches"] is True and check2["stock_qty"] == 15.0


# ════════════════ محطة 12: ربط الحركات بمصدر/وجهة + التحويل بين مواقع ═
PROD_BASE = "/api/v1/production"


async def _make_location(cli, code):
    r = await cli.post(f"{PROD_BASE}/bins", json={"code": code, "name_ar": f"موقع {code}"})
    return r.json()["id"]


@pytest.mark.asyncio
async def test_receipt_without_destination_uses_default_location(cli):
    """حركة وارد بدون تحديد وجهة → تروح تلقائيًا للموقع الافتراضي."""
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]
    mov = await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 20, "unit_cost": 10.0,
    })
    mvs = (await cli.get(f"{BASE}/movements/{mid}")).json()
    assert mvs[0]["destination_location_id"] is not None


@pytest.mark.asyncio
async def test_transfer_between_locations_with_explicit_receipt(cli):
    """استلام مباشر في موقع محدد، ثم تحويل جزء منه لموقع تاني."""
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]
    loc_a = await _make_location(cli, "WH-A")
    loc_b = await _make_location(cli, "WH-B")

    # استلام عن طريق batch بيستخدم الموقع الافتراضي — بدل كده هنستلم يدوي في LOC-A
    # مباشرة عن طريق تحويل "افتراضي → LOC-A" بعد استلام عادي، لإثبات إعادة التوزيع
    await cli.post(f"{BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": 30, "unit_cost": 10.0,
    })
    mvs = (await cli.get(f"{BASE}/movements/{mid}")).json()
    default_loc_id = mvs[0]["destination_location_id"]

    r_t1 = await cli.post(f"{BASE}/movements/transfer", json={
        "material_id": mid, "source_location_id": default_loc_id,
        "destination_location_id": loc_a, "qty": 30,
    })
    assert r_t1.status_code == 201

    stock_after_t1 = (await cli.get(f"{BASE}/materials/{mid}/stock-check")).json()
    assert stock_after_t1["stock_qty"] == 30.0   # الرصيد الإجمالي ثابت، توزيع بس

    r_t2 = await cli.post(f"{BASE}/movements/transfer", json={
        "material_id": mid, "source_location_id": loc_a,
        "destination_location_id": loc_b, "qty": 12,
    })
    assert r_t2.status_code == 201

    stock_after_t2 = (await cli.get(f"{BASE}/materials/{mid}/stock-check")).json()
    assert stock_after_t2["stock_qty"] == 30.0   # لسه ثابت

    quants = (await cli.get(f"{BASE}/materials/{mid}/quants")).json()
    qty_at_a = next((q["qty"] for q in quants if q["location_id"] == loc_a), 0)
    qty_at_b = next((q["qty"] for q in quants if q["location_id"] == loc_b), 0)
    assert qty_at_a == 18.0   # 30 - 12
    assert qty_at_b == 12.0


@pytest.mark.asyncio
async def test_transfer_same_source_and_destination_rejected(cli):
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]
    loc = await _make_location(cli, "SAME-LOC")
    r2 = await cli.post(f"{BASE}/movements/transfer", json={
        "material_id": mid, "source_location_id": loc, "destination_location_id": loc, "qty": 5,
    })
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_transfer_insufficient_source_quant_rejected(cli):
    r = await cli.post(f"{BASE}/materials", json=_mat_payload())
    mid = r.json()["id"]
    loc_a = await _make_location(cli, "EMPTY-A")
    loc_b = await _make_location(cli, "EMPTY-B")
    r2 = await cli.post(f"{BASE}/movements/transfer", json={
        "material_id": mid, "source_location_id": loc_a, "destination_location_id": loc_b, "qty": 5,
    })
    assert r2.status_code == 400
