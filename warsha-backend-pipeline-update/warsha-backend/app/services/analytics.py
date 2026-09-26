"""
خدمة تسجيل أحداث المنتج (Product Analytics).

مبدأ التصميم: التسجيل يجب ألا يكسر أي عملية تجارية حقيقية أبدًا.
لذلك log_event بتفتح جلسة قاعدة بيانات مستقلة خاصة بيها وتعمل commit
فورًا، وأي فشل فيها بيتسجل في الـ log بس من غير ما يأثر على العملية
الأصلية (إنشاء فاتورة، أمر عمل... إلخ) اللي استدعتها.

الاستخدام:
    from app.services.analytics import log_event
    await log_event(tenant_id=current["tenant_id"], event_type="invoice_created",
                     entity_type="invoice", entity_id=inv.id,
                     metadata={"total": float(inv.total)}, source_path=str(request.url.path))
"""
import logging
from typing import Optional

from app.core.database import AsyncSessionLocal
from app.models.models import AnalyticsEvent

logger = logging.getLogger("warsha.analytics")


async def log_event(
    event_type: str,
    tenant_id: Optional[int] = None,
    user_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    metadata: Optional[dict] = None,
    source_path: Optional[str] = None,
) -> None:
    try:
        async with AsyncSessionLocal() as session:
            session.add(AnalyticsEvent(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                event_metadata=metadata,
                source_path=source_path,
            ))
            await session.commit()
    except Exception:
        # التسجيل نفسه مايستهلش استثناء يكسر العملية التجارية الأصلية،
        # لكن لازم يظهر في الـ logs عشان نلاحظ لو التتبع بيفشل بشكل متكرر.
        logger.warning("Failed to log analytics event '%s'", event_type, exc_info=True)


# ── أسماء الأحداث القياسية — استخدمها بدل ما تكتب strings حرة، عشان نضمن اتساق البيانات لاحقًا ──
class Events:
    USER_LOGIN = "user_login"
    QUOTATION_CREATED = "quotation_created"
    ORDER_CREATED = "order_created"
    WORK_ORDER_CREATED = "work_order_created"
    INVOICE_CREATED = "invoice_created"
    PAYMENT_RECEIVED = "payment_received"
    CATALOG_ITEM_IMPORTED = "catalog_item_imported"
    PURCHASE_ORDER_CREATED = "purchase_order_created"
