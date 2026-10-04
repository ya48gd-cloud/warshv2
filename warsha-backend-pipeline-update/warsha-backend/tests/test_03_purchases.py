"""
test_03_purchases.py — اختبارات المشتريات
موردون · فواتير شراء · دفعات · مرفقات
"""
import pytest
from datetime import date

BASE = "/api/v1/purchases"


def _supplier():
    return {"name": "مورد تجريبي", "phone": "0500000002", "supplier_type": "local"}


def _invoice(supplier_id):
    return {
        "code": f"PI-{supplier_id}-001",
        "supplier_id": supplier_id,
        "date": str(date.today()),
        "lines": [{"description": "مادة خام", "qty": 10, "unit": "كجم", "unit_cost": 20.0, "update_stock": False}],
    }


# ════════════════ SUPPLIERS ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_supplier(cli):
    r = await cli.post(f"{BASE}/suppliers", json=_supplier())
    assert r.status_code == 201
    assert r.json()["name"] == "مورد تجريبي"


@pytest.mark.asyncio
async def test_list_suppliers(cli):
    await cli.post(f"{BASE}/suppliers", json=_supplier())
    r = await cli.get(f"{BASE}/suppliers")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_supplier(cli):
    r = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/suppliers/{sid}")
    assert r2.status_code == 200
    assert r2.json()["id"] == sid


@pytest.mark.asyncio
async def test_update_supplier(cli):
    r = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = r.json()["id"]
    upd = _supplier()
    upd["name"] = "مورد محدَّث"
    r2 = await cli.put(f"{BASE}/suppliers/{sid}", json=upd)
    assert r2.status_code == 200
    assert r2.json()["name"] == "مورد محدَّث"


@pytest.mark.asyncio
async def test_delete_supplier(cli):
    r = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/suppliers/{sid}")
    assert r2.status_code in (200, 204)


