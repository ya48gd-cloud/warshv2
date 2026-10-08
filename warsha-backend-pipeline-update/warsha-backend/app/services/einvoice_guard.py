"""
einvoice_guard.py — حراس عدم التعديل للمستندات المُرسلة للفاتورة الإلكترونية.

بعد الإرسال لمصلحة الضرائب لا يجوز تعديل/حذف/إلغاء المستند في الـ ERP وإلا اختلف الـ ERP عن ETA:
  * المقفول: queued / submitting / submitted / valid  -> لا تعديل ولا حذف ولا إلغاء.
  * cancelled (أُلغي لدى المصلحة): لا تعديل ولا إعادة إرسال (الـ internalID محروق) والحذف مسموح.
  * rejected / invalid / failed / NULL: قابل للتعديل والحذف.
تُستدعى هذه الدوال قبل require_approval وقبل أي عكس (مخزون/قيد/رصيد) فلا آثار جانبية عند الرفض.
"""
from fastapi import HTTPException
from sqlalchemy import delete

from app.models.models import EInvoiceSubmission

LOCKED_STATUSES = ("queued", "submitting", "submitted", "valid")


def eta_locked(doc) -> bool:
    return (getattr(doc, "eta_status", None) or None) in LOCKED_STATUSES


def assert_editable(doc, what: str = "المستند") -> None:
    st = getattr(doc, "eta_status", None) or None
    if st in LOCKED_STATUSES:
        raise HTTPException(
            400,
            f"لا يمكن تعديل {what} — تم إرساله للفاتورة الإلكترونية (مصلحة الضرائب). "
            f"أصدر إشعارًا دائنًا بدل التعديل، أو ألغِ المستند لدى المصلحة أولًا (من الأدمن).",
        )
    if st == "cancelled":
        raise HTTPException(400, f"لا يمكن تعديل {what} — أُلغي لدى مصلحة الضرائب (الرقم الداخلي محروق). يمكن حذفه فقط.")


def assert_deletable(doc, what: str = "المستند") -> None:
    if eta_locked(doc):
        raise HTTPException(
            400,
            f"لا يمكن حذف {what} — تم إرساله للفاتورة الإلكترونية (مصلحة الضرائب). "
            f"ألغِ المستند لدى المصلحة أولًا (من الأدمن) أو أصدر إشعارًا دائنًا.",
        )


def assert_cancellable(doc, what: str = "المستند") -> None:
    if eta_locked(doc):
        raise HTTPException(
            400,
            f"لا يمكن إلغاء {what} — ما زال مُسجَّلًا لدى مصلحة الضرائب. ألغِ المستند لدى المصلحة أولًا.",
        )


async def purge_submissions(db, tid: int, doc_type: str, doc_id: int) -> None:
    """عند حذف المستند: احذف صف التسليم اليتيم (ID قد يُعاد استخدامه في SQLite). لا commit هنا."""
    await db.execute(
        delete(EInvoiceSubmission).where(
            EInvoiceSubmission.tenant_id == tid,
            EInvoiceSubmission.doc_type == doc_type,
            EInvoiceSubmission.doc_id == doc_id,
        ).execution_options(synchronize_session=False)
    )
