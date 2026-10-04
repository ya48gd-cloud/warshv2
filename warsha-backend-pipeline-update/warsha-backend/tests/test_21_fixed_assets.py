"""
test_21_fixed_assets.py — الأصول الثابتة: الشراء، الإهلاك، الاستبعاد/البيع

يغطي إصلاحات جولة 2 (gap analysis):
  - شراء أصل ثابت كان يُسجَّل من غير أي قيد محاسبي مقابل (لا 1400 ولا نقص نقدية)
  - استبعاد/بيع أصل (dispose) كان يغيّر status فقط من غير أي أثر محاسبي —
    لا إقفال لتكلفة الأصل/مجمع إهلاكه ولا إثبات ربح/خسارة الاستبعاد
  - تعديل/حذف أصل كان يسيب قيد الشراء الأصلي قائماً بلا عكس (ليدجر يتيم)
  - عدم تكرار الإهلاك لنفس الفترة (كانت موجودة بالفعل — تأكيد فقط)
"""
import pytest
from datetime import date

BASE = "/api/v1/fixed-assets"
ACC_BASE = "/api/v1/accounts"


async def _seed(cli):
    await cli.post(f"{ACC_BASE}/chart/seed")


async def _acc_balance(cli, code):
    tb = (await cli.get(f"{ACC_BASE}/trial-balance")).json()
    row = next((a for a in tb["accounts"] if a["code"] == code), None)
    return row["balance"] if row else 0.0


@pytest.mark.asyncio
async def test_create_asset_posts_acquisition_entry(cli):
    await _seed(cli)
    r = await cli.post(BASE, json={
        "name": "رافعة شوكية", "asset_type": "equipment",
        "purchase_date": str(date.today()), "cost": 50000.0,
        "salvage_value": 5000.0, "useful_life_years": 5,
    })
    assert r.status_code == 200, r.text
    assert await _acc_balance(cli, "1400") == 50000.0
    assert await _acc_balance(cli, "1110") == -50000.0  # صندوق نقص بقيمة الشراء


@pytest.mark.asyncio
async def test_dispose_asset_with_gain_posts_entry_and_removes_from_active(cli):
    await _seed(cli)
    r = await cli.post(BASE, json={
        "name": "معدة للبيع", "purchase_date": str(date.today()),
        "cost": 10000.0, "salvage_value": 0.0, "useful_life_years": 5,
    })
    aid = r.json()["id"]

    # بيع بعائد أعلى من القيمة الدفترية (10000 - 0 إهلاك) = ربح 2000
    dis = await cli.patch(f"{BASE}/{aid}/dispose", json={"sale_amount": 12000.0})
    assert dis.status_code == 200, dis.text
    assert dis.json()["status"] == "disposed"

    assert await _acc_balance(cli, "1400") == 0.0       # تكلفة الأصل اتقفلت
    assert await _acc_balance(cli, "4400") == 2000.0    # ربح استبعاد اتسجل

    active = (await cli.get(f"{BASE}?status=active")).json()
    assert aid not in [a["id"] for a in active]


@pytest.mark.asyncio
async def test_dispose_asset_with_loss_posts_loss_entry(cli):
    await _seed(cli)
    r = await cli.post(BASE, json={
        "name": "معدة قديمة", "purchase_date": str(date.today()),
        "cost": 10000.0, "salvage_value": 0.0, "useful_life_years": 5,
    })
    aid = r.json()["id"]

    dis = await cli.patch(f"{BASE}/{aid}/dispose", json={"sale_amount": 1000.0})
    assert dis.status_code == 200, dis.text
    assert await _acc_balance(cli, "5600") == 9000.0  # خسارة استبعاد = 10000 - 1000


@pytest.mark.asyncio
async def test_cannot_dispose_asset_twice(cli):
    await _seed(cli)
    r = await cli.post(BASE, json={
        "name": "أصل", "purchase_date": str(date.today()),
        "cost": 1000.0, "useful_life_years": 5,
    })
    aid = r.json()["id"]
    first = await cli.patch(f"{BASE}/{aid}/dispose")
    assert first.status_code == 200
    second = await cli.patch(f"{BASE}/{aid}/dispose")
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_depreciation_cannot_double_post_same_period(cli):
    await _seed(cli)
    r = await cli.post(BASE, json={
        # تاريخ شراء قبل فترة الإهلاك (2025/01) — الإهلاك قبل الشراء بقى مرفوض
        "name": "أصل إهلاك", "purchase_date": "2024-12-01",
        "cost": 12000.0, "salvage_value": 0.0, "useful_life_years": 1,
    })
    aid = r.json()["id"]
    ok = await cli.post(f"{BASE}/{aid}/depreciate", json={"period_year": 2025, "period_month": 1})
    assert ok.status_code == 200, ok.text
    dup = await cli.post(f"{BASE}/{aid}/depreciate", json={"period_year": 2025, "period_month": 1})
    assert dup.status_code == 400


@pytest.mark.asyncio
async def test_depreciation_rejects_invalid_month(cli):
    await _seed(cli)
    r = await cli.post(BASE, json={
        "name": "أصل", "purchase_date": str(date.today()),
        "cost": 1000.0, "useful_life_years": 5,
    })
    aid = r.json()["id"]
    bad = await cli.post(f"{BASE}/{aid}/depreciate", json={"period_year": 2025, "period_month": 13})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_delete_asset_reverses_purchase_entry(cli):
    await _seed(cli)
    r = await cli.post(BASE, json={
        "name": "أصل للحذف", "purchase_date": str(date.today()),
        "cost": 3000.0, "useful_life_years": 5,
    })
    aid = r.json()["id"]
    assert await _acc_balance(cli, "1400") == 3000.0

    dele = await cli.delete(f"{BASE}/{aid}")
    assert dele.status_code == 200, dele.text

    assert await _acc_balance(cli, "1400") == 0.0, (
        "حذف الأصل لازم يعكس قيد الشراء الأصلي وإلا يفضل قيد يتيم في 1400"
    )


