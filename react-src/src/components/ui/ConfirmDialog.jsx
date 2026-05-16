import Modal, { ModalFooter } from './Modal'

export default function ConfirmDialog({ open, onClose, onConfirm, title = 'تأكيد', message, danger }) {
  return (
    <Modal open={open} onClose={onClose} title={title} size="sm">
      <p className="text-sm text-erp-muted">{message}</p>
      <ModalFooter>
        <button className="btn" onClick={onClose}>إلغاء</button>
        <button
          className={danger ? 'btn btn-danger' : 'btn btn-primary'}
          onClick={() => { onConfirm(); onClose() }}
        >تأكيد</button>
      </ModalFooter>
    </Modal>
  )
}
