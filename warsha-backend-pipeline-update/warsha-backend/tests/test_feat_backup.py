"""
test_feat_backup.py — النسخ الاحتياطي والاسترجاع + تصدير بيانات الورشة

يشغَّل دايماً بـ TEST_DB_PATH خاص: الاسترجاع بيستبدل محتوى ملف قاعدة الاختبار نفسه.
ERP_BACKUP_DIR دايماً tmp_path (autouse) والجدولة التلقائية معطّلة.
"""
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.api import backups as backups_api
from app.api.auth import create_token
from app.core.database import Base
from app.models.models import (
    AccountingEntry, AccountingEntryLine, BackupRun, CustomerOrder, DeliveryOrder,
    DeliveryOrderLine, SalesOrderWorkOrder, User,
)
from app.services import backup_scheduler as sched
from app.services import backup_service as svc
from app.services import tenant_export as texp
from tests.conftest import TestSessionLocal

SA = "/api/v1/super-admin"
BK = f"{SA}/backups"
TB = "/api/v1/backups"
_LOGIN = {"username": "superadmin", "password": "changeme123"}


# ── fixtures ────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    d = tmp_path / "bk"
    monkeypatch.setenv("ERP_BACKUP_DIR", str(d))
    monkeypatch.setenv("ERP_BACKUP_AUTO", "0")
    monkeypatch.setenv("ERP_BACKUP_RESTORE_GRACE_SECONDS", "0")
    for k in ("ERP_BACKUP_RETENTION", "ERP_BACKUP_KEEP_PRE_RESTORE", "ERP_BACKUP_STALE_HOURS",
              "ERP_BACKUP_INTERVAL_HOURS", "ERP_BACKUP_RETRY_HOURS", "ERP_PG_DUMP_PATH",
              "ERP_STATIC_DIR"):
        monkeypatch.delenv(k, raising=False)
    svc._restore_tokens.clear()
    assert not svc.is_busy()
    yield d
    svc._restore_tokens.clear()
    assert not svc.is_restoring()


@pytest_asyncio.fixture
async def sa(client):
    r = await client.post(f"{SA}/login", json=_LOGIN)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _backup(client, sa):
    r = await client.post(BK, headers=sa)
    assert r.status_code == 201, r.text
    return r.json()


