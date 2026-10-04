import { useEffect, useId, useRef } from 'react'

// مكدّس المودالات المفتوحة — Escape يقفل المودال الأعلى فقط بدل ما يقفل
// كل المودالات المتداخلة مرة واحدة (مثلاً ConfirmDialog فوق نموذج مفتوح)
const _modalStack = []

export default function Modal({ open = true, onClose, title, children, size = 'md' }) {
  const id = useId()
  const titleId = `${id}-title`
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return
    _modalStack.push(id)
    const handler = (e) => {
      if (e.key !== 'Escape') return
      if (_modalStack[_modalStack.length - 1] !== id) return
      onCloseRef.current?.()
    }
    document.addEventListener('keydown', handler)
    return () => {
      document.removeEventListener('keydown', handler)
      const idx = _modalStack.lastIndexOf(id)
      if (idx !== -1) _modalStack.splice(idx, 1)
    }
  }, [open, id])

  if (!open) return null

  const widths = { sm: '420px', md: '540px', lg: '720px', xl: '900px' }

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose?.()}>
      <div
        className="modal-box"
        style={{ maxWidth: widths[size] || widths.md }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        dir="rtl"
      >
        {title && (
          <div className="flex items-center justify-between mb-5">
            <h2 id={titleId} className="text-base font-semibold">{title}</h2>
            <button
              type="button"
              aria-label="إغلاق"
              title="إغلاق"
              onClick={onClose}
              className="text-xl leading-none text-erp-muted hover:text-erp-text transition-colors cursor-pointer"
            >×</button>
          </div>
        )}
        {children}
      </div>
    </div>
  )
}

export function ModalFooter({ children }) {
  return (
    <div className="flex gap-2 justify-end mt-6 pt-4 border-t border-erp-border">
      {children}
    </div>
  )
}
