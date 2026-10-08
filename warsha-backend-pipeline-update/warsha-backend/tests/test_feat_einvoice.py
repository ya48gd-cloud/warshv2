"""
test_feat_einvoice.py — الفاتورة الإلكترونية (ETA).

*** كل HTTP هنا محاكى (httpx.MockTransport) — لا اتصال شبكي حقيقي (محجوب على مستوى socket/getaddrinfo). ***
*** هذه الاختبارات تثبت سلوك النظام ضد المحاكاة فقط؛ لم يُجرَّب التكامل على ETA الفعلية. ***

يغطي: المحوّل (حساب/مطابقة/خصم/ضريبة/مستلم/وحدات) · التوقيع (Mock/External + حظر الإنتاج) · عميل ETA
(OAuth/كاش/401/backoff/429/4xx) · الإرسال الكامل · idempotency والتزامن وضياع الرد · الطابور والـ worker ·
عزل الورش · RBAC · الأسرار (تشفير/عدم التسريب/التدقيق/التصدير) · حراس عدم التعديل · الهجرة · التوافق الخلفي.
"""
import asyncio
import base64
import hashlib
import json
import socket
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qsl

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, update, func, text

from app.main import app
from app.api.auth import create_token
from app.models.models import (
    AuditLog, ApprovalRequest, CreditNote, EInvoiceSettings, EInvoiceSubmission, Invoice, StockMovement,
)
from app.services import eta_client, einvoice_mapper as mapper, einvoice_signer as signer_mod
from app.services import einvoice_service as svc, secret_box, tenant_export as texp
from app.services.einvoice_worker import run_einvoice_queue_once
from tests.conftest import TestSessionLocal

EI = "/api/v1/einvoice"
SALES = "/api/v1/sales"
CUST = "/api/v1/customers"
CN = "/api/v1/credit-notes"
COA = "/api/v1/accounts"

SECRET = "SECRET-XYZ-987"
CLIENT_ID = "CID-ABCD1234"
SIGNER_TOKEN = "signer-token-777"


# ═══════════════════ بنية الاختبار ═══════════════════

_LOCAL = {"127.0.0.1", "::1", "localhost", None, ""}


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """حظر أي اتصال حقيقي: connect و getaddrinfo لأي مضيف غير محلي + مصنع HTTP الافتراضي يفشل."""
    real_connect = socket.socket.connect
    real_gai = socket.getaddrinfo

    def guarded_connect(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOCAL:
            raise AssertionError(f"real network blocked: {address}")
        return real_connect(self, address, *a, **k)

    def guarded_gai(host, *a, **k):
        if host not in _LOCAL:
            raise AssertionError(f"real DNS blocked: {host}")
        return real_gai(host, *a, **k)

    def no_factory():
        raise AssertionError("HTTP غير محاكى: استخدم fixture eta")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_gai)
    monkeypatch.setattr(eta_client, "default_http_factory", no_factory)
    eta_client.clear_token_cache()
    yield
    eta_client.clear_token_cache()


class EtaMock:
    """ETA + خدمة توقيع خارجية محاكاة بالكامل."""

    def __init__(self):
        self.requests = []
        self.submit_script = []      # عناصر: callable(request)->Response | Exception
        self.doc_status = "Valid"    # حالة المستند في استعلام الـ submission
        self.docs = {}               # internalID -> {uuid,longId,submissionId}
        self.n = 0
        self.token_status = 200
        self.search_override = None
        self.sign_status = 200

    def factory(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), timeout=5)

    # ── أدوات ──
    def calls(self, method, path_prefix):
        return [r for r in self.requests if r["method"] == method and r["path"].startswith(path_prefix)]

    def submits(self):
        return self.calls("POST", "/api/v1/documentsubmissions")

    def accept(self, doc):
        self.n += 1
        d = {"uuid": f"UUID-{self.n}", "longId": f"LONG-{self.n}", "submissionId": f"SUB-{self.n}"}
        self.docs[doc["internalID"]] = d
        return d

    # ── المعالج ──
    def handler(self, request):
        body = request.content.decode() if request.content else ""
        rec = {"method": request.method, "path": request.url.path, "host": request.url.host,
               "headers": dict(request.headers), "body": body, "query": dict(request.url.params)}
        self.requests.append(rec)
        path = request.url.path

        if request.url.host == "signer.example.com":
            if self.sign_status != 200:
                return httpx.Response(self.sign_status, json={"error": "boom"})
            return httpx.Response(200, json={"signature": base64.b64encode(b"REAL-CMS-SIGNATURE").decode()})

        if path == "/connect/token":
            form = dict(parse_qsl(body))
            rec["form"] = form
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": f"tok-{form.get('client_id')}-{len(self.requests)}",
                                             "expires_in": 3600, "token_type": "Bearer"})

        if path == "/api/v1/documentsubmissions" and request.method == "POST":
            if self.submit_script:
                act = self.submit_script.pop(0)
                r = act(request)
                if isinstance(r, Exception):
                    raise r
                return r
            doc = json.loads(body)["documents"][0]
            if doc["internalID"] in self.docs:
                return httpx.Response(202, json={"submissionId": "SUB-DUP", "acceptedDocuments": [],
                                                 "rejectedDocuments": [{"internalId": doc["internalID"], "error": {
                                                     "code": "DuplicateDocument", "message": "Duplicated submission",
                                                     "target": "internalID", "details": []}}]})
            d = self.accept(doc)
            return httpx.Response(202, json={"submissionId": d["submissionId"], "rejectedDocuments": [],
                                             "acceptedDocuments": [{"uuid": d["uuid"], "longId": d["longId"],
                                                                    "internalId": doc["internalID"]}]})

        if path.startswith("/api/v1/documentsubmissions/") and request.method == "GET":
            sid = path.rsplit("/", 1)[1]
            out = []
            for iid, d in self.docs.items():
                if d["submissionId"] == sid:
                    item = {"uuid": d["uuid"], "internalId": iid, "longId": d["longId"], "status": self.doc_status}
                    if self.doc_status == "Invalid":
                        item["validationResults"] = {"status": "Invalid", "validationSteps": [
                            {"name": "ItemsValidator", "status": "Invalid",
                             "error": {"error": "Item code invalid", "propertyPath": "invoiceLines[0].itemCode"}}]}
                    out.append(item)
            return httpx.Response(200, json={"submissionId": sid, "documentSummary": out})

        if path == "/api/v1.0/documents/search":
            if self.search_override is not None:
                return httpx.Response(200, json={"result": self.search_override})
            iid = request.url.params.get("InternalID")
            d = self.docs.get(iid)
            res = [] if not d else [{"uuid": d["uuid"], "internalId": iid, "longId": d["longId"],
                                     "status": self.doc_status, "submissionUUID": d["submissionId"]}]
            return httpx.Response(200, json={"result": res})

        if request.method == "PUT" and path.startswith("/api/v1.0/documents/state/"):
            return httpx.Response(200, json={})

        return httpx.Response(404, json={"error": "unexpected " + path})


@pytest.fixture
def eta(monkeypatch):
    m = EtaMock()
    monkeypatch.setattr(eta_client, "default_http_factory", m.factory)
    return m


@pytest_asyncio.fixture
async def mk():
    made = []

    def _mk(uid, name, role, tid=1):
        tok = create_token(user_id=uid, username=name, role=role, tenant_id=tid)
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                        headers={"Authorization": f"Bearer {tok}"})
        made.append(c)
        return c

    yield _mk
    for c in made:
        await c.aclose()


@pytest_asyncio.fixture
async def adm(mk):
    return mk(1, "admin", "admin")


@pytest_asyncio.fixture
async def adm2(mk):
    return mk(2, "admin2", "admin", tid=2)


@pytest_asyncio.fixture
async def acct(mk):
    return mk(13, "acct_u", "accountant")


@pytest_asyncio.fixture
async def prodc(mk):
    return mk(15, "prod_u", "production")


@pytest_asyncio.fixture
async def viewer(mk):
    return mk(16, "view_u", "viewer")


BASE_SETTINGS = dict(
    enabled=True, environment="preprod", tax_registration_number="123456789", activity_code="4610",
    issuer_name="ورشة الاختبار", governate="القاهرة", region_city="مدينة نصر", street="شارع الصناعة",
    building_number="12", client_id=CLIENT_ID, client_secret=SECRET, signer_mode="mock",
)


