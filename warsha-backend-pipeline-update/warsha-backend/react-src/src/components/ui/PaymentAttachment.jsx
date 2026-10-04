import { useApi } from '../../hooks/useApi'
import api from '../../api/client'
import { useToast } from '../../store/toast'

/**
 * يعرض رابط/مصغّر لإثبات دفع مرفوع لكيان دفعة (عميل/مورد) إن وُجد.
 * entityType: 'customer_payment' | 'supplier_payment'
 */
export default function PaymentAttachment({ entityType, entityId }) {
  const toast = useToast()
  // المكوّن بيتعرض لكل صف في جدول الدفعات — لو فشل جلب المرفقات لصف واحد
  // مانطلعش توست خطأ لكل صف؛ نعرض "—" بهدوء. وبدون entityId مانبعتش طلب أصلاً.
  const { data: attachments } = useApi(
    () => (entityType && entityId != null
      ? api.listAttachments(entityType, entityId).catch(() => [])
      : Promise.resolve([])),
    [entityType, entityId],
  )
  const att = Array.isArray(attachments) ? attachments[0] : null
  if (!att) return <span className="text-erp-muted">—</span>
  return (
    <button
      type="button"
      className="btn btn-xs"
      style={{ fontSize: 11 }}
      title={att.original_name || ''}
      onClick={async () => {
        try { await api.openFile(`/purchases/attachments/${att.id}/download`) }
        catch (err) { toast(err?.message || 'تعذّر فتح الملف', 'error') }
      }}
    >
      📎 عرض الإثبات
    </button>
  )
}
