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
        "name": "أصل إهلاك", "purchase_date": str(date.today()),
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
