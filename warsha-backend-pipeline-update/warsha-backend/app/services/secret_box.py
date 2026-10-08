"""
secret_box.py — تشفير أسرار الفاتورة الإلكترونية (client_secret / client_id / signer token) بـ Fernet.

  * المفتاح: متغيّر البيئة ERP_EINVOICE_KEY (مفتاح Fernet صالح — مُوصى به)،
    وإلا يُشتق من مفتاح الـ JWT الخاص بالتثبيت (app.api.auth.SECRET_KEY).
  * القيم المخزّنة تبدأ بـ 'v1:' عشان نقدر نغيّر الخوارزمية/المفتاح مستقبلاً.
  * لو المفتاح اتغيّر (مثلاً تغيير JWT_SECRET) فالمخزَّن لا يمكن فكّه:
    decrypt يرفع SecretBoxError برسالة عربية واضحة (يعيد المدير إدخال السر) — لا 500.
  * ملاحظة: النسخ الاحتياطي (dump) يحتوي النصوص المشفّرة؛ حمايتها = حماية المفتاح.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "v1:"


class SecretBoxError(Exception):
    """خطأ تشفير/فك تشفير — الرسالة عربية وآمنة للعرض."""


def _fernet() -> Fernet:
    env = (os.getenv("ERP_EINVOICE_KEY") or "").strip()
    if env:
        try:
            return Fernet(env.encode())
        except Exception:
            raise SecretBoxError("مفتاح تشفير الفاتورة الإلكترونية (ERP_EINVOICE_KEY) غير صالح")
    # تحميل متأخر لتفادي الاستيراد الدائري
    from app.api.auth import SECRET_KEY
    digest = hashlib.sha256((str(SECRET_KEY) + ":einvoice").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plain: str) -> str:
    if plain is None or plain == "":
        raise SecretBoxError("القيمة المراد تشفيرها فارغة")
    return PREFIX + _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    if not token or not token.startswith(PREFIX):
        raise SecretBoxError("القيمة المخزّنة غير مشفّرة بصيغة معروفة — أعد إدخال السر")
    try:
        return _fernet().decrypt(token[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise SecretBoxError("تعذّر فك تشفير البيانات المخزّنة (تغيّر مفتاح التشفير) — أعد إدخال بيانات الاعتماد")
    except SecretBoxError:
        raise
    except Exception:
        raise SecretBoxError("تعذّر فك تشفير البيانات المخزّنة — أعد إدخال بيانات الاعتماد")


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)
