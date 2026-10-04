import { useState, useEffect, useCallback } from 'react'
import api from '../api/client'
import { useApi } from '../hooks/useApi'
import Modal, { ModalFooter } from '../components/ui/Modal'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { useToast } from '../store/toast'

/* ─── helpers ─────────────────────────────────────────────── */
const STATUS_COLOR = {
  draft:  { bg: '#F3F4F6', color: '#6B7280', label: 'مسودة' },
  issued: { bg: '#DCFCE7', color: '#15803D', label: 'صادرة' },
}

function StatusPill({ status }) {
  const s = STATUS_COLOR[status] || STATUS_COLOR.draft
  return (
    <span style={{ background: s.bg, color: s.color,
      fontSize: 11, fontWeight: 700, padding: '2px 10px', borderRadius: 99 }}>
      {s.label}
    </span>
  )
}

// عدد الأيام المتبقية حتى تاريخ (YYYY-MM-DD) محسوبة على التاريخ المحلي —
// new Date('YYYY-MM-DD') بيتفسّر UTC فكان الضمان اللي بينتهي النهارده يظهر "منتهي".
function daysUntil(end) {
  if (!end) return null
  const [y, m, d] = String(end).slice(0, 10).split('-').map(Number)
  if (!y || !m || !d) return null
  const now = new Date()
  const todayMid = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((new Date(y, m - 1, d) - todayMid) / 86400000)
}

function DaysLeft({ end }) {
  if (!end) return null
  const days = daysUntil(end)
  if (days === null) return null
  if (days < 0)  return <span style={{ color: '#991B1B', fontWeight: 700, fontSize: 11 }}>منتهي</span>
  if (days < 30) return <span style={{ color: '#D97706', fontWeight: 700, fontSize: 11 }}>{days} يوم</span>
  return <span style={{ color: '#15803D', fontSize: 11 }}>{days} يوم</span>
}