@pytest.mark.asyncio
async def test_update_asset_cost_reposts_journal_entry(cli):
    await _seed(cli)
    r = await cli.post(BASE, json={
        "name": "أصل للتعديل", "purchase_date": str(date.today()),
        "cost": 4000.0, "useful_life_years": 5,
    })
    aid = r.json()["id"]
    upd = await cli.patch(f"{BASE}/{aid}", json={
        "name": "أصل للتعديل", "purchase_date": str(date.today()),
        "cost": 6000.0, "useful_life_years": 5,
    })
    assert upd.status_code == 200, upd.text
    assert await _acc_balance(cli, "1400") == 6000.0, (
        "بعد تعديل التكلفة، رصيد 1400 لازم يعكس القيمة الجديدة فقط (6000)، "
        "مش القديمة (4000) ولا مجموعهما"
    )



# ══ جولة 3 — gap/validation analysis ═══════════════════════════════════
@pytest.mark.asyncio
async def test_viewer_cannot_create_fixed_asset():
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.api.auth import create_token
    tok = create_token(user_id=990201, username="viewer_fa", role="viewer", tenant_id=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as v:
        r = await v.post(BASE, json={
            "name": "x", "purchase_date": str(date.today()), "cost": 100.0, "useful_life_years": 5,
        })
        assert r.status_code == 403
        assert (await v.get(BASE)).status_code == 200


@pytest.mark.asyncio
async def test_fixed_asset_cross_tenant_access_blocked(cli, cli2):
    await _seed(cli)
    r = await cli.post(BASE, json={
        "name": "أصل ورشة 1", "purchase_date": str(date.today()), "cost": 1000.0, "useful_life_years": 5,
    })
    aid = r.json()["id"]
    assert (await cli2.get(f"{BASE}/{aid}")).status_code == 404
    assert (await cli2.delete(f"{BASE}/{aid}")).status_code == 404
    assert (await cli2.patch(f"{BASE}/{aid}/dispose")).status_code == 404


@pytest.mark.asyncio
async def test_fixed_asset_duplicate_code_and_invalid_type_rejected(cli):
    await _seed(cli)
    body = {"code": "FA-X1", "name": "أصل", "purchase_date": str(date.today()),
            "cost": 1000.0, "useful_life_years": 5}
    assert (await cli.post(BASE, json=body)).status_code == 200
    dup = await cli.post(BASE, json=body)
    assert dup.status_code == 400
    bad = await cli.post(BASE, json={**body, "code": "FA-X2", "asset_type": "spaceship"})
    assert bad.status_code == 422
    # حساب دفع غير نقدي (إيراد) مرفوض
    bad_acc = await cli.post(BASE, json={**body, "code": "FA-X3", "payment_account_code": "4100"})
    assert bad_acc.status_code == 400


@pytest.mark.asyncio
async def test_depreciation_before_purchase_or_future_rejected(cli):
    await _seed(cli)
    r = await cli.post(BASE, json={
        "name": "أصل", "purchase_date": "2025-06-15", "cost": 1200.0, "useful_life_years": 1,
    })
    aid = r.json()["id"]
    before = await cli.post(f"{BASE}/{aid}/depreciate", json={"period_year": 2025, "period_month": 5})
    assert before.status_code == 400
    future = await cli.post(f"{BASE}/{aid}/depreciate", json={"period_year": 2099, "period_month": 1})
    assert future.status_code == 400
    ok = await cli.post(f"{BASE}/{aid}/depreciate", json={"period_year": 2025, "period_month": 6})
    assert ok.status_code == 200, ok.text
    assert ok.json()["accumulated_depreciation"] == 100.0


@pytest.mark.asyncio
async def test_disposed_asset_cannot_be_deleted_or_cost_edited(cli):
    await _seed(cli)
    r = await cli.post(BASE, json={
        "name": "أصل مستبعد", "purchase_date": str(date.today()), "cost": 2000.0, "useful_life_years": 5,
    })
    aid = r.json()["id"]
    assert (await cli.patch(f"{BASE}/{aid}/dispose")).status_code == 200
    assert (await cli.delete(f"{BASE}/{aid}")).status_code == 400
    upd = await cli.patch(f"{BASE}/{aid}", json={
        "name": "أصل مستبعد", "purchase_date": str(date.today()), "cost": 5000.0, "useful_life_years": 5,
    })
    assert upd.status_code == 400
    # تعديل البيانات الوصفية فقط مسموح
    upd2 = await cli.patch(f"{BASE}/{aid}", json={
        "name": "اسم جديد", "purchase_date": str(date.today()), "cost": 2000.0, "useful_life_years": 5,
    })
    assert upd2.status_code == 200, upd2.text
    assert await _acc_balance(cli, "1400") == 0.0


@pytest.mark.asyncio
async def test_update_cost_keeps_original_payment_account(cli):
    await _seed(cli)
    r = await cli.post(BASE, json={
        "name": "أصل بنكي", "purchase_date": str(date.today()), "cost": 1000.0,
        "useful_life_years": 5, "payment_account_code": "1120",
    })
    aid = r.json()["id"]
    upd = await cli.patch(f"{BASE}/{aid}", json={
        "name": "أصل بنكي", "purchase_date": str(date.today()), "cost": 1500.0, "useful_life_years": 5,
    })
    assert upd.status_code == 200, upd.text
    assert await _acc_balance(cli, "1120") == -1500.0
    assert await _acc_balance(cli, "1110") == 0.0
