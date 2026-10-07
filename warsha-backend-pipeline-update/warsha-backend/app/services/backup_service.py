"""
backup_service.py — النسخ الاحتياطي والاسترجاع (Backup & Restore)

- نسخة كاملة للنظام: SQLite عبر sqlite3 backup API (+ integrity_check + sha256) أو
  PostgreSQL عبر pg_dump (subprocess، الباسورد في PGPASSWORD فقط).
- مجلد النسخ من ERP_BACKUP_DIR (مرفوض لو جوه ERP_STATIC_DIR لأن main.py بيخدمه بدون مصادقة).
- retention (آخر N نسخة)، تنظيف .part اليتيمة، إغلاق العمليات المعلّقة.
- استرجاع SQLite فقط كملف كامل (خطوتين: prepare ثم restore بتوكن لمرة واحدة + نص تأكيد)
  مع نسخة أمان إجبارية قبله ورجوع تلقائي لو فشل.
- لا يدخل في أي نسخة: jwt_secret.key ولا متغيرات البيئة (ننسخ ملف قاعدة البيانات فقط).

قواعد داخلية:
  * كل استعلام BackupRun هنا بـ tenant_id IS NULL صريح (الخدمة بتشتغل من jobs/سوبر أدمن
    بدون ContextVar للـ tenant، فلا يوجد فلتر تلقائي).
  * لا نمسك AsyncSession طول عملية النسخ/الاسترجاع — جلسات قصيرة من
    app.core.database.AsyncSessionLocal (بنقرأها من الموديول وقت الاستدعاء).
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core import database
from app.core.config import settings
from app.models.models import BackupRun

logger = logging.getLogger("warsha.backup")

APP_VERSION = "4.0.0"

# أنواع العمليات
KIND_FULL = "full_backup"
KIND_PRE_RESTORE = "pre_restore"
KIND_RESTORE = "restore"
KIND_DELETE = "delete"
KIND_TENANT_EXPORT = "tenant_export"
FILE_KINDS = (KIND_FULL, KIND_PRE_RESTORE)
SYSTEM_KINDS = (KIND_FULL, KIND_PRE_RESTORE, KIND_RESTORE, KIND_DELETE)

CONFIRM_TEXT = "RESTORE"
TOKEN_TTL_SECONDS = 300
REAP_AFTER_HOURS = 2
PART_MAX_AGE_SECONDS = 3600
PRE_RESTORE_MIN_KEEP_HOURS = 24

# صيغة pg_dump (custom) — مصدر واحد لكل من الأمر الفعلي وتعليمات الاسترجاع اليدوي
PG_DUMP_FORMAT_FLAG = "-Fc"
PG_FILE_EXT = ".dump"
SQLITE_FILE_EXT = ".db"

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
_BACKUP_NAME_RE = re.compile(r"^warsherp_[A-Za-z0-9._-]+\.(db|dump)$")
REQUIRED_TABLES = ("tenants", "users", "materials", "invoices", "stock_movements", "accounting_entries")

AUDIT_LOG_NAME = "backup_audit.log"
MANIFEST_SUFFIX = ".manifest.json"


class BackupError(HTTPException):
    """خطأ نسخ احتياطي — HTTPException عشان يوصل للواجهة برسالته العربية وكوده مباشرة."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code=status_code, detail=detail)


# ── حالة داخل الذاكرة (عملية uvicorn واحدة) ───────────────────
_backup_lock = asyncio.Lock()
_RESTORING = False
_restore_tokens: Dict[str, Dict[str, Any]] = {}


def is_restoring() -> bool:
    return _RESTORING


def is_busy() -> bool:
    return _backup_lock.locked() or _RESTORING


# ═════════════════════════════════════════════════════════════
# Config (كلها بتتقرأ وقت الاستدعاء عشان الاختبارات تقدر تغيّرها)
# ═════════════════════════════════════════════════════════════

def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        v = int(os.getenv(name, str(default)))
    except ValueError:
        v = default
    return max(v, minimum)


def get_retention() -> int:
    return _env_int("ERP_BACKUP_RETENTION", 14, 1)


def get_keep_pre_restore() -> int:
    return _env_int("ERP_BACKUP_KEEP_PRE_RESTORE", 5, 1)


def auto_enabled() -> bool:
    return os.getenv("ERP_BACKUP_AUTO", "1").strip().lower() not in ("0", "false", "no", "off", "")


def get_interval_hours() -> int:
    return _env_int("ERP_BACKUP_INTERVAL_HOURS", 24, 1)


def get_stale_hours() -> int:
    return _env_int("ERP_BACKUP_STALE_HOURS", 36, 1)


def get_retry_hours() -> int:
    return _env_int("ERP_BACKUP_RETRY_HOURS", 6, 1)


def get_pgdump_timeout() -> int:
    return _env_int("ERP_BACKUP_PGDUMP_TIMEOUT", 1800, 5)


def _restore_grace_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("ERP_BACKUP_RESTORE_GRACE_SECONDS", "1.0")))
    except ValueError:
        return 1.0


def utcnow() -> datetime:
    """UTC naive (نفس عُرف باقي الموديلات)."""
    return datetime.utcnow()


def iso_z(dt: Optional[datetime]) -> Optional[str]:
    return (dt.isoformat() + "Z") if dt else None


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).rstrip("Z"))
    except ValueError:
        return None


def db_engine_kind() -> str:
    return "sqlite" if settings.DATABASE_URL.startswith("sqlite") else "postgresql"


def sqlite_db_path() -> str:
    """مسار ملف SQLite الحي. in-memory يعطي خطأ 400 واضح."""
    db = make_url(settings.DATABASE_URL).database
    if not db or db == ":memory:" or db.startswith("file::memory:"):
        raise BackupError(400, "قاعدة البيانات في الذاكرة (in-memory) — لا يمكن أخذ نسخة احتياطية منها")
    return os.path.abspath(db)


