import { useState, useEffect, useRef, useCallback } from 'react'
import { ROLE_META } from '../../store/auth'
import api from '../../api/client'

const PAGE_TITLES = {
  dashboard:  'لوحة التحكم',
  materials:  'المواد والخامات',
  movements:  'حركات المخزون',
  workorders: 'أوامر الإنتاج',
  costing:    'التكاليف والقيود',
  equipment:  'المعدات والبنية',
  bom:        'تفكيك BOM',
  workers:    'العاملون',
  payroll:    'الرواتب الأسبوعية',
  attendance: 'الحضور والغياب',
  customers:  'سجل العملاء',
  orders:     'المبيعات (عروض/طلبات)',
  invoices:   'الفواتير',
  production: 'إدارة الإنتاج',
  mrp:        'اقتراحات الشراء (MRP)',
  scrap:      'إدارة الهالك',
  stockcount: 'جرد المخزون',
  users:      'إدارة المستخدمين',
  reports:    'التقارير',
  workshop:   'بروفايل الورشة',
  purchases:  'المشتريات',
  suppliers:  'الموردون',
  fieldservice: 'الخدمة الميدانية',
  // صفحات كانت بتظهر بمفتاحها الإنجليزي الخام في الشريط العلوي
  inventory:       'المخزون',
  stockbatches:    'دفعات المخزون (Lot)',
  warehouses:      'المخازن المنطقية',
  pipeline:        'تتبع دورة حياة المعدة',
  installments:    'الأقساط المستحقة',
  purchaseorders:  'المشتريات (أوامر/فواتير)',
  accounting:      'المحاسبة والحسابات',
  extprojects:     'مشاريع التركيب والتشغيل',
  pricecatalog:    'كتالوج الأسعار',
  treasury:        'الخزينة والمدفوعات',
  supplycontracts: 'عقود التوريد',
  deliverynotes:   'أوامر التوريد',
  workcompletioncerts: 'الشهادات (إتمام/ضمان)',
}

// ── Global Search ─────────────────────────────────────────────
const SEARCH_SOURCES = [
  { key: 'customers', label: 'عملاء',      icon: '👤', page: 'customers',  fn: () => api.customers.list() },
  { key: 'materials', label: 'مواد',        icon: '📦', page: 'materials',  fn: () => api.inventory.materials() },
  { key: 'quotations',label: 'عروض أسعار', icon: '📋', page: 'orders', fn: () => api.sales.quotations() },
  { key: 'invoices',  label: 'فواتير',      icon: '🧾', page: 'invoices',   fn: () => api.sales.invoices() },
]

