import { useApi } from '../../hooks/useApi'
import api from '../../api/client'

/**
 * يعرض رابط/مصغّر لإثبات دفع مرفوع لكيان دفعة (عميل/مورد) إن وُجد.
 * entityType: 'customer_payment' | 'supplier_payment'
 */
export default function PaymentAttachment({ entityType, entityId }) {
  const { data: attachments } = useApi(() => api.listAttachments(entityType, entityId), [entityType, entityId])
  const att = attachments?.[0]
  if (!att) return <span className="text-erp-muted">—</span>
  return (
    <button
      className="btn btn-xs"
      style={{ fontSize: 11 }}
      title={att.original_name}
      onClick={() => api.openFile(`/purchases/attachments/${att.id}/download`)}
    >
      📎 عرض الإثبات
    </button>
  )
}
