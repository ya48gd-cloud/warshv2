"""
test_17_warehouses.py — محطة 10: المخازن المنطقية (Warehouse)
"""
import pytest

BASE = "/api/v1/warehouses"

DEFAULT_CODES = {"RAW", "SPARE", "IMPORTED", "WIP", "FG", "REMNANT", "SCRAP", "RETURNS"}


@pytest.mark.asyncio
async def test_default_warehouses_seeded(cli):
    """Tenant جديد → الـ8 مخازن الافتراضية موجودة تلقائيًا من غير أي إجراء يدوي."""
    r = await cli.get(BASE)
    assert r.status_code == 200
    codes = {w["code"] for w in r.json()}
    assert codes == DEFAULT_CODES
    assert all(w["is_default"] for w in r.json())
    assert all(w["is_active"] for w in r.json())


@pytest.mark.asyncio
async def test_seeding_is_idempotent(cli):
    """استدعاء seed_default_warehouses تاني على نفس الـ tenant (زي إعادة تشغيل UPDATE.bat) ما بيكررش المخازن."""
    from app.api.warehouses import seed_default_warehouses
    from tests.conftest import TestSessionLocal
    async with TestSessionLocal() as session:
        added = await seed_default_warehouses(session, 1)
        await session.commit()
    assert added == 0   # كلهم موجودين بالفعل من الـ clean_db fixture

    r = await cli.get(BASE)
    assert len(r.json()) == 8   # مفيش تكرار


@pytest.mark.asyncio
async def test_create_custom_warehouse(cli):
    r = await cli.post(BASE, json={"code": "SITE1", "name_ar": "مخزن الموقع 1", "warehouse_type": "general"})
    assert r.status_code == 201
    w = r.json()
    assert w["code"] == "SITE1"
    assert w["is_default"] is False

    all_wh = (await cli.get(BASE)).json()
    assert len(all_wh) == 9


@pytest.mark.asyncio
async def test_duplicate_warehouse_code_rejected(cli):
    r = await cli.post(BASE, json={"code": "RAW", "name_ar": "تكرار", "warehouse_type": "general"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_default_warehouse_blocked(cli):
    """مخزن أساسي (من الـ8) ميتحذفش خالص، حتى لو مفيهوش أي رصيد."""
    warehouses = (await cli.get(BASE)).json()
    raw_wh = next(w for w in warehouses if w["code"] == "RAW")
    r = await cli.delete(f"{BASE}/{raw_wh['id']}")
    assert r.status_code == 400
    # لسه موجود بعد محاولة الحذف
    still_there = (await cli.get(BASE)).json()
    assert any(w["code"] == "RAW" for w in still_there)


@pytest.mark.asyncio
async def test_delete_custom_warehouse_allowed(cli):
    """مخزن مخصّص (مش من الأساسيين) ينفع يتحذف عادي."""
    created = (await cli.post(BASE, json={"code": "TEMP1", "name_ar": "مؤقت", "warehouse_type": "general"})).json()
    r = await cli.delete(f"{BASE}/{created['id']}")
    assert r.status_code == 200
    remaining = (await cli.get(BASE)).json()
    assert not any(w["code"] == "TEMP1" for w in remaining)


@pytest.mark.asyncio
async def test_toggle_warehouse_active(cli):
    created = (await cli.post(BASE, json={"code": "TEMP2", "name_ar": "مؤقت 2"})).json()
    r = await cli.post(f"{BASE}/{created['id']}/toggle-active")
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # اتخفى من القائمة الافتراضية (النشطة بس)
    active_only = (await cli.get(BASE)).json()
    assert not any(w["code"] == "TEMP2" for w in active_only)

    # لسه ظاهر لو طلبت include_inactive
    with_inactive = (await cli.get(f"{BASE}?include_inactive=true")).json()
    assert any(w["code"] == "TEMP2" for w in with_inactive)


@pytest.mark.asyncio
async def test_update_warehouse(cli):
    created = (await cli.post(BASE, json={"code": "TEMP3", "name_ar": "اسم قديم"})).json()
    r = await cli.put(f"{BASE}/{created['id']}", json={"code": "TEMP3", "name_ar": "اسم جديد", "warehouse_type": "general"})
    assert r.status_code == 200
    assert r.json()["name_ar"] == "اسم جديد"


@pytest.mark.asyncio
async def test_update_default_warehouse_name_allowed_but_code_locked(cli):
    """اسم مخزن أساسي ينفع يتعدّل، لكن كوده (RAW/WIP/...) لازم يفضل ثابت —
    كود تاني في النظام (get_or_create_warehouse_bin وغيره) بيدوّر عليه بالاسم
    الحرفي، فتغييره هيسيب مواقع تخزين يتيمة من غير حد يلاحظ."""
    warehouses = (await cli.get(BASE)).json()
    raw_wh = next(w for w in warehouses if w["code"] == "RAW")

    # تغيير الاسم بس (نفس الكود) لازم يعدي عادي
    r_name = await cli.put(f"{BASE}/{raw_wh['id']}", json={
        "code": "RAW", "name_ar": "اسم جديد للخامات", "warehouse_type": raw_wh["warehouse_type"],
    })
    assert r_name.status_code == 200
    assert r_name.json()["name_ar"] == "اسم جديد للخامات"

    # محاولة تغيير الكود نفسه لازم تترفض
    r_code = await cli.put(f"{BASE}/{raw_wh['id']}", json={
        "code": "RAWX", "name_ar": "اسم جديد للخامات", "warehouse_type": raw_wh["warehouse_type"],
    })
    assert r_code.status_code == 400
    still_there = (await cli.get(BASE)).json()
    assert any(w["code"] == "RAW" for w in still_there)


@pytest.mark.asyncio
async def test_warehouses_isolated_per_tenant(cli, cli2):
    """مخزن مخصّص اتعمل لـ tenant1 مايظهرش عند tenant2."""
    await cli.post(BASE, json={"code": "ONLY_T1", "name_ar": "خاص بالمستأجر 1"})
    t2_warehouses = (await cli2.get(BASE)).json()
    assert not any(w["code"] == "ONLY_T1" for w in t2_warehouses)
    # لكن الـ8 الأساسيين موجودين لـ tenant2 برضه (مستقلين)
    assert len(t2_warehouses) == 8