function GlobalSearch({ onNavigate }) {
  const [q, setQ]             = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen]       = useState(false)
  const cacheRef              = useRef({})
  const inputRef              = useRef(null)
  const boxRef                = useRef(null)
  const timer                 = useRef(null)

  useEffect(() => {
    const h = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  useEffect(() => {
    const h = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); inputRef.current?.focus(); setOpen(true) }
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [])

  const search = useCallback(async (term) => {
    if (!term.trim()) { setResults([]); return }
    setLoading(true)
    try {
      const hits = []
      for (const src of SEARCH_SOURCES) {
        if (!cacheRef.current[src.key]) {
          try { cacheRef.current[src.key] = await src.fn() } catch { continue }
        }
        const data = cacheRef.current[src.key]
        if (!Array.isArray(data)) continue
        const lq = term.toLowerCase()
        data.forEach(item => {
          const text = [item.name, item.name_ar, item.name_en, item.code,
            item.customer_name, item.description, item.phone]
            .filter(Boolean).join(' ').toLowerCase()
          if (!text.includes(lq)) return
          const label = item.name_ar || item.name_en || item.name ||
            item.customer_name || item.code || item.description || '—'
          const sub = item.code || item.phone ||
            (item.total != null ? `${Number(item.total).toLocaleString('ar-EG')} ج` : '') ||
            (item.stock_qty != null ? `${item.stock_qty} ${item.unit || ''}` : '') || ''
          hits.push({ icon: src.icon, label, sub, page: src.page, type: src.label })
        })
      }
      setResults(hits.slice(0, 10))
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    clearTimeout(timer.current)
    if (!q) { setResults([]); return }
    timer.current = setTimeout(() => search(q), 280)
    return () => clearTimeout(timer.current)
  }, [q, search])

  return (
    <div ref={boxRef} style={{ position: 'relative', flex: 1, maxWidth: 340 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        background: 'var(--bg-card)', border: '1px solid var(--erp-border)',
        borderRadius: 8, padding: '5px 10px',
      }}>
        <span style={{ color: 'var(--erp-muted)', fontSize: 13, flexShrink: 0 }}>🔍</span>
        <input
          ref={inputRef}
          value={q}
          onChange={e => { setQ(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          placeholder="بحث… (Ctrl+K)"
          style={{
            background: 'none', border: 'none', outline: 'none',
            fontSize: 13, color: 'var(--erp-text)', flex: 1, fontFamily: 'inherit',
          }}
        />
        {loading && <span style={{ fontSize: 11, color: 'var(--erp-muted)', flexShrink: 0 }}>⟳</span>}
        {q && (
          <button type="button" aria-label="مسح البحث" onClick={() => { setQ(''); setResults([]) }}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--erp-muted)', fontSize: 12, lineHeight: 1, flexShrink: 0 }}>✕</button>
        )}
      </div>

      {open && (
        <div style={{
          position: 'absolute', top: '100%', right: 0, left: 0, marginTop: 4,
          background: 'var(--bg)', border: '1px solid var(--erp-border)',
          borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,.13)',
          zIndex: 9999, maxHeight: 360, overflowY: 'auto',
        }}>
          {!q && (
            <div style={{ padding: '12px 14px', fontSize: 12, color: 'var(--erp-muted)', textAlign: 'center' }}>
              ابحث في العملاء، المواد، الفواتير، عروض الأسعار…
            </div>
          )}
          {q && !loading && results.length === 0 && (
            <div style={{ padding: '14px', fontSize: 12, color: 'var(--erp-muted)', textAlign: 'center' }}>
              لا توجد نتائج لـ "{q}"
            </div>
          )}
          {results.map((r, i) => (
            <button key={i} onClick={() => { onNavigate?.(r.page); setOpen(false); setQ('') }}
              style={{
                display: 'flex', alignItems: 'center', gap: 10, width: '100%',
                padding: '9px 14px', background: 'none', border: 'none',
                borderBottom: '1px solid var(--erp-border)', cursor: 'pointer',
                textAlign: 'right', fontFamily: 'inherit',
              }}
              onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-card)'}
              onMouseLeave={e => e.currentTarget.style.background = 'none'}
            >
              <span style={{ fontSize: 16, flexShrink: 0 }}>{r.icon}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--erp-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.label}</div>
                {r.sub && <div style={{ fontSize: 11, color: 'var(--erp-muted)', marginTop: 1 }}>{r.sub}</div>}
              </div>
              <span style={{ fontSize: 10, padding: '1px 7px', borderRadius: 10, background: 'var(--teal-lt)', color: 'var(--teal)', flexShrink: 0 }}>{r.type}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Notification Bell ─────────────────────────────────────────
const LEVEL_STYLE = {
  critical: { dot: '#ef4444', bg: 'var(--red-lt)',   color: 'var(--red)',   label: 'حرج' },
  warning:  { dot: '#f59e0b', bg: 'var(--amber-lt)', color: 'var(--amber)', label: 'تحذير' },
  info:     { dot: '#3b82f6', bg: 'var(--blue-lt)',  color: 'var(--blue)',  label: 'معلومة' },
}

function NotificationBell() {
  const [open, setOpen]       = useState(false)
  const [alerts, setAlerts]   = useState([])
  const [loading, setLoading] = useState(true)
  const ref = useRef(null)

  const load = useCallback(async () => {
    try {
      const data = await api.alerts()
      setAlerts(Array.isArray(data) ? data : [])
    } catch { setAlerts([]) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t) }, [load])

  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  const critical = alerts.filter(a => a.level === 'critical').length
  const total    = alerts.length

  return (
    <div ref={ref} style={{ position: 'relative', flexShrink: 0 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          position: 'relative', background: 'none',
          border: '1px solid var(--erp-border)', borderRadius: 8,
          padding: '5px 9px', cursor: 'pointer', fontSize: 15, lineHeight: 1,
          color: 'var(--erp-text)',
        }}
        onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-card)'}
        onMouseLeave={e => e.currentTarget.style.background = 'none'}
        title="التنبيهات"
      >
        🔔
        {total > 0 && (
          <span style={{
            position: 'absolute', top: -5, left: -5,
            background: critical > 0 ? '#ef4444' : '#f59e0b',
            color: '#fff', borderRadius: '50%', fontSize: 9, fontWeight: 700,
            width: 16, height: 16, display: 'flex', alignItems: 'center', justifyContent: 'center',
            border: '2px solid var(--bg)',
          }}>{total > 9 ? '9+' : total}</span>
        )}
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, marginTop: 6,
          width: 360, maxHeight: 440, overflowY: 'auto',
          background: 'var(--bg)', border: '1px solid var(--erp-border)',
          borderRadius: 12, boxShadow: '0 8px 28px rgba(0,0,0,.15)', zIndex: 9999,
        }}>
          <div style={{
            padding: '11px 16px', borderBottom: '1px solid var(--erp-border)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontWeight: 700, fontSize: 13 }}>🔔 التنبيهات</span>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {total > 0 && (
                <span style={{
                  background: critical > 0 ? 'var(--red-lt)' : 'var(--amber-lt)',
                  color: critical > 0 ? 'var(--red)' : 'var(--amber)',
                  fontSize: 10, padding: '1px 8px', borderRadius: 10, fontWeight: 700,
                }}>{total} تنبيه</span>
              )}
              <button onClick={load}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 13, color: 'var(--erp-muted)' }}
                title="تحديث">↻</button>
            </div>
          </div>

          {loading && <div style={{ padding: 20, textAlign: 'center', color: 'var(--erp-muted)', fontSize: 12 }}>جاري التحميل…</div>}

          {!loading && total === 0 && (
            <div style={{ padding: '24px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 28, marginBottom: 8 }}>✅</div>
              <div style={{ fontSize: 13, color: 'var(--erp-muted)' }}>لا توجد تنبيهات — النظام يعمل بشكل طبيعي</div>
            </div>
          )}

          {!loading && alerts.map((a, i) => {
            const s = LEVEL_STYLE[a.level] || LEVEL_STYLE.info
            return (
              <div key={i} style={{
                padding: '10px 16px', borderBottom: '1px solid var(--erp-border)',
                display: 'flex', gap: 10, alignItems: 'flex-start',
              }}>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: s.dot, flexShrink: 0, marginTop: 5 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: s.color }}>
                    {a.icon || ''} {a.title}
                  </div>
                  {a.detail && (
                    <div style={{ fontSize: 11, color: 'var(--erp-muted)', marginTop: 2, lineHeight: 1.5 }}>{a.detail}</div>
                  )}
                </div>
                <span style={{
                  fontSize: 9, padding: '2px 7px', borderRadius: 10, flexShrink: 0,
                  background: s.bg, color: s.color, fontWeight: 700,
                }}>{s.label}</span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Main Topbar ───────────────────────────────────────────────
export default function Topbar({ page, onRefresh, user, onAddClick, showAdd, onNavigate }) {
  const meta = user ? ROLE_META[user.role] : null

  return (
    <header
      className="bg-white border-b border-erp-border px-4 flex items-center gap-3 sticky top-0 z-40"
      style={{ height: 52 }}
    >
      <h1 className="text-[14px] font-semibold whitespace-nowrap flex-shrink-0">
        {PAGE_TITLES[page] || page}
      </h1>

      <GlobalSearch onNavigate={onNavigate} />

      <div className="flex items-center gap-2 flex-shrink-0">
        <NotificationBell />

        {meta && (
          <span className={`badge ${meta.color} text-[11px] hidden sm:inline-flex`}>
            {meta.icon} {meta.label}
          </span>
        )}

        {user && (
          <span className="text-xs text-erp-muted hidden md:block">
            {user.full_name || user.username}
          </span>
        )}

        <button className="btn btn-sm" onClick={onRefresh} title="تحديث">🔄</button>

        {showAdd && (
          <button className="btn btn-primary btn-sm" onClick={onAddClick}>+ إضافة</button>
        )}
      </div>
    </header>
  )
}