@pytest.mark.asyncio
async def test_supplier_not_found(cli):
    r = await cli.get(f"{BASE}/suppliers/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_supplier_next_code(cli):
    r = await cli.get(f"{BASE}/suppliers/next-code")
    assert r.status_code == 200
    assert "code" in r.json()


@pytest.mark.asyncio
async def test_supplier_statement(cli):
    r = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/suppliers/{sid}/statement")
    assert r2.status_code == 200


# ════════════════ PURCHASE INVOICES ══════════════════════════════════
@pytest.mark.asyncio
async def test_create_purchase_invoice(cli):
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    r = await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    assert r.status_code == 201
    d = r.json()
    assert d["supplier_id"] == sid
    assert d["subtotal"] == 200.0


@pytest.mark.asyncio
async def test_list_purchase_invoices(cli):
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    r = await cli.get(f"{BASE}/invoices")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_purchase_invoice(cli):
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    r = await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    iid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/invoices/{iid}")
    assert r2.status_code == 200
    assert r2.json()["id"] == iid


@pytest.mark.asyncio
async def test_delete_purchase_invoice(cli):
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    r = await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    iid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/invoices/{iid}")
    assert r2.status_code in (200, 204)


# ════════════════ INVOICE PAYMENT ════════════════════════════════════
@pytest.mark.asyncio
async def test_purchase_invoice_payment(cli):
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    ir = await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    iid = ir.json()["id"]
    r = await cli.post(f"{BASE}/invoices/{iid}/payment", json={
        "amount": 100.0,
        "payment_date": str(date.today()),
        "payment_method": "cash",
    })
    assert r.status_code in (200, 201)
    inv = await cli.get(f"{BASE}/invoices/{iid}")
    assert inv.json()["paid_amount"] >= 100.0


# ════════════════ DASHBOARD ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_purchases_dashboard(cli):
    r = await cli.get(f"{BASE}/dashboard")
    assert r.status_code == 200
    d = r.json()
    assert "total_suppliers" in d
    assert "total_invoices" in d


# ════════════════ ROUND-2 GAP REGRESSION TESTS ═══════════════════════

@pytest.mark.asyncio
async def test_delete_invoice_blocked_after_partial_payment(cli):
    """كانت الفاتورة المدفوعة جزئياً ("partial") تُحذف بحرية، بينما القيد
    المحاسبي العكسي (journal_purchase_invoice_delete) بيعكس كامل الـtotal
    من حساب الموردين رغم إن جزء منه اتعكس بالفعل عند تسجيل الدفعة الجزئية
    — بيسيب حساب الموردين (2110) بفارق سالب يساوي المبلغ المسدد فعلياً.
    دلوقتي لازم الحذف يتمنع بنفس منطق update_purchase_invoice."""
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    ir = await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    iid = ir.json()["id"]

    pay = await cli.post(f"{BASE}/invoices/{iid}/payment", json={
        "amount": 50.0, "payment_date": str(date.today()), "payment_method": "cash",
    })
    assert pay.status_code in (200, 201)
    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["status"] == "partial"

    r = await cli.delete(f"{BASE}/invoices/{iid}")
    assert r.status_code == 400, r.text

    # الفاتورة لسه موجودة زي ما هي
    still = await cli.get(f"{BASE}/invoices/{iid}")
    assert still.status_code == 200


@pytest.mark.asyncio
async def test_supplier_payment_rejects_invoice_of_another_supplier(cli):
    """POST /purchases/payments كان بيقبل invoice_id لفاتورة تخص مورد تاني
    مادام نفس الـtenant — يسدد الفاتورة ويخصم من رصيد المورد التاني الغلط
    بدل صاحب الفاتورة الحقيقي. لازم يترفض برسالة 400."""
    s1 = (await cli.post(f"{BASE}/suppliers", json={"name": "مورد أ"})).json()["id"]
    s2 = (await cli.post(f"{BASE}/suppliers", json={"name": "مورد ب"})).json()["id"]
    ir = await cli.post(f"{BASE}/invoices", json=_invoice(s1))
    iid = ir.json()["id"]

    r = await cli.post(f"{BASE}/payments", json={
        "supplier_id": s2, "invoice_id": iid, "amount": 50.0,
        "payment_date": str(date.today()), "payment_method": "cash",
    })
    assert r.status_code == 400, r.text

    # لا الفاتورة اتأثرت ولا رصيد أي مورد اتغيّر
    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["paid_amount"] == 0
    sup2 = (await cli.get(f"{BASE}/suppliers/{s2}")).json()
    assert sup2["balance"] == 0


@pytest.mark.asyncio
async def test_create_invoice_rejects_supplier_of_another_tenant(cli, cli2):
    """كان db.get(Supplier, supplier_id) بيرجع None تلقائياً (فلتر الـORM
    التلقائي حسب tenant) لمورد تابع لـtenant تاني، لكن الشرط القديم
    "supplier_for_loc and supplier_for_loc.tenant_id != tid" كان كوداً ميتاً
    (مايتحققش أبداً لإن supplier_for_loc أصلاً None) — فالفاتورة كانت تتنشئ
    بنجاح (201) بـsupplier_id يتيم تابع لـtenant تاني، من غير أي خطأ ومن غير
    تحديث رصيد أي مورد حقيقي. لازم ترفض بـ400."""
    r2 = await cli2.post(f"{BASE}/suppliers", json={"name": "مورد تينانت 2"})
    assert r2.status_code == 201, r2.text
    other_tenant_sid = r2.json()["id"]

    r = await cli.post(f"{BASE}/invoices", json=_invoice(other_tenant_sid))
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_create_invoice_rejects_material_of_another_tenant(cli, cli2):
    """كان db.get(Material, material_id) بيرجع مادة أي tenant من غير أي
    تحقق — فاتورة شراء لـ tenant 1 بتشاور على material_id تابع لـ tenant 2
    كانت بترفع مخزون وسعر مادة الـtenant التاني فعلياً. لازم تترفض."""
    # مادة تابعة لـ tenant 2
    mr2 = await cli2.post("/api/v1/inventory/materials", json={
        "name_ar": "مادة تينانت 2", "unit": "كجم", "unit_cost": 5.0,
    })
    assert mr2.status_code == 201, mr2.text
    other_tenant_mid = mr2.json()["id"]

    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    payload = {
        "code": "PI-CROSS-001",
        "supplier_id": sid,
        "date": str(date.today()),
        "lines": [{
            "material_id": other_tenant_mid,
            "description": "مادة مشبوهة",
            "qty": 5, "unit": "كجم", "unit_cost": 20.0,
            "update_stock": True,
        }],
    }
    r = await cli.post(f"{BASE}/invoices", json=payload)
    assert r.status_code == 400, r.text

    # تأكيد إن مخزون مادة الـtenant التاني لم يتأثر
    mat_after = await cli2.get(f"/api/v1/inventory/materials/{other_tenant_mid}")
    assert mat_after.json()["stock_qty"] == 0


@pytest.mark.asyncio
async def test_supplier_payment_rejects_wht_larger_than_invoice_portion(cli):
    """كان wht_amount يتحقق بس مقابل إجمالي amount، رغم إن جزء من الدفعة ممكن
    يروح "تحت الحساب" (excess) لو المبلغ أكبر من المتبقي على الفاتورة. القيد
    المحاسبي (journal_purchase_invoice_payment) بيحمّل wht_amount كامل على
    الجزء المطبَّق على الفاتورة (invoice_amount) بس — لو wht_amount أكبر من
    invoice_amount، سطر الصندوق (1110) بيتسجل بقيمة سالبة (قيد معكوس فعلياً
    رغم توازن الطرفين ظاهرياً). لازم يترفض بـ400."""
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    # فاتورة بقيمة إجمالية 200 (10 * 20) — المتبقي كامل الـ200
    ir = await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    iid = ir.json()["id"]

    # دفعة 250 (أكبر من المتبقي 200 → invoice_amount=200, excess=50)
    # لكن wht_amount=220 أكبر من invoice_amount=200 (رغم إنه أصغر من amount=250)
    r = await cli.post(f"{BASE}/payments", json={
        "supplier_id": sid, "invoice_id": iid, "amount": 250.0,
        "wht_amount": 220.0,
        "payment_date": str(date.today()), "payment_method": "cash",
    })
    assert r.status_code == 400, r.text

    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["paid_amount"] == 0


@pytest.mark.asyncio
async def test_delete_invoice_cleans_up_attachments(cli):
    """حذف فاتورة الشراء كان بيسيب سجلات FileAttachment يتيمة (وملفات على
    القرص) تشاور على invoice_id بقى محذوف. لازم تتنضف مع حذف الفاتورة."""
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    ir = await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    iid = ir.json()["id"]

    # ملف صورة صغير وهمي (PNG header بس كفاية للاختبار)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    up = await cli.post(
        f"{BASE}/invoices/{iid}/attachment",
        files={"file": ("test.png", png_bytes, "image/png")},
    )
    assert up.status_code == 200, up.text
    att_id = up.json()["id"]

    r = await cli.delete(f"{BASE}/invoices/{iid}")
    assert r.status_code in (200, 204)

    dl = await cli.get(f"{BASE}/attachments/{att_id}/download")
    assert dl.status_code == 404



