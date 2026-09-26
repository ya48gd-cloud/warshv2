import Modal, { ModalFooter } from './Modal'

export default function ConfirmDialog({
  open, onClose, onCancel, onConfirm,
  title = 'تأكيد', message,
  confirmLabel = 'تأكيد',
  confirmVariant, danger,
}) {
  const handleClose = onCancel || onClose || (() => {})
  const isDanger = danger || confirmVariant === 'danger'

  return (
    <Modal open={open} onClose={handleClose} title={title} size="sm">
      <p className="text-sm text-erp-muted">{message}</p>
      <ModalFooter>
        <button className="btn" onClick={handleClose}>إلغاء</button>
        <button
          className={isDanger ? 'btn btn-danger' : 'btn btn-primary'}
          onClick={() => { onConfirm?.(); handleClose() }}
        >{confirmLabel}</button>
      </ModalFooter>
    </Modal>
  )
}
