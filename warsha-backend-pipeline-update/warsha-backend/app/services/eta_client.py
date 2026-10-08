"""
eta_client.py — عميل HTTP لواجهة مصلحة الضرائب المصرية (ETA e-Invoicing API).

*** غير مُجرَّب على بيئة ETA الفعلية. *** المسارات وأسماء الحقول مكتوبة من وصف الـ SDK
العام ومُختبَرة فقط ضد محاكاة (httpx.MockTransport). يحتاج شهادة توقيع + client_id/secret + بيئة تجريبية.

  * OAuth: POST {id_base}/connect/token (client_credentials, scope=InvoicingAPI) + كاش للتوكن في الذاكرة.
  * submit / get_submission / search_by_internal_id / cancel.
  * retries: أخطاء الاتصال و5xx و429 (يحترم Retry-After بحد 60s) بـ backoff أُسّي 2s/4s/8s + jitter؛
    401 = إعادة مصادقة مرة واحدة فقط؛ بقية 4xx لا تُعاد وتتحول لـ EtaPermanentError.
  * كل الاتصالات تمر عبر http_factory (افتراضيًا httpx.AsyncClient) — الاختبارات تحقن MockTransport.
  * الأسرار وترويسة Authorization لا تُكتب في السجلات ولا في نصوص الاستثناءات.
"""
import asyncio
import ipaddress
import logging
import os
import random
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("warsha.einvoice")

# ── البيئات ───────────────────────────────────────────────────
ENV_PRESETS: Dict[str, Dict[str, str]] = {
    "preprod": {
        "id": "https://id.preprod.eta.gov.eg",
        "api": "https://api.preprod.invoicing.eta.gov.eg",
        "portal": "https://preprod.invoicing.eta.gov.eg",
    },
    "prod": {
        "id": "https://id.eta.gov.eg",
        "api": "https://api.invoicing.eta.gov.eg",
        "portal": "https://invoicing.eta.gov.eg",
    },
}
VALID_ENVIRONMENTS = ("preprod", "prod", "custom")
PROD_API_HOST = urlsplit(ENV_PRESETS["prod"]["api"]).hostname
TOKEN_SCOPE = "InvoicingAPI"
MAX_RETRY_AFTER = 60.0
DEFAULT_BACKOFF_BASE = 2.0


class EtaError(Exception):
    """أساس أخطاء العميل — الرسالة عربية آمنة للعرض (بلا أسرار)."""


class EtaTransientError(EtaError):
    """خطأ مؤقت (اتصال/مهلة/5xx/429) — يُعاد لاحقًا."""


class EtaAuthError(EtaError):
    """بيانات الاعتماد مرفوضة (401/403 أو فشل التوكن)."""