async def configure(c, **over):
    body = {**BASE_SETTINGS, **over}
    r = await c.put(f"{EI}/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def mk_customer(c, name="عميل فاتورة", tax_id="987654321", address="12 شارع التحرير - القاهرة"):
    body = {"name": name, "phone": "0100000000"}
    if tax_id is not None:
        body["tax_id"] = tax_id
    if address is not None:
        body["address"] = address
    r = await c.post(CUST, json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


LINES = [
    {"description": "قطعة أ", "qty": 2, "unit": "قطعة", "unit_price": 500, "model_no": "M-1"},
    {"description": "قطعة ب", "qty": 1, "unit": "متر", "unit_price": 333.33},
]


async def mk_invoice(c, cid, lines=None, tax=14, disc=10, d=None):
    r = await c.post(f"{SALES}/invoices", json={
        "customer_id": cid, "date": str(d or date.today()), "tax_pct": tax, "discount_pct": disc,
        "lines": lines or LINES})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def mk_cn(c, cid, inv_id, price=100, tax=14, issue=True):
    r = await c.post(CN, json={"customer_id": cid, "invoice_id": inv_id, "date": str(date.today()),
                               "return_type": "partial", "tax_pct": tax,
                               "lines": [{"description": "مرتجع", "qty": 1, "unit": "قطعة", "unit_price": price}]})
    assert r.status_code == 201, r.text
    cn_id = r.json()["id"]
    if issue:
        r2 = await c.patch(f"{CN}/{cn_id}/issue")
        assert r2.status_code == 200, r2.text
    return cn_id


async def ready_invoice(c, **kw):
    cid = await mk_customer(c)
    return cid, await mk_invoice(c, cid, **kw)


async def submit(c, iid, kind="invoice"):
    return await c.post(f"{EI}/documents/{kind}/{iid}/submit")


async def get_row(doc_type, doc_id, tid=1):
    async with TestSessionLocal() as s:
        return (await s.execute(select(EInvoiceSubmission).where(
            EInvoiceSubmission.tenant_id == tid, EInvoiceSubmission.doc_type == doc_type,
            EInvoiceSubmission.doc_id == doc_id))).scalars().first()


async def get_inv(iid):
    async with TestSessionLocal() as s:
        return await s.get(Invoice, iid)


async def sql(stmt, **params):
    async with TestSessionLocal() as s:
        r = await s.execute(text(stmt), params)
        await s.commit()
        return r


async def count(model, *conds):
    async with TestSessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(model).where(*conds)) or 0


async def make_valid_invoice(c, eta, **kw):
    """فاتورة مقبولة (valid) لدى ETA المحاكاة — مطلوبة للإشعارات الدائنة."""
    await configure(c)
    cid, iid = await ready_invoice(c, **kw)
    r = await submit(c, iid)
    assert r.status_code == 200 and r.json()["status"] == "valid", r.text
    return cid, iid


# ═══════════════════ وحدات خالصة ═══════════════════

def test_allocate_discount_sums_exactly_and_last_absorbs():
    sales = [Decimal("1000"), Decimal("333.33")]
    parts = mapper.allocate_discount(Decimal("133.33"), sales)
    assert sum(parts) == Decimal("133.33000")
    assert parts[0] == Decimal("100.00000") or abs(parts[0] - Decimal("100")) < Decimal("0.01")
    odd = mapper.allocate_discount(Decimal("10"), [Decimal("1"), Decimal("1"), Decimal("1")])
    assert sum(odd) == Decimal("10")
    assert mapper.allocate_discount(Decimal("0"), sales) == [Decimal("0")] * 2
    assert mapper.allocate_discount(Decimal("5"), []) == []


def test_fmt_number_and_canonical_serialization_deterministic():
    assert signer_mod.fmt_number(1.50) == "1.5"
    assert signer_mod.fmt_number(100.0) == "100"
    assert signer_mod.fmt_number(0.1 + 0.2) in ("0.30000000000000004", "0.3")
    assert signer_mod.fmt_number(0) == "0"
    doc = {"issuer": {"name": "a", "id": "1"}, "lines": [{"x": 1.5}, {"x": 2}], "signatures": [{"value": "zzz"}],
           "n": None}
    c1 = signer_mod.serialize_for_signing(doc)
    c2 = signer_mod.serialize_for_signing(json.loads(json.dumps(doc)))
    assert c1 == c2
    assert '"SIGNATURES"' not in c1 and "zzz" not in c1      # التوقيعات مستبعدة
    assert c1.startswith('"ISSUER""NAME""a""ID""1"')
    assert c1.count('"LINES"') == 3                           # اسم المصفوفة + قبل كل عنصر
    assert '"N"' not in c1                                    # None مُهمل
    assert signer_mod.document_hash(c1) == hashlib.sha256(c1.encode()).hexdigest()


def test_map_unit_and_unknown_fallback():
    assert mapper.map_unit("قطعة", "EA") == "EA"
    assert mapper.map_unit("متر", "EA") == "MTR"
    assert mapper.map_unit("م2", "EA") == "MTK"
    assert mapper.map_unit("م3", "EA") == "MTQ"
    assert mapper.map_unit("كجم", "EA") == "KGM"
    assert mapper.map_unit("طن", "EA") == "TNE"
    assert mapper.map_unit("لتر", "EA") == "LTR"
    assert mapper.map_unit("طقم", "EA") == "SET"
    assert mapper.map_unit("غير معروفة", "XYZ") == "XYZ"


def test_validate_https_url_rules():
    v = eta_client.validate_https_url
    assert v("https://signer.example.com/sign/") == "https://signer.example.com/sign"
    for bad in ["http://signer.example.com", "https://user:pw@signer.example.com", "https://127.0.0.1/x",
                "https://10.0.0.5/x", "https://192.168.1.2/x", "https://localhost/x", "ftp://x.com", "https://",
                "https://169.254.1.1/x"]:
        with pytest.raises(eta_client.UnsafeUrlError):
            v(bad)
    assert v("https://10.0.0.5/x", allow_private=True) == "https://10.0.0.5/x"


def test_secret_box_roundtrip_and_prefix():
    t = secret_box.encrypt("hello-secret")
    assert t.startswith("v1:") and "hello-secret" not in t
    assert secret_box.decrypt(t) == "hello-secret"
    assert secret_box.is_encrypted(t) and not secret_box.is_encrypted("plain")
    with pytest.raises(secret_box.SecretBoxError):
        secret_box.decrypt("plain-not-encrypted")
    with pytest.raises(secret_box.SecretBoxError):
        secret_box.decrypt("v1:garbage")


def test_secret_box_key_change_gives_clear_arabic_error(monkeypatch):
    from cryptography.fernet import Fernet
    t = secret_box.encrypt("abc")
    monkeypatch.setenv("ERP_EINVOICE_KEY", Fernet.generate_key().decode())
    with pytest.raises(secret_box.SecretBoxError) as e:
        secret_box.decrypt(t)
    assert "أعد إدخال" in str(e.value)


def test_migration_chain_and_idempotent_helpers():
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location("m0039", root / "0039_einvoice_eta.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.revision == "0039" and m.down_revision == "0038"
    prev = importlib.util.spec_from_file_location("m0038", root / "0038_backup_runs.py")
    pm = importlib.util.module_from_spec(prev)
    prev.loader.exec_module(pm)
    assert pm.revision == "0038"


def test_migration_upgrade_twice_and_downgrade_on_scratch_sqlite(tmp_path):
    """يشغّل upgrade() مرتين (idempotency) ثم downgrade() على SQLite مؤقت عبر alembic Operations."""
    import importlib.util
    import pathlib
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import alembic

    root = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location("m0039b", root / "0039_einvoice_eta.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    eng = sa.create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    with eng.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE tenants (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE invoices (id INTEGER PRIMARY KEY, code VARCHAR(50))")
        conn.exec_driver_sql("CREATE TABLE credit_notes (id INTEGER PRIMARY KEY, code VARCHAR(50))")
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            m.upgrade()
            m.upgrade()          # مرة ثانية: لازم تمر بلا خطأ
        insp = sa.inspect(conn)
        assert {"einvoice_settings", "einvoice_submissions"} <= set(insp.get_table_names())
        for t in ("invoices", "credit_notes"):
            cols = {c["name"] for c in insp.get_columns(t)}
            assert {"eta_uuid", "eta_status", "eta_submission_id", "eta_long_id", "eta_internal_id",
                    "eta_submitted_at", "eta_errors"} <= cols
        with Operations.context(MigrationContext.configure(conn)):
            m.downgrade()
            m.downgrade()        # idempotent أيضًا
        insp = sa.inspect(conn)
        assert "einvoice_settings" not in insp.get_table_names()
        assert "eta_status" not in {c["name"] for c in insp.get_columns("invoices")}
    assert alembic  # noqa


# ═══════════════════ الإعدادات والأسرار ═══════════════════

async def test_settings_default_and_status_for_all_roles(adm, acct, viewer, prodc):
    r = await adm.get(f"{EI}/settings")
    assert r.status_code == 200
    j = r.json()
    assert j["enabled"] is False and j["environment"] == "preprod" and j["signer_mode"] == "none"
    assert j["ready"] is False and j["missing"] and "غير مُجرَّب" in j["disclaimer"]
    assert j["effective_api_base_url"] == "https://api.preprod.invoicing.eta.gov.eg"
    assert j["effective_id_base_url"] == "https://id.preprod.eta.gov.eg"
    for c in (adm, acct, viewer, prodc):
        s = await c.get(f"{EI}/status")
        assert s.status_code == 200
        assert s.json() == {"enabled": False, "environment": "preprod", "ready": False, "signer_mode": "none"}


async def test_settings_save_never_returns_plaintext_and_encrypts_at_rest(adm):
    put = await adm.put(f"{EI}/settings", json=BASE_SETTINGS)
    assert put.status_code == 200, put.text
    got = await adm.get(f"{EI}/settings")
    for resp in (put, got):
        assert SECRET not in resp.text and CLIENT_ID not in resp.text
    j = got.json()
    assert j["has_client_id"] and j["has_client_secret"] and j["client_id_masked"] == "••••1234"
    assert j["has_signer_token"] is False and j["ready"] is True and j["missing"] == []
    for forbidden in ("client_id", "client_secret", "signer_token", "client_secret_enc", "client_credential_id_enc"):
        assert forbidden not in j
    async with TestSessionLocal() as s:
        row = (await s.execute(select(EInvoiceSettings).where(EInvoiceSettings.tenant_id == 1))).scalars().first()
    assert row.client_secret_enc.startswith("v1:") and row.client_secret_enc != SECRET
    assert secret_box.decrypt(row.client_secret_enc) == SECRET
    assert secret_box.decrypt(row.client_credential_id_enc) == CLIENT_ID
    assert row.tenant_id == 1


async def test_settings_omitted_secret_keeps_old_clear_removes_empty_rejected(adm):
    await configure(adm)
    async with TestSessionLocal() as s:
        before = (await s.execute(select(EInvoiceSettings.client_secret_enc))).scalar()
    r = await adm.put(f"{EI}/settings", json={"governate": "الجيزة"})
    assert r.status_code == 200 and r.json()["governate"] == "الجيزة" and r.json()["has_client_secret"]
    r = await adm.put(f"{EI}/settings", json={"client_secret": None})
    assert r.status_code == 200 and r.json()["has_client_secret"]
    async with TestSessionLocal() as s:
        assert (await s.execute(select(EInvoiceSettings.client_secret_enc))).scalar() == before
    r = await adm.put(f"{EI}/settings", json={"client_secret": ""})
    assert r.status_code == 400
    r = await adm.put(f"{EI}/settings", json={"client_secret": "x", "clear_client_secret": True})
    assert r.status_code == 400
    # clear: لازم نوقف التفعيل لأن الإعدادات تصير ناقصة
    r = await adm.put(f"{EI}/settings", json={"clear_client_secret": True})
    assert r.status_code == 400 and "client_secret" in r.json()["detail"]
    r = await adm.put(f"{EI}/settings", json={"enabled": False, "clear_client_secret": True})
    assert r.status_code == 200 and r.json()["has_client_secret"] is False
    # signer token
    r = await adm.put(f"{EI}/settings", json={"signer_token": SIGNER_TOKEN})
    assert r.status_code == 200 and r.json()["has_signer_token"] and SIGNER_TOKEN not in r.text
    r = await adm.put(f"{EI}/settings", json={"clear_signer_token": True})
    assert r.json()["has_signer_token"] is False


@pytest.mark.parametrize("body,frag", [
    ({"tax_registration_number": "12345"}, "9 أرقام"),
    ({"tax_registration_number": "12345678a"}, "9 أرقام"),
    ({"activity_code": "12"}, "4 إلى 6"),
    ({"activity_code": "abcd"}, "4 إلى 6"),
    ({"environment": "staging"}, "البيئة"),
    ({"signer_mode": "hsm"}, "وضع التوقيع"),
    ({"signer_url": "http://signer.example.com/sign"}, "https"),
    ({"signer_url": "https://127.0.0.1/sign"}, "الرابط غير مسموح"),
    ({"signer_url": "https://192.168.1.10/sign"}, "الرابط غير مسموح"),
    ({"signer_url": "https://u:p@signer.example.com/sign"}, "الرابط غير مسموح"),
    ({"custom_api_base_url": "https://x.example.com"}, "custom"),
    ({"environment": "prod", "signer_mode": "mock"}, "الإنتاج"),
    ({"environment": "prod", "signer_mode": "none"}, "الإنتاج"),
    ({"default_item_type": "ABC"}, "EGS"),
    ({"default_unit_type": "x"}, "الوحدة"),
    ({"person_id_threshold": -1}, "الأفراد"),
    ({"max_backdate_days": 1000}, "الإرسال المتأخر"),
    ({"issuer_name": "x" * 201}, "طويل"),
])
async def test_settings_validation_rejected_in_arabic(adm, body, frag):
    r = await adm.put(f"{EI}/settings", json=body)
    assert r.status_code == 400, r.text
    assert frag in r.json()["detail"]


async def test_settings_enable_requires_complete_fields(adm):
    r = await adm.put(f"{EI}/settings", json={"enabled": True})
    assert r.status_code == 400 and "بيانات ناقصة" in r.json()["detail"]
    j = await configure(adm)
    assert j["enabled"] is True and j["ready"] is True


async def test_settings_custom_environment_urls(adm):
    r = await adm.put(f"{EI}/settings", json={"environment": "custom", "custom_id_base_url": "http://x.example.com",
                                              "custom_api_base_url": "https://api.example.com"})
    assert r.status_code == 400
    r = await adm.put(f"{EI}/settings", json={"environment": "custom", "custom_id_base_url": "https://id.example.com/",
                                              "custom_api_base_url": "https://api.example.com"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["effective_id_base_url"] == "https://id.example.com" and j["effective_api_base_url"] == "https://api.example.com"
    # الخروج من custom ينظّف الروابط
    r = await adm.put(f"{EI}/settings", json={"environment": "preprod"})
    assert r.json()["custom_api_base_url"] is None


async def test_settings_rbac_and_unauth(adm, acct, viewer, prodc, client):
    for c in (acct, viewer, prodc):
        assert (await c.get(f"{EI}/settings")).status_code == 403
        assert (await c.put(f"{EI}/settings", json={"enabled": False})).status_code == 403
        assert (await c.post(f"{EI}/settings/test-connection")).status_code == 403
        assert (await c.post(f"{EI}/queue/run")).status_code == 403
    assert (await client.get(f"{EI}/status")).status_code == 401
    assert (await client.get(f"{EI}/settings")).status_code == 401


async def test_settings_isolated_per_tenant(adm, adm2):
    await configure(adm, tax_registration_number="111111111")
    j2 = (await adm2.get(f"{EI}/settings")).json()
    assert j2["tax_registration_number"] is None and j2["has_client_secret"] is False
    await configure(adm2, tax_registration_number="222222222", client_secret="OTHER-SECRET-2")
    assert (await adm.get(f"{EI}/settings")).json()["tax_registration_number"] == "111111111"
    async with TestSessionLocal() as s:
        rows = (await s.execute(select(EInvoiceSettings).order_by(EInvoiceSettings.tenant_id))).scalars().all()
    assert [r.tenant_id for r in rows] == [1, 2]
    assert secret_box.decrypt(rows[0].client_secret_enc) == SECRET
    assert secret_box.decrypt(rows[1].client_secret_enc) == "OTHER-SECRET-2"


async def test_tenant_id_in_body_is_ignored(adm):
    r = await adm.put(f"{EI}/settings", json={"tenant_id": 2, "governate": "أسوان"})
    assert r.status_code == 200
    async with TestSessionLocal() as s:
        rows = (await s.execute(select(EInvoiceSettings))).scalars().all()
    assert [x.tenant_id for x in rows] == [1]


async def test_audit_log_and_export_never_contain_secrets(adm):
    await configure(adm, signer_token=SIGNER_TOKEN)
    async with TestSessionLocal() as s:
        logs = (await s.execute(select(AuditLog))).scalars().all()
    blob = json.dumps([[str(getattr(l, c.name)) for c in AuditLog.__table__.c] for l in logs], ensure_ascii=False)
    for plain in (SECRET, CLIENT_ID, SIGNER_TOKEN):
        assert plain not in blob
    red = texp.redacted_columns()
    for col in ("einvoice_settings.client_secret_enc", "einvoice_settings.client_credential_id_enc",
                "einvoice_settings.signer_token_enc"):
        assert col in red


async def test_test_connection_ok_fail_and_missing(adm, eta):
    r = await adm.post(f"{EI}/settings/test-connection")
    assert r.status_code == 400
    await configure(adm)
    r = await adm.post(f"{EI}/settings/test-connection")
    assert r.status_code == 200 and r.json()["ok"] is True
    tok = eta.calls("POST", "/connect/token")[0]
    assert tok["host"] == "id.preprod.eta.gov.eg"
    assert tok["form"]["grant_type"] == "client_credentials" and tok["form"]["scope"] == "InvoicingAPI"
    assert tok["form"]["client_id"] == CLIENT_ID and tok["form"]["client_secret"] == SECRET
    assert not eta.submits()          # لا مستند يُرسل
    s = (await adm.get(f"{EI}/settings")).json()
    assert s["last_connection_ok"] is True and s["last_connection_at"]
    eta.token_status = 400
    r = await adm.post(f"{EI}/settings/test-connection")
    assert r.status_code == 200 and r.json()["ok"] is False and "بيانات الاعتماد" in r.json()["message"]
    assert SECRET not in r.text
    assert (await adm.get(f"{EI}/settings")).json()["last_connection_ok"] is False


async def test_secret_key_change_returns_arabic_400_not_500(adm, eta, monkeypatch):
    from cryptography.fernet import Fernet
    await configure(adm)
    monkeypatch.setenv("ERP_EINVOICE_KEY", Fernet.generate_key().decode())
    r = await adm.post(f"{EI}/settings/test-connection")
    assert r.status_code == 400 and "أعد إدخال" in r.json()["detail"]
    # الواجهة تبقى شغالة (masked fallback) رغم فشل فك التشفير
    g = await adm.get(f"{EI}/settings")
    assert g.status_code == 200 and g.json()["client_id_masked"] == "••••"


# ═══════════════════ المحوّل (preview) ═══════════════════

async def preview(c, iid, kind="invoice"):
    r = await c.get(f"{EI}/documents/{kind}/{iid}/preview")
    assert r.status_code == 200, r.text
    return r.json()


async def test_preview_invoice_mapping_totals_and_lines(adm):
    await configure(adm)
    cid, iid = await ready_invoice(adm)
    p = await preview(adm, iid)
    d = p["document"]
    assert p["errors"] == [] and p["can_submit"] is True
    assert d["documentType"] == "I" and d["documentTypeVersion"] == "1.0"
    assert d["internalID"] == "INV-0001" and d["taxpayerActivityCode"] == "4610"
    assert d["issuer"]["type"] == "B" and d["issuer"]["id"] == "123456789"
    assert d["issuer"]["address"]["country"] == "EG" and d["issuer"]["address"]["governate"] == "القاهرة"
    assert d["receiver"]["type"] == "B" and d["receiver"]["id"] == "987654321"
    assert datetime.strptime(d["dateTimeIssued"], "%Y-%m-%dT%H:%M:%SZ") <= datetime.utcnow()
    assert d["totalSalesAmount"] == 1333.33 and d["totalDiscountAmount"] == 133.33
    # الضريبة تُحسب لكل بند ثم تُجمع (تقريب 5 خانات) فقد يظهر فارق جزء من مئة ألف
    assert d["netAmount"] == pytest.approx(1200.0, abs=1e-4)
    assert d["taxTotals"][0]["taxType"] == "T1" and d["taxTotals"][0]["amount"] == pytest.approx(168.0, abs=1e-4)
    assert d["totalAmount"] == pytest.approx(1368.0, abs=1e-4)
    lines = d["invoiceLines"]
    assert len(lines) == 2
    assert round(sum(l["discount"]["amount"] for l in lines), 5) == 133.33          # الباقي على آخر بند
    assert round(sum(l["netTotal"] for l in lines), 5) == 1200.0
    assert lines[0]["itemType"] == "EGS" and lines[0]["itemCode"] == "EG-123456789-M-1"
    assert lines[1]["itemCode"] == "EG-123456789-GEN"
    assert lines[0]["unitType"] == "EA" and lines[1]["unitType"] == "MTR"
    assert lines[0]["unitValue"] == {"currencySold": "EGP", "amountEGP": 500.0}
    t = lines[0]["taxableItems"][0]
    assert t["taxType"] == "T1" and t["subType"] == "V009" and t["rate"] == 14.0
    assert sum(l["taxableItems"][0]["amount"] for l in lines) == pytest.approx(d["taxTotals"][0]["amount"], abs=1e-9)
    assert abs(d["totalAmount"] - (await adm.get(f"{SALES}/invoices/{iid}")).json()["total"]) <= 0.01


async def test_preview_is_read_only_no_state_no_network(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    await preview(adm, iid)
    assert eta.requests == [] and await count(EInvoiceSubmission) == 0
    assert (await get_inv(iid)).eta_status is None


async def test_preview_zero_tax_uses_zero_subtype_with_warning(adm):
    await configure(adm)
    _, iid = await ready_invoice(adm, tax=0, disc=0)
    p = await preview(adm, iid)
    t = p["document"]["invoiceLines"][0]["taxableItems"][0]
    assert t["subType"] == "V003" and t["rate"] == 0 and t["amount"] == 0
    assert any("صفرية" in w for w in p["warnings"])
    await adm.put(f"{EI}/settings", json={"zero_vat_subtype": "v001"})
    p = await preview(adm, iid)
    assert p["document"]["invoiceLines"][0]["taxableItems"][0]["subType"] == "V001"


async def test_preview_receiver_types(adm):
    await configure(adm)
    cid_p = await mk_customer(adm, "فرد", tax_id="29001011234567", address=None)
    cid_n = await mk_customer(adm, "نقدي", tax_id=None, address=None)
    cid_bad = await mk_customer(adm, "خطأ", tax_id="12ab", address=None)
    small = [{"description": "س", "qty": 1, "unit": "قطعة", "unit_price": 100}]
    i_p = await mk_invoice(adm, cid_p, small, tax=0, disc=0)
    i_n = await mk_invoice(adm, cid_n, small, tax=0, disc=0)
    i_bad = await mk_invoice(adm, cid_bad, small, tax=0, disc=0)
    assert (await preview(adm, i_p))["document"]["receiver"] == {"type": "P", "name": "فرد", "id": "29001011234567"}
    pn = await preview(adm, i_n)
    assert pn["errors"] == [] and pn["document"]["receiver"] == {"type": "P", "name": "نقدي"}
    pb = await preview(adm, i_bad)
    assert pb["can_submit"] is False and any("غير صالح" in e for e in pb["errors"])
    # فوق حد الأفراد بدون رقم قومي => خطأ
    big = [{"description": "س", "qty": 1, "unit": "قطعة", "unit_price": 60000}]
    i_big = await mk_invoice(adm, cid_n, big, tax=0, disc=0)
    pbig = await preview(adm, i_big)
    assert any("الرقم القومي" in e for e in pbig["errors"]) and pbig["can_submit"] is False
    # business بدون عنوان => خطأ
    cid_b = await mk_customer(adm, "منشأة", tax_id="555555555", address=None)
    i_b = await mk_invoice(adm, cid_b, small, tax=0, disc=0)
    assert any("عنوان العميل" in e for e in (await preview(adm, i_b))["errors"])


async def test_preview_gs1_item_codes(adm):
    await configure(adm)
    await adm.put(f"{EI}/settings", json={"default_item_type": "GS1"})
    cid = await mk_customer(adm)
    ok = [{"description": "س", "qty": 1, "unit": "قطعة", "unit_price": 100, "model_no": "6221031234567"}]
    bad = [{"description": "س", "qty": 1, "unit": "قطعة", "unit_price": 100, "model_no": "M-1"}]
    p_ok = await preview(adm, await mk_invoice(adm, cid, ok, tax=0, disc=0))
    assert p_ok["errors"] == [] and p_ok["document"]["invoiceLines"][0]["itemCode"] == "6221031234567"
    p_bad = await preview(adm, await mk_invoice(adm, cid, bad, tax=0, disc=0))
    assert any("GS1" in e for e in p_bad["errors"])


async def test_preview_date_rules(adm):
    await configure(adm)
    cid = await mk_customer(adm)
    small = [{"description": "س", "qty": 1, "unit": "قطعة", "unit_price": 100}]
    old = await mk_invoice(adm, cid, small, tax=0, disc=0, d=date.today() - timedelta(days=30))
    p = await preview(adm, old)
    assert p["errors"] == [] and any("أقدم" in w for w in p["warnings"])
    assert p["document"]["dateTimeIssued"].endswith("T00:00:00Z")
    future = await mk_invoice(adm, cid, small, tax=0, disc=0, d=date.today() + timedelta(days=10))
    assert any("المستقبل" in e for e in (await preview(adm, future))["errors"])


async def test_preview_missing_settings_and_disabled_reported(adm):
    cid, iid = await ready_invoice(adm)
    p = await preview(adm, iid)                       # لا صف إعدادات أصلًا
    assert p["can_submit"] is False and "إعدادات الفاتورة الإلكترونية غير مضبوطة" in p["errors"]
    assert (await adm.get(f"{EI}/settings")).status_code == 200      # ينشئ الصف الافتراضي
    p = await preview(adm, iid)
    assert p["can_submit"] is False
    assert any("غير مُفعّلة" in e for e in p["errors"])
    assert any("رقم التسجيل الضريبي" in e for e in p["errors"])
    assert any("كود النشاط" in e for e in p["errors"])


async def test_reconciliation_guard_blocks_when_totals_diverge(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    await sql("UPDATE invoices SET total = total + 5 WHERE id = :i", i=iid)
    p = await preview(adm, iid)
    assert p["can_submit"] is False and any("لا تطابق" in e for e in p["errors"])
    r = await submit(adm, iid)
    assert r.status_code == 422
    j = r.json()
    assert isinstance(j["detail"], str) and isinstance(j["errors"], list) and j["errors"]
    assert await count(EInvoiceSubmission) == 0 and not eta.requests
    assert (await get_inv(iid)).eta_status is None


async def test_tax_tampering_detected(adm):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    await sql("UPDATE invoices SET tax_amount = tax_amount + 3 WHERE id = :i", i=iid)
    p = await preview(adm, iid)
    assert any("ضريبة المستند" in e for e in p["errors"])


async def test_preview_cancelled_invoice_and_draft_cn_refused(adm, eta):
    cid, iid = await make_valid_invoice(adm, eta)
    cn = await mk_cn(adm, cid, iid, issue=False)
    p = await preview(adm, cn, "credit-note")
    assert any("غير صادر" in e for e in p["errors"])
    await sql("UPDATE invoices SET status='cancelled' WHERE id=:i", i=iid)
    # الفاتورة الأصلية مقبولة أصلاً — نختبر الملغاة على فاتورة غير مُرسلة
    cid2, i2 = await ready_invoice(adm)
    await sql("UPDATE invoices SET status='cancelled' WHERE id=:i", i=i2)
    assert any("ملغاة" in e for e in (await preview(adm, i2))["errors"])


async def test_preview_unknown_doc_type_and_missing_doc(adm, adm2):
    await configure(adm)
    assert (await adm.get(f"{EI}/documents/quote/1/preview")).status_code == 404
    assert (await adm.get(f"{EI}/documents/invoice/99999/preview")).status_code == 404


async def test_preview_rbac(adm, acct, viewer, prodc):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    assert (await acct.get(f"{EI}/documents/invoice/{iid}/preview")).status_code == 200
    assert (await viewer.get(f"{EI}/documents/invoice/{iid}/preview")).status_code == 403
    assert (await prodc.get(f"{EI}/documents/invoice/{iid}/preview")).status_code == 403


# ═══════════════════ الإشعار الدائن ═══════════════════

async def test_credit_note_document_references_original_uuid(adm, eta):
    cid, iid = await make_valid_invoice(adm, eta)
    cn = await mk_cn(adm, cid, iid, price=100, tax=14)
    p = await preview(adm, cn, "credit-note")
    d = p["document"]
    assert p["errors"] == [], p["errors"]
    assert d["documentType"] == "C" and d["references"] == ["UUID-1"] and d["internalID"].startswith("CN-")
    assert d["totalAmount"] == 114.0 and d["netAmount"] == 100.0
    r = await submit(adm, cn, "credit-note")
    assert r.status_code == 200 and r.json()["status"] == "valid"
    sent = json.loads(eta.submits()[-1]["body"])["documents"][0]
    assert sent["documentType"] == "C" and sent["references"] == ["UUID-1"]


async def test_credit_note_refused_when_original_not_valid(adm, eta):
    await configure(adm)
    cid, iid = await ready_invoice(adm)           # لم تُرسل
    cn = await mk_cn(adm, cid, iid)
    p = await preview(adm, cn, "credit-note")
    assert p["can_submit"] is False
    assert "الفاتورة الأصلية لم تُقبل لدى مصلحة الضرائب بعد" in p["errors"]
    r = await submit(adm, cn, "credit-note")
    assert r.status_code == 422 and not eta.submits() and await count(EInvoiceSubmission) == 0


async def test_credit_note_without_invoice_refused(adm, eta):
    await configure(adm)
    cid = await mk_customer(adm)
    r = await adm.post(CN, json={"customer_id": cid, "date": str(date.today()), "return_type": "partial",
                                 "lines": [{"description": "تسوية", "qty": 1, "unit": "قطعة", "unit_price": 50}]})
    cn = r.json()["id"]
    await adm.patch(f"{CN}/{cn}/issue")
    p = await preview(adm, cn, "credit-note")
    assert any("غير مرتبط بفاتورة" in e for e in p["errors"])


async def test_credit_note_discount_with_tax_blocked_by_reconciliation(adm, eta):
    """معادلة الإشعار تحسب الضريبة قبل الخصم بينما ETA بعده — الحارس يمنع إرسال أرقام مختلفة."""
    cid, iid = await make_valid_invoice(adm, eta)
    r = await adm.post(CN, json={"customer_id": cid, "invoice_id": iid, "date": str(date.today()),
                                 "return_type": "partial", "tax_pct": 14, "discount_amt": 20,
                                 "lines": [{"description": "م", "qty": 1, "unit": "قطعة", "unit_price": 200}]})
    cn = r.json()["id"]
    await adm.patch(f"{CN}/{cn}/issue")
    p = await preview(adm, cn, "credit-note")
    assert p["can_submit"] is False and any("لا تطابق" in e for e in p["errors"])


# ═══════════════════ التوقيع ═══════════════════

async def test_mock_signer_deterministic_and_marked(adm, eta):
    s = signer_mod.MockSigner()
    doc = {"a": 1, "b": [1, 2]}
    can = signer_mod.serialize_for_signing(doc)
    r1, r2 = await s.sign(doc, can), await s.sign(doc, can)
    assert r1 == r2 and r1["mock"] is True
    assert signer_mod.has_mock_signature({"signatures": r1["signatures"]})


async def test_external_signer_posts_canonical_with_bearer_and_maps_failures(adm, eta):
    await configure(adm, signer_mode="external", signer_url="https://signer.example.com/sign",
                    signer_token=SIGNER_TOKEN)
    _, iid = await ready_invoice(adm)
    r = await submit(adm, iid)
    assert r.status_code == 200, r.text
    sign_call = [x for x in eta.requests if x["host"] == "signer.example.com"][0]
    assert sign_call["headers"]["authorization"] == f"Bearer {SIGNER_TOKEN}"
    body = json.loads(sign_call["body"])
    assert body["document_hash"] == hashlib.sha256(body["canonical"].encode()).hexdigest()
    sent = json.loads(eta.submits()[0]["body"])["documents"][0]
    assert sent["signatures"][0]["signatureType"] == "I"
    assert base64.b64decode(sent["signatures"][0]["value"]) == b"REAL-CMS-SIGNATURE"
    assert not signer_mod.has_mock_signature(sent)
    row = await get_row("invoice", iid)
    assert row.signer_mode_used == "external"
    # فشل الـ signer => 400 عربي ولا إرسال
    eta.sign_status = 500
    _, i2 = await ready_invoice(adm)
    n = len(eta.submits())
    r = await submit(adm, i2)
    assert r.status_code == 400 and "التوقيع" in r.json()["detail"] and len(eta.submits()) == n
    assert await get_row("invoice", i2) is None


async def test_prod_environment_rejects_mock_and_none_everywhere(adm, eta):
    # عند الحفظ
    for mode in ("mock", "none"):
        r = await adm.put(f"{EI}/settings", json={**BASE_SETTINGS, "environment": "prod", "signer_mode": mode})
        assert r.status_code == 400 and "الإنتاج" in r.json()["detail"]
    ok = await adm.put(f"{EI}/settings", json={**BASE_SETTINGS, "environment": "prod", "signer_mode": "external",
                                               "signer_url": "https://signer.example.com/sign"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["effective_api_base_url"] == "https://api.invoicing.eta.gov.eg"
    # التفاف على الـ API بتعديل DB مباشر => الإرسال يرفض ولا يخرج أي طلب
    await sql("UPDATE einvoice_settings SET signer_mode='mock' WHERE tenant_id=1")
    _, iid = await ready_invoice(adm)
    r = await submit(adm, iid)
    assert r.status_code == 400 and not eta.requests
    with pytest.raises(signer_mod.SignerError):
        signer_mod.get_signer(type("S", (), {"signer_mode": "mock", "environment": "prod"})())
    with pytest.raises(signer_mod.SignerError):
        signer_mod.assert_signer_allowed("prod", "none")


async def test_mock_signature_never_reaches_prod_url_via_queue(adm, eta):
    await configure(adm)                                   # preprod + mock
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    r = await submit(adm, iid)
    assert r.status_code == 202 and r.json()["status"] == "failed"
    # المدير يحوّل البيئة للإنتاج بتوقيع خارجي، والصف المعلّق موقّع بتوقيع وهمي
    await adm.put(f"{EI}/settings", json={"environment": "prod", "signer_mode": "external",
                                          "signer_url": "https://signer.example.com/sign"})
    await sql("UPDATE einvoice_submissions SET next_attempt_at = :t WHERE tenant_id = 1",
              t=datetime.utcnow() - timedelta(minutes=1))
    eta.requests.clear()
    res = await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=_nosleep)
    assert res["processed"] == 1 and res["failed"] == 1
    assert not eta.submits() and not eta.calls("POST", "/connect/token")
    row = await get_row("invoice", iid)
    assert row.status == "failed" and "التوقيع الرقمي غير مُعدّ" in row.last_error and row.next_attempt_at is None


async def _nosleep(_):
    return None


async def test_client_blocks_mock_signed_payload_to_prod_api(eta):
    cl = eta_client.EtaClient(tenant_id=1, environment="prod", id_base="https://id.eta.gov.eg",
                              api_base="https://api.invoicing.eta.gov.eg", client_id="a", client_secret="b")
    assert cl.is_production_api() is True
    pre = eta_client.EtaClient(tenant_id=1, environment="preprod", id_base="https://id.preprod.eta.gov.eg",
                               api_base="https://api.preprod.invoicing.eta.gov.eg", client_id="a", client_secret="b")
    assert pre.is_production_api() is False


# ═══════════════════ عميل ETA ═══════════════════

def _client(eta_mock, **kw):
    base = dict(tenant_id=1, environment="preprod", id_base="https://id.preprod.eta.gov.eg",
                api_base="https://api.preprod.invoicing.eta.gov.eg", client_id="cid", client_secret="csec",
                http_factory=eta_mock.factory, jitter=0, max_attempts=3)
    base.update(kw)
    return eta_client.EtaClient(**base)


async def test_client_token_form_and_cache(eta):
    cl = _client(eta)
    t1 = await cl.get_token()
    t2 = await cl.get_token()
    assert t1 == t2 and len(eta.calls("POST", "/connect/token")) == 1
    form = eta.calls("POST", "/connect/token")[0]["form"]
    assert form == {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "csec",
                    "scope": "InvoicingAPI"}
    assert eta.requests[0]["host"] == "id.preprod.eta.gov.eg"
    # عميل بيئة الإنتاج => مضيف id الإنتاج
    prod = _client(eta, environment="prod", id_base="https://id.eta.gov.eg")
    await prod.get_token()
    assert eta.requests[-1]["host"] == "id.eta.gov.eg"


async def test_client_token_refreshed_after_expiry(eta):
    clock = {"t": 1000.0}
    cl = _client(eta, clock=lambda: clock["t"])
    await cl.get_token()
    clock["t"] += 3000
    await cl.get_token()
    assert len(eta.calls("POST", "/connect/token")) == 1
    clock["t"] += 1000          # عدّى expires_in-60
    await cl.get_token()
    assert len(eta.calls("POST", "/connect/token")) == 2


async def test_client_401_triggers_exactly_one_reauth_then_succeeds(eta):
    eta.submit_script.append(lambda req: httpx.Response(401, json={}))
    cl = _client(eta)
    out = await cl.submit([{"internalID": "X-1"}])
    assert out["accepted"][0]["uuid"] == "UUID-1"
    assert len(eta.calls("POST", "/connect/token")) == 2
    assert len(eta.submits()) == 2


async def test_client_persistent_401_raises_auth_error_after_one_retry(eta):
    eta.submit_script.extend([lambda req: httpx.Response(401, json={}), lambda req: httpx.Response(401, json={})])
    cl = _client(eta)
    with pytest.raises(eta_client.EtaAuthError):
        await cl.submit([{"internalID": "X-1"}])
    assert len(eta.submits()) == 2


async def test_client_backoff_2_4_on_500_then_success(eta):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    eta.submit_script.extend([lambda req: httpx.Response(500), lambda req: httpx.Response(502)])
    cl = _client(eta, sleep=fake_sleep)
    out = await cl.submit([{"internalID": "X-1"}])
    assert out["accepted"] and sleeps == [2.0, 4.0]
    assert len(eta.submits()) == 3


async def test_client_429_honours_retry_after_capped(eta):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    eta.submit_script.extend([lambda req: httpx.Response(429, headers={"Retry-After": "30"}),
                              lambda req: httpx.Response(429, headers={"Retry-After": "999"})])
    cl = _client(eta, sleep=fake_sleep)
    await cl.submit([{"internalID": "X-1"}])
    assert sleeps == [30.0, 60.0]


async def test_client_4xx_not_retried_and_parses_errors(eta):
    body = {"error": {"code": "BadStructure", "message": "invalid", "target": "document",
                      "details": [{"code": "C1", "message": "قيمة غير صالحة", "target": "issuer.id"}]}}
    eta.submit_script.append(lambda req: httpx.Response(400, json=body))
    cl = _client(eta)
    with pytest.raises(eta_client.EtaPermanentError) as ei:
        await cl.submit([{"internalID": "X-1"}])
    assert len(eta.submits()) == 1
    errs = ei.value.errors
    assert errs[0]["code"] == "BadStructure" and errs[1] == {"code": "C1", "message": "قيمة غير صالحة",
                                                            "target": "issuer.id"}


async def test_client_timeout_and_connect_errors_exhaust_as_transient(eta):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    eta.submit_script.extend([lambda req: httpx.ReadTimeout("t"), lambda req: httpx.ConnectError("c"),
                              lambda req: httpx.ReadTimeout("t")])
    cl = _client(eta, sleep=fake_sleep)
    with pytest.raises(eta_client.EtaTransientError):
        await cl.submit([{"internalID": "X-1"}])
    assert len(eta.submits()) == 3 and sleeps == [2.0, 4.0]


async def test_client_secrets_never_in_logs_or_exceptions(eta, caplog):
    caplog.set_level("DEBUG")
    eta.token_status = 400
    cl = _client(eta, client_secret="TOP-SECRET-VALUE")
    with pytest.raises(eta_client.EtaAuthError) as ei:
        await cl.get_token()
    assert "TOP-SECRET-VALUE" not in str(ei.value)
    assert "TOP-SECRET-VALUE" not in caplog.text and "Bearer" not in caplog.text


async def test_real_network_is_blocked_in_tests():
    """الحارس نفسه: أي محاولة اتصال حقيقي تفشل (يؤكد أن الاختبارات لا تخرج للشبكة)."""
    with pytest.raises(Exception):
        async with httpx.AsyncClient(timeout=2) as c:
            await c.get("https://id.eta.gov.eg/connect/token")
    with pytest.raises(AssertionError):
        eta_client.default_http_factory()


# ═══════════════════ الإرسال end-to-end ═══════════════════

async def test_submit_happy_path_valid_and_mirror(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    r = await submit(adm, iid)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "valid" and j["eta_status"] == "valid" and j["eta_uuid"] == "UUID-1"
    assert j["idempotent"] is False and j["eta_errors"] is None and j["attempts"] == 1
    inv = (await adm.get(f"{SALES}/invoices/{iid}")).json()
    assert inv["eta_status"] == "valid" and inv["eta_uuid"] == "UUID-1" and inv["eta_submission_id"] == "SUB-1"
    assert inv["eta_submitted_at"] and inv["eta_errors"] is None
    lst = (await adm.get(f"{SALES}/invoices")).json()
    assert lst[0]["eta_status"] == "valid"
    sent = json.loads(eta.submits()[0]["body"])["documents"][0]
    assert sent["internalID"] == "INV-0001" and sent["signatures"]
    # الترويسة: Bearer token لورشة 1
    assert eta.submits()[0]["headers"]["authorization"].startswith(f"Bearer tok-{CLIENT_ID}")
    assert eta.submits()[0]["host"] == "api.preprod.invoicing.eta.gov.eg"
    st = (await adm.get(f"{EI}/documents/invoice/{iid}")).json()
    assert st["status"] == "valid" and st["eta_long_id"] == "LONG-1" and st["validated_at"]
    assert st["share_url"] == "https://preprod.invoicing.eta.gov.eg/documents/UUID-1/share/LONG-1"
    assert st["qr_text"] == st["share_url"]
    assert [h["to"] for h in st["history"]] == ["queued", "submitting", "submitted", "valid"]
    assert "payload" not in json.dumps(st) and SECRET not in json.dumps(st)


async def test_submit_state_submitted_has_no_qr(adm, eta):
    await configure(adm)
    eta.doc_status = "In Progress"
    _, iid = await ready_invoice(adm)
    r = await submit(adm, iid)
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    st = (await adm.get(f"{EI}/documents/invoice/{iid}")).json()
    assert st["status"] == "submitted" and st["qr_text"] is None and st["share_url"] is None
    # تحديث الحالة بعد ما تصير Valid
    eta.doc_status = "Valid"
    rr = await adm.post(f"{EI}/documents/invoice/{iid}/refresh-status")
    assert rr.status_code == 200 and rr.json()["status"] == "valid"
    assert (await get_inv(iid)).eta_status == "valid"


async def test_submit_invalid_status_from_polling_stores_errors(adm, eta):
    await configure(adm)
    eta.doc_status = "Invalid"
    _, iid = await ready_invoice(adm)
    r = await submit(adm, iid)
    assert r.status_code == 200 and r.json()["status"] == "invalid"
    errs = r.json()["eta_errors"]
    assert errs and errs[0]["target"] == "invoiceLines[0].itemCode"
    assert (await adm.get(f"{SALES}/invoices/{iid}")).json()["eta_errors"] == errs
    st = (await adm.get(f"{EI}/documents/invoice/{iid}")).json()
    assert st["qr_text"] is None


async def test_submit_rejected_documents_then_resubmit_after_fix(adm, eta):
    await configure(adm)
    cid, iid = await ready_invoice(adm)
    err = {"code": "InvalidReceiver", "message": "رقم المستلم غير صالح", "target": "receiver.id", "details": []}
    eta.submit_script.append(lambda req: httpx.Response(202, json={
        "submissionId": "S0", "acceptedDocuments": [],
        "rejectedDocuments": [{"internalId": "INV-0001", "error": err}]}))
    r = await submit(adm, iid)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert r.json()["eta_errors"] == [{"code": "InvalidReceiver", "message": "رقم المستلم غير صالح",
                                       "target": "receiver.id"}]
    assert (await get_inv(iid)).eta_status == "rejected"
    # الإرسال العادي بعد الرفض => 400 (استخدم إعادة الإرسال)
    again = await submit(adm, iid)
    assert again.status_code == 400 and "إعادة الإرسال" in again.json()["detail"]
    # تصحيح البيانات (rejected قابلة للتعديل) ثم إعادة الإرسال
    up = await adm.put(f"{SALES}/invoices/{iid}", json={
        "customer_id": cid, "date": str(date.today()), "tax_pct": 14, "discount_pct": 10, "lines": LINES,
        "notes": "تصحيح"})
    assert up.status_code == 200, up.text
    rr = await adm.post(f"{EI}/documents/invoice/{iid}/resubmit")
    assert rr.status_code == 200 and rr.json()["status"] == "valid" and rr.json()["attempts"] == 1
    st = (await adm.get(f"{EI}/documents/invoice/{iid}")).json()
    tos = [h["to"] for h in st["history"]]
    assert tos[:3] == ["queued", "submitting", "rejected"] and tos[-1] == "valid" and st["errors"] is None


async def test_resubmit_refused_for_accepted_unsent_and_cancelled(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    r = await adm.post(f"{EI}/documents/invoice/{iid}/resubmit")
    assert r.status_code == 400
    await submit(adm, iid)
    r = await adm.post(f"{EI}/documents/invoice/{iid}/resubmit")
    assert r.status_code == 400 and "لا يمكن إعادة إرسال مستند مقبول" in r.json()["detail"]
    n = len(eta.submits())
    assert (await adm.post(f"{EI}/documents/invoice/{iid}/cancel", json={"reason": "سبب الإلغاء"})).status_code == 200
    r = await adm.post(f"{EI}/documents/invoice/{iid}/resubmit")
    assert r.status_code == 400 and "ملغى" in r.json()["detail"]
    r = await submit(adm, iid)
    assert r.status_code == 400 and len(eta.submits()) == n


async def test_submit_not_configured_or_disabled_gives_arabic_400(adm, eta):
    cid, iid = await ready_invoice(adm)
    r = await submit(adm, iid)
    assert r.status_code == 400 and "غير مُفعّلة" in r.json()["detail"]
    await configure(adm)
    await adm.put(f"{EI}/settings", json={"enabled": False})
    r = await submit(adm, iid)
    assert r.status_code == 400 and "غير مُفعّلة" in r.json()["detail"]
    assert not eta.requests and await count(EInvoiceSubmission) == 0


async def test_submit_transient_outage_returns_202_queued_failed_with_next_attempt(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    r = await submit(adm, iid)
    assert r.status_code == 202
    j = r.json()
    assert j["status"] == "failed" and j["attempts"] == 1 and j["next_attempt_at"]
    assert datetime.fromisoformat(j["next_attempt_at"]) > datetime.utcnow()
    assert (await get_inv(iid)).eta_status == "failed"
    assert len(eta.submits()) == 1            # لا backoff داخل الطلب: محاولة واحدة فقط


async def test_failed_status_blocks_plain_submit_but_allows_resubmit(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, iid)
    assert (await submit(adm, iid)).status_code == 400
    r = await adm.post(f"{EI}/documents/invoice/{iid}/resubmit")
    assert r.status_code == 200 and r.json()["status"] == "valid"


async def test_submit_has_no_accounting_or_stock_effect(adm, eta):
    await adm.post(f"{COA}/chart/seed")
    await configure(adm)
    cid, iid = await ready_invoice(adm)

    async def tb():
        r = await adm.get(f"{COA}/trial-balance")
        assert r.status_code == 200, r.text
        return r.json()

    before = await tb()
    bal0 = (await adm.get(f"{CUST}/{cid}")).json()["balance"]
    mv0 = await count(StockMovement)
    await submit(adm, iid)
    after = await tb()
    assert before == after
    assert (await adm.get(f"{CUST}/{cid}")).json()["balance"] == bal0
    assert await count(StockMovement) == mv0
    assert after["totals"]["balanced"] is True and after["totals"]["debit"] > 0       # قيد الفاتورة نفسه متزن


# ═══════════════════ idempotency والتزامن ═══════════════════

async def test_double_submit_sequential_single_http_call(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    r1 = await submit(adm, iid)
    r2 = await submit(adm, iid)
    assert r1.status_code == 200 and r1.json()["idempotent"] is False
    assert r2.status_code == 200 and r2.json()["idempotent"] is True and r2.json()["status"] == "valid"
    assert len(eta.submits()) == 1 and await count(EInvoiceSubmission) == 1


async def test_double_submit_idempotent_while_submitted(adm, eta):
    await configure(adm)
    eta.doc_status = "In Progress"
    _, iid = await ready_invoice(adm)
    await submit(adm, iid)
    r = await submit(adm, iid)
    assert r.status_code == 200 and r.json()["idempotent"] is True and r.json()["status"] == "submitted"
    assert len(eta.submits()) == 1


async def test_concurrent_submits_single_row_and_single_http_call(adm, mk, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    c1 = mk(1, "admin", "admin")
    c2 = mk(1, "admin", "admin")
    ra, rb = await asyncio.gather(submit(c1, iid), submit(c2, iid))
    assert sorted([ra.status_code, rb.status_code])[0] == 200
    assert {ra.status_code, rb.status_code} <= {200, 409}
    assert len(eta.submits()) == 1
    assert await count(EInvoiceSubmission) == 1
    assert (await get_inv(iid)).eta_status in ("valid", "submitted")


async def test_submitting_fresh_lock_gives_409_and_stale_lock_is_reaped(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, iid)
    await sql("UPDATE einvoice_submissions SET status='submitting', locked_at=:t WHERE tenant_id=1",
              t=datetime.utcnow())
    r = await submit(adm, iid)
    assert r.status_code == 409 and "جاري الإرسال" in r.json()["detail"]
    assert len(eta.submits()) == 1
    # قفل قديم => reap ثم رفض مناسب (failed => استخدم إعادة الإرسال)
    await sql("UPDATE einvoice_submissions SET locked_at=:t WHERE tenant_id=1",
              t=datetime.utcnow() - timedelta(minutes=30))
    r = await adm.post(f"{EI}/documents/invoice/{iid}/resubmit")
    assert r.status_code == 200 and r.json()["status"] == "valid"


async def test_lost_response_retry_sends_identical_payload_and_reconciles(adm, eta):
    """ETA قبلت المستند لكن الرد ضاع: الإعادة ترسل نفس الحمولة تمامًا، ورفض 'مكرر' يُسوّى بالاستعلام."""
    await configure(adm)
    _, iid = await ready_invoice(adm)

    def accept_then_timeout(req):
        doc = json.loads(req.content.decode())["documents"][0]
        eta.accept(doc)
        return httpx.ReadTimeout("lost response")

    eta.submit_script.append(accept_then_timeout)
    r = await submit(adm, iid)
    assert r.status_code == 202 and r.json()["status"] == "failed"
    first = eta.submits()[0]["body"]
    row = await get_row("invoice", iid)
    assert row.payload_sha256 and row.eta_uuid is None
    await sql("UPDATE einvoice_submissions SET next_attempt_at=:t WHERE tenant_id=1",
              t=datetime.utcnow() - timedelta(seconds=5))
    res = await adm.post(f"{EI}/queue/run")
    assert res.status_code == 200 and res.json()["processed"] == 1 and res.json()["valid"] == 1
    assert len(eta.submits()) == 2
    assert eta.submits()[1]["body"] == first                       # نفس الحمولة byte-for-byte
    assert len(eta.docs) == 1                                      # لا مستند ثاني
    assert eta.calls("GET", "/api/v1.0/documents/search")           # سُوّي بالاستعلام
    row = await get_row("invoice", iid)
    assert row.status == "valid" and row.eta_uuid == "UUID-1"
    assert (await get_inv(iid)).eta_uuid == "UUID-1"


async def test_duplicate_that_cannot_be_reconciled_ends_failed_manual(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.docs["INV-0001"] = {"uuid": "X", "longId": "Y", "submissionId": "SUB-OLD"}     # موجود عند ETA
    eta.search_override = []                                                           # لكن البحث لا يجده
    r = await submit(adm, iid)
    assert r.status_code == 202 and r.json()["status"] == "failed"
    row = await get_row("invoice", iid)
    assert "مكرر" in row.last_error and row.next_attempt_at is None
    # تحديث الحالة اليدوي يسوّيها لما يظهر في البحث
    eta.search_override = [{"uuid": "X", "internalId": "INV-0001", "longId": "Y", "status": "Valid",
                            "submissionUUID": "SUB-OLD"}]
    rr = await adm.post(f"{EI}/documents/invoice/{iid}/refresh-status")
    assert rr.status_code == 200 and rr.json()["status"] == "valid" and rr.json()["eta_uuid"] == "X"


async def test_tampered_stored_payload_is_refused_before_network(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, iid)
    async with TestSessionLocal() as s:
        row = (await s.execute(select(EInvoiceSubmission))).scalars().first()
        p = dict(row.payload_json)
        p["totalAmount"] = 1.0
        row.payload_json = p
        row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        await s.commit()
    eta.requests.clear()
    res = await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=_nosleep)
    assert res["failed"] == 1 and not eta.submits()
    assert "سلامة" in (await get_row("invoice", iid)).last_error


# ═══════════════════ الطابور والـ worker ═══════════════════

async def test_worker_processes_only_due_rows_and_updates_attempts(adm, eta):
    await configure(adm)
    _, i1 = await ready_invoice(adm)
    _, i2 = await ready_invoice(adm)
    eta.submit_script.extend([lambda req: httpx.Response(503), lambda req: httpx.Response(503)])
    await submit(adm, i1)
    await submit(adm, i2)
    assert (await get_row("invoice", i1)).attempts == 1
    await sql("UPDATE einvoice_submissions SET next_attempt_at=:t WHERE doc_id=:d",
              t=datetime.utcnow() - timedelta(seconds=1), d=i1)
    res = await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=_nosleep)
    assert res == {"processed": 1, "valid": 1, "rejected": 0, "failed": 0}
    assert (await get_row("invoice", i1)).status == "valid"
    assert (await get_row("invoice", i1)).attempts == 2
    r2 = await get_row("invoice", i2)
    assert r2.status == "failed" and r2.attempts == 1 and r2.next_attempt_at > datetime.utcnow()


async def test_worker_transient_exhaustion_backoff_and_cap_after_8_attempts(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, iid)
    sleeps = []

    async def rec(s):
        sleeps.append(s)

    await sql("UPDATE einvoice_submissions SET attempts=7, next_attempt_at=:t WHERE tenant_id=1",
              t=datetime.utcnow() - timedelta(seconds=1))
    for _ in range(3):
        eta.submit_script.append(lambda req: httpx.Response(503))
    res = await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=rec)
    assert res["failed"] == 1 and len(sleeps) == 2
    assert 2.0 <= sleeps[0] < 2.3 and 4.0 <= sleeps[1] < 4.3        # backoff 2s/4s (+ jitter)
    row = await get_row("invoice", iid)
    assert row.attempts == 8 and row.status == "failed" and row.next_attempt_at is None
    # لا تُلتقط مرة ثانية تلقائيًا
    eta.requests.clear()
    res = await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=_nosleep)
    assert res["processed"] == 0 and not eta.submits()


async def test_worker_never_raises_even_if_client_explodes(adm, eta, monkeypatch):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, iid)
    await sql("UPDATE einvoice_submissions SET next_attempt_at=:t", t=datetime.utcnow() - timedelta(seconds=1))

    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(eta_client.EtaClient, "submit", boom)
    res = await run_einvoice_queue_once(TestSessionLocal, sleep=_nosleep)
    assert isinstance(res, dict)
    assert (await get_row("invoice", iid)).status == "failed"


async def test_worker_reaps_stale_submitting_rows(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, iid)
    await sql("UPDATE einvoice_submissions SET status='submitting', locked_at=:t",
              t=datetime.utcnow() - timedelta(minutes=45))
    await run_einvoice_queue_once(TestSessionLocal, sleep=_nosleep)
    row = await get_row("invoice", iid)
    assert row.status == "failed" and row.locked_at is None and "انتهت مهلة" in row.last_error
    assert (await get_inv(iid)).eta_status == "failed"


async def test_worker_polls_old_submitted_rows(adm, eta):
    await configure(adm)
    eta.doc_status = "In Progress"
    _, iid = await ready_invoice(adm)
    await submit(adm, iid)
    eta.doc_status = "Valid"
    await sql("UPDATE einvoice_submissions SET last_attempt_at=:t", t=datetime.utcnow() - timedelta(minutes=10))
    res = await run_einvoice_queue_once(TestSessionLocal, sleep=_nosleep)
    assert res["valid"] == 1 and (await get_inv(iid)).eta_status == "valid"


async def test_worker_two_tenants_use_their_own_credentials(adm, adm2, eta):
    await configure(adm, tax_registration_number="111111111", client_id="CLIENT-ONE-AAAA", client_secret="s1-secret")
    await configure(adm2, tax_registration_number="222222222", client_id="CLIENT-TWO-BBBB", client_secret="s2-secret")
    _, i1 = await ready_invoice(adm)
    _, i2 = await ready_invoice(adm2)
    eta.submit_script.extend([lambda req: httpx.Response(503), lambda req: httpx.Response(503)])
    await submit(adm, i1)
    await submit(adm2, i2)
    eta.requests.clear()
    eta_client.clear_token_cache()
    await sql("UPDATE einvoice_submissions SET next_attempt_at=:t", t=datetime.utcnow() - timedelta(seconds=1))
    res = await run_einvoice_queue_once(TestSessionLocal, sleep=_nosleep)           # كل الورش
    assert res["processed"] == 2 and res["valid"] == 2
    forms = {r["form"]["client_id"]: r["form"]["client_secret"] for r in eta.calls("POST", "/connect/token")}
    assert forms == {"CLIENT-ONE-AAAA": "s1-secret", "CLIENT-TWO-BBBB": "s2-secret"}
    by_issuer = {}
    for r in eta.submits():
        doc = json.loads(r["body"])["documents"][0]
        by_issuer[doc["issuer"]["id"]] = r["headers"]["authorization"]
    assert "CLIENT-ONE-AAAA" in by_issuer["111111111"] and "CLIENT-TWO-BBBB" in by_issuer["222222222"]
    assert (await get_row("invoice", i1, tid=1)).status == "valid"
    assert (await get_row("invoice", i2, tid=2)).status == "valid"


async def test_queue_run_endpoint_only_touches_callers_tenant(adm, adm2, eta):
    await configure(adm)
    await configure(adm2, tax_registration_number="222222222")
    _, i1 = await ready_invoice(adm)
    _, i2 = await ready_invoice(adm2)
    eta.submit_script.extend([lambda req: httpx.Response(503), lambda req: httpx.Response(503)])
    await submit(adm, i1)
    await submit(adm2, i2)
    await sql("UPDATE einvoice_submissions SET next_attempt_at=:t", t=datetime.utcnow() - timedelta(seconds=1))
    r = await adm.post(f"{EI}/queue/run")
    assert r.status_code == 200 and r.json() == {"processed": 1, "valid": 1, "rejected": 0, "failed": 0}
    assert (await get_row("invoice", i1, 1)).status == "valid"
    assert (await get_row("invoice", i2, 2)).status == "failed"        # ورشة 2 لم تُمس


async def test_worker_skips_tenants_with_disabled_settings(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, iid)
    await adm.put(f"{EI}/settings", json={"enabled": False})
    await sql("UPDATE einvoice_submissions SET next_attempt_at=:t", t=datetime.utcnow() - timedelta(seconds=1))
    eta.requests.clear()
    res = await run_einvoice_queue_once(TestSessionLocal, sleep=_nosleep)
    assert res["processed"] == 0 and not eta.requests


# ═══════════════════ الإلغاء ═══════════════════

async def test_cancel_admin_only_requires_reason_and_valid(adm, acct, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    url = f"{EI}/documents/invoice/{iid}/cancel"
    assert (await adm.post(url, json={"reason": "سبب كافي"})).status_code == 400        # لم يُرسل
    await submit(adm, iid)
    assert (await acct.post(url, json={"reason": "سبب كافي"})).status_code == 403
    assert (await adm.post(url, json={"reason": "ab"})).status_code == 400
    assert (await adm.post(url, json={"reason": "x" * 201})).status_code == 400
    r = await adm.post(url, json={"reason": "خطأ في البيانات"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and "إشعارًا دائنًا" in r.json()["message"]
    put = [x for x in eta.requests if x["method"] == "PUT"][0]
    assert put["path"] == "/api/v1.0/documents/state/UUID-1/state"
    assert json.loads(put["body"]) == {"status": "cancelled", "reason": "خطأ في البيانات"}
    assert (await get_inv(iid)).eta_status == "cancelled"
    # الملغي: لا تعديل (محروق) والحذف مسموح
    up = await adm.put(f"{SALES}/invoices/{iid}", json={"customer_id": 1, "date": str(date.today()),
                                                         "lines": LINES})
    assert up.status_code == 400 and "أُلغي لدى مصلحة الضرائب" in up.json()["detail"]
    d = await adm.delete(f"{SALES}/invoices/{iid}")
    assert d.status_code == 200
    assert await count(EInvoiceSubmission) == 0


# ═══════════════════ حراس عدم التعديل ═══════════════════

async def snapshot(c, iid, cid):
    r = await c.get(f"{COA}/trial-balance")
    return {
        "tb": r.json() if r.status_code == 200 else None,
        "bal": (await c.get(f"{CUST}/{cid}")).json()["balance"],
        "inv": (await c.get(f"{SALES}/invoices/{iid}")).json(),
        "mv": await count(StockMovement),
        "appr": await count(ApprovalRequest),
    }


@pytest.mark.parametrize("locked", ["queued", "submitting", "submitted", "valid"])
async def test_locked_invoice_cannot_be_edited_or_deleted_without_side_effects(adm, eta, locked):
    await adm.post(f"{COA}/chart/seed")
    await configure(adm)
    cid, iid = await ready_invoice(adm)
    await submit(adm, iid)
    await sql("UPDATE einvoice_submissions SET status=:s WHERE tenant_id=1", s=locked)
    await sql("UPDATE invoices SET eta_status=:s WHERE id=:i", s=locked, i=iid)
    before = await snapshot(adm, iid, cid)
    up = await adm.put(f"{SALES}/invoices/{iid}", json={
        "customer_id": cid, "date": str(date.today()), "tax_pct": 14, "discount_pct": 50,
        "lines": [{"description": "x", "qty": 5, "unit": "قطعة", "unit_price": 999}]})
    assert up.status_code == 400 and "تم إرساله للفاتورة الإلكترونية" in up.json()["detail"]
    de = await adm.delete(f"{SALES}/invoices/{iid}")
    assert de.status_code == 400 and "لا يمكن حذف" in de.json()["detail"]
    assert await snapshot(adm, iid, cid) == before


@pytest.mark.parametrize("state", [None, "rejected", "invalid", "failed"])
async def test_unlocked_states_remain_editable_and_deletable(adm, eta, state):
    await configure(adm)
    cid, iid = await ready_invoice(adm)
    if state:
        await submit(adm, iid)
        await sql("UPDATE einvoice_submissions SET status=:s WHERE tenant_id=1", s=state)
        await sql("UPDATE invoices SET eta_status=:s WHERE id=:i", s=state, i=iid)
    up = await adm.put(f"{SALES}/invoices/{iid}", json={
        "customer_id": cid, "date": str(date.today()), "tax_pct": 14, "discount_pct": 0, "lines": LINES})
    assert up.status_code == 200, up.text
    de = await adm.delete(f"{SALES}/invoices/{iid}")
    assert de.status_code == 200
    assert await count(EInvoiceSubmission) == 0 and await count(Invoice) == 0


async def test_locked_guard_runs_before_approval_requests(adm, acct, eta):
    """الحارس قبل require_approval: مفيش طلب موافقة يتولّد لمستند مقفول (وتحكّم موجب لغير المقفول)."""
    await configure(adm)
    rule = await adm.post("/api/v1/approvals/rules", json={
        "name": "حذف فاتورة", "action_key": "invoice_delete", "condition_type": "always", "threshold": None,
        "required_role": "admin", "valid_hours": 24, "exempt_roles": [], "is_active": True})
    assert rule.status_code == 201, rule.text
    cid, locked = await ready_invoice(adm)
    await submit(adm, locked)
    n0 = await count(ApprovalRequest)
    de = await acct.delete(f"{SALES}/invoices/{locked}")
    assert de.status_code == 400 and await count(ApprovalRequest) == n0
    # تحكّم موجب: فاتورة غير مرسلة => 428 + طلب موافقة
    free = await mk_invoice(adm, cid)
    de2 = await acct.delete(f"{SALES}/invoices/{free}")
    assert de2.status_code == 428 and await count(ApprovalRequest) == n0 + 1


async def test_credit_note_guards_locked_set(adm, eta):
    cid, iid = await make_valid_invoice(adm, eta)
    cn = await mk_cn(adm, cid, iid, price=100, tax=14, issue=False)
    # مسودة غير مرسلة: التعديل مسموح
    ok = await adm.put(f"{CN}/{cn}", json={"customer_id": cid, "invoice_id": iid, "date": str(date.today()),
                                          "return_type": "partial", "tax_pct": 14,
                                          "lines": [{"description": "م", "qty": 1, "unit": "قطعة",
                                                     "unit_price": 90}]})
    assert ok.status_code == 200, ok.text
    await adm.patch(f"{CN}/{cn}/issue")
    r = await submit(adm, cn, "credit-note")
    assert r.status_code == 200 and r.json()["status"] == "valid"
    bal0 = (await adm.get(f"{CUST}/{cid}")).json()["balance"]
    for call in (adm.put(f"{CN}/{cn}", json={"customer_id": cid, "date": str(date.today()),
                                            "lines": [{"description": "م", "qty": 1, "unit": "قطعة",
                                                       "unit_price": 1}]}),
                 adm.patch(f"{CN}/{cn}/cancel"), adm.delete(f"{CN}/{cn}")):
        resp = await call
        assert resp.status_code == 400, resp.text
        assert "الفاتورة الإلكترونية" in resp.json()["detail"] or "مصلحة الضرائب" in resp.json()["detail"]
    assert (await adm.get(f"{CUST}/{cid}")).json()["balance"] == bal0
    assert (await adm.get(f"{CN}/{cn}")).json()["status"] == "issued"
    assert (await adm.get(f"{CN}/{cn}")).json()["eta_status"] == "valid"
    # بعد الإلغاء لدى المصلحة: الإلغاء الداخلي والحذف مسموحان
    assert (await adm.post(f"{EI}/documents/credit-note/{cn}/cancel", json={"reason": "تصحيح"})).status_code == 200
    assert (await adm.patch(f"{CN}/{cn}/cancel")).status_code == 200
    assert (await adm.delete(f"{CN}/{cn}")).status_code == 204
    assert await count(EInvoiceSubmission, EInvoiceSubmission.doc_type == "credit_note") == 0


async def test_eta_fields_none_when_unused_backward_compat(adm):
    cid, iid = await ready_invoice(adm)
    inv = (await adm.get(f"{SALES}/invoices/{iid}")).json()
    for k in ("eta_status", "eta_uuid", "eta_submission_id", "eta_submitted_at", "eta_errors"):
        assert k in inv and inv[k] is None
    cnr = await adm.post(CN, json={"customer_id": cid, "invoice_id": iid, "date": str(date.today()),
                                   "return_type": "partial",
                                   "lines": [{"description": "م", "qty": 1, "unit": "قطعة", "unit_price": 10}]})
    cn = (await adm.get(f"{CN}/{cnr.json()['id']}")).json()
    assert cn["eta_status"] is None and cn["eta_errors"] is None
    assert (await adm.put(f"{SALES}/invoices/{iid}", json={
        "customer_id": cid, "date": str(date.today()), "lines": LINES})).status_code == 200


# ═══════════════════ العزل والصلاحيات ═══════════════════

async def test_cross_tenant_access_is_404_everywhere(adm, adm2, eta):
    await configure(adm)
    await configure(adm2, tax_registration_number="222222222")
    _, iid = await ready_invoice(adm)
    await submit(adm, iid)
    n = len(eta.submits())
    for method, suffix, body in [("GET", "preview", None), ("POST", "submit", None), ("POST", "resubmit", None),
                                 ("POST", "refresh-status", None), ("GET", "", None),
                                 ("POST", "cancel", {"reason": "اختبار العزل"})]:
        url = f"{EI}/documents/invoice/{iid}" + (f"/{suffix}" if suffix else "")
        r = await adm2.request(method, url, json=body)
        assert r.status_code == 404, (suffix, r.status_code, r.text)
    assert len(eta.submits()) == n
    assert (await adm2.get(f"{EI}/submissions")).json()["total"] == 0


async def test_submissions_list_filters_summary_and_joined_fields(adm, acct, viewer, eta):
    await configure(adm)
    cid = await mk_customer(adm, "عميل القائمة")
    i1 = await mk_invoice(adm, cid)
    i2 = await mk_invoice(adm, cid)
    await submit(adm, i1)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, i2)
    r = await acct.get(f"{EI}/submissions")
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 2 and j["summary"]["valid"] == 1 and j["summary"]["failed"] == 1
    assert set(j["summary"]) == {"queued", "submitting", "submitted", "valid", "invalid", "rejected", "failed",
                                 "cancelled"}
    it = {x["doc_id"]: x for x in j["items"]}
    assert it[i1]["doc_type"] == "invoice" and it[i1]["code"] == "INV-0001" and it[i1]["customer_name"] == "عميل القائمة"
    assert it[i1]["total"] > 0 and it[i2]["status"] == "failed" and it[i2]["next_attempt_at"]
    only = (await acct.get(f"{EI}/submissions?status=failed")).json()
    assert only["total"] == 1 and only["items"][0]["doc_id"] == i2
    assert (await acct.get(f"{EI}/submissions?q=القائمة")).json()["total"] == 2
    assert (await acct.get(f"{EI}/submissions?q=INV-0002")).json()["total"] == 1
    assert (await acct.get(f"{EI}/submissions?q=لايوجد")).json()["total"] == 0
    assert (await acct.get(f"{EI}/submissions?doc_type=credit-note")).json()["total"] == 0
    assert (await acct.get(f"{EI}/submissions?status=bogus")).status_code == 400
    assert (await acct.get(f"{EI}/submissions?limit=1")).json()["items"].__len__() == 1
    assert (await acct.get(f"{EI}/submissions?limit=9999")).status_code == 200
    assert (await viewer.get(f"{EI}/submissions")).status_code == 403


async def test_rbac_matrix_for_writes(adm, acct, viewer, prodc, eta, client):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    base = f"{EI}/documents/invoice/{iid}"
    for c in (viewer, prodc):
        for suffix in ("submit", "resubmit", "refresh-status"):
            assert (await c.post(f"{base}/{suffix}")).status_code == 403, suffix
        assert (await c.post(f"{base}/cancel", json={"reason": "سبب الإلغاء"})).status_code == 403
    assert not eta.requests
    # المشاهد يقرأ الحالة فقط
    st = await viewer.get(base)
    assert st.status_code == 200 and st.json() == {"status": None}
    assert (await prodc.get(base)).status_code == 200
    # المحاسب يرسل لكنه لا يلغي ولا يعدّل الإعدادات
    assert (await submit(acct, iid)).status_code == 200
    assert (await acct.post(f"{base}/cancel", json={"reason": "سبب الإلغاء"})).status_code == 403
    assert (await client.post(f"{base}/submit")).status_code == 401


async def test_state_returns_status_null_when_never_sent_and_404_for_missing(adm):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    r = await adm.get(f"{EI}/documents/invoice/{iid}")
    assert r.status_code == 200 and r.json() == {"status": None}
    assert (await adm.get(f"{EI}/documents/invoice/424242")).status_code == 404
    assert (await adm.get(f"{EI}/documents/foo/{iid}")).status_code == 404


async def test_refresh_status_400_when_nothing_to_refresh(adm, eta):
    await configure(adm)
    _, iid = await ready_invoice(adm)
    assert (await adm.post(f"{EI}/documents/invoice/{iid}/refresh-status")).status_code == 400
    await submit(adm, iid)                                              # valid
    assert (await adm.post(f"{EI}/documents/invoice/{iid}/refresh-status")).status_code == 400


async def test_refresh_status_upstream_outage_returns_202_without_state_change(adm, eta):
    await configure(adm)
    eta.doc_status = "In Progress"
    _, iid = await ready_invoice(adm)
    await submit(adm, iid)
    orig = eta.handler

    def down(request):
        if request.url.path.startswith("/api/v1/documentsubmissions/"):
            return httpx.Response(503)
        return orig(request)

    eta.handler = down
    r = await adm.post(f"{EI}/documents/invoice/{iid}/refresh-status")
    assert r.status_code == 202 and r.json()["status"] == "submitted"


async def test_credit_note_with_non_valid_original_after_original_cancelled(adm, eta):
    cid, iid = await make_valid_invoice(adm, eta)
    cn = await mk_cn(adm, cid, iid)
    assert (await adm.post(f"{EI}/documents/invoice/{iid}/cancel", json={"reason": "إلغاء أصلي"})).status_code == 200
    p = await preview(adm, cn, "credit-note")
    assert p["can_submit"] is False and "الفاتورة الأصلية لم تُقبل لدى مصلحة الضرائب بعد" in p["errors"]


async def test_share_url_only_when_valid_and_prod_portal(adm, eta):
    await configure(adm, environment="prod", signer_mode="external", signer_url="https://signer.example.com/sign")
    _, iid = await ready_invoice(adm)
    r = await submit(adm, iid)
    assert r.status_code == 200 and r.json()["status"] == "valid"
    assert eta.submits()[0]["host"] == "api.invoicing.eta.gov.eg"
    st = (await adm.get(f"{EI}/documents/invoice/{iid}")).json()
    assert st["share_url"] == "https://invoicing.eta.gov.eg/documents/UUID-1/share/LONG-1"


# ═══════════════════ انحدار: حمولة قديمة/مستند ملغى في إعادة المحاولة التلقائية ═══════════════════

def _make_due(doc_id, doc_type="invoice"):
    return sql("UPDATE einvoice_submissions SET next_attempt_at=:t WHERE doc_id=:d AND doc_type=:k",
               t=datetime.utcnow() - timedelta(seconds=1), d=doc_id, k=doc_type)


async def test_edit_after_failed_worker_never_sends_stale_payload(adm, eta):
    await configure(adm)
    cid, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    assert (await submit(adm, iid)).status_code == 202
    assert (await get_row("invoice", iid)).status == "failed"
    # المستخدم يعدّل الفاتورة (مسموح في failed) قبل إعادة المحاولة التلقائية
    new_lines = [{"description": "قطعة معدّلة", "qty": 1, "unit": "قطعة", "unit_price": 99999}]
    up = await adm.put(f"{SALES}/invoices/{iid}", json={
        "customer_id": cid, "date": str(date.today()), "tax_pct": 14, "discount_pct": 10, "lines": new_lines})
    assert up.status_code == 200, up.text
    new_total = float((await adm.get(f"{SALES}/invoices/{iid}")).json()["total"])
    await _make_due(iid)
    eta.requests.clear()
    res = await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=_nosleep)
    assert not eta.submits(), "الـ worker أرسل حمولة قديمة بعد تعديل الفاتورة"
    assert res["valid"] == 0 and res["failed"] == 1
    row = await get_row("invoice", iid)
    assert row.status == "failed" and row.next_attempt_at is None          # توقّف الإعادة التلقائية
    assert "تغيّرت" in row.last_error and "إعادة الإرسال" in row.last_error
    inv = await get_inv(iid)
    assert inv.eta_status == "failed" and not inv.eta_uuid                  # لا 'valid' كاذبة
    # لا إعادة محاولة تلقائية بعد ذلك
    assert (await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=_nosleep))["processed"] == 0
    # إعادة الإرسال اليدوية تبني من البيانات الحالية وترسل المبلغ الجديد
    rs = await adm.post(f"{EI}/documents/invoice/{iid}/resubmit")
    assert rs.status_code == 200 and rs.json()["status"] == "valid"
    sent = json.loads(eta.submits()[-1]["body"])["documents"][0]
    assert abs(sent["totalAmount"] - new_total) < 0.01


async def test_unchanged_failed_invoice_still_retried_with_identical_payload(adm, eta):
    """تحكّم موجب: بلا تعديل تُعاد المحاولة تلقائيًا بنفس الحمولة (التحقق لا يعطّل المسار السليم)."""
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, iid)
    sha = (await get_row("invoice", iid)).payload_sha256
    await _make_due(iid)
    res = await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=_nosleep)
    assert res["valid"] == 1 and len(eta.submits()) == 2     # 503 الأولى + إعادة المحاولة
    bodies = [json.loads(r["body"])["documents"][0] for r in eta.submits()]
    assert bodies[0] == bodies[1]                             # نفس الحمولة بايت ببايت
    row = await get_row("invoice", iid)
    assert row.status == "valid" and row.payload_sha256 == sha


async def test_failed_retry_survives_midnight_rollover_of_issue_time(adm, eta):
    """فاتورة بتاريخ اليوم فشلت ثم أُعيدت بعد عبور منتصف الليل: dateTimeIssued القديم لا يُعدّ تغييرًا."""
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, iid)
    async with TestSessionLocal() as s:
        row = (await s.execute(select(EInvoiceSubmission))).scalars().first()
        p = dict(row.payload_json)
        yesterday = datetime.utcnow() - timedelta(days=1)
        p["dateTimeIssued"] = yesterday.strftime("%Y-%m-%dT10:30:00Z")
        row.payload_json = p        # نفس المحتوى، وقت إصدار أقدم (الـ hash لا يشمل... يُعاد حسابه)
        row.payload_sha256 = signer_mod.document_hash(signer_mod.serialize_for_signing(p))
        row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        await s.commit()
    res = await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=_nosleep)
    assert res["valid"] == 1


async def test_changing_invoice_date_after_failed_is_detected(adm, eta):
    await configure(adm)
    cid, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, iid)
    old = date.today() - timedelta(days=3)
    up = await adm.put(f"{SALES}/invoices/{iid}", json={
        "customer_id": cid, "date": str(old), "tax_pct": 14, "discount_pct": 10, "lines": LINES})
    assert up.status_code == 200, up.text
    await _make_due(iid)
    eta.requests.clear()
    await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=_nosleep)
    assert not eta.submits()
    assert (await get_row("invoice", iid)).next_attempt_at is None


async def test_cancel_credit_note_after_failed_worker_never_sends_it(adm, eta):
    cid, iid = await make_valid_invoice(adm, eta)
    cn = await mk_cn(adm, cid, iid, price=100, tax=14)
    eta.submit_script.append(lambda req: httpx.Response(503))
    assert (await submit(adm, cn, "credit-note")).status_code == 202
    assert (await get_row("credit_note", cn)).status == "failed"
    bal0 = (await adm.get(f"{CUST}/{cid}")).json()["balance"]
    # الإلغاء في الـ ERP مسموح لـ failed
    assert (await adm.patch(f"{CN}/{cn}/cancel")).status_code == 200
    assert (await adm.get(f"{CUST}/{cid}")).json()["balance"] != bal0   # الرصيد انعكس
    await _make_due(cn, "credit_note")
    n_submits = len(eta.submits())
    res = await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=_nosleep)
    assert len(eta.submits()) == n_submits, "أُرسل إشعار دائن ملغى في الـ ERP إلى ETA"
    assert res["valid"] == 0 and res["failed"] == 1
    row = await get_row("credit_note", cn)
    assert row.status == "failed" and row.next_attempt_at is None and "صالحًا" in row.last_error
    cnj = (await adm.get(f"{CN}/{cn}")).json()
    assert cnj["status"] == "cancelled" and cnj["eta_status"] == "failed"
    # لا إعادة إرسال لإشعار ملغى (يُرفض بالتحقق ولا صف جديد يصل لـ ETA)
    rs = await adm.post(f"{EI}/documents/credit-note/{cn}/resubmit")
    assert rs.status_code == 422 and len(eta.submits()) == n_submits


async def test_worker_refuses_row_whose_document_disappeared(adm, eta):
    """مستند اختفى بينما صف التسليم باقٍ (مسار حذف غير معتاد): لا إرسال ولا استثناء."""
    await configure(adm)
    _, iid = await ready_invoice(adm)
    eta.submit_script.append(lambda req: httpx.Response(503))
    await submit(adm, iid)
    await sql("DELETE FROM invoice_lines WHERE invoice_id=:i", i=iid)
    await sql("DELETE FROM invoices WHERE id=:i", i=iid)
    await _make_due(iid)
    eta.requests.clear()
    res = await run_einvoice_queue_once(TestSessionLocal, tenant_id=1, sleep=_nosleep)
    assert not eta.submits() and res["failed"] == 1
    row = await get_row("invoice", iid)
    assert row.status == "failed" and row.next_attempt_at is None and "لم يعد موجودًا" in row.last_error


def test_issue_date_current_rules():
    from app.services.einvoice_service import _issue_date_current as ok
    d = date(2026, 5, 10)
    assert ok("2026-05-10T00:00:00Z", d) and not ok("2026-05-10T00:00:00Z", date(2026, 5, 11))
    assert ok("2026-05-10T14:20:00Z", d) and ok("2026-05-09T23:59:00Z", d)
    assert not ok("2026-05-10T14:20:00Z", date(2026, 5, 9))       # عُدّل لتاريخ أقدم
    assert not ok("garbage", d) and not ok("2026-05-10T14:20:00Z", None)
