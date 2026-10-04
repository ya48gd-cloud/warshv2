import { useState, useRef, useEffect } from 'react'

/**
 * قائمة إجراءات منسدلة (⋮) — تجمع الأفعال الثانوية في زرار واحد بدل ما
 * تتكرر كأزرار منفصلة في كل صف جدول. راجع تقرير ازدحام الـ UX.
 *
 * items: [{ label, icon, onClick, danger?, disabled? }]
 */
export default function ActionMenu({ items, title = 'إجراءات أخرى' }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  // Escape يقفل القائمة
  useEffect(() => {
    if (!open) return
    const k = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', k)
    return () => document.removeEventListener('keydown', k)
  }, [open])

  const visibleItems = (items || []).filter(Boolean)
  if (visibleItems.length === 0) return null

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
      <button
        type="button"
        className="btn btn-sm text-xs"
        title={title}
        aria-label={title}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(o => !o)}
        style={{ padding: '4px 8px' }}
      >⋮</button>

      {open && (
        <div role="menu" style={{
          position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 30,
          minWidth: 160, background: 'var(--bg)', border: '1px solid var(--erp-border)',
          borderRadius: 8, boxShadow: '0 4px 16px rgba(0,0,0,0.12)', overflow: 'hidden',
        }}>
          {visibleItems.map((item, i) => (
            <button
              key={i}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onClick={() => { setOpen(false); item.onClick?.() }}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                padding: '8px 12px', fontSize: 13, textAlign: 'right',
                background: 'none', border: 'none', cursor: item.disabled ? 'default' : 'pointer',
                color: item.danger ? 'var(--red)' : 'var(--erp-text)',
                opacity: item.disabled ? 0.5 : 1,
              }}
              onMouseEnter={e => !item.disabled && (e.currentTarget.style.background = 'var(--bg-card)')}
              onMouseLeave={e => e.currentTarget.style.background = 'none'}
            >
              <span>{item.icon}</span>
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