async def _items(client, sa, **params):
    r = await client.get(BK, headers=sa, params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def _backdate(run_id, **cols):
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    async with TestSessionLocal() as s:
        await s.execute(text(f"UPDATE backup_runs SET {sets} WHERE id = :rid"), {**cols, "rid": run_id})
        await s.commit()


async def _insert_run(**kw) -> int:
    async with TestSessionLocal() as s:
        base = dict(tenant_id=None, kind="full_backup", trigger="manual", status="success",
                    db_engine="sqlite", started_at=datetime.utcnow())
        base.update(kw)
        r = BackupRun(**base)
        s.add(r)
        await s.commit()
        return r.id


def _tok(role, tid=1, uid=50):
    return {"Authorization": "Bearer " + create_token(user_id=uid, username=f"u_{role}", role=role, tenant_id=tid)}


async def _prepare_restore(client, sa, run_id):
    r = await client.post(f"{BK}/{run_id}/restore/prepare", headers=sa)
    assert r.status_code == 200, r.text
    return r.json()


async def _do_restore(client, sa, run_id):
    p = await _prepare_restore(client, sa, run_id)
    return await client.post(f"{BK}/{run_id}/restore", headers=sa,
                             json={"confirm_token": p["confirm_token"], "confirm_text": p["confirm_text"]})


# ═══════════════ RBAC / token worlds ═══════════════════════════════
SA_ROUTES = [
    ("GET", "/status", None), ("GET", "", None), ("POST", "", None),
    ("GET", "/restore-instructions", None), ("GET", "/1/download", None),
    ("DELETE", "/1", None), ("POST", "/1/verify", None),
    ("POST", "/1/restore/prepare", None),
    ("POST", "/1/restore", {"confirm_token": "x" * 30, "confirm_text": "RESTORE"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", SA_ROUTES)
async def test_sa_routes_reject_missing_bogus_and_tenant_tokens(client, headers_t1, method, path, body):
    for hdr in ({}, {"Authorization": "Bearer nope"}, headers_t1):
        r = await client.request(method, BK + path, headers=hdr, json=body)
        assert r.status_code == 401, (method, path, r.text)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [("POST", "/tenant-export"), ("GET", "/exports")])
async def test_tenant_routes_auth_and_roles(client, sa, method, path):
    r = await client.request(method, TB + path)
    assert r.status_code == 401
    # توكن السوبر أدمن مش JWT
    r = await client.request(method, TB + path, headers=sa)
    assert r.status_code == 401
    for role in ("viewer", "accountant", "production"):
        r = await client.request(method, TB + path, headers=_tok(role))
        assert r.status_code == 403, (role, r.text)
    r = await client.request(method, TB + path, headers=_tok("admin"))
    assert r.status_code == 200


# ═══════════════ النسخة الكاملة (SQLite) ═══════════════════════════
@pytest.mark.asyncio
async def test_full_backup_happy_path(client, sa, _env):
    item = await _backup(client, sa)
    assert item["status"] == "success" and item["kind"] == "full_backup"
    assert item["trigger"] == "manual" and item["created_by"] == "superadmin"
    assert item["integrity_ok"] is True and item["restorable"] and item["downloadable"]
    assert item["file_exists"] and not item["file_deleted"]
    assert item["started_at"].endswith("Z") and item["finished_at"].endswith("Z")
    f = _env / item["file_name"]
    assert f.is_file()
    assert item["size_bytes"] == f.stat().st_size
    assert item["checksum_sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    con = sqlite3.connect(str(f))
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT count(*) FROM tenants").fetchone()[0] == 2
    finally:
        con.close()
    assert (_env / (item["file_name"] + ".manifest.json")).is_file()
    # في القائمة وبـ tenant_id NULL
    items = await _items(client, sa)
    assert item["id"] in [i["id"] for i in items]
    async with TestSessionLocal() as s:
        row = await s.get(BackupRun, item["id"])
        assert row.tenant_id is None


@pytest.mark.asyncio
async def test_backup_dir_has_no_secrets_or_paths(client, sa, _env, tmp_path):
    item = await _backup(client, sa)
    names = sorted(p.name for p in _env.iterdir())
    for n in names:
        assert re.match(r"^warsherp_[A-Za-z0-9._-]+\.(db|dump)(\.manifest\.json)?$", n) or n == "backup_audit.log", n
    assert not any("jwt_secret" in n for n in names)
    blob = ""
    for n in names:
        if n.endswith(".json") or n.endswith(".log"):
            blob += (_env / n).read_text(encoding="utf-8")
    assert str(tmp_path) not in blob and str(_env) not in blob
    assert "DATABASE_URL" not in blob and "password" not in blob.lower()
    assert item["file_name"] in blob


@pytest.mark.asyncio
async def test_list_filters_and_validation(client, sa):
    await _backup(client, sa)
    assert len(await _items(client, sa, kind="full_backup")) == 1
    assert await _items(client, sa, kind="restore") == []
    r = await client.get(BK, headers=sa, params={"kind": "bogus"})
    assert r.status_code == 400
    r = await client.get(BK, headers=sa, params={"limit": 0})
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_two_sequential_backups_distinct_files(client, sa, _env):
    a = await _backup(client, sa)
    b = await _backup(client, sa)
    assert a["file_name"] != b["file_name"]
    assert len(list(_env.glob("*.db"))) == 2


@pytest.mark.asyncio
async def test_backup_lock_held_returns_409_and_no_running_row(client, sa):
    await svc._backup_lock.acquire()
    try:
        r = await client.post(BK, headers=sa)
        assert r.status_code == 409
        assert "جارية" in r.json()["detail"]
    finally:
        svc._backup_lock.release()
    assert await _items(client, sa) == []


@pytest.mark.asyncio
async def test_backup_dir_inside_static_dir_refused(client, sa, tmp_path, monkeypatch):
    monkeypatch.setenv("ERP_STATIC_DIR", str(tmp_path))
    r = await client.post(BK, headers=sa)
    assert r.status_code == 500
    assert "الواجهة" in r.json()["detail"]
    assert not (tmp_path / "bk").exists() or not list((tmp_path / "bk").glob("*.db"))


@pytest.mark.asyncio
async def test_relative_backup_dir_refused(client, sa, monkeypatch):
    monkeypatch.setenv("ERP_BACKUP_DIR", "relative/dir")
    r = await client.post(BK, headers=sa)
    assert r.status_code == 500


@pytest.mark.asyncio
async def test_failed_backup_recorded_with_sanitized_error(client, sa, monkeypatch):
    def boom(final):
        raise RuntimeError("cannot open C:\\secret\\dir\\file.db password=hunter2")
    monkeypatch.setattr(svc, "_snapshot_sqlite", boom)
    r = await client.post(BK, headers=sa)
    assert r.status_code == 500
    items = await _items(client, sa)
    assert items[0]["status"] == "failed" and items[0]["file_name"] is None
    assert "C:\\secret" not in (items[0]["error"] or "")
    assert items[0]["downloadable"] is False


# ═══════════════ تنزيل / حذف / تحقق ════════════════════════════════
@pytest.mark.asyncio
async def test_download_bytes_and_checksum(client, sa, _env):
    item = await _backup(client, sa)
    r = await client.get(f"{BK}/{item['id']}/download", headers=sa)
    assert r.status_code == 200
    assert r.content == (_env / item["file_name"]).read_bytes()
    assert r.headers["x-checksum-sha256"] == item["checksum_sha256"]
    assert item["file_name"] in r.headers["content-disposition"]
    assert (await client.get(f"{BK}/99999/download", headers=sa)).status_code == 404


@pytest.mark.asyncio
async def test_download_refuses_planted_paths(client, sa):
    for bad in ("../x.db", "/etc/passwd", "..\\x.db", "a/b.db", "evil.txt"):
        rid = await _insert_run(file_name=bad, size_bytes=1, checksum_sha256="0" * 64)
        r = await client.get(f"{BK}/{rid}/download", headers=sa)
        assert r.status_code in (400, 404), (bad, r.status_code)
    rid = await _insert_run(kind="restore", file_name=None)
    assert (await client.get(f"{BK}/{rid}/download", headers=sa)).status_code == 400


@pytest.mark.asyncio
async def test_delete_flow_and_idempotency(client, sa, _env):
    item = await _backup(client, sa)
    fn = item["file_name"]
    r = await client.delete(f"{BK}/{item['id']}", headers=sa)
    assert r.status_code == 200 and r.json()["id"] == item["id"]
    assert not (_env / fn).exists() and not (_env / (fn + ".manifest.json")).exists()
    items = await _items(client, sa)
    row = next(i for i in items if i["id"] == item["id"])
    assert row["file_deleted"] is True and row["downloadable"] is False and row["restorable"] is False
    dels = [i for i in items if i["kind"] == "delete"]
    assert len(dels) == 1 and dels[0]["details"]["target_run_id"] == item["id"]
    assert dels[0]["details"]["file_name"] == fn
    assert (await client.get(f"{BK}/{item['id']}/download", headers=sa)).status_code == 410
    # نفس الحذف تاني: 200 idempotent
    r2 = await client.delete(f"{BK}/{item['id']}", headers=sa)
    assert r2.status_code == 200
    dels = [i for i in await _items(client, sa) if i["kind"] == "delete"]
    assert any(d["details"].get("already_deleted") for d in dels)
    # restore بعد الحذف: 410
    assert (await client.post(f"{BK}/{item['id']}/restore/prepare", headers=sa)).status_code == 410


@pytest.mark.asyncio
async def test_delete_validation(client, sa):
    assert (await client.delete(f"{BK}/99999", headers=sa)).status_code == 404
    rid = await _insert_run(status="running", file_name="warsherp_full_x.db")
    assert (await client.delete(f"{BK}/{rid}", headers=sa)).status_code == 409
    rid2 = await _insert_run(kind="restore", file_name=None)
    assert (await client.delete(f"{BK}/{rid2}", headers=sa)).status_code == 400


@pytest.mark.asyncio
async def test_delete_blocked_while_restoring(client, sa):
    item = await _backup(client, sa)
    svc._RESTORING = True
    try:
        r = await client.delete(f"{BK}/{item['id']}", headers=sa)
        assert r.status_code == 409
    finally:
        svc._RESTORING = False


@pytest.mark.asyncio
async def test_verify_detects_corruption_and_prepare_refuses(client, sa, _env):
    item = await _backup(client, sa)
    r = await client.post(f"{BK}/{item['id']}/verify", headers=sa)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "checksum_match": True, "integrity": "ok", "size_bytes": item["size_bytes"]}
    f = _env / item["file_name"]
    data = bytearray(f.read_bytes())
    for i in range(len(data) // 2, len(data) // 2 + 64):
        data[i] ^= 0xFF
    f.write_bytes(bytes(data))
    r = await client.post(f"{BK}/{item['id']}/verify", headers=sa)
    assert r.json()["checksum_match"] is False and r.json()["ok"] is False
    r = await client.post(f"{BK}/{item['id']}/restore/prepare", headers=sa)
    assert r.status_code == 409 and "تالف" in r.json()["detail"]


@pytest.mark.asyncio
async def test_prepare_refuses_garbage_and_missing_file(client, sa, _env):
    item = await _backup(client, sa)
    f = _env / item["file_name"]
    garbage = b"this is not sqlite" * 100
    f.write_bytes(garbage)
    # حتى لو الـ checksum اتعدّل مع الملف (عبث مقصود) لازم يترفض لأنه مش SQLite
    await _backdate(item["id"], checksum_sha256=hashlib.sha256(garbage).hexdigest())
    assert (await client.post(f"{BK}/{item['id']}/restore/prepare", headers=sa)).status_code == 409
    f.unlink()
    assert (await client.post(f"{BK}/{item['id']}/restore/prepare", headers=sa)).status_code == 409


# ═══════════════ Retention / تنظيف ═════════════════════════════════
@pytest.mark.asyncio
async def test_retention_keeps_newest(client, sa, _env, monkeypatch):
    monkeypatch.setenv("ERP_BACKUP_RETENTION", "2")
    made = [await _backup(client, sa) for _ in range(4)]
    files = sorted(p.name for p in _env.glob("*.db"))
    assert files == sorted(m["file_name"] for m in made[2:])
    assert len(list(_env.glob("*.manifest.json"))) == 2
    items = {i["id"]: i for i in await _items(client, sa)}
    assert items[made[0]["id"]]["file_deleted"] is True
    assert items[made[0]["id"]]["details"]["pruned_by"] == "retention"
    assert items[made[3]["id"]]["file_deleted"] is False


@pytest.mark.asyncio
async def test_retention_zero_behaves_as_one(client, sa, _env, monkeypatch):
    monkeypatch.setenv("ERP_BACKUP_RETENTION", "0")
    await _backup(client, sa)
    last = await _backup(client, sa)
    assert [p.name for p in _env.glob("*.db")] == [last["file_name"]]


@pytest.mark.asyncio
async def test_retention_protects_recent_pre_restore(_env, monkeypatch):
    monkeypatch.setenv("ERP_BACKUP_KEEP_PRE_RESTORE", "1")
    for _ in range(3):
        await svc._create_backup_locked("pre_restore", "superadmin", svc.KIND_PRE_RESTORE)
    res = await svc.apply_retention()
    assert res["pruned"] == []  # أحدث من 24 ساعة
    assert len(list(_env.glob("warsherp_prerestore_*.db"))) == 3


@pytest.mark.asyncio
async def test_stale_running_row_is_reaped(client, sa):
    rid = await _insert_run(status="running", file_name=None,
                            started_at=datetime.utcnow() - timedelta(hours=3))
    items = await _items(client, sa)
    row = next(i for i in items if i["id"] == rid)
    assert row["status"] == "failed" and "انقطعت" in row["error"]


@pytest.mark.asyncio
async def test_cleanup_part_files(_env):
    _env.mkdir(parents=True, exist_ok=True)
    old = _env / "warsherp_full_old.db.part"
    new = _env / "warsherp_full_new.db.part"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    past = time.time() - 7200
    os.utime(old, (past, past))
    assert svc.cleanup_part_files() == 1
    assert not old.exists() and new.exists()


# ═══════════════ الحالة (status) ═══════════════════════════════════
@pytest.mark.asyncio
async def test_status_levels(client, sa, monkeypatch):
    r = await client.get(f"{BK}/status", headers=sa)
    assert r.status_code == 200
    st = r.json()
    assert st["level"] == "critical" and st["stale"] is True and st["last_success"] is None
    assert st["db_engine"] == "sqlite" and st["restore_mode"] == "file"
    assert st["tool"]["pg_dump_available"] is None
    item = await _backup(client, sa)
    st = (await client.get(f"{BK}/status", headers=sa)).json()
    assert st["level"] == "ok" and st["stale"] is False and st["running"] is False
    assert st["last_success"]["id"] == item["id"] and st["last_success"]["finished_at"].endswith("Z")
    assert st["retention"] == 14 and st["keep_pre_restore"] == 5 and st["auto_enabled"] is False
    monkeypatch.setenv("ERP_BACKUP_STALE_HOURS", "1")
    await _backdate(item["id"], finished_at=datetime.utcnow() - timedelta(hours=5))
    st = (await client.get(f"{BK}/status", headers=sa)).json()
    assert st["level"] == "warning" and st["stale"] is True and st["age_hours"] >= 4.9
    assert st["message_ar"]


@pytest.mark.asyncio
async def test_status_last_failure_and_running_flag(client, sa, monkeypatch):
    def boom(final):
        raise RuntimeError("x")
    monkeypatch.setattr(svc, "_snapshot_sqlite", boom)
    await client.post(BK, headers=sa)
    st = (await client.get(f"{BK}/status", headers=sa)).json()
    assert st["last_failure"] and st["last_failure"]["error"]
    await svc._backup_lock.acquire()
    try:
        assert (await client.get(f"{BK}/status", headers=sa)).json()["running"] is True
    finally:
        svc._backup_lock.release()


# ═══════════════ الاسترجاع (SQLite) ════════════════════════════════
async def _names(cli):
    r = await cli.get("/api/v1/customers")
    assert r.status_code == 200, r.text
    return sorted(c["name"] for c in r.json())


@pytest.mark.asyncio
async def test_restore_happy_path(client, cli, sa, _env):
    assert (await cli.post("/api/v1/customers", json={"name": "عميل أصلي", "phone": "0501"})).status_code == 201
    assert await _names(cli) == ["عميل أصلي"]  # الاتصالات المجمّعة اتفتحت
    item = await _backup(client, sa)
    assert (await cli.post("/api/v1/customers", json={"name": "عميل بعد النسخة", "phone": "0502"})).status_code == 201
    assert len(await _names(cli)) == 2
    prep = await _prepare_restore(client, sa, item["id"])
    assert prep["confirm_text"] == "RESTORE" and prep["expires_in_seconds"] == 300
    assert len(prep["confirm_token"]) >= 20 and prep["warnings_ar"]
    assert prep["backup"]["file_name"] == item["file_name"]
    r = await client.post(f"{BK}/{item['id']}/restore", headers=sa,
                          json={"confirm_token": prep["confirm_token"], "confirm_text": "RESTORE"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requires_relogin"] is True
    assert body["restored_from"]["file_name"] == item["file_name"]
    assert body["safety_backup"]["file_name"].startswith("warsherp_prerestore_")
    # بيانات الورشة رجعت للنسخة (متابعة عبر الاتصالات الجديدة)
    assert await _names(cli) == ["عميل أصلي"]
    # لقطة النسخة نفسها: صفها كان running وقت اللقطة → لازم يفضل success وقابل للاسترجاع
    items = await _items(client, sa)
    src = next(i for i in items if i["file_name"] == item["file_name"])
    assert src["status"] == "success" and src["restorable"] and src["checksum_sha256"] == item["checksum_sha256"]
    safety = next(i for i in items if i["file_name"] == body["safety_backup"]["file_name"])
    assert safety["kind"] == "pre_restore" and safety["status"] == "success" and safety["restorable"]
    assert safety["id"] == body["safety_backup"]["run_id"]
    rest = [i for i in items if i["kind"] == "restore"]
    assert len(rest) == 1 and rest[0]["status"] == "success"
    assert rest[0]["details"]["safety_file"] == safety["file_name"]
    assert rest[0]["started_at"].endswith("Z")
    assert (await client.get("/health")).status_code == 200
    st = (await client.get(f"{BK}/status", headers=sa)).json()
    assert st["level"] == "ok"
    assert "restore" in (_env / "backup_audit.log").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_restore_safety_backup_allows_going_back(client, cli, sa):
    await cli.post("/api/v1/customers", json={"name": "قبل", "phone": "1"})
    item = await _backup(client, sa)
    await cli.post("/api/v1/customers", json={"name": "بعد", "phone": "2"})
    r = await _do_restore(client, sa, item["id"])
    assert await _names(cli) == ["قبل"]
    # نرجع من نسخة الأمان
    r2 = await _do_restore(client, sa, r.json()["safety_backup"]["run_id"])
    assert r2.status_code == 200, r2.text
    assert await _names(cli) == ["بعد", "قبل"]


@pytest.mark.asyncio
async def test_restore_rejects_bad_confirmation(client, sa):
    item = await _backup(client, sa)
    rid = item["id"]
    url = f"{BK}/{rid}/restore"
    # نص تأكيد غلط
    p = await _prepare_restore(client, sa, rid)
    r = await client.post(url, headers=sa, json={"confirm_token": p["confirm_token"], "confirm_text": "restore"})
    assert r.status_code == 400
    # التوكن اتستهلك حتى مع النص الغلط
    r = await client.post(url, headers=sa, json={"confirm_token": p["confirm_token"], "confirm_text": "RESTORE"})
    assert r.status_code == 400
    # توكن مجهول
    r = await client.post(url, headers=sa, json={"confirm_token": "z" * 40, "confirm_text": "RESTORE"})
    assert r.status_code == 400
    # توكن قصير جداً
    r = await client.post(url, headers=sa, json={"confirm_token": "abc", "confirm_text": "RESTORE"})
    assert r.status_code == 400
    # منتهي
    p = await _prepare_restore(client, sa, rid)
    svc._restore_tokens[p["confirm_token"]]["expires_at"] = 0
    r = await client.post(url, headers=sa, json={"confirm_token": p["confirm_token"], "confirm_text": "RESTORE"})
    assert r.status_code == 400 and "صلاحية" in r.json()["detail"]
    # توكن نسخة تانية
    other = await _backup(client, sa)
    p = await _prepare_restore(client, sa, other["id"])
    r = await client.post(url, headers=sa, json={"confirm_token": p["confirm_token"], "confirm_text": "RESTORE"})
    assert r.status_code == 400
    # جلسة سوبر أدمن تانية
    p = await _prepare_restore(client, sa, rid)
    r2 = await client.post(f"{SA}/login", json=_LOGIN)
    sa2 = {"Authorization": f"Bearer {r2.json()['token']}"}
    r = await client.post(url, headers=sa2, json={"confirm_token": p["confirm_token"], "confirm_text": "RESTORE"})
    assert r.status_code == 400
    # كل ده والداتابيز لسه سليمة ومفيش نسخة أمان اتعملت
    assert not any(i["kind"] in ("restore", "pre_restore") for i in await _items(client, sa))


@pytest.mark.asyncio
async def test_restore_token_is_single_use(client, cli, sa):
    item = await _backup(client, sa)
    p = await _prepare_restore(client, sa, item["id"])
    body = {"confirm_token": p["confirm_token"], "confirm_text": "RESTORE"}
    r1 = await client.post(f"{BK}/{item['id']}/restore", headers=sa, json=body)
    assert r1.status_code == 200
    r2 = await client.post(f"{BK}/{item['id']}/restore", headers=sa, json=body)
    assert r2.status_code == 400
    assert len([i for i in await _items(client, sa) if i["kind"] == "restore"]) == 1


@pytest.mark.asyncio
async def test_restore_in_progress_returns_409(client, sa):
    item = await _backup(client, sa)
    p = await _prepare_restore(client, sa, item["id"])
    await svc._backup_lock.acquire()
    try:
        r = await client.post(f"{BK}/{item['id']}/restore", headers=sa,
                              json={"confirm_token": p["confirm_token"], "confirm_text": "RESTORE"})
        assert r.status_code == 409
    finally:
        svc._backup_lock.release()


@pytest.mark.asyncio
async def test_restore_aborts_untouched_when_safety_backup_fails(client, cli, sa, monkeypatch):
    await cli.post("/api/v1/customers", json={"name": "أ", "phone": "1"})
    item = await _backup(client, sa)
    await cli.post("/api/v1/customers", json={"name": "ب", "phone": "2"})
    p = await _prepare_restore(client, sa, item["id"])

    def boom(final):
        raise RuntimeError("disk exploded")
    monkeypatch.setattr(svc, "_snapshot_sqlite", boom)
    r = await client.post(f"{BK}/{item['id']}/restore", headers=sa,
                          json={"confirm_token": p["confirm_token"], "confirm_text": "RESTORE"})
    assert r.status_code == 500 and "نسخة الأمان" in r.json()["detail"]
    assert await _names(cli) == ["أ", "ب"]  # الداتابيز الحية ما اتلمستش
    assert not svc.is_restoring() and not svc._backup_lock.locked()


@pytest.mark.asyncio
async def test_restore_rolls_back_when_copy_fails(client, cli, sa, monkeypatch):
    await cli.post("/api/v1/customers", json={"name": "أ", "phone": "1"})
    item = await _backup(client, sa)
    await cli.post("/api/v1/customers", json={"name": "ب", "phone": "2"})
    p = await _prepare_restore(client, sa, item["id"])
    orig = svc._restore_in_place
    calls = []

    def flaky(src, live):
        calls.append(src)
        if len(calls) == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return orig(src, live)  # الرجوع من نسخة الأمان
    monkeypatch.setattr(svc, "_restore_in_place", flaky)
    r = await client.post(f"{BK}/{item['id']}/restore", headers=sa,
                          json={"confirm_token": p["confirm_token"], "confirm_text": "RESTORE"})
    assert r.status_code == 500 and "تم الرجوع" in r.json()["detail"]
    assert len(calls) == 2 and "prerestore" in calls[1]
    assert await _names(cli) == ["أ", "ب"]
    failed = [i for i in await _items(client, sa) if i["kind"] == "restore"]
    assert len(failed) == 1 and failed[0]["status"] == "failed"
    assert not svc.is_restoring() and not svc._backup_lock.locked()


@pytest.mark.asyncio
async def test_restore_maintenance_mode_blocks_tenant_requests(cli, client, sa):
    svc._RESTORING = True
    try:
        r = await cli.get("/api/v1/customers")
        assert r.status_code == 503 and "الاسترجاع" in r.json()["detail"]
        assert (await client.get("/health")).status_code == 200
        assert (await client.get(f"{BK}/status", headers=sa)).status_code == 200
    finally:
        svc._RESTORING = False
    assert (await cli.get("/api/v1/customers")).status_code == 200


@pytest.mark.asyncio
async def test_restore_of_older_schema_gets_missing_columns(client, sa, _env):
    """نسخة أقدم من الكود (عمود ناقص) تترقّى بعد الاسترجاع."""
    item = await _backup(client, sa)
    f = _env / item["file_name"]
    con = sqlite3.connect(str(f))
    con.execute("DROP TABLE backup_runs")  # جدول ميزة جديدة مش موجود في النسخة القديمة
    con.commit()
    con.close()
    await _backdate(item["id"], checksum_sha256=hashlib.sha256(f.read_bytes()).hexdigest())
    r = await _do_restore(client, sa, item["id"])
    assert r.status_code == 200, r.text
    # الجدول اتعمل تاني بـ create_all وعملية الاسترجاع اتسجلت
    items = await _items(client, sa)
    assert any(i["kind"] == "restore" and i["status"] == "success" for i in items)
    assert any(i["file_name"] == item["file_name"] for i in items)


# ═══════════════ PostgreSQL (بالـ mocks فقط) ═══════════════════════
PG_URL = "postgresql+asyncpg://pguser:s3cretPW@dbhost:5432/warshdb"


@pytest.mark.asyncio
async def test_pg_status_and_manual_restore(client, sa, monkeypatch):
    monkeypatch.setattr(svc.settings, "DATABASE_URL", PG_URL)
    monkeypatch.setattr(svc.shutil, "which", lambda n: None)
    st = (await client.get(f"{BK}/status", headers=sa)).json()
    assert st["db_engine"] == "postgresql" and st["restore_mode"] == "manual"
    assert st["tool"]["pg_dump_available"] is False
    rid = await _insert_run(db_engine="postgresql", file_name="warsherp_full_x.dump")
    for path in (f"/{rid}/restore/prepare", f"/{rid}/restore"):
        r = await client.post(BK + path, headers=sa,
                              json={"confirm_token": "t" * 30, "confirm_text": "RESTORE"})
        assert r.status_code == 400
        assert "restore-instructions" in r.json()["detail"]
    ins = (await client.get(f"{BK}/restore-instructions", headers=sa)).json()
    assert ins["supported_in_app"] is False and ins["engine"] == "postgresql"
    cmds = " ".join(c["command"] for c in ins["commands"])
    assert "pg_restore" in cmds and "alembic upgrade head" in cmds and "<DB_NAME>" in cmds
    blob = json.dumps(ins, ensure_ascii=False)
    assert "s3cretPW" not in blob and "pguser" not in blob and "dbhost" not in blob
    assert svc.PG_FILE_EXT in blob and svc.PG_DUMP_FORMAT_FLAG in blob


@pytest.mark.asyncio
async def test_sqlite_restore_instructions(client, sa):
    ins = (await client.get(f"{BK}/restore-instructions", headers=sa)).json()
    assert ins["supported_in_app"] is True and ins["steps_ar"] and ins["warnings_ar"]
    joined = " ".join(ins["warnings_ar"])
    assert "التدقيق" in joined and "الموافقات" in joined


@pytest.mark.asyncio
async def test_pg_backup_without_tool_is_503_and_recorded(client, sa, monkeypatch):
    monkeypatch.setattr(svc.settings, "DATABASE_URL", PG_URL)
    monkeypatch.setattr(svc.shutil, "which", lambda n: None)
    r = await client.post(BK, headers=sa)
    assert r.status_code == 503 and "pg_dump" in r.json()["detail"]
    items = await _items(client, sa)
    assert items[0]["status"] == "failed" and items[0]["db_engine"] == "postgresql"
    assert "pg_dump" in items[0]["error"]


@pytest.mark.asyncio
async def test_pg_backup_with_fake_tool(client, sa, _env, tmp_path, monkeypatch):
    monkeypatch.setattr(svc.settings, "DATABASE_URL", PG_URL)
    fake = tmp_path / "pg_dump"
    fake.write_text("x")
    monkeypatch.setenv("ERP_PG_DUMP_PATH", str(fake))
    monkeypatch.setattr(svc, "_find_pg_restore", lambda tool: None)
    seen = {}

    async def fake_run(argv, env, timeout):
        seen["argv"], seen["env"] = list(argv), dict(env)
        out = argv[argv.index("-f") + 1]
        Path(out).write_bytes(b"PGDMP" + b"\x00" * 100)
        return 0, ""
    monkeypatch.setattr(svc, "_run_tool", fake_run)
    r = await client.post(BK, headers=sa)
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["file_name"].endswith(".dump") and item["db_engine"] == "postgresql"
    assert "s3cretPW" not in " ".join(seen["argv"])
    assert seen["env"]["PGPASSWORD"] == "s3cretPW"
    assert "-Fc" in seen["argv"] and "pguser" in seen["argv"] and "warshdb" in seen["argv"]
    assert item["downloadable"] is True and item["restorable"] is False
    d = await client.get(f"{BK}/{item['id']}/download", headers=sa)
    assert d.status_code == 200 and d.content.startswith(b"PGDMP")
    v = (await client.post(f"{BK}/{item['id']}/verify", headers=sa)).json()
    assert v["ok"] is True
    blob = "".join(p.read_text(encoding="utf-8") for p in _env.glob("*.json")) \
        + (_env / "backup_audit.log").read_text(encoding="utf-8")
    assert "s3cretPW" not in blob


@pytest.mark.asyncio
async def test_pg_backup_failure_sanitized(client, sa, tmp_path, monkeypatch):
    monkeypatch.setattr(svc.settings, "DATABASE_URL", PG_URL)
    fake = tmp_path / "pg_dump"
    fake.write_text("x")
    monkeypatch.setenv("ERP_PG_DUMP_PATH", str(fake))

    async def fake_run(argv, env, timeout):
        return 1, 'pg_dump: error: connection to postgresql://pguser:s3cretPW@dbhost/warshdb failed, s3cretPW'
    monkeypatch.setattr(svc, "_run_tool", fake_run)
    r = await client.post(BK, headers=sa)
    assert r.status_code == 500 and "s3cretPW" not in r.text
    items = await _items(client, sa)
    assert items[0]["status"] == "failed" and "s3cretPW" not in (items[0]["error"] or "")
    assert not list(Path(os.environ["ERP_BACKUP_DIR"]).glob("*.part"))


# ═══════════════ الجدولة ═══════════════════════════════════════════
@pytest.mark.asyncio
async def test_scheduler_creates_then_skips(monkeypatch, _env):
    monkeypatch.setenv("ERP_BACKUP_AUTO", "1")
    res = await sched.run_scheduled_backup_once()
    assert res["ran"] is True
    async with TestSessionLocal() as s:
        row = await s.get(BackupRun, res["run_id"])
        assert row.tenant_id is None and row.trigger == "scheduled" and row.created_by == "scheduler"
    res2 = await sched.run_scheduled_backup_once()
    assert res2 == {"skipped": True, "reason": "recent_backup"}
    assert len(list(_env.glob("*.db"))) == 1


@pytest.mark.asyncio
async def test_scheduler_disabled_and_busy(monkeypatch):
    assert await sched.run_scheduled_backup_once() == {"skipped": True, "reason": "disabled"}
    monkeypatch.setenv("ERP_BACKUP_AUTO", "1")
    await svc._backup_lock.acquire()
    try:
        assert await sched.run_scheduled_backup_once() == {"skipped": True, "reason": "busy"}
    finally:
        svc._backup_lock.release()


@pytest.mark.asyncio
async def test_scheduler_failure_never_raises_and_backs_off(monkeypatch, client, sa):
    monkeypatch.setenv("ERP_BACKUP_AUTO", "1")
    n = {"c": 0}

    def boom(final):
        n["c"] += 1
        raise RuntimeError("tool missing")
    monkeypatch.setattr(svc, "_snapshot_sqlite", boom)
    res = await sched.run_scheduled_backup_once()
    assert res["ran"] is False and res["failed"] is True
    # المحاولات التالية خلال نافذة الـ retry لا تكتب صفوف فاشلة جديدة
    for _ in range(3):
        again = await sched.run_scheduled_backup_once()
        assert again == {"skipped": True, "reason": "backoff"}
    assert n["c"] == 1
    failed = [i for i in await _items(client, sa) if i["status"] == "failed"]
    assert len(failed) == 1
    # بعد نافذة الـ retry يحاول تاني
    await _backdate(failed[0]["id"], finished_at=datetime.utcnow() - timedelta(hours=7))
    again = await sched.run_scheduled_backup_once()
    assert again["failed"] is True and n["c"] == 2


@pytest.mark.asyncio
async def test_scheduler_applies_retention_and_interval(monkeypatch, client, sa, _env):
    monkeypatch.setenv("ERP_BACKUP_AUTO", "1")
    monkeypatch.setenv("ERP_BACKUP_RETENTION", "1")
    old = await _backup(client, sa)
    await _backdate(old["id"], finished_at=datetime.utcnow() - timedelta(hours=30))
    res = await sched.run_scheduled_backup_once()
    assert res["ran"] is True
    files = [p.name for p in _env.glob("*.db")]
    assert len(files) == 1 and files[0] != old["file_name"]


@pytest.mark.asyncio
async def test_scheduler_reaps_and_cleans(monkeypatch, client, sa, _env):
    monkeypatch.setenv("ERP_BACKUP_AUTO", "1")
    rid = await _insert_run(status="running", file_name=None,
                            started_at=datetime.utcnow() - timedelta(hours=3))
    _env.mkdir(parents=True, exist_ok=True)
    part = _env / "warsherp_full_orphan.db.part"
    part.write_bytes(b"x")
    past = time.time() - 7200
    os.utime(part, (past, past))
    await sched.run_scheduled_backup_once()
    assert not part.exists()
    row = next(i for i in await _items(client, sa) if i["id"] == rid)
    assert row["status"] == "failed"


# ═══════════════ تصدير بيانات الورشة ═══════════════════════════════
def _zip(resp):
    return zipfile.ZipFile(io.BytesIO(resp.content))


async def _seed_tenant_data():
    """بيانات لورشتين: جداول فيها tenant_id + الجداول الابن اللي مالهاش tenant_id."""
    async with TestSessionLocal() as s:
        for tid in (1, 2):
            s.add(User(tenant_id=tid, username=f"user{tid}", password_hash=f"pbkdf2$salt$SECRETHASH{tid}",
                       full_name=f"مستخدم {tid}", role="admin"))
            e = AccountingEntry(tenant_id=tid, entry_date=date(2025, 1, 1), description=f"قيد ورشة {tid}",
                                source="manual", posted=True)
            e.lines.append(AccountingEntryLine(account_id=1, side="debit", amount=Decimal("1234.56"),
                                               notes=f"line-t{tid}"))
            s.add(e)
            do = DeliveryOrder(tenant_id=tid, code=f"DO-{tid}", order_date=date(2025, 1, 2), customer_id=1)
            do.lines.append(DeliveryOrderLine(description=f"dol-t{tid}", qty="1", unit="قطعة"))
            s.add(do)
            co = CustomerOrder(tenant_id=tid, code=f"CO-{tid}", customer_id=1)
            s.add(co)
            await s.flush()
            s.add(SalesOrderWorkOrder(customer_order_id=co.id, work_order_id=900 + tid, notes=f"sowo-t{tid}"))
        await s.commit()


def test_export_guard_covers_all_tables():
    plan = {p["table"].name: p["mode"] for p in texp.table_plan()}
    assert set(plan) == set(Base.metadata.tables)
    # أي جدول جديد (من ميزة تانية) بلا tenant_id لازم يتضاف لـ CHILD_SCOPES أو GLOBAL_TABLES
    assert [n for n, m in plan.items() if m == "unscoped"] == [], \
        "جدول بلا tenant_id وبلا نطاق — أضفه لـ CHILD_SCOPES أو GLOBAL_TABLES"
    for name, (fk, parent) in texp.CHILD_SCOPES.items():
        assert fk in Base.metadata.tables[name].c and "tenant_id" in Base.metadata.tables[parent].c
    for name, t in Base.metadata.tables.items():
        if "tenant_id" in t.c and name not in texp.EXPORT_EXCLUDE_TABLES:
            assert plan[name] in ("tenant", "tenant_row")
    assert texp.redacted_columns() == ["users.password_hash"]
    assert "global_catalog" in texp.GLOBAL_TABLES and texp.EXPORT_EXCLUDE_TABLES >= {"backup_runs"}


@pytest.mark.asyncio
async def test_tenant_export_zip_content_and_isolation(cli, cli2, tmp_path, monkeypatch):
    created = []
    real_mkdtemp = tempfile.mkdtemp

    def spy(*a, **k):
        p = real_mkdtemp(*a, **k)
        created.append(p)
        return p
    monkeypatch.setattr(backups_api.tempfile, "mkdtemp", spy)
    await _seed_tenant_data()
    assert (await cli.post("/api/v1/customers", json={"name": "عميل ورشة 1", "phone": "1"})).status_code == 201
    assert (await cli2.post("/api/v1/customers", json={"name": "عميل ورشة 2", "phone": "2"})).status_code == 201
    r = await cli.post(f"{TB}/tenant-export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert re.match(r'.*warsha-export-1-\d{8}-\d{6}\.zip', r.headers["content-disposition"])
    assert r.headers["x-export-checksum-sha256"] == hashlib.sha256(r.content).hexdigest()
    z = _zip(r)
    names = set(z.namelist())
    man = json.loads(z.read("manifest.json"))
    assert man["tenant"] == {"id": 1, "name": "Tenant 1", "slug": "tenant-1"}
    assert man["redacted_columns"] == ["users.password_hash"]
    assert man["format_version"] == 1 and "الملفات" in man["note"]
    assert man["exported_at"].endswith("Z")
    assert set(man["tables"]) | {"manifest"} == {n[:-5] for n in names}
    # كل جدول فيه tenant_id (ما عدا المستبعد) متصدّر
    for tname, t in Base.metadata.tables.items():
        if "tenant_id" in t.c and tname not in texp.EXPORT_EXCLUDE_TABLES:
            assert tname in man["tables"], tname
    assert "backup_runs" not in man["tables"] and "global_catalog" not in man["tables"]
    for child in texp.CHILD_SCOPES:
        assert child in man["tables"]
    data = {n[:-5]: json.loads(z.read(n)) for n in names if n != "manifest.json"}
    assert {t["id"] for t in data["tenants"]} == {1}
    assert [c["name"] for c in data["customers"]] == ["عميل ورشة 1"]
    assert [e["description"] for e in data["accounting_entries"]] == ["قيد ورشة 1"]
    assert [l["notes"] for l in data["accounting_entry_lines"]] == ["line-t1"]
    assert data["accounting_entry_lines"][0]["amount"] == "1234.56"  # دقة الـ Decimal
    assert [l["description"] for l in data["delivery_order_lines"]] == ["dol-t1"]
    assert [l["notes"] for l in data["sales_order_work_orders"]] == ["sowo-t1"]
    assert [u["username"] for u in data["users"]] == ["user1"]
    assert "password_hash" not in data["users"][0]
    raw = b"".join(z.read(n) for n in names)
    assert b"SECRETHASH" not in raw and b"user2" not in raw and "قيد ورشة 2".encode() not in raw
    assert all(man["tables"][k] == len(v) for k, v in data.items())
    # الملف المؤقت اتمسح
    assert created and not any(os.path.exists(p) for p in created)
    # ورشة 2 ما بتشوفش بيانات ورشة 1
    r2 = await cli2.post(f"{TB}/tenant-export")
    z2 = _zip(r2)
    d2 = {n[:-5]: json.loads(z2.read(n)) for n in z2.namelist() if n != "manifest.json"}
    assert [c["name"] for c in d2["customers"]] == ["عميل ورشة 2"]
    assert [l["notes"] for l in d2["accounting_entry_lines"]] == ["line-t2"]
    assert {t["id"] for t in d2["tenants"]} == {2}


@pytest.mark.asyncio
async def test_tenant_export_json_and_history(cli, cli2, client, sa):
    await _seed_tenant_data()
    r = await cli.post(f"{TB}/tenant-export", params={"format": "json"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    doc = json.loads(r.content)
    assert set(doc) == {"tables", "manifest"}
    assert doc["manifest"]["tables"]["users"] == 1
    assert "password_hash" not in doc["tables"]["users"][0]
    assert b"SECRETHASH" not in r.content
    assert (await cli.post(f"{TB}/tenant-export", params={"format": "xml"})).status_code == 400
    await cli2.post(f"{TB}/tenant-export")
    await _backup(client, sa)  # عملية نظام: ما تظهرش في سجل الورشة
    hist = (await cli.get(f"{TB}/exports")).json()
    assert len(hist) == 1
    h = hist[0]
    assert h["status"] == "success" and h["format"] == "json" and h["size_bytes"] == len(r.content)
    assert h["checksum_sha256"] == hashlib.sha256(r.content).hexdigest()
    assert h["tables"]["users"] == 1 and h["started_at"].endswith("Z") and h["created_by"] == "admin"
    assert int(r.headers["x-export-run-id"]) == h["id"]
    h2 = (await cli2.get(f"{TB}/exports")).json()
    assert len(h2) == 1 and h2[0]["id"] != h["id"]
    assert (await cli.get(f"{TB}/exports", params={"limit": 0})).status_code == 422
    async with TestSessionLocal() as s:
        rows = (await s.execute(text("SELECT tenant_id, kind FROM backup_runs ORDER BY id"))).all()
    assert (None, "full_backup") in [tuple(x) for x in rows]
    assert (1, "tenant_export") in [tuple(x) for x in rows]


@pytest.mark.asyncio
async def test_tenant_export_concurrent_returns_409(cli):
    backups_api._export_running.add(1)
    try:
        r = await cli.post(f"{TB}/tenant-export")
        assert r.status_code == 409
        # التصدير الجاري بتاع ورشة 1 مايمنعش ورشة 2
    finally:
        backups_api._export_running.discard(1)
    assert (await cli.post(f"{TB}/tenant-export")).status_code == 200
    assert 1 not in backups_api._export_running


@pytest.mark.asyncio
async def test_tenant_export_failure_marks_failed_and_cleans_temp(cli, monkeypatch):
    created = []
    real_mkdtemp = tempfile.mkdtemp

    def spy(*a, **k):
        p = real_mkdtemp(*a, **k)
        created.append(p)
        return p
    monkeypatch.setattr(backups_api.tempfile, "mkdtemp", spy)

    async def boom(db, tid, fmt, dest):
        Path(dest).write_bytes(b"partial")
        raise RuntimeError("stream broke")
    monkeypatch.setattr(backups_api.texp, "export_tenant_to_file", boom)
    r = await cli.post(f"{TB}/tenant-export")
    assert r.status_code == 500
    assert created and not any(os.path.exists(p) for p in created)
    hist = (await cli.get(f"{TB}/exports")).json()
    assert hist[0]["status"] == "failed" and hist[0]["error"]
    assert 1 not in backups_api._export_running


@pytest.mark.asyncio
async def test_tenant_export_works_with_audit_flush(cli):
    """BackupRun(tenant_export) بيعدّي على audit_events (create/update) من غير أخطاء."""
    r = await cli.post(f"{TB}/tenant-export")
    assert r.status_code == 200
    async with TestSessionLocal() as s:
        n = (await s.execute(text("SELECT count(*) FROM backup_runs WHERE tenant_id = 1"))).scalar()
    assert n == 1


@pytest.mark.asyncio
async def test_tenant_admin_cannot_see_system_runs(cli, client, sa):
    await _backup(client, sa)
    assert (await cli.get(f"{TB}/exports")).json() == []
    # ولا يقدر يوصل لـ BackupRun النظام عبر أي endpoint سوبر
    assert (await cli.get(BK)).status_code == 401


# ═══════════════ الهجرة ════════════════════════════════════════════
def test_migration_0038_chain():
    vdir = Path(__file__).resolve().parent.parent / "migrations" / "versions"
    src = (vdir / "0038_backup_runs.py").read_text(encoding="utf-8")
    assert re.search(r"^revision = '0038'", src, re.M)
    assert re.search(r"^down_revision = '0037'", src, re.M)
    assert "backup_runs" in src and "_table_exists" in src and "_index_exists" in src
    revs = []
    for f in vdir.glob("*.py"):
        m = re.search(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", f.read_text(encoding="utf-8"), re.M)
        if m:
            revs.append(m.group(1))
    assert len(revs) == len(set(revs)), "duplicate revision ids"


# ═══════════════ تكميلات: عزل/تدقيق/انحراف البنية ═════════════════
@pytest.mark.asyncio
async def test_sa_cannot_reach_tenant_export_rows(cli, client, sa):
    assert (await cli.post(f"{TB}/tenant-export")).status_code == 200
    async with TestSessionLocal() as s:
        rid = (await s.execute(text("SELECT id FROM backup_runs WHERE kind = 'tenant_export'"))).scalar()
    assert rid
    assert all(i["id"] != rid for i in await _items(client, sa))
    assert (await client.get(f"{BK}/{rid}/download", headers=sa)).status_code == 404
    assert (await client.delete(f"{BK}/{rid}", headers=sa)).status_code == 404
    assert (await client.post(f"{BK}/{rid}/verify", headers=sa)).status_code == 404
    assert (await client.post(f"{BK}/{rid}/restore/prepare", headers=sa)).status_code == 404
    # ولا يقدر يفلتر بنوع tenant_export
    assert (await client.get(BK, headers=sa, params={"kind": "tenant_export"})).status_code == 400


@pytest.mark.asyncio
async def test_backup_runs_not_audited(cli, client, sa):
    from app.services import audit as audit_svc
    assert "backup_runs" in audit_svc.IGNORED_TABLES
    assert (await cli.post(f"{TB}/tenant-export")).status_code == 200
    async with TestSessionLocal() as s:
        n = (await s.execute(text("SELECT count(*) FROM audit_logs WHERE entity_type = 'backup_runs'"))).scalar()
    assert n == 0


@pytest.mark.asyncio
async def test_prepare_warns_on_schema_version_drift(client, sa, _env, monkeypatch):
    item = await _backup(client, sa)
    f = _env / item["file_name"]
    con = sqlite3.connect(str(f))
    con.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
    con.execute("DELETE FROM alembic_version")
    con.execute("INSERT INTO alembic_version VALUES ('0001')")
    con.commit()
    con.close()
    await _backdate(item["id"], checksum_sha256=hashlib.sha256(f.read_bytes()).hexdigest())
    monkeypatch.setattr(svc, "_expected_alembic_head", lambda: "0038")
    prep = await _prepare_restore(client, sa, item["id"])
    drift = [w for w in prep["warnings_ar"] if "0001" in w and "0038" in w]
    assert len(drift) == 1
    assert len(prep["warnings_ar"]) == len(svc.restore_warnings_ar()) + 1
    # نفس الإصدار → لا تحذير إضافي. وغير معروف → لا تحذير ولا خطأ
    monkeypatch.setattr(svc, "_expected_alembic_head", lambda: "0001")
    assert svc.schema_drift_warnings(f) == []
    monkeypatch.setattr(svc, "_expected_alembic_head", lambda: None)
    assert svc.schema_drift_warnings(f) == []
    assert svc.schema_drift_warnings(_env / "missing.db") == []


def test_expected_alembic_head_matches_migrations():
    head = svc._expected_alembic_head()
    assert head in (None, "0038") or head > "0038"
