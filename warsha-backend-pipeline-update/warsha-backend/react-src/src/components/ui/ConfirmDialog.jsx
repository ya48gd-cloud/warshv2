import { useEffect, useRef } from 'react'
import Modal, { ModalFooter } from './Modal'

export default function ConfirmDialog({
  open, onClose, onCancel, onConfirm,
  title = 'تأكيد', message,
  confirmLabel = 'تأكيد',
  confirmVariant, danger,
}) {
  const handleClose = onCancel || onClose || (() => {})
  const isDanger = danger || confirmVariant === 'danger'
  // حماية من الضغط المزدوج على "تأكيد" (كان بيستدعي onConfirm مرتين → حذف/ترحيل مكرر)
  const firedRef = useRef(false)
  useEffect(() => { if (open) firedRef.current = false }, [open])

  return (
    <Modal open={open} onClose={handleClose} title={title} size="sm">
      <div className="text-sm text-erp-muted" style={{ whiteSpace: 'pre-line' }}>{message}</div>
      <ModalFooter>
        <button type="button" className="btn" autoFocus onClick={handleClose}>إلغاء</button>
        <button
          type="button"
          className={isDanger ? 'btn btn-danger' : 'btn btn-primary'}
          onClick={() => {
            if (firedRef.current) return
            firedRef.current = true
            onConfirm?.(); handleClose()
          }}
        >{confirmLabel}</button>
      </ModalFooter>
    </Modal>
  )
}
