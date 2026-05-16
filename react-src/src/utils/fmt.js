/** Format a number as Egyptian Pounds */
export const egp = (n) =>
  Number(n || 0).toLocaleString('ar-EG', { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + ' ج'

/** Format a number with decimals */
export const num = (n, d = 1) => Number(n || 0).toFixed(d)

/** Arabic status labels */
export const WO_STATUS = {
  draft:       { label: 'مسودة',  badge: 'badge-blue'  },
  in_progress: { label: 'جاري',   badge: 'badge-amber' },
  done:        { label: 'منتهي',  badge: 'badge-green' },
  cancelled:   { label: 'ملغي',   badge: 'badge-red'   },
}

export const INVOICE_STATUS = {
  draft:   { label: 'مسودة',    badge: 'badge-gray'  },
  sent:    { label: 'مرسلة',    badge: 'badge-blue'  },
  paid:    { label: 'مدفوعة',   badge: 'badge-green' },
  partial: { label: 'جزئي',     badge: 'badge-amber' },
  overdue: { label: 'متأخرة',   badge: 'badge-red'   },
}

export const QUOTE_STATUS = {
  draft:    { label: 'مسودة',    badge: 'badge-gray'  },
  sent:     { label: 'مرسل',     badge: 'badge-blue'  },
  accepted: { label: 'مقبول',    badge: 'badge-green' },
  rejected: { label: 'مرفوض',    badge: 'badge-red'   },
}

/** Today as YYYY-MM-DD */
export const today = () => new Date().toISOString().slice(0, 10)

/** Format a date string to Arabic-friendly format */
export const fmtDate = (s) => {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('ar-EG', { year: 'numeric', month: 'short', day: 'numeric' })
}

/** Truncate a string */
export const trunc = (s, n = 30) => s && s.length > n ? s.slice(0, n) + '…' : (s || '—')

/** Clamp 0–100 */
export const pct = (v, t) => t ? Math.min(100, Math.round((v / t) * 100)) : 0