def _default_backup_dir() -> str:
    if db_engine_kind() == "sqlite":
        try:
            return os.path.join(os.path.dirname(sqlite_db_path()), "backups")
        except BackupError:
            pass
    base = os.getenv("ERP_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".warsherp")
    return os.path.join(os.path.abspath(base), "backups")


def get_backup_dir(create: bool = True) -> Path:
    raw = os.getenv("ERP_BACKUP_DIR", "").strip()
    if raw:
        if not os.path.isabs(raw):
            raise BackupError(500, "مجلد النسخ الاحتياطي (ERP_BACKUP_DIR) لازم يكون مسار مطلق")
        d = Path(raw)
    else:
        d = Path(_default_backup_dir())
    static = os.getenv("ERP_STATIC_DIR")
    if static:
        inside = True
        try:
            d.resolve().relative_to(Path(static).resolve())
        except (ValueError, OSError):
            inside = False
        if inside:
            raise BackupError(
                500, "مجلد النسخ الاحتياطي داخل مجلد الواجهة المنشور — غيّر ERP_BACKUP_DIR لمجلد آخر")
    if create:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise BackupError(500, "تعذّر إنشاء مجلد النسخ الاحتياطي — تحقق من الصلاحيات")
    return d


def resolve_backup_path(file_name: Optional[str], must_exist: bool = True) -> Path:
    """مسار ملف نسخة من اسمه فقط (basename) — مع فحص احتواء داخل مجلد النسخ."""
    if (not file_name or not _NAME_RE.match(file_name) or file_name in (".", "..")
            or not _BACKUP_NAME_RE.match(file_name)):
        raise BackupError(400, "اسم ملف النسخة غير صالح")
    d = get_backup_dir(create=False)
    p = d / file_name
    try:
        p.resolve().relative_to(d.resolve())
    except (ValueError, OSError):
        raise BackupError(400, "مسار ملف النسخة غير صالح")
    if must_exist and not p.is_file():
        raise BackupError(404, "ملف النسخة غير موجود على القرص")
    return p


# ═════════════════════════════════════════════════════════════
# أدوات مساعدة
# ═════════════════════════════════════════════════════════════

_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/)(?:[^\s'\"\\/:*?<>|]+[\\/])*([^\s'\"\\/:*?<>|]+)")
_URL_CRED_RE = re.compile(r"(\w+(?:\+\w+)?://)[^/\s@]*@")


def sanitize_error(msg: Any) -> str:
    """يشيل الباسوردات والمسارات المطلقة ويقصّ الرسالة (<=500)."""
    s = str(msg or "")
    s = _URL_CRED_RE.sub(r"\1***@", s)
    try:
        pw = make_url(settings.DATABASE_URL).password
        if pw:
            s = s.replace(pw, "***")
    except Exception:
        pass
    s = _PATH_RE.sub(r"\1", s)
    return s.strip()[:500]


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ro_connect(path) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def sqlite_integrity_check(path) -> str:
    """'ok' أو رسالة المشكلة. ملف مش SQLite أو تالف يعطي رسالة، مش exception."""
    try:
        with open(path, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return "الملف ليس قاعدة بيانات SQLite صالحة"
        conn = _ro_connect(path)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
        if len(rows) == 1 and str(rows[0][0]).lower() == "ok":
            return "ok"
        return "; ".join(str(r[0]) for r in rows[:3])[:300]
    except Exception as e:  # noqa: BLE001
        return sanitize_error(f"فشل فحص السلامة: {e}")


def sqlite_missing_tables(path) -> List[str]:
    try:
        conn = _ro_connect(path)
        try:
            have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        return [t for t in REQUIRED_TABLES if t not in have]
    except Exception:
        return list(REQUIRED_TABLES)


def _new_file_name(kind: str, engine: str) -> str:
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    rnd = secrets.token_hex(2)
    tag = "prerestore" if kind == KIND_PRE_RESTORE else "full"
    ext = SQLITE_FILE_EXT if engine == "sqlite" else PG_FILE_EXT
    return f"warsherp_{tag}_{stamp}_{rnd}{ext}"


def _manifest_path(path: Path) -> Path:
    return path.with_name(path.name + MANIFEST_SUFFIX)


def _write_manifest(path: Path, data: dict) -> None:
    mp = _manifest_path(path)
    tmp = mp.with_name(mp.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, mp)


def _read_manifest(mp: Path) -> Optional[dict]:
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def append_audit_line(op: str, status: str, **fields) -> None:
    """سطر JSON في backup_audit.log (append-only) — يفضل حتى لو الداتابيز اتسترجعت."""
    try:
        d = get_backup_dir()
        rec = {"ts": iso_z(utcnow()), "op": op, "status": status}
        for k, v in fields.items():
            if v is not None:
                rec[k] = sanitize_error(v) if k == "error" else v
        with open(d / AUDIT_LOG_NAME, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        logger.warning("backup audit log write failed", exc_info=True)


def _factory(session_factory=None):
    return session_factory or database.AsyncSessionLocal


def _sys_runs():
    """شرط tenant_id IS NULL صريح (مفيش فلتر تلقائي خارج طلب ورشة)."""
    return BackupRun.tenant_id.is_(None)


# ═════════════════════════════════════════════════════════════
# النسخة الكاملة
# ═════════════════════════════════════════════════════════════

def _sqlite_backup_to(src: str, dst: str) -> None:
    s = sqlite3.connect(src, timeout=30)
    d = sqlite3.connect(dst)
    try:
        s.backup(d, pages=2048, sleep=0.01)
        # ما نسيبش ملفات -wal/-shm جنب النسخة
        d.execute("PRAGMA journal_mode=DELETE").fetchall()
        d.commit()
    finally:
        d.close()
        s.close()


def _check_free_space(dst_dir: Path, needed: int) -> None:
    try:
        free = shutil.disk_usage(str(dst_dir)).free
    except OSError:
        return
    if free < needed:
        raise BackupError(500, "المساحة الفارغة على القرص غير كافية لأخذ نسخة احتياطية")


def _snapshot_sqlite(final: Path) -> dict:
    """يشتغل في thread: نسخ، فحص، checksum، rename. يرجّع {size, sha, integrity}."""
    src = sqlite_db_path()
    src_size = os.path.getsize(src) if os.path.exists(src) else 0
    _check_free_space(final.parent, int(src_size * 1.2) + 1024 * 1024)
    part = final.with_name(final.name + ".part")
    try:
        _sqlite_backup_to(src, str(part))
        integ = sqlite_integrity_check(part)
        if integ != "ok":
            raise BackupError(500, "فشل فحص سلامة النسخة الناتجة — لم تُحفظ")
        sha = sha256_file(part)
        size = part.stat().st_size
        os.replace(part, final)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    return {"size": size, "sha": sha, "integrity": integ, "source_size": src_size}


def find_pg_dump() -> Optional[str]:
    p = os.getenv("ERP_PG_DUMP_PATH", "").strip()
    if p:
        return p if os.path.isfile(p) else None
    return shutil.which("pg_dump")


def _find_pg_restore(pg_dump: str) -> Optional[str]:
    d = os.path.dirname(pg_dump)
    for name in ("pg_restore", "pg_restore.exe"):
        cand = os.path.join(d, name) if d else None
        if cand and os.path.isfile(cand):
            return cand
    return shutil.which("pg_restore")


async def _run_tool(argv: List[str], env: Dict[str, str], timeout: int):
    """يشغّل أداة خارجية (بدون shell). يرجّع (returncode, stderr_text)."""
    proc = await asyncio.create_subprocess_exec(
        *argv, env=env,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise BackupError(500, "انتهت مهلة pg_dump قبل اكتمال النسخة")
    return proc.returncode, (err or b"").decode("utf-8", "replace")


async def _snapshot_postgres(final: Path) -> dict:
    tool = find_pg_dump()
    if not tool:
        raise BackupError(
            503, "أداة pg_dump غير متوفرة على الخادم — ثبّت أدوات عميل PostgreSQL أو اضبط ERP_PG_DUMP_PATH")
    url = make_url(settings.DATABASE_URL)
    part = final.with_name(final.name + ".part")
    argv = [tool, PG_DUMP_FORMAT_FLAG, "--no-owner", "--no-password"]
    if url.host:
        argv += ["-h", url.host]
    if url.port:
        argv += ["-p", str(url.port)]
    if url.username:
        argv += ["-U", url.username]
    argv += ["-d", url.database or "", "-f", str(part)]
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password  # الباسورد في البيئة فقط، مش في argv
    try:
        rc, err = await _run_tool(argv, env, get_pgdump_timeout())
        if rc != 0:
            raise BackupError(500, "فشل pg_dump: " + (sanitize_error(err) or f"رمز الخروج {rc}"))
        if not part.is_file() or part.stat().st_size == 0:
            raise BackupError(500, "pg_dump لم ينتج ملفاً صالحاً")
        # تحقق: pg_restore --list لو متاح، وإلا الهيدر PGDMP
        restore_tool = _find_pg_restore(tool)
        if restore_tool:
            rc2, err2 = await _run_tool([restore_tool, "--list", str(part)], env, 300)
            if rc2 != 0:
                raise BackupError(500, "فشل التحقق من ملف النسخة: " + sanitize_error(err2))
        else:
            with open(part, "rb") as f:
                if f.read(5) != b"PGDMP":
                    raise BackupError(500, "ملف pg_dump الناتج غير صالح")
        sha = await asyncio.to_thread(sha256_file, part)
        size = part.stat().st_size
        os.replace(part, final)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    return {"size": size, "sha": sha, "integrity": "ok", "source_size": None}


async def _create_backup_locked(trigger: str, created_by: str, kind: str = KIND_FULL,
                                session_factory=None, extra_details: Optional[dict] = None) -> BackupRun:
    """ينفّذ النسخة (المتصل ماسك القفل). يسجّل BackupRun دايماً (نجاح أو فشل)."""
    factory = _factory(session_factory)
    engine = db_engine_kind()
    file_name = _new_file_name(kind, engine)
    started = utcnow()
    async with factory() as s:
        run = BackupRun(
            tenant_id=None, kind=kind, trigger=trigger, status="running", db_engine=engine,
            file_name=file_name, created_by=created_by, started_at=started,
            details={"app_version": APP_VERSION, **(extra_details or {})},
        )
        s.add(run)
        await s.commit()
        run_id = run.id

    try:
        final = get_backup_dir() / file_name
        if engine == "sqlite":
            res = await asyncio.to_thread(_snapshot_sqlite, final)
        else:
            res = await _snapshot_postgres(final)
        finished = utcnow()
        manifest = {
            "format_version": 1, "kind": kind, "trigger": trigger, "status": "success",
            "db_engine": engine, "file_name": file_name,
            "created_at": iso_z(started), "finished_at": iso_z(finished),
            "size_bytes": res["size"], "checksum_sha256": res["sha"],
            "integrity_ok": True, "created_by": created_by, "app_version": APP_VERSION,
        }
        await asyncio.to_thread(_write_manifest, final, manifest)
        async with factory() as s:
            run = await s.get(BackupRun, run_id)
            run.status = "success"
            run.finished_at = finished
            run.size_bytes = res["size"]
            run.checksum_sha256 = res["sha"]
            run.integrity_ok = True
            d = dict(run.details or {})
            if res.get("source_size") is not None:
                d["source_db_size"] = res["source_size"]
            run.details = d
            await s.commit()
            await s.refresh(run)
            s.expunge(run)
        append_audit_line(kind, "success", run_id=run_id, file=file_name, by=created_by,
                          trigger=trigger, size=res["size"])
        return run
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, BackupError):
            msg, err = e.detail, e
        elif isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
            msg, err = "انقطعت العملية", None
        else:
            logger.error("backup failed", exc_info=True)
            msg = "فشل النسخ الاحتياطي: " + sanitize_error(f"{type(e).__name__}: {e}")
            err = BackupError(500, msg)
        try:
            async with factory() as s:
                run = await s.get(BackupRun, run_id)
                if run is not None:
                    run.status = "failed"
                    run.finished_at = utcnow()
                    run.file_name = None
                    run.error = sanitize_error(msg)
                    await s.commit()
        except Exception:  # noqa: BLE001
            logger.warning("could not record failed backup run", exc_info=True)
        append_audit_line(kind, "failed", run_id=run_id, by=created_by, trigger=trigger, error=msg)
        if err is None:
            raise
        raise err


async def create_full_backup(trigger: str = "manual", created_by: str = "superadmin",
                             session_factory=None) -> BackupRun:
    """نسخة كاملة الآن. القفل ماسوك يعطي 409 (من غير ما نسيب صف running)."""
    if _RESTORING or _backup_lock.locked():
        raise BackupError(409, "عملية نسخ احتياطي جارية بالفعل")
    await _backup_lock.acquire()
    try:
        run = await _create_backup_locked(trigger, created_by, KIND_FULL, session_factory)
    finally:
        _backup_lock.release()
    try:
        await apply_retention(session_factory)
    except Exception:  # noqa: BLE001
        logger.warning("retention failed", exc_info=True)
    return run


# ═════════════════════════════════════════════════════════════
# Retention / تنظيف / reindex
# ═════════════════════════════════════════════════════════════

def _remove_backup_files(file_name: str) -> bool:
    """يمسح الملف + الـ manifest. True لو الملف اتمسح/مش موجود."""
    try:
        p = resolve_backup_path(file_name, must_exist=False)
    except BackupError:
        return False
    ok = True
    for f in (p, _manifest_path(p)):
        for attempt in (1, 2):
            try:
                if f.exists():
                    f.unlink()
                break
            except PermissionError:
                if attempt == 2:
                    ok = False
                else:
                    time.sleep(0.2)
            except OSError:
                ok = False
                break
    return ok


async def apply_retention(session_factory=None) -> dict:
    """يحتفظ بآخر N نسخة كاملة ناجحة وآخر K نسخة أمان (مع حماية أي نسخة أمان أحدث من 24 ساعة)."""
    factory = _factory(session_factory)
    keep_full = get_retention()
    keep_pre = get_keep_pre_restore()
    pruned: List[str] = []
    async with factory() as s:
        for kind, keep in ((KIND_FULL, keep_full), (KIND_PRE_RESTORE, keep_pre)):
            rows = (await s.execute(
                select(BackupRun).where(
                    _sys_runs(), BackupRun.kind == kind, BackupRun.status == "success",
                    BackupRun.file_deleted.is_(False), BackupRun.file_name.isnot(None),
                ).order_by(BackupRun.id.desc())
            )).scalars().all()
            for r in rows[keep:]:
                if kind == KIND_PRE_RESTORE and r.finished_at \
                        and utcnow() - r.finished_at < timedelta(hours=PRE_RESTORE_MIN_KEEP_HOURS):
                    continue
                if await asyncio.to_thread(_remove_backup_files, r.file_name):
                    r.file_deleted = True
                    d = dict(r.details or {})
                    d["pruned_by"] = "retention"
                    r.details = d
                    pruned.append(r.file_name)
        await s.commit()
    if pruned:
        append_audit_line("retention", "success", pruned=len(pruned))
    return {"pruned": pruned}


def cleanup_part_files(max_age_seconds: int = PART_MAX_AGE_SECONDS) -> int:
    """يمسح ملفات .part اليتيمة الأقدم من ساعة."""
    n = 0
    try:
        d = get_backup_dir()
        now = time.time()
        for p in d.glob("*.part"):
            try:
                if now - p.stat().st_mtime > max_age_seconds:
                    p.unlink()
                    n += 1
            except OSError:
                pass
    except BackupError:
        pass
    return n


def _apply_manifest_to_row(row: BackupRun, m: dict) -> None:
    row.status = "success"
    row.size_bytes = m.get("size_bytes", row.size_bytes)
    row.checksum_sha256 = m.get("checksum_sha256", row.checksum_sha256)
    row.integrity_ok = bool(m.get("integrity_ok", True))
    row.finished_at = _parse_iso(m.get("finished_at")) or row.finished_at or utcnow()
    row.error = None


async def reindex_backup_dir(session_factory=None) -> dict:
    """يطابق صفوف BackupRun مع الـ manifests على القرص:
    - manifest بدون صف (مثلاً بعد استرجاع) يُنشأ له صف.
    - صف عالق running/failed ومعاه manifest ناجح (لقطة النسخة بتحمل صف نفسها بحالة running)
      يُصحَّح لـ success.
    """
    factory = _factory(session_factory)
    try:
        d = get_backup_dir()
    except BackupError:
        return {"created": 0, "reconciled": 0}
    manifests = []
    for mp in d.glob("*" + MANIFEST_SUFFIX):
        m = _read_manifest(mp)
        fn = mp.name[: -len(MANIFEST_SUFFIX)]
        if not m or not _BACKUP_NAME_RE.match(fn) or m.get("file_name") != fn:
            continue
        if m.get("status") != "success" or m.get("kind") not in FILE_KINDS:
            continue
        if not (d / fn).is_file():
            continue
        manifests.append((fn, m))
    if not manifests:
        return {"created": 0, "reconciled": 0}
    created = reconciled = 0
    async with factory() as s:
        rows = (await s.execute(
            select(BackupRun).where(_sys_runs(), BackupRun.kind.in_(FILE_KINDS),
                                    BackupRun.file_name.isnot(None))
        )).scalars().all()
        by_name = {r.file_name: r for r in rows}
        for fn, m in manifests:
            r = by_name.get(fn)
            if r is None:
                s.add(BackupRun(
                    tenant_id=None, kind=m["kind"], trigger=m.get("trigger") or "manual",
                    status="success", db_engine=m.get("db_engine") or "sqlite", file_name=fn,
                    size_bytes=m.get("size_bytes"), checksum_sha256=m.get("checksum_sha256"),
                    integrity_ok=True, created_by=m.get("created_by"),
                    started_at=_parse_iso(m.get("created_at")) or utcnow(),
                    finished_at=_parse_iso(m.get("finished_at")),
                    details={"reindexed": True, "app_version": m.get("app_version")},
                ))
                created += 1
            elif r.status != "success":
                _apply_manifest_to_row(r, m)
                reconciled += 1
            elif r.file_deleted:
                # الملف + manifest موجودين فعلياً: الصف من لقطة أقدم
                r.file_deleted = False
                reconciled += 1
        if created or reconciled:
            await s.commit()
    return {"created": created, "reconciled": reconciled}


async def reap_stale_runs(session_factory=None) -> int:
    """يقفل صفوف running العالقة كـ failed (انقطعت العملية). لا يلمس صف له manifest ناجح."""
    factory = _factory(session_factory)
    now = utcnow()
    n = 0
    async with factory() as s:
        rows = (await s.execute(
            select(BackupRun).where(_sys_runs(), BackupRun.status == "running")
        )).scalars().all()
        for r in rows:
            age = now - (r.started_at or now)
            stale = age > timedelta(hours=REAP_AFTER_HOURS) or \
                (not _backup_lock.locked() and not _RESTORING and age > timedelta(minutes=5))
            if not stale:
                continue
            if r.file_name and r.kind in FILE_KINDS:
                try:
                    mp = _manifest_path(resolve_backup_path(r.file_name, must_exist=False))
                    m = _read_manifest(mp) if mp.exists() else None
                except BackupError:
                    m = None
                if m and m.get("status") == "success":
                    _apply_manifest_to_row(r, m)
                    continue
            r.status = "failed"
            r.finished_at = now
            r.error = "انقطعت العملية"
            r.file_name = None
            n += 1
        if rows:
            await s.commit()
    return n


# ═════════════════════════════════════════════════════════════
# الحالة (status)
# ═════════════════════════════════════════════════════════════

async def _last_success(s) -> Optional[BackupRun]:
    rows = (await s.execute(
        select(BackupRun).where(
            _sys_runs(), BackupRun.kind.in_(FILE_KINDS), BackupRun.status == "success",
            BackupRun.file_deleted.is_(False), BackupRun.finished_at.isnot(None),
        ).order_by(BackupRun.finished_at.desc(), BackupRun.id.desc()).limit(10)
    )).scalars().all()
    for r in rows:
        try:
            resolve_backup_path(r.file_name)
            return r
        except BackupError:
            continue
    return None


async def last_success_info(session_factory=None) -> Optional[BackupRun]:
    async with _factory(session_factory)() as s:
        return await _last_success(s)


async def compute_status(session_factory=None) -> dict:
    await reap_stale_runs(session_factory)
    await reindex_backup_dir(session_factory)
    engine = db_engine_kind()
    stale_hours = get_stale_hours()
    async with _factory(session_factory)() as s:
        last = await _last_success(s)
        fail = (await s.execute(
            select(BackupRun).where(_sys_runs(), BackupRun.kind.in_(FILE_KINDS),
                                    BackupRun.status == "failed")
            .order_by(BackupRun.id.desc()).limit(1)
        )).scalars().first()
    age = None
    if last and last.finished_at:
        age = round((utcnow() - last.finished_at).total_seconds() / 3600.0, 2)
    if last is None:
        level, stale = "critical", True
        msg = "لم يتم أخذ أي نسخة احتياطية بعد — خُذ نسخة الآن"
    elif age is not None and age > stale_hours:
        level, stale = "warning", True
        msg = f"آخر نسخة احتياطية قديمة (منذ {int(age)} ساعة) — خُذ نسخة جديدة"
    else:
        level, stale = "ok", False
        msg = "النسخ الاحتياطي سليم — آخر نسخة حديثة"
    pg_tool = None
    pg_hint = None
    if engine == "postgresql":
        pg_tool = find_pg_dump() is not None
        pg_hint = os.getenv("ERP_PG_DUMP_PATH") or ("pg_dump" if pg_tool else None)
    try:
        bdir = str(get_backup_dir(create=False))
    except BackupError:
        bdir = os.getenv("ERP_BACKUP_DIR", "")
    return {
        "db_engine": engine,
        "restore_mode": "file" if engine == "sqlite" else "manual",
        "auto_enabled": auto_enabled(),
        "interval_hours": get_interval_hours(),
        "retention": get_retention(),
        "keep_pre_restore": get_keep_pre_restore(),
        "stale_hours": stale_hours,
        "backup_dir": bdir,
        "tool": {"pg_dump_available": pg_tool, "pg_dump_path_hint": pg_hint},
        "last_success": None if not last else {
            "id": last.id, "file_name": last.file_name, "finished_at": iso_z(last.finished_at),
            "size_bytes": last.size_bytes, "trigger": last.trigger,
        },
        "last_failure": None if not fail else {
            "id": fail.id, "finished_at": iso_z(fail.finished_at), "error": fail.error,
        },
        "age_hours": age,
        "stale": stale,
        "level": level,
        "message_ar": msg,
        "running": is_busy(),
    }


# ═════════════════════════════════════════════════════════════
# عناصر القائمة
# ═════════════════════════════════════════════════════════════

def run_to_item(run: BackupRun, engine: Optional[str] = None) -> dict:
    engine = engine or db_engine_kind()
    exists = False
    if run.kind in FILE_KINDS and run.file_name and not run.file_deleted:
        try:
            resolve_backup_path(run.file_name)
            exists = True
        except BackupError:
            exists = False
    ok_file = run.kind in FILE_KINDS and run.status == "success" and exists and not run.file_deleted
    return {
        "id": run.id, "kind": run.kind, "trigger": run.trigger, "status": run.status,
        "db_engine": run.db_engine, "file_name": run.file_name, "file_exists": exists,
        "file_deleted": bool(run.file_deleted), "size_bytes": run.size_bytes,
        "checksum_sha256": run.checksum_sha256, "integrity_ok": run.integrity_ok,
        "created_by": run.created_by, "started_at": iso_z(run.started_at),
        "finished_at": iso_z(run.finished_at), "error": run.error, "details": run.details,
        "restorable": bool(ok_file and engine == "sqlite" and run.db_engine == "sqlite"),
        "downloadable": bool(ok_file),
    }


async def list_runs(limit: int = 50, kind: Optional[str] = None, session_factory=None) -> List[dict]:
    await reap_stale_runs(session_factory)
    await reindex_backup_dir(session_factory)
    q = select(BackupRun).where(_sys_runs())
    if kind:
        q = q.where(BackupRun.kind == kind)
    q = q.order_by(BackupRun.id.desc()).limit(limit)
    async with _factory(session_factory)() as s:
        rows = (await s.execute(q)).scalars().all()
    engine = db_engine_kind()
    return [run_to_item(r, engine) for r in rows]


async def get_system_run(run_id: int, session_factory=None) -> BackupRun:
    async with _factory(session_factory)() as s:
        run = (await s.execute(
            select(BackupRun).where(BackupRun.id == run_id, _sys_runs())
        )).scalars().first()
        if run is None:
            raise BackupError(404, "النسخة غير موجودة")
        s.expunge(run)
        return run


def file_path_for_run(run: BackupRun) -> Path:
    """مسار ملف نسخة صف: 400 لو النوع بلا ملف، 410 لو اتحذف، 404 لو مفقود."""
    if run.kind not in FILE_KINDS:
        raise BackupError(400, "هذه العملية لا تحتوي على ملف")
    if run.file_deleted:
        raise BackupError(410, "ملف هذه النسخة تم حذفه")
    if not run.file_name:
        raise BackupError(404, "لا يوجد ملف لهذه النسخة")
    return resolve_backup_path(run.file_name)


# ═════════════════════════════════════════════════════════════
# حذف / تحقق
# ═════════════════════════════════════════════════════════════

async def delete_backup(run_id: int, created_by: str = "superadmin", session_factory=None) -> dict:
    if _RESTORING:
        raise BackupError(409, "الاسترجاع جارٍ الآن — لا يمكن حذف نسخ في هذه اللحظة")
    factory = _factory(session_factory)
    async with factory() as s:
        run = (await s.execute(
            select(BackupRun).where(BackupRun.id == run_id, _sys_runs()))).scalars().first()
        if run is None:
            raise BackupError(404, "النسخة غير موجودة")
        if run.kind not in FILE_KINDS:
            raise BackupError(400, "هذه العملية لا تحتوي على ملف")
        if run.status == "running":
            raise BackupError(409, "النسخة قيد التنفيذ الآن — لا يمكن حذفها")
        already = bool(run.file_deleted)
        fname = run.file_name
        if not already and fname:
            ok = await asyncio.to_thread(_remove_backup_files, fname)
            if not ok:
                raise BackupError(409, "تعذّر حذف الملف (قيد الاستخدام) — حاول مرة أخرى")
            run.file_deleted = True
            d = dict(run.details or {})
            d["deleted_by"] = created_by
            run.details = d
        now = utcnow()
        s.add(BackupRun(
            tenant_id=None, kind=KIND_DELETE, trigger="delete", status="success",
            db_engine=db_engine_kind(), file_name=None, created_by=created_by,
            started_at=now, finished_at=now,
            details={"target_run_id": run.id, "file_name": fname, "already_deleted": already},
        ))
        await s.commit()
    append_audit_line("delete", "success", run_id=run_id, file=fname, by=created_by,
                      already_deleted=already)
    return {"message": "تم حذف النسخة" if not already else "النسخة محذوفة بالفعل", "id": run_id}


async def verify_backup(run_id: int, session_factory=None) -> dict:
    run = await get_system_run(run_id, session_factory)
    path = file_path_for_run(run)

    def _work():
        sha = sha256_file(path)
        if run.db_engine == "sqlite":
            integ = sqlite_integrity_check(path)
        else:
            with open(path, "rb") as f:
                integ = "ok" if f.read(5) == b"PGDMP" else "الملف ليس نسخة pg_dump صالحة"
        return sha, integ, path.stat().st_size

    sha, integ, size = await asyncio.to_thread(_work)
    match = bool(run.checksum_sha256) and sha == run.checksum_sha256
    return {"ok": bool(match and integ == "ok"), "checksum_match": match,
            "integrity": integ, "size_bytes": size}


# ═════════════════════════════════════════════════════════════
# الاسترجاع
# ═════════════════════════════════════════════════════════════

def restore_warnings_ar() -> List[str]:
    return [
        "سيتم استبدال كل بيانات النظام (كل الورش) بمحتوى النسخة المحددة — أي بيانات أحدث منها ستضيع",
        "سجل التدقيق وبيانات الموافقات وكلمات مرور المستخدمين ستعود لحالتها وقت أخذ النسخة",
        "ستُنشأ نسخة أمان تلقائياً قبل الاسترجاع — هي الطريق الوحيد للرجوع",
        "كل المستخدمين في كل الورش سيحتاجون لتسجيل الدخول من جديد",
    ]


def restore_instructions() -> dict:
    engine = db_engine_kind()
    if engine == "sqlite":
        return {
            "supported_in_app": True, "engine": engine,
            "steps_ar": [
                "اختر النسخة من القائمة واضغط «استرجاع»",
                "اقرأ التحذيرات واكتب كلمة التأكيد المطلوبة",
                "سيُنشئ النظام نسخة أمان تلقائياً ثم يستبدل قاعدة البيانات بالنسخة المختارة",
                "سجّل الدخول من جديد بعد انتهاء الاسترجاع",
            ],
            "commands": [],
            "warnings_ar": restore_warnings_ar(),
        }
    return {
        "supported_in_app": False, "engine": engine,
        "steps_ar": [
            "أوقف تطبيق Warsh ERP بالكامل (كل العمليات/الحاويات التي تتصل بقاعدة البيانات)",
            "حمّل ملف النسخة (" + PG_FILE_EXT + ") من قائمة النسخ إلى الخادم",
            "(موصى به) خذ نسخة pg_dump إضافية من الحالة الحالية قبل الاسترجاع",
            "نفّذ أمر pg_restore أدناه على قاعدة البيانات المطلوبة",
            "نفّذ alembic upgrade head لتحديث البنية إلى آخر إصدار",
            "شغّل التطبيق وسجّل الدخول من جديد",
        ],
        "commands": [
            {"label_ar": "نسخة أمان من الحالة الحالية",
             "command": f"pg_dump {PG_DUMP_FORMAT_FLAG} --no-owner -h <HOST> -p <PORT> -U <USER> -d <DB_NAME> -f before_restore{PG_FILE_EXT}"},
            {"label_ar": "استرجاع النسخة",
             "command": "pg_restore --clean --if-exists --no-owner -h <HOST> -p <PORT> -U <USER> -d <DB_NAME> <BACKUP_FILE>"},
            {"label_ar": "تحديث بنية الجداول",
             "command": "alembic upgrade head"},
        ],
        "warnings_ar": restore_warnings_ar(),
    }


def _token_hash(sa_token: str) -> str:
    return hashlib.sha256((sa_token or "").encode()).hexdigest()


def _prune_tokens() -> None:
    now = time.time()
    for t in [t for t, v in _restore_tokens.items() if v["expires_at"] < now]:
        _restore_tokens.pop(t, None)
    if len(_restore_tokens) > 200:
        for t in sorted(_restore_tokens, key=lambda k: _restore_tokens[k]["expires_at"])[:100]:
            _restore_tokens.pop(t, None)


def _validate_restore_source(path: Path, expected_sha: Optional[str]) -> None:
    """sha256 + integrity + الجداول الأساسية + مش هو الملف الحي. يرفع BackupError."""
    if not expected_sha or sha256_file(path) != expected_sha:
        raise BackupError(409, "الملف تالف أو تم العبث به")
    if sqlite_integrity_check(path) != "ok":
        raise BackupError(409, "الملف تالف أو تم العبث به")
    if sqlite_missing_tables(path):
        raise BackupError(409, "الملف تالف أو تم العبث به")
    try:
        same = os.path.samefile(path, sqlite_db_path())
    except FileNotFoundError:
        same = False
    if same:
        raise BackupError(400, "لا يمكن الاسترجاع من ملف قاعدة البيانات الحية نفسها")


def _expected_alembic_head() -> Optional[str]:
    """آخر revision في مجلد migrations (None لو المجلد/alembic مش متاح، مثلاً نسخة سطح المكتب)."""
    try:
        from alembic.script import ScriptDirectory
        mig = Path(__file__).resolve().parents[2] / "migrations"
        if not mig.is_dir():
            return None
        heads = ScriptDirectory(str(mig)).get_heads()
        return heads[0] if len(heads) == 1 else None
    except Exception:  # noqa: BLE001
        return None


def _backup_alembic_version(path) -> Optional[str]:
    try:
        conn = _ro_connect(path)
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        finally:
            conn.close()
        return str(row[0]) if row else None
    except Exception:  # noqa: BLE001
        return None


def schema_drift_warnings(path) -> List[str]:
    """تحذير (مش منع) لو إصدار بنية النسخة مختلف عن إصدار الكود الحالي. يرجع [] لو غير معروف."""
    try:
        head = _expected_alembic_head()
        have = _backup_alembic_version(path)
        if head and have and have != head:
            return [
                f"إصدار بنية قاعدة البيانات في النسخة ({have}) يختلف عن إصدار النظام الحالي ({head}) — "
                "قد يلزم تنفيذ alembic upgrade head بعد الاسترجاع قبل تشغيل النظام"
            ]
    except Exception:  # noqa: BLE001
        pass
    return []


def _not_sqlite_error() -> BackupError:
    return BackupError(
        400, "الاسترجاع داخل التطبيق متاح لـ SQLite فقط — اتبع الخطوات اليدوية في /restore-instructions")


async def prepare_restore(run_id: int, sa_token: str, session_factory=None) -> dict:
    if db_engine_kind() != "sqlite":
        raise _not_sqlite_error()
    run = await get_system_run(run_id, session_factory)
    if run.kind not in FILE_KINDS:
        raise BackupError(400, "هذه العملية ليست نسخة قابلة للاسترجاع")
    if run.file_deleted:
        raise BackupError(410, "ملف هذه النسخة تم حذفه")
    if run.status != "success" or run.db_engine != "sqlite":
        raise BackupError(400, "هذه النسخة غير قابلة للاسترجاع")
    try:
        path = resolve_backup_path(run.file_name)
    except BackupError as e:
        if e.status_code == 404:
            raise BackupError(409, "الملف تالف أو تم العبث به")
        raise
    await asyncio.to_thread(_validate_restore_source, path, run.checksum_sha256)
    _prune_tokens()
    token = secrets.token_urlsafe(32)
    _restore_tokens[token] = {
        "run_id": run.id, "checksum": run.checksum_sha256,
        "bound_to": _token_hash(sa_token), "expires_at": time.time() + TOKEN_TTL_SECONDS,
    }
    return {
        "confirm_token": token, "expires_in_seconds": TOKEN_TTL_SECONDS, "confirm_text": CONFIRM_TEXT,
        "backup": {"id": run.id, "file_name": run.file_name, "size_bytes": run.size_bytes,
                   "created_at": iso_z(run.started_at), "checksum_sha256": run.checksum_sha256},
        "warnings_ar": restore_warnings_ar() + await asyncio.to_thread(schema_drift_warnings, path),
    }


def _consume_token(token: str, run_id: int, sa_token: str, confirm_text: str) -> dict:
    """يستهلك التوكن (pop) قبل أي شغل — الضغط المزدوج لا يشغّل العملية مرتين."""
    entry = _restore_tokens.pop(token or "", None)
    if entry is None:
        raise BackupError(400, "رمز التأكيد غير صالح أو مستخدم من قبل — اطلب رمزاً جديداً")
    if entry["expires_at"] < time.time():
        raise BackupError(400, "انتهت صلاحية رمز التأكيد — اطلب رمزاً جديداً")
    if entry["bound_to"] != _token_hash(sa_token):
        raise BackupError(400, "رمز التأكيد غير صالح لهذه الجلسة")
    if entry["run_id"] != run_id:
        raise BackupError(400, "رمز التأكيد غير صالح لهذه النسخة")
    if (confirm_text or "").strip() != CONFIRM_TEXT:
        raise BackupError(400, "نص التأكيد غير صحيح — اطلب رمزاً جديداً")
    return entry


def _restore_in_place(src_path: str, live_path: str) -> str:
    """يشتغل في thread: يكتب محتوى src فوق قاعدة البيانات الحية بـ backup API ثم يفحصها."""
    src = _ro_connect(src_path)
    try:
        dst = sqlite3.connect(live_path, timeout=30)
        try:
            src.backup(dst, pages=-1)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    return sqlite_integrity_check(live_path)


async def _record_restore_row(factory, run: BackupRun, safety_name: Optional[str], started: datetime,
                              failed: bool, error: Optional[str] = None,
                              safety_run_id: Optional[int] = None,
                              schema_synced: Optional[bool] = None) -> None:
    try:
        async with factory() as s:
            s.add(BackupRun(
                tenant_id=None, kind=KIND_RESTORE, trigger="restore",
                status="failed" if failed else "success", db_engine="sqlite", file_name=None,
                created_by="superadmin", started_at=started, finished_at=utcnow(),
                error=sanitize_error(error) if error else None,
                details={"source_run_id": run.id, "source_file": run.file_name,
                         "safety_backup_run_id": safety_run_id, "safety_file": safety_name,
                         "schema_synced": schema_synced},
            ))
            await s.commit()
    except Exception:  # noqa: BLE001
        logger.warning("could not record restore run", exc_info=True)


async def restore_sqlite_backup(run_id: int, confirm_token: str, confirm_text: str, sa_token: str,
                                session_factory=None) -> dict:
    global _RESTORING
    if db_engine_kind() != "sqlite":
        raise _not_sqlite_error()
    entry = _consume_token(confirm_token, run_id, sa_token, confirm_text)
    run = await get_system_run(run_id, session_factory)
    if run.kind not in FILE_KINDS:
        raise BackupError(400, "هذه العملية ليست نسخة قابلة للاسترجاع")
    if run.file_deleted:
        raise BackupError(410, "ملف هذه النسخة تم حذفه")
    if run.status != "success" or run.db_engine != "sqlite":
        raise BackupError(400, "هذه النسخة غير قابلة للاسترجاع")
    if _RESTORING or _backup_lock.locked():
        raise BackupError(409, "عملية نسخ احتياطي أو استرجاع جارية بالفعل")
    await _backup_lock.acquire()
    _RESTORING = True
    started = utcnow()
    factory = _factory(session_factory)
    live_touched = False
    try:
        # مهلة قصيرة عشان الطلبات الجارية تخلص (الـ middleware بدأ يرجّع 503 للطلبات الجديدة)
        grace = _restore_grace_seconds()
        if grace:
            await asyncio.sleep(grace)
        src_path = resolve_backup_path(run.file_name)
        await asyncio.to_thread(_validate_restore_source, src_path, entry["checksum"])
        # 1) نسخة أمان إجبارية — لو فشلت نوقف ولا نلمس الداتابيز الحية
        try:
            safety = await _create_backup_locked(
                "pre_restore", "superadmin", KIND_PRE_RESTORE, session_factory,
                {"before_restore_of": run.id})
        except BackupError as e:
            raise BackupError(
                500, "فشل إنشاء نسخة الأمان قبل الاسترجاع — لم يتم تغيير قاعدة البيانات (" + str(e.detail) + ")")
        safety_name = safety.file_name
        safety_path = resolve_backup_path(safety_name)
        live = sqlite_db_path()
        # 2) أغلق اتصالات التطبيق المجمّعة ثم انسخ فوق الملف الحي
        await database.engine.dispose()
        failure: Optional[str] = None
        live_touched = True
        try:
            integ = await asyncio.to_thread(_restore_in_place, str(src_path), live)
            if integ != "ok":
                failure = "فشل فحص سلامة قاعدة البيانات بعد الاسترجاع"
        except Exception as e:  # noqa: BLE001
            logger.error("restore copy failed", exc_info=True)
            failure = "فشل نسخ البيانات أثناء الاسترجاع: " + sanitize_error(e)
        if failure:
            try:
                integ2 = await asyncio.to_thread(_restore_in_place, str(safety_path), live)
                rolled_back = integ2 == "ok"
            except Exception:  # noqa: BLE001
                logger.error("rollback from safety backup failed", exc_info=True)
                rolled_back = False
            await database.engine.dispose()
            append_audit_line("restore", "failed", run_id=run_id, file=run.file_name, by="superadmin",
                              error=failure, rolled_back=rolled_back)
            if rolled_back:
                await _record_restore_row(
                    factory, run, safety_name, started, failed=True,
                    error=failure + " — تم الرجوع للحالة السابقة من نسخة الأمان")
                raise BackupError(500, failure + " — تم الرجوع تلقائياً للحالة السابقة من نسخة الأمان")
            raise BackupError(
                500, failure + " — وفشل الرجوع التلقائي! استخدم ملف نسخة الأمان (" + str(safety_name)
                + ") للاسترجاع اليدوي فوراً")
        # 3) رقّي البنية لو النسخة أقدم من الكود الحالي
        schema_synced = True
        try:
            from app.desktop import _sqlite_add_missing_columns
            async with database.engine.begin() as conn:
                await conn.run_sync(database.Base.metadata.create_all)
                await conn.run_sync(_sqlite_add_missing_columns)
        except Exception:  # noqa: BLE001
            schema_synced = False
            logger.error("post-restore schema sync failed", exc_info=True)
        await database.engine.dispose()
        # 4) الداتابيز المسترجعة مفيهاش صفوف العمليات الأحدث: نعيد بناءها من الـ manifests
        await reindex_backup_dir(session_factory)
        async with factory() as s:
            sr = (await s.execute(select(BackupRun).where(
                _sys_runs(), BackupRun.file_name == safety_name))).scalars().first()
            safety_run_id = sr.id if sr else None
            src_row = (await s.execute(select(BackupRun).where(
                _sys_runs(), BackupRun.file_name == run.file_name))).scalars().first()
            restored_from_id = src_row.id if src_row else run.id
        await _record_restore_row(factory, run, safety_name, started, failed=False,
                                  safety_run_id=safety_run_id, schema_synced=schema_synced)
        append_audit_line("restore", "success", run_id=restored_from_id, file=run.file_name,
                          by="superadmin", safety_file=safety_name)
        return {
            "message": "تم الاسترجاع بنجاح — سجّل الدخول من جديد",
            "restored_from": {"id": restored_from_id, "file_name": run.file_name},
            "safety_backup": {"run_id": safety_run_id, "file_name": safety_name},
            "requires_relogin": True,
        }
    except BackupError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("restore failed", exc_info=True)
        raise BackupError(500, "فشل الاسترجاع: " + sanitize_error(f"{type(e).__name__}: {e}")
                          + ("" if live_touched else " — لم يتم تغيير قاعدة البيانات"))
    finally:
        _RESTORING = False
        _backup_lock.release()
        try:
            await apply_retention(session_factory)
        except Exception:  # noqa: BLE001
            logger.warning("retention after restore failed", exc_info=True)