// التاريخ المحلي (toISOString بيرجع UTC فممكن يطلع تاريخ امبارح/بكرة)
const today = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function WarrantyCerts() {
  const addToast = useToast()
  const [certs, setCerts]         = useState([])
  const [loading, setLoading]     = useState(true)
  const [selected, setSelected]   = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [showDetail, setShowDetail] = useState(false)
  const [confirmDel, setConfirmDel] = useState(null)
  const [saving, setSaving]       = useState(false)
  const [acting, setActing]       = useState(false)   // منع الضغط المزدوج على إصدار/حذف
  const [confirmIssue, setConfirmIssue] = useState(null)
  const [filterStatus, setFilter] = useState('')
  const [filterExp, setFilterExp] = useState('')    // expiring | expired

  const { data: customers = [] }  = useApi(() => api.customers.list())
  const { data: invoices = [] }   = useApi(() => api.sales.invoices())
  const { data: wccList = [] }    = useApi(() => api.workCompletionCerts.list())

  const [form, setForm] = useState({
    customer_id: '', invoice_id: '', wcc_id: '',
    date: today(), product_name: '', model_no: '', serial_no: '',
    warranty_months: 12, warranty_start: today(),
    covered_items: 'عيوب التصنيع والمواد والتشغيل',
    exclusions: 'الإساءة في الاستخدام — الحوادث — التعديلات غير المرخصة',
    service_contact: '', notes: '',
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const p = filterStatus ? { status: filterStatus } : {}
      const data = await api.warrantyCerts.list(p)
      let list = Array.isArray(data) ? data : []
      if (filterExp === 'expiring') {
        list = list.filter(c => { const d = daysUntil(c.warranty_end); return d !== null && d >= 0 && d <= 30 })
      } else if (filterExp === 'expired') {
        list = list.filter(c => { const d = daysUntil(c.warranty_end); return d !== null && d < 0 })
      }
      setCerts(list)
    } catch (e) { addToast(e?.message || 'خطأ في التحميل', 'error') }
    finally { setLoading(false) }
  }, [filterStatus, filterExp])

  useEffect(() => { load() }, [load])

  const stats = {
    total:    certs.length,
    active:   certs.filter(c => (daysUntil(c.warranty_end) ?? -1) >= 0).length,
    expiring: certs.filter(c => {
      const d = daysUntil(c.warranty_end)
      return d !== null && d >= 0 && d <= 30
    }).length,
    expired:  certs.filter(c => (daysUntil(c.warranty_end) ?? 0) < 0).length,
  }

  const handleCreate = async () => {
    if (saving) return
    if (!form.customer_id)   return addToast('اختر العميل', 'error')
    if (!String(form.product_name || '').trim())  return addToast('أدخل اسم المنتج', 'error')
    if (!form.date)           return addToast('أدخل تاريخ الشهادة', 'error')
    if (!form.warranty_start) return addToast('أدخل تاريخ بداية الضمان', 'error')
    // كان parseInt(...) || 12 بيحوّل أي قيمة غير صالحة (أو صفر) لـ 12 شهر بصمت
    const months = Number(form.warranty_months)
    if (form.warranty_months === '' || !Number.isInteger(months) || months < 1)
      return addToast('مدة الضمان لازم تكون عدد صحيح من الشهور أكبر من صفر', 'error')
    setSaving(true)
    try {
      const payload = {
        customer_id:     parseInt(form.customer_id),
        invoice_id:      form.invoice_id ? parseInt(form.invoice_id) : null,
        wcc_id:          form.wcc_id ? parseInt(form.wcc_id) : null,
        date:            form.date,
        product_name:    String(form.product_name).trim(),
        model_no:        form.model_no || null,
        serial_no:       form.serial_no || null,
        warranty_months: months,
        warranty_start:  form.warranty_start,
        covered_items:   form.covered_items || null,
        exclusions:      form.exclusions || null,
        service_contact: form.service_contact || null,
        notes:           form.notes || null,
      }
      await api.warrantyCerts.create(payload)
      addToast('تم إنشاء شهادة الضمان', 'success')
      setShowCreate(false)
      resetForm()
      load()
    } catch (e) { addToast(e?.message || 'خطأ في الحفظ', 'error') }
    finally { setSaving(false) }
  }

  const resetForm = () => setForm({
    customer_id: '', invoice_id: '', wcc_id: '',
    date: today(), product_name: '', model_no: '', serial_no: '',
    warranty_months: 12, warranty_start: today(),
    covered_items: 'عيوب التصنيع والمواد والتشغيل',
    exclusions: 'الإساءة في الاستخدام — الحوادث — التعديلات غير المرخصة',
    service_contact: '', notes: '',
  })

  const handleIssue = async (id) => {
    if (acting) return
    setActing(true)
    try {
      await api.warrantyCerts.issue(id)
      addToast('تم إصدار شهادة الضمان', 'success')
      setShowDetail(false)
      load()
    } catch (e) { addToast(e?.message || 'خطأ', 'error') }
    finally { setActing(false) }
  }

  const handleDelete = async (id) => {
    if (acting) return
    setActing(true)
    try {
      await api.warrantyCerts.delete(id)
      addToast('تم الحذف', 'success')
      setConfirmDel(null)
      setShowDetail(false)
      load()
    } catch (e) { addToast(e?.message || 'خطأ', 'error') }
    finally { setActing(false) }
  }

  const printCert = (id) => api.print(`/warranty-certs/${id}/print`)
  const printAging = () => api.print('/reports/sales/aging/print')
  const openDetail = (c) => { setSelected(c); setShowDetail(true) }

  /* ════════════════════════════════════════════════════════ */
  return (
    <div style={{ direction: 'rtl', fontFamily: 'Tajawal, sans-serif', padding: '0 4px' }}>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 800, color: '#0C2340', margin: 0 }}>
            🛡️ شهادات الضمان
          </h1>
          <p style={{ fontSize: 12, color: '#9CA3AF', margin: '4px 0 0' }}>
            إدارة ضمانات المنتجات والأجهزة
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button onClick={printAging}
            style={{ background: '#F59E0B', color: '#fff', border: 'none', borderRadius: 10,
              padding: '10px 18px', fontWeight: 700, fontSize: 13, cursor: 'pointer', fontFamily: 'inherit' }}>
            📊 طباعة تقرير المديونيات
          </button>
          <button onClick={() => setShowCreate(true)}
            style={{ background: '#0A6E3F', color: '#fff', border: 'none', borderRadius: 10,
              padding: '10px 22px', fontWeight: 700, fontSize: 14, cursor: 'pointer', fontFamily: 'inherit' }}>
            + شهادة ضمان جديدة
          </button>
        </div>
      </div>

      {/* Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 20 }}>
        {[
          { label: 'إجمالي الشهادات', val: stats.total,    color: '#0C2340' },
          { label: 'ضمانات سارية',   val: stats.active,   color: '#15803D' },
          { label: 'تنتهي خلال 30 يوم', val: stats.expiring, color: '#D97706' },
          { label: 'ضمانات منتهية',  val: stats.expired,  color: '#991B1B' },
        ].map(s => (
          <div key={s.label} style={{ background: '#fff', borderRadius: 12, padding: '14px 18px',
            boxShadow: '0 1px 6px rgba(0,0,0,.06)', borderTop: `3px solid ${s.color}` }}>
            <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 4 }}>{s.label}</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: s.color }}>{s.val}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
        <select value={filterStatus} onChange={e => setFilter(e.target.value)}
          style={{ border: '1px solid #E5E7EB', borderRadius: 8, padding: '7px 12px',
            fontSize: 13, fontFamily: 'inherit', background: '#fff' }}>
          <option value="">كل الحالات</option>
          <option value="draft">مسودة</option>
          <option value="issued">صادرة</option>
        </select>
        <select value={filterExp} onChange={e => setFilterExp(e.target.value)}
          style={{ border: '1px solid #E5E7EB', borderRadius: 8, padding: '7px 12px',
            fontSize: 13, fontFamily: 'inherit', background: '#fff' }}>
          <option value="">كل الضمانات</option>
          <option value="expiring">تنتهي خلال 30 يوم</option>
          <option value="expired">منتهية</option>
        </select>
      </div>

      {/* Table */}
      <div style={{ background: '#fff', borderRadius: 14, boxShadow: '0 1px 8px rgba(0,0,0,.06)', overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: '#9CA3AF' }}>جاري التحميل…</div>
        ) : certs.length === 0 ? (
          <div style={{ padding: 60, textAlign: 'center' }}>
            <div style={{ fontSize: 42, marginBottom: 12 }}>🛡️</div>
            <div style={{ fontSize: 15, fontWeight: 600, color: '#374151' }}>لا توجد شهادات ضمان</div>
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#0C2340', color: '#fff' }}>
                {['رقم الشهادة', 'المنتج', 'العميل', 'موديل / S/N', 'بداية', 'نهاية', 'متبقي', 'الحالة', ''].map(h => (
                  <th key={h} style={{ padding: '11px 14px', fontSize: 12, fontWeight: 700,
                    textAlign: 'right', whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {certs.map((c, i) => {
                const expired = new Date(c.warranty_end) < new Date()
                return (
                  <tr key={c.id}
                    onClick={() => openDetail(c)}
                    style={{ background: expired ? '#FFF5F5' : i % 2 === 0 ? '#FAFAFA' : '#fff',
                      cursor: 'pointer', transition: 'background .15s' }}
                    onMouseEnter={e => e.currentTarget.style.background = '#F0FDF4'}
                    onMouseLeave={e => e.currentTarget.style.background = expired ? '#FFF5F5' : i % 2 === 0 ? '#FAFAFA' : '#fff'}>
                    <td style={{ padding: '10px 14px', fontWeight: 700, color: '#0A6E3F', fontSize: 13 }}>{c.code}</td>
                    <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600 }}>{c.product_name}</td>
                    <td style={{ padding: '10px 14px', fontSize: 13 }}>{c.customer_name}</td>
                    <td style={{ padding: '10px 14px', fontSize: 11, color: '#6B7280' }}>
                      {c.model_no || '—'} {c.serial_no ? `/ ${c.serial_no}` : ''}
                    </td>
                    <td style={{ padding: '10px 14px', fontSize: 12 }}>{c.warranty_start}</td>
                    <td style={{ padding: '10px 14px', fontSize: 12, color: expired ? '#991B1B' : '#15803D', fontWeight: 600 }}>
                      {c.warranty_end}
                    </td>
                    <td style={{ padding: '10px 14px' }}><DaysLeft end={c.warranty_end} /></td>
                    <td style={{ padding: '10px 14px' }}><StatusPill status={c.status} /></td>
                    <td style={{ padding: '10px 14px' }} onClick={e => e.stopPropagation()}>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button onClick={() => printCert(c.id)} title="طباعة"
                          style={{ background: '#F3F4F6', border: 'none', borderRadius: 6,
                            padding: '5px 10px', cursor: 'pointer', fontSize: 14 }}>🖨️</button>
                        {c.status === 'draft' && (
                          <button onClick={() => setConfirmDel(c.id)} title="حذف"
                            style={{ background: '#FEE2E2', border: 'none', borderRadius: 6,
                              padding: '5px 10px', cursor: 'pointer', fontSize: 14 }}>🗑️</button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Detail Modal */}
      {showDetail && selected && (
        <Modal title={`شهادة ضمان — ${selected.code}`} onClose={() => setShowDetail(false)} size="lg">
          {/* Warranty Banner */}
          <div style={{ background: 'linear-gradient(135deg,#052015,#0a3a20)', borderRadius: 12,
            padding: '16px 22px', marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: 11, color: 'rgba(255,255,255,.5)' }}>مدة الضمان</div>
              <div style={{ fontSize: 32, fontWeight: 900, color: '#4ade80' }}>{selected.warranty_months} شهر</div>
            </div>
            <div style={{ textAlign: 'left' }}>
              <div style={{ fontSize: 11, color: 'rgba(255,255,255,.5)', marginBottom: 4 }}>
                من <b style={{ color: '#fff' }}>{selected.warranty_start}</b>
              </div>
              <div style={{ fontSize: 11, color: 'rgba(255,255,255,.5)' }}>
                إلى <b style={{ color: '#4ade80' }}>{selected.warranty_end}</b>
              </div>
              <div style={{ marginTop: 6 }}><DaysLeft end={selected.warranty_end} /></div>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 16 }}>
            {[
              { label: 'المنتج', val: selected.product_name },
              { label: 'موديل', val: selected.model_no || '—' },
              { label: 'الرقم التسلسلي', val: selected.serial_no || '—' },
              { label: 'العميل', val: selected.customer_name },
              { label: 'الفاتورة', val: selected.invoice_code || '—' },
              { label: 'شهادة الإتمام', val: selected.wcc_code || '—' },
              { label: 'الحالة', val: <StatusPill status={selected.status} /> },
              { label: 'تاريخ الإصدار', val: selected.date },
              { label: 'خدمة العملاء', val: selected.service_contact || '—' },
            ].map(it => (
              <div key={it.label} style={{ background: '#F9FAFB', borderRadius: 8, padding: '10px 14px' }}>
                <div style={{ fontSize: 10, color: '#9CA3AF', marginBottom: 3 }}>{it.label}</div>
                <div style={{ fontSize: 13, fontWeight: 700 }}>{it.val}</div>
              </div>
            ))}
          </div>

          {selected.covered_items && (
            <div style={{ background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: 8,
              padding: '10px 14px', marginBottom: 10 }}>
              <div style={{ fontSize: 10, fontWeight: 800, color: '#15803D', marginBottom: 4 }}>✅ ما يشمله الضمان</div>
              <div style={{ fontSize: 13 }}>{selected.covered_items}</div>
            </div>
          )}
          {selected.exclusions && (
            <div style={{ background: '#FFF5F5', border: '1px solid #FCA5A5', borderRadius: 8,
              padding: '10px 14px', marginBottom: 10 }}>
              <div style={{ fontSize: 10, fontWeight: 800, color: '#991B1B', marginBottom: 4 }}>❌ ما لا يشمله الضمان</div>
              <div style={{ fontSize: 13 }}>{selected.exclusions}</div>
            </div>
          )}

          <ModalFooter>
            <button onClick={() => printCert(selected.id)}
              style={{ background: '#0A6E3F', color: '#fff', border: 'none', borderRadius: 8,
                padding: '9px 20px', fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit' }}>
              🖨️ طباعة
            </button>
            {selected.status === 'draft' && (
              <button onClick={() => setConfirmIssue(selected.id)} disabled={acting}
                style={{ background: '#15803D', color: '#fff', border: 'none', borderRadius: 8,
                  padding: '9px 20px', fontWeight: 700, cursor: acting ? 'not-allowed' : 'pointer',
                  opacity: acting ? .7 : 1, fontFamily: 'inherit' }}>
                {acting ? 'جاري…' : '✅ إصدار الشهادة'}
              </button>
            )}
            {selected.status === 'draft' && (
              <button onClick={() => setConfirmDel(selected.id)} disabled={acting}
                style={{ background: '#FEE2E2', color: '#991B1B', border: 'none', borderRadius: 8,
                  padding: '9px 20px', fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit' }}>
                🗑️ حذف
              </button>
            )}
            <button onClick={() => setShowDetail(false)}
              style={{ background: '#F3F4F6', color: '#374151', border: 'none', borderRadius: 8,
                padding: '9px 20px', fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
              إغلاق
            </button>
          </ModalFooter>
        </Modal>
      )}

      {/* Create Modal */}
      {showCreate && (
        <Modal title="شهادة ضمان جديدة" onClose={() => { setShowCreate(false); resetForm() }} size="xl">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 16 }}>
            {[
              { label: 'العميل *', field: 'customer_id', type: 'select',
                options: (Array.isArray(customers) ? customers : []).map(c => ({ value: c.id, label: c.name })) },
              { label: 'تاريخ الشهادة', field: 'date', type: 'date' },
              { label: 'اسم المنتج *', field: 'product_name', type: 'text', placeholder: 'اسم الجهاز / المنتج', col: 2 },
              { label: 'الموديل', field: 'model_no', type: 'text', placeholder: 'رقم الموديل' },
              { label: 'الرقم التسلسلي', field: 'serial_no', type: 'text', placeholder: 'S/N' },
              { label: 'مدة الضمان (شهر) *', field: 'warranty_months', type: 'number', min: 1, step: 1 },
              { label: 'تاريخ بداية الضمان *', field: 'warranty_start', type: 'date' },
              { label: 'للتواصل والصيانة', field: 'service_contact', type: 'text', placeholder: 'رقم هاتف أو بريد' },
            ].map(f => (
              <div key={f.field} style={{ gridColumn: f.col === 2 ? '1 / -1' : 'auto' }}>
                <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>{f.label}</label>
                {f.type === 'select' ? (
                  <select value={form[f.field]}
                    onChange={e => setForm(frm => ({ ...frm, [f.field]: e.target.value }))}
                    style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                      padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}>
                    <option value="">اختر…</option>
                    {(f.options || []).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                ) : (
                  <input type={f.type} value={form[f.field]}
                    placeholder={f.placeholder || ''}
                    min={f.min} step={f.step}
                    onChange={e => setForm(frm => ({ ...frm, [f.field]: e.target.value }))}
                    style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                      padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
                )}
              </div>
            ))}
            {/* Optional links */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>فاتورة مرجعية</label>
              <select value={form.invoice_id}
                onChange={e => setForm(f => ({ ...f, invoice_id: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}>
                <option value="">بدون فاتورة</option>
                {(Array.isArray(invoices) ? invoices : [])
                  .filter(inv => !form.customer_id || !inv.customer_id || String(inv.customer_id) === String(form.customer_id) || String(inv.id) === String(form.invoice_id))
                  .map(inv => (
                  <option key={inv.id} value={inv.id}>{inv.code}</option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>شهادة إتمام</label>
              <select value={form.wcc_id}
                onChange={e => setForm(f => ({ ...f, wcc_id: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}>
                <option value="">بدون ربط</option>
                {(Array.isArray(wccList) ? wccList : [])
                  .filter(w => !form.customer_id || !w.customer_id || String(w.customer_id) === String(form.customer_id) || String(w.id) === String(form.wcc_id))
                  .map(w => (
                  <option key={w.id} value={w.id}>{w.code} — {w.project_name}</option>
                ))}
              </select>
            </div>
            {/* Covered / Exclusions */}
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>✅ ما يشمله الضمان</label>
              <textarea value={form.covered_items}
                onChange={e => setForm(f => ({ ...f, covered_items: e.target.value }))}
                rows={2}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit', resize: 'vertical' }}/>
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>❌ ما لا يشمله الضمان</label>
              <textarea value={form.exclusions}
                onChange={e => setForm(f => ({ ...f, exclusions: e.target.value }))}
                rows={2}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit', resize: 'vertical' }}/>
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>ملاحظات</label>
              <textarea value={form.notes}
                onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                rows={2}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit', resize: 'vertical' }}/>
            </div>
          </div>

          <ModalFooter>
            <button onClick={handleCreate} disabled={saving}
              style={{ background: '#0A6E3F', color: '#fff', border: 'none', borderRadius: 8,
                padding: '9px 24px', fontWeight: 700, cursor: saving ? 'not-allowed' : 'pointer',
                opacity: saving ? .7 : 1, fontFamily: 'inherit' }}>
              {saving ? 'جاري الحفظ…' : '💾 حفظ الشهادة'}
            </button>
            <button onClick={() => { setShowCreate(false); resetForm() }}
              style={{ background: '#F3F4F6', color: '#374151', border: 'none', borderRadius: 8,
                padding: '9px 20px', fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
              إلغاء
            </button>
          </ModalFooter>
        </Modal>
      )}

      <ConfirmDialog
        open={!!confirmDel}
        title="حذف الشهادة"
        message="هل أنت متأكد من حذف هذه الشهادة؟"
        confirmLabel="حذف"
        confirmVariant="danger"
        onConfirm={() => handleDelete(confirmDel)}
        onCancel={() => setConfirmDel(null)}
      />

      <ConfirmDialog
        open={!!confirmIssue}
        title="إصدار شهادة الضمان"
        message="بعد الإصدار لا يمكن حذف الشهادة. هل تريد المتابعة؟"
        confirmLabel="إصدار"
        onConfirm={() => handleIssue(confirmIssue)}
        onCancel={() => setConfirmIssue(null)}
      />
    </div>
  )
}
