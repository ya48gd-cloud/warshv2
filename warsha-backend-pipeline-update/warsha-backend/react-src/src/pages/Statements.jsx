import { useState, useEffect } from 'react'
import { useApi } from '../hooks/useApi'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import { Select, Input } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'

export default function Statements() {
  const toast = useToast()
  const [tab, setTab] = useState('suppliers')   // suppliers | customers
  const [entityId, setEntityId] = useState('')
  const [locationId, setLocationId] = useState('')
  const [locations, setLocations] = useState([])
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)

  const { data: suppliers } = useApi(() => api.purchases.suppliers().catch(bgErr('قائمة الموردين', [])))
  const { data: customers } = useApi(() => api.customers.list().catch(bgErr('قائمة العملاء', [])))

  // مواقع العميل المختار (للعملاء فقط)
  useEffect(() => {
    if (tab === 'customers' && entityId) {
      api.customers.locations(entityId).then(r => setLocations(Array.isArray(r) ? r : [])).catch(bgErr('عناوين العميل', () => setLocations([])))
    } else {
      setLocations([]); setLocationId('')
    }
  }, [tab, entityId])

  const entities = tab === 'suppliers'
    ? (Array.isArray(suppliers) ? suppliers : [])
    : (Array.isArray(customers) ? customers : [])

  // نصفّر الاختيار عند تبديل التبويب
  useEffect(() => { setEntityId(''); setReport(null) }, [tab])

  const run = async () => {
    if (!entityId) { toast('اختر الجهة', 'error'); return }
    setLoading(true)
    try {
      const params = {}
      if (dateFrom) params.date_from = dateFrom
      if (dateTo)   params.date_to   = dateTo
      if (tab === 'customers' && locationId) params.location_id = locationId
      const data = tab === 'suppliers'
        ? await api.purchases.supplierLedger(entityId, params)
        : await api.customers.ledger(entityId, params)
      setReport(data)
    } catch (e) {
      toast('تعذّر إنشاء التقرير', 'error')
    } finally { setLoading(false) }
  }

  const exportCsv = () => {
    if (!entityId) return
    const params = {}
    if (dateFrom) params.date_from = dateFrom
    if (dateTo)   params.date_to   = dateTo
    if (tab === 'customers' && locationId) params.location_id = locationId
    if (tab === 'suppliers') api.export.supplierLedger(entityId, params)
    else api.export.customerLedger(entityId, params)
  }

  const printReport = () => {
    if (!report) return
    const who = tab === 'suppliers' ? report.supplier : report.customer
    const rows = report.rows || []
    const html = `<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8">
<style>
  body{font-family:Tajawal,Arial,sans-serif;padding:32px;color:#1a1a18;font-size:13px}
  h2{color:#0F6E56;margin:0 0 4px}
  .meta{color:#6b7280;font-size:12px;margin-bottom:16px}
  table{width:100%;border-collapse:collapse;margin-top:12px}
  th{background:#f5f4f0;padding:8px;text-align:right;font-size:11px;border-bottom:2px solid #e2e0d8}
  td{padding:8px;border-bottom:1px solid #f3f4f6;font-size:12px}
  .debit{color:#b91c1c}.credit{color:#15803d}
  .tot{font-weight:700;background:#E1F5EE}
  @media print{body{padding:16px}}
</style></head><body>
  <h2>كشف حساب ${tab === 'suppliers' ? 'مورّد' : 'عميل'}</h2>
  <div class="meta">
    ${who?.name || ''} ${who?.code ? '— ' + who.code : ''}<br>
    الفترة: ${report.date_from || 'البداية'} إلى ${report.date_to || 'النهاية'}
  </div>
  <table>
    <thead><tr><th>التاريخ</th><th>النوع</th><th>المرجع</th><th>البيان</th><th>مدين</th><th>دائن</th><th>الرصيد</th></tr></thead>
    <tbody>
      ${report.date_from ? `<tr class="tot"><td colspan="6">رصيد افتتاحي (قبل ${fmtDate(report.date_from)})</td><td>${egp(report.opening_balance || 0)}</td></tr>` : ''}
      ${rows.map(r => `<tr>
        <td>${fmtDate(r.date)}</td>
        <td>${r.type === 'invoice' ? 'فاتورة' : 'دفعة'}</td>
        <td>${r.ref}</td>
        <td>${r.description}</td>
        <td class="debit">${r.debit ? egp(r.debit) : ''}</td>
        <td class="credit">${r.credit ? egp(r.credit) : ''}</td>
        <td>${egp(r.balance)}</td>
      </tr>`).join('')}
      <tr class="tot"><td colspan="4">الإجمالي</td><td>${egp(report.total_debit)}</td><td>${egp(report.total_credit)}</td><td>${egp(report.balance)}</td></tr>
    </tbody>
  </table>
</body></html>`
    const w = window.open('', '_blank')
    w.document.write(html); w.document.close()
    setTimeout(() => w.print(), 500)
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">📋 تقارير التصفية (كشوف الحسابات)</h2>

      {/* تبويبات */}
      <div className="flex gap-2">
        {[['suppliers', 'الموردين'], ['customers', 'العملاء']].map(([id, label]) => (
          <button key={id} className="btn btn-sm"
            style={tab === id ? { background: 'var(--teal)', color: '#fff' } : {}}
            onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      {/* فلاتر */}
      <div className="card flex flex-wrap items-end gap-3">
        <div style={{ minWidth: 220 }}>
          <Select label={tab === 'suppliers' ? 'المورّد' : 'العميل'} value={entityId}
            onChange={e => setEntityId(e.target.value)}>
            <option value="">— اختر —</option>
            {entities.map(en => <option key={en.id} value={en.id}>{en.name}</option>)}
          </Select>
        </div>
        <Input label="من تاريخ" type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
        <Input label="إلى تاريخ" type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} />
        {tab === 'customers' && locations.length > 0 && (
          <div style={{ minWidth: 180 }}>
            <Select label="الموقع / الفرع" value={locationId} onChange={e => setLocationId(e.target.value)}>
              <option value="">كل المواقع</option>
              {locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            </Select>
          </div>
        )}
        <button className="btn btn-primary btn-sm" onClick={run} disabled={loading}>
          {loading ? '...' : 'عرض الكشف'}
        </button>
        {report && <button className="btn btn-sm" onClick={printReport}>🖨️ طباعة</button>}
        {report && <button className="btn btn-sm" onClick={exportCsv}>⬇️ تصدير CSV</button>}
      </div>

      {/* النتيجة */}
      {loading ? <PageSpinner /> : report && (
        <div className="card">
          <div className="flex flex-wrap gap-4 mb-3 text-sm">
            {report.date_from && (
              <div>الرصيد الافتتاحي: <strong>{egp(report.opening_balance || 0)}</strong></div>
            )}
            <div>إجمالي مدين: <strong style={{ color: 'var(--red)' }}>{egp(report.total_debit)}</strong></div>
            <div>إجمالي دائن: <strong style={{ color: 'var(--green)' }}>{egp(report.total_credit)}</strong></div>
            <div>الرصيد: <strong>{egp(report.balance)}</strong></div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-erp-muted text-xs border-b" style={{ borderColor: 'var(--border)' }}>
                  <th className="text-right py-2 px-2">التاريخ</th>
                  <th className="text-right py-2 px-2">النوع</th>
                  <th className="text-right py-2 px-2">المرجع</th>
                  <th className="text-right py-2 px-2">البيان</th>
                  <th className="text-right py-2 px-2">مدين</th>
                  <th className="text-right py-2 px-2">دائن</th>
                  <th className="text-right py-2 px-2">الرصيد</th>
                </tr>
              </thead>
              <tbody>
                {report.date_from && (
                  <tr className="border-b" style={{ borderColor: 'var(--border-lt)', background: 'var(--surface-2, #f5f4f0)' }}>
                    <td colSpan={6} className="py-2 px-2 font-semibold">رصيد افتتاحي (قبل {fmtDate(report.date_from)})</td>
                    <td className="py-2 px-2 font-semibold">{egp(report.opening_balance || 0)}</td>
                  </tr>
                )}
                {(report.rows || []).map((r, i) => (
                  <tr key={i} className="border-b" style={{ borderColor: 'var(--border-lt)' }}>
                    <td className="py-2 px-2 whitespace-nowrap">{fmtDate(r.date)}</td>
                    <td className="py-2 px-2">{r.type === 'invoice' ? '🧾 فاتورة' : '💵 دفعة'}</td>
                    <td className="py-2 px-2">{r.ref}</td>
                    <td className="py-2 px-2 text-erp-muted">{r.description}</td>
                    <td className="py-2 px-2" style={{ color: 'var(--red)' }}>{r.debit ? egp(r.debit) : ''}</td>
                    <td className="py-2 px-2" style={{ color: 'var(--green)' }}>{r.credit ? egp(r.credit) : ''}</td>
                    <td className="py-2 px-2 font-semibold">{egp(r.balance)}</td>
                  </tr>
                ))}
                {!(report.rows || []).length && (
                  <tr><td colSpan={7} className="text-center text-erp-muted py-6">لا توجد حركات في الفترة</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
