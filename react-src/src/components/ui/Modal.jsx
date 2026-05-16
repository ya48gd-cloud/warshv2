import { useEffect } from 'react'

export default function Modal({ open, onClose, title, children, size = 'md' }) {
  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  const widths = { sm: '420px', md: '540px', lg: '720px', xl: '900px' }

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-box" style={{ maxWidth: widths[size] || widths.md }}>
        {title && (
          <div className="flex items-center justify-between mb-5">
            <h2 className="text-base font-semibold">{title}</h2>
            <button
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