class EtaPermanentError(EtaError):
    """رفض دائم (4xx) — يحمل قائمة الأخطاء المُحلّلة [{code,message,target}]."""

    def __init__(self, message: str, status_code: int = 400, errors: Optional[List[dict]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.errors = errors or []


class UnsafeUrlError(ValueError):
    """رابط غير مسموح (غير https / userinfo / عنوان خاص)."""


def allow_private_hosts() -> bool:
    return (os.getenv("ERP_EINVOICE_ALLOW_PRIVATE_SIGNER") or "").strip() == "1"


def validate_https_url(url: str, *, allow_private: Optional[bool] = None) -> str:
    """https فقط، بلا userinfo، وبلا عناوين محلية/خاصة حرفية (IP literal) أو localhost —
    ما لم يُضبط ERP_EINVOICE_ALLOW_PRIVATE_SIGNER=1 (signer داخلي على الشبكة المحلية).
    لا يحل DNS (قرار مقصود: تجنّب التعقيد؛ التحقق الكامل من SSRF مسؤولية الشبكة)."""
    if allow_private is None:
        allow_private = allow_private_hosts()
    u = (url or "").strip()
    try:
        parts = urlsplit(u)
        host = parts.hostname
    except ValueError:
        raise UnsafeUrlError("الرابط غير صالح")
    if parts.scheme != "https":
        raise UnsafeUrlError("الرابط يجب أن يبدأ بـ https://")
    if not host:
        raise UnsafeUrlError("الرابط غير صالح")
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise UnsafeUrlError("لا يُسمح ببيانات مستخدم داخل الرابط")
    if not allow_private:
        h = host.lower()
        if h == "localhost" or h.endswith(".localhost") or h.endswith(".local"):
            raise UnsafeUrlError("لا يُسمح بعناوين محلية")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local
                               or ip.is_reserved or ip.is_unspecified or ip.is_multicast):
            raise UnsafeUrlError("لا يُسمح بعناوين IP خاصة أو محلية")
    return u.rstrip("/")


def default_http_factory() -> httpx.AsyncClient:
    """يُستبدل في الاختبارات (monkeypatch) بمصنع يعيد AsyncClient(transport=MockTransport)."""
    return httpx.AsyncClient(timeout=30.0, follow_redirects=False)


def resolve_base_urls(environment: str, custom_id: Optional[str] = None,
                      custom_api: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    if environment in ENV_PRESETS:
        return ENV_PRESETS[environment]["id"], ENV_PRESETS[environment]["api"]
    if environment == "custom":
        return (custom_id.rstrip("/") if custom_id else None,
                custom_api.rstrip("/") if custom_api else None)
    return None, None


def portal_base(environment: str) -> Optional[str]:
    p = ENV_PRESETS.get(environment)
    return p["portal"] if p else None


# ── كاش التوكن (في الذاكرة فقط) ─────────────────────────────────
_TOKEN_CACHE: Dict[tuple, Tuple[str, float]] = {}


def clear_token_cache() -> None:
    _TOKEN_CACHE.clear()


def parse_errors(body) -> List[dict]:
    """يطبّع أجسام أخطاء ETA/OAuth المختلفة إلى [{code,message,target}]."""
    out: List[dict] = []

    def _one(e, parent_target=""):
        if isinstance(e, str):
            out.append({"code": "", "message": e[:500], "target": parent_target})
            return
        if not isinstance(e, dict):
            return
        msg = e.get("message") or e.get("errorAr") or e.get("error_description") or e.get("error") or ""
        if isinstance(msg, dict):
            msg = msg.get("message") or ""
        code = e.get("code") or (e.get("error") if isinstance(e.get("error"), str) else "") or ""
        target = e.get("target") or e.get("propertyPath") or parent_target or ""
        if msg or code:
            out.append({"code": str(code)[:100], "message": str(msg)[:500], "target": str(target)[:200]})
        for key in ("details", "innerError"):
            inner = e.get(key)
            if isinstance(inner, list):
                for d in inner:
                    _one(d, str(target))
            elif isinstance(inner, dict):
                _one(inner, str(target))

    if isinstance(body, dict):
        if "error" in body and isinstance(body["error"], (dict, list)):
            err = body["error"]
            for e in (err if isinstance(err, list) else [err]):
                _one(e)
        elif "error" in body or "message" in body or "code" in body:
            _one(body)
        for key in ("errors", "validationErrors"):
            if isinstance(body.get(key), list):
                for e in body[key]:
                    _one(e)
    elif isinstance(body, list):
        for e in body:
            _one(e)
    return out


def _jsonable_body(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:
        return None


def _short_errors(errs: List[dict]) -> str:
    if not errs:
        return ""
    e = errs[0]
    sep = ": " if e.get("code") and e.get("message") else ""
    return ((e.get("code") or "") + sep + (e.get("message") or ""))[:300]


class EtaClient:
    def __init__(self, *, tenant_id: int, environment: str, id_base: str, api_base: str,
                 client_id: str, client_secret: str,
                 http_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
                 sleep: Optional[Callable] = None, max_attempts: int = 3,
                 jitter: float = 0.25, clock: Callable[[], float] = time.monotonic):
        self.tenant_id = tenant_id
        self.environment = environment
        self.id_base = id_base.rstrip("/")
        self.api_base = api_base.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._factory = http_factory
        self._sleep = sleep or asyncio.sleep
        self.max_attempts = max(1, int(max_attempts))
        self.jitter = jitter
        self._clock = clock

    # ── مساعدات ──
    def _http(self) -> httpx.AsyncClient:
        # البحث عن default_http_factory وقت الاستدعاء كي يعمل monkeypatch في الاختبارات
        return (self._factory or default_http_factory)()

    @property
    def _cache_key(self) -> tuple:
        return (self.tenant_id, self.environment, self.id_base, self._client_id)

    def is_production_api(self) -> bool:
        return urlsplit(self.api_base).hostname == PROD_API_HOST

    def _delay(self, attempt_index: int, retry_after: Optional[float]) -> float:
        base = DEFAULT_BACKOFF_BASE * (2 ** attempt_index)
        if retry_after is not None:
            base = max(base, min(retry_after, MAX_RETRY_AFTER))
        if self.jitter:
            base += random.random() * self.jitter
        return base

    @staticmethod
    def _retry_after(resp: httpx.Response) -> Optional[float]:
        ra = resp.headers.get("Retry-After")
        if not ra:
            return None
        try:
            return max(0.0, min(float(ra), MAX_RETRY_AFTER))
        except ValueError:
            return None

    async def _send(self, method: str, url: str, *, headers: Optional[dict] = None,
                    data=None, json=None, params=None) -> httpx.Response:
        """إرسال مع retries للأخطاء المؤقتة فقط. يرجّع آخر Response غير مؤقت."""
        last_msg = "تعذّر الاتصال بمصلحة الضرائب"
        for i in range(self.max_attempts):
            retry_after = None
            try:
                async with self._http() as http:
                    resp = await http.request(method, url, headers=headers, data=data, json=json, params=params)
            except httpx.TimeoutException:
                last_msg = "انتهت مهلة الاتصال بمصلحة الضرائب"
            except httpx.TransportError:
                last_msg = "تعذّر الاتصال بمصلحة الضرائب"
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_msg = f"خدمة مصلحة الضرائب غير متاحة مؤقتًا (HTTP {resp.status_code})"
                    retry_after = self._retry_after(resp)
                else:
                    return resp
            if i + 1 < self.max_attempts:
                await self._sleep(self._delay(i, retry_after))
        raise EtaTransientError(last_msg)

    # ── OAuth ──
    async def get_token(self, force: bool = False) -> str:
        key = self._cache_key
        if not force:
            hit = _TOKEN_CACHE.get(key)
            if hit and hit[1] > self._clock():
                return hit[0]
        resp = await self._send(
            "POST", f"{self.id_base}/connect/token",
            data={"grant_type": "client_credentials", "client_id": self._client_id,
                  "client_secret": self._client_secret, "scope": TOKEN_SCOPE},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise EtaAuthError("رُفضت بيانات الاعتماد لدى مصلحة الضرائب — راجع client_id و client_secret")
        body = _jsonable_body(resp) or {}
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise EtaAuthError("ردّ غير متوقع من خادم المصادقة")
        try:
            ttl = float(body.get("expires_in") or 3600)
        except (TypeError, ValueError):
            ttl = 3600.0
        _TOKEN_CACHE[key] = (token, self._clock() + max(30.0, ttl - 60.0))
        return token

    async def _authed(self, method: str, path: str, *, json=None, params=None) -> httpx.Response:
        url = f"{self.api_base}{path}"
        reauthed = False
        while True:
            token = await self.get_token(force=reauthed)
            resp = await self._send(method, url, json=json, params=params,
                                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            if resp.status_code == 401 and not reauthed:
                _TOKEN_CACHE.pop(self._cache_key, None)
                reauthed = True
                continue
            if resp.status_code in (401, 403):
                raise EtaAuthError("مصلحة الضرائب رفضت الصلاحية (401/403) — راجع بيانات الاعتماد وصلاحيات الحساب")
            return resp

    @staticmethod
    def _raise_for_4xx(resp: httpx.Response):
        errs = parse_errors(_jsonable_body(resp))
        raise EtaPermanentError(
            _short_errors(errs) or f"رفضت مصلحة الضرائب الطلب (HTTP {resp.status_code})",
            status_code=resp.status_code, errors=errs)

    # ── العمليات ──
    async def submit(self, documents: List[dict]) -> dict:
        """يرجّع {submissionId, accepted:[{uuid,longId,internalId}], rejected:[{internalId,errors:[...]}]}"""
        resp = await self._authed("POST", "/api/v1/documentsubmissions", json={"documents": documents})
        if resp.status_code >= 400:
            self._raise_for_4xx(resp)
        body = _jsonable_body(resp)
        if not isinstance(body, dict):
            raise EtaTransientError("ردّ غير متوقع من مصلحة الضرائب")
        accepted = [
            {"uuid": d.get("uuid"), "longId": d.get("longId"), "internalId": d.get("internalId")}
            for d in (body.get("acceptedDocuments") or []) if isinstance(d, dict)
        ]
        rejected = []
        for d in (body.get("rejectedDocuments") or []):
            if not isinstance(d, dict):
                continue
            errs = parse_errors({"error": d.get("error")}) if d.get("error") else []
            rejected.append({"internalId": d.get("internalId"), "errors": errs})
        return {"submissionId": body.get("submissionId"), "accepted": accepted, "rejected": rejected}

    async def get_submission(self, submission_id: str) -> List[dict]:
        """يرجّع ملخص المستندات [{uuid,status,internalId,longId,errors}] (status بحروف صغيرة)."""
        resp = await self._authed("GET", f"/api/v1/documentsubmissions/{submission_id}",
                                  params={"PageSize": 100})
        if resp.status_code >= 400:
            self._raise_for_4xx(resp)
        body = _jsonable_body(resp) or {}
        return [self._summary(d) for d in (body.get("documentSummary") or []) if isinstance(d, dict)]

    async def search_by_internal_id(self, internal_id: str) -> List[dict]:
        """بحث best-effort بالـ internalID (لتسوية حالة ضياع الرد). مسار غير مُجرَّب."""
        resp = await self._authed("GET", "/api/v1.0/documents/search",
                                  params={"InternalID": internal_id, "PageSize": 10})
        if resp.status_code >= 400:
            self._raise_for_4xx(resp)
        body = _jsonable_body(resp) or {}
        return [self._summary(d) for d in (body.get("result") or []) if isinstance(d, dict)]

    async def cancel(self, uuid: str, reason: str) -> None:
        resp = await self._authed("PUT", f"/api/v1.0/documents/state/{uuid}/state",
                                  json={"status": "cancelled", "reason": reason})
        if resp.status_code >= 400:
            self._raise_for_4xx(resp)

    @staticmethod
    def _summary(d: dict) -> dict:
        errs: List[dict] = []
        vr = d.get("validationResults") or {}
        for step in (vr.get("validationSteps") or []):
            if not isinstance(step, dict):
                continue
            if str(step.get("status", "")).lower() == "invalid":
                es = parse_errors({"error": step.get("error")}) if step.get("error") else []
                if not es:
                    es = [{"code": "", "message": str(step.get("name") or "فشل التحقق"), "target": ""}]
                errs.extend(es)
        if not errs and d.get("documentStatusReason"):
            errs.append({"code": "", "message": str(d.get("documentStatusReason"))[:500], "target": ""})
        return {
            "uuid": d.get("uuid"),
            "status": str(d.get("status") or "").lower(),
            "internalId": d.get("internalId"),
            "longId": d.get("longId"),
            "submissionUUID": d.get("submissionUUID"),
            "errors": errs,
        }
