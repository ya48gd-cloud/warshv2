"""
einvoice_signer.py — واجهة التوقيع الرقمي القابلة للتوصيل للفاتورة الإلكترونية.

*** لا يوجد تشفير فعلي هنا. *** التوقيع الحقيقي (CAdES-BES بشهادة USB token / HSM) يتم خارج النظام:
  - MockSigner: للاختبار والبيئة التجريبية فقط (توقيع وهمي مُعلَّم بـ "MOCK:") — محظور على الإنتاج.
  - ExternalSigner: يرسل النص القانوني (canonical) لخدمة توقيع خارجية يضبطها الأدمن
    (signer_url + bearer token) وتعيد {"signature": "<base64 CMS/CAdES-BES>"} — راجع docs/EINVOICE.md.

serialize_for_signing: التسلسل القانوني حسب وصف الـ SDK العام (أسماء الخصائص بحروف كبيرة، كل قيمة
بين علامتي اقتباس، والمصفوفات تكرّر اسم الخاصية قبل كل عنصر). *** غير مُتحقَّق منه ضد مدقّق ETA الفعلي ***
فقد يحتاج تعديلًا عند التجربة على البيئة التجريبية.
"""
import base64
import hashlib
from decimal import Decimal
from typing import Optional

from app.services import eta_client
from app.services import secret_box

MOCK_PREFIX = b"MOCK:"
PROD_SIGNER_ERROR = "التوقيع الرقمي غير مُعدّ — لا يمكن الإرسال لبيئة الإنتاج"
SIGNER_MODES = ("none", "mock", "external")


class SignerError(Exception):
    """فشل التوقيع — الرسالة عربية آمنة للعرض."""


def fmt_number(v) -> str:
    """رقم بلا ضجيج float وبلا أصفار زائدة: 1.50 -> '1.5'، 100.0 -> '100'، 0.1+0.2 -> '0.3'."""
    d = Decimal(str(v)).normalize()
    s = format(d, "f")
    return "0" if s in ("-0", "") else s


def _ser(obj) -> str:
    if isinstance(obj, dict):
        out = []
        for key, val in obj.items():
            if val is None:
                continue
            k = f'"{str(key).upper()}"'
            out.append(k)
            if isinstance(val, list):
                for el in val:
                    out.append(k)
                    out.append(_ser(el))
            else:
                out.append(_ser(val))
        return "".join(out)
    if isinstance(obj, bool):
        return f'"{"true" if obj else "false"}"'
    if isinstance(obj, (int, float, Decimal)):
        return f'"{fmt_number(obj)}"'
    return f'"{obj}"'


def serialize_for_signing(document: dict) -> str:
    """النص القانوني للمستند (بدون خاصية signatures)."""
    body = {k: v for k, v in document.items() if k != "signatures"}
    return _ser(body)


def document_hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_mock_signature_value(value: str) -> bool:
    try:
        return base64.b64decode(value or "", validate=False).startswith(MOCK_PREFIX)
    except Exception:
        return False


def has_mock_signature(document: dict) -> bool:
    return any(is_mock_signature_value(s.get("value", "")) for s in (document.get("signatures") or []))


def assert_signer_allowed(environment: str, mode: str) -> None:
    """حارس صلب: لا توقيع وهمي/غائب على الإنتاج. يُستدعى عند حفظ الإعدادات وعند الإرسال."""
    if mode not in SIGNER_MODES:
        raise SignerError("وضع التوقيع غير معروف")
    if environment == "prod" and mode in ("none", "mock"):
        raise SignerError(PROD_SIGNER_ERROR)


class MockSigner:
    mode = "mock"

    async def sign(self, document: dict, canonical: str) -> dict:
        digest = document_hash(canonical)
        value = base64.b64encode(MOCK_PREFIX + digest.encode("ascii")).decode("ascii")
        return {"signatures": [{"signatureType": "I", "value": value}], "mock": True}


class ExternalSigner:
    mode = "external"

    def __init__(self, url: str, token: Optional[str] = None, timeout: float = 20.0):
        self.url = url
        self.token = token
        self.timeout = timeout

    async def sign(self, document: dict, canonical: str) -> dict:
        try:
            url = eta_client.validate_https_url(self.url)
        except eta_client.UnsafeUrlError as e:
            raise SignerError(f"رابط خدمة التوقيع غير مسموح: {e}")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        payload = {"canonical": canonical, "document_hash": document_hash(canonical)}
        try:
            async with eta_client.default_http_factory() as http:
                resp = await http.post(url, json=payload, headers=headers, timeout=self.timeout)
        except Exception:
            raise SignerError("تعذّر الاتصال بخدمة التوقيع الرقمي")
        if resp.status_code != 200:
            raise SignerError(f"خدمة التوقيع رفضت الطلب (HTTP {resp.status_code})")
        try:
            sig = resp.json().get("signature")
        except Exception:
            sig = None
        if not isinstance(sig, str) or not sig.strip():
            raise SignerError("ردّ خدمة التوقيع لا يحتوي على التوقيع")
        try:
            base64.b64decode(sig, validate=True)
        except Exception:
            raise SignerError("توقيع خدمة التوقيع ليس base64 صالحًا")
        if is_mock_signature_value(sig):
            raise SignerError("خدمة التوقيع أعادت توقيعًا وهميًا")
        return {"signatures": [{"signatureType": "I", "value": sig}], "mock": False}


def get_signer(settings) -> "MockSigner | ExternalSigner":
    """يختار الـ Signer حسب إعدادات الورشة بعد تطبيق حارس الإنتاج."""
    mode = settings.signer_mode or "none"
    assert_signer_allowed(settings.environment, mode)
    if mode == "mock":
        return MockSigner()
    if mode == "external":
        if not settings.signer_url:
            raise SignerError("رابط خدمة التوقيع غير مضبوط")
        token = None
        if settings.signer_token_enc:
            try:
                token = secret_box.decrypt(settings.signer_token_enc)
            except secret_box.SecretBoxError as e:
                raise SignerError(str(e))
        return ExternalSigner(settings.signer_url, token)
    raise SignerError("التوقيع الرقمي غير مُعدّ")
