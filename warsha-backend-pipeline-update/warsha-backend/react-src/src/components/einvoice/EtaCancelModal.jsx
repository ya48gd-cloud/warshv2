/**
 * إلغاء مستند مقبول لدى مصلحة الضرائب (للمدير فقط، من حالة valid فقط).
 * POST /einvoice/documents/{kind}/{id}/cancel {reason 3..200}
 * ملاحظة: الإلغاء لدى الضرائب لا يعكس المستند في الـ ERP تلقائياً.
 */
import { useState, useEffect } from 'react'
import Modal, { ModalFooter } from '../ui/Modal'
import { Textarea } from '../ui/Field'
import { etaKindLabel } from '../../utils/einvoice'

export default function EtaCancelModal({ open, kind, doc, busy, onClose, onConfirm }) {
  const [reason, setReason] = useState('')
  useEffect(() => { if (open) setReason('') }, [open])

  const r = reason.trim()
  const err = r.length > 0 && r.length < 3 ? 'السبب يجب ألا يقل عن 3 أحرف'
    : r.length > 200 ? 'السبب يجب ألا يزيد عن 200 حرف' : null
  const ok = r.length >= 3 && r.length <= 200

  return (
    <Modal open={open} onClose={onClose} size="sm" title={`إلغاء لدى الضرائب — ${etaKindLabel(kind)} ${doc?.code || ''}`}>
      <div className="flex flex-col gap-3">
        <div className="px-3 py-2.5 rounded-erp text-xs leading-6" style={{ background: 'var(--amber-lt)', color: 'var(--amber)' }}>
          سيتم إلغاء المستند لدى مصلحة الضرائب. هذا الإجراء لا يعكس الفاتورة تلقائياً في النظام —
          أصدر إشعاراً دائناً أو اتبع الدورة المعتادة لتصحيح الأثر المحاسبي.
        </div>
        <Textarea label="سبب الإلغاء *" rows={3} value={reason} maxLength={200}
                  onChange={(e) => setReason(e.target.value)} error={err} />
      </div>
      <ModalFooter>
        <button className="btn" onClick={onClose}>تراجع</button>
        <button className="btn btn-danger" disabled={!ok || busy} style={{ opacity: !ok || busy ? 0.5 : 1 }}
                onClick={() => onConfirm(r)}>
          {busy ? '…' : 'تأكيد الإلغاء'}
        </button>
      </ModalFooter>
    </Modal>
  )
}