# ════════════════ ROUND 3: PURCHASES VALIDATION / SECURITY FIXES ═══════

@pytest.mark.asyncio
async def test_viewer_cannot_write_purchases_but_can_read(cli):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.api.auth import create_token
    tok = create_token(user_id=991, username="viewer_pur", role="viewer", tenant_id=1)
    sid = (await cli.post(f"{BASE}/suppliers", json=_supplier())).json()["id"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as vcli:
        assert (await vcli.post(f"{BASE}/suppliers", json=_supplier())).status_code == 403
        assert (await vcli.post(f"{BASE}/invoices", json=_invoice(sid))).status_code == 403
        assert (await vcli.delete(f"{BASE}/suppliers/{sid}")).status_code == 403
        assert (await vcli.post("/api/v1/purchase-orders", json={
            "order_date": str(date.today()), "supplier_id": sid,
            "lines": [{"description": "x", "ordered_qty": 1, "unit_cost": 1}]})).status_code == 403
        assert (await vcli.get(f"{BASE}/suppliers")).status_code == 200
        assert (await vcli.get(f"{BASE}/invoices")).status_code == 200


@pytest.mark.asyncio
async def test_invoice_rejects_bad_tax_discount_and_due_date(cli):
    sid = (await cli.post(f"{BASE}/suppliers", json=_supplier())).json()["id"]
    p = _invoice(sid)
    p["code"] = f"PI-BAD-{sid}"
    for patch in ({"tax_pct": -5}, {"tax_pct": 150}, {"discount_amt": -1},
                  {"discount_amt": 99999}, {"due_date": "2000-01-01"}):
        r = await cli.post(f"{BASE}/invoices", json={**p, **patch})
        assert r.status_code == 400, (patch, r.text)


@pytest.mark.asyncio
async def test_duplicate_invoice_code_returns_400_not_500(cli):
    sid = (await cli.post(f"{BASE}/suppliers", json=_supplier())).json()["id"]
    p = _invoice(sid)
    p["code"] = f"PI-DUP-{sid}"
    assert (await cli.post(f"{BASE}/invoices", json=p)).status_code == 201
    r = await cli.post(f"{BASE}/invoices", json=p)
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_auto_invoice_code_unique_after_delete(cli):
    """الكود التلقائي PI-YYYY-NNNN كان بالعدّ فقط → بعد حذف فاتورة يطلع مكرر (500)."""
    sid = (await cli.post(f"{BASE}/suppliers", json=_supplier())).json()["id"]
    p = _invoice(sid)
    p.pop("code")
    a = (await cli.post(f"{BASE}/invoices", json=p)).json()["id"]
    b = await cli.post(f"{BASE}/invoices", json=p)
    assert b.status_code == 201, b.text
    assert (await cli.delete(f"{BASE}/invoices/{a}")).status_code == 200
    c = await cli.post(f"{BASE}/invoices", json=p)
    assert c.status_code == 201, c.text
    assert c.json()["code"] != b.json()["code"]


@pytest.mark.asyncio
async def test_duplicate_supplier_code_returns_400(cli):
    r = await cli.post(f"{BASE}/suppliers", json={**_supplier(), "code": "SUP-DUPX"})
    assert r.status_code == 201
    r2 = await cli.post(f"{BASE}/suppliers", json={**_supplier(), "code": "SUP-DUPX"})
    assert r2.status_code == 400, r2.text
    r3 = await cli.post(f"{BASE}/suppliers", json={**_supplier(), "name": "   "})
    assert r3.status_code == 422


@pytest.mark.asyncio
async def test_generic_attach_rejects_path_traversal_entity_type(cli):
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    r = await cli.post(
        f"{BASE}/attach?entity_type=../../evil&entity_id=1",
        files={"file": ("x.png", png_bytes, "image/png")},
    )
    assert r.status_code == 400, r.text
    ok = await cli.post(
        f"{BASE}/attach?entity_type=supplier_payment&entity_id=1",
        files={"file": ("x.png", png_bytes, "image/png")},
    )
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_print_purchase_invoice_escapes_html(cli):
    sid = (await cli.post(f"{BASE}/suppliers", json=_supplier())).json()["id"]
    p = _invoice(sid)
    p["code"] = f"PI-XSS-{sid}"
    p["notes"] = "<script>alert(1)</script>"
    p["lines"][0]["description"] = "<img src=x onerror=alert(2)>"
    iid = (await cli.post(f"{BASE}/invoices", json=p)).json()["id"]
    r = await cli.get(f"{BASE}/invoices/{iid}/print")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "<img src=x onerror" not in r.text

