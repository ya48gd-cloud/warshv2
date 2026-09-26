/**
 * Format a number with the workshop's configured currency symbol.
 * window.__ERP_CURRENCY__ is set at app startup from the workshop profile.
 * Falls back to 'ج' if nothing is configured.
 */
export const egp = (n, currency) => {
  const sym = currency || (typeof window !== 'undefined' && window.__ERP_CURRENCY__) || 'ج'
  // نستخدم الأرقام الغربية (latn) لأن الوثائق المالية تعتمدها حتى في البيئات العربية
  return Number(n || 0).toLocaleString('ar-EG', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
    numberingSystem: 'latn',
  }) + ' ' + sym
}

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
  unpaid:  { label: 'غير مدفوعة', badge: 'badge-amber' },
  partial: { label: 'جزئي',       badge: 'badge-blue'  },
  paid:    { label: 'مدفوعة',     badge: 'badge-green' },
  draft:   { label: 'مسودة',      badge: 'badge-gray'  },
}

export const QUOTE_STATUS = {
  draft:    { label: 'مسودة',    badge: 'badge-gray'  },
  sent:     { label: 'مرسل',     badge: 'badge-blue'  },
  accepted: { label: 'مقبول',    badge: 'badge-green' },
  rejected: { label: 'مرفوض',    badge: 'badge-red'   },
}

/** Today as YYYY-MM-DD */
export const today = () => new Date().toISOString().slice(0, 10)

/**
 * Format a date string to Arabic-friendly format.
 * Appends T00:00:00 to treat the string as local time, not UTC midnight,
 * preventing the off-by-one-day bug in timezones east of UTC.
 */
export const fmtDate = (s) => {
  if (!s) return '—'
  const d = s.includes('T') ? new Date(s) : new Date(s + 'T00:00:00')
  return d.toLocaleDateString('ar-EG', { year: 'numeric', month: 'short', day: 'numeric' })
}

/** Truncate a string */
export const trunc = (s, n = 30) => s && s.length > n ? s.slice(0, n) + '…' : (s || '—')

/** Clamp 0–100 */
export const pct = (v, t) => t ? Math.min(100, Math.round((v / t) * 100)) : 0
