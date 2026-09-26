import { useState, useEffect, useCallback } from 'react'
import api from '../api/client'
import { useApi } from '../hooks/useApi'
import Modal, { ModalFooter } from '../components/ui/Modal'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { useToast } from '../store/toast'

/* ─── helpers ─────────────────────────────────────────────── */
const egp = (v) => {
  const n = Number(v || 0)
  const sym = window.__ERP_CURRENCY__ || 'ج.م'
  return `${n.toLocaleString('ar-EG', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${sym}`
}

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

const today = () => new Date().toISOString().slice(0, 10)
const emptyItem = () => ({ description: '', qty: 1, unit: 'وحدة', unit_price: 0, sort_order: 0 })

export default function WorkCompletionCerts() {
  const addToast = useToast()
  const [certs, setCerts]         = useState([])
  const [loading, setLoading]     = useState(true)
  const [selected, setSelected]   = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [showDetail, setShowDetail] = useState(false)
  const [confirmDel, setConfirmDel] = useState(null)
  const [saving, setSaving]       = useState(false)
  const [filterStatus, setFilter] = useState('')

  const { data: customers = [] }       = useApi(() => api.customers.list())
  const { data: supplyContracts = [] } = useApi(() => api.supplyContracts.list())
  const { data: invoices = [] }        = useApi(() => api.sales.invoices())

  const [form, setForm] = useState({
    customer_id: '', invoice_id: '', contract_id: '',
    date: today(), project_name: '', project_location: '',
    start_date: '', end_date: '',
    contract_value: 0, warranty_months: 12,
    scope_summary: '', notes: '',
    items: [emptyItem()],
  })

  /* ── fetch ── */
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const p = filterStatus ? { status: filterStatus } : {}
      const data = await api.workCompletionCerts.list(p)
      setCerts(Array.isArray(data) ? data : [])
    } catch { addToast('خطأ في التحميل', 'error') }
    finally { setLoading(false) }
  }, [filterStatus])

  useEffect(() => { load() }, [load])

  /* ── computed totals ── */
  const formTotal = form.items.reduce((s, it) =>
    s + (parseFloat(it.qty || 0) * parseFloat(it.unit_price || 0)), 0)

  /* ── stats ── */
  const stats = {
    total:    certs.length,
    draft:    certs.filter(c => c.status === 'draft').length,
    issued:   certs.filter(c => c.status === 'issued').length,
    totalVal: certs.filter(c => c.status === 'issued')
                   .reduce((s, c) => s + (c.contract_value || 0), 0),
  }

  /* ── line helpers ── */
  const setItem = (i, field, val) =>
    setForm(f => ({ ...f, items: f.items.map((it, idx) => idx === i ? { ...it, [field]: val } : it) }))
  const addItem    = () => setForm(f => ({ ...f, items: [...f.items, emptyItem()] }))
  const removeItem = (i) => setForm(f => ({ ...f, items: f.items.filter((_, idx) => idx !== i) }))

  /* ── create ── */
  const handleCreate = async () => {
    if (!form.customer_id)   return addToast('اختر العميل', 'error')
    if (!form.project_name)  return addToast('أدخل اسم المشروع', 'error')
    setSaving(true)
    try {
      const payload = {
        customer_id:      parseInt(form.customer_id),
        invoice_id:       form.invoice_id  ? parseInt(form.invoice_id)  : null,
        contract_id:      form.contract_id ? parseInt(form.contract_id) : null,
        date:             form.date,
        project_name:     form.project_name,
        project_location: form.project_location || null,
        start_date:       form.start_date || null,
        end_date:         form.end_date || null,
        contract_value:   parseFloat(form.contract_value) || 0,
        warranty_months:  parseInt(form.warranty_months) || 12,
        scope_summary:    form.scope_summary || null,
        notes:            form.notes || null,
        items: form.items.map((it, i) => ({
          description: it.description,
          qty:         parseFloat(it.qty) || 1,
          unit:        it.unit || 'وحدة',
          unit_price:  parseFloat(it.unit_price) || 0,
          sort_order:  i,
        })).filter(it => it.description),
      }
      await api.workCompletionCerts.create(payload)
      addToast('تم إنشاء الشهادة', 'success')
      setShowCreate(false)
      resetForm()
      load()
    } catch (e) { addToast(e?.detail || 'خطأ في الحفظ', 'error') }
    finally { setSaving(false) }
  }

  const resetForm = () => setForm({
    customer_id: '', invoice_id: '', contract_id: '',
    date: today(), project_name: '', project_location: '',
    start_date: '', end_date: '',
    contract_value: 0, warranty_months: 12,
    scope_summary: '', notes: '',
    items: [emptyItem()],
  })

  /* ── from contract ── */
  const handleFromContract = async (cid) => {
    if (!cid) return
    try {
      await api.workCompletionCerts.fromContract(cid)
      addToast('تم إنشاء شهادة إتمام من العقد', 'success')
      load()
    } catch (e) { addToast(e?.detail || 'خطأ', 'error') }
  }

  /* ── issue ── */
  const handleIssue = async (id) => {
    try {
      await api.workCompletionCerts.issue(id)
      addToast('تم إصدار الشهادة', 'success')
      setShowDetail(false)
      load()
    } catch (e) { addToast(e?.detail || 'خطأ', 'error') }
  }

  /* ── delete ── */
  const handleDelete = async (id) => {
    try {
      await api.workCompletionCerts.delete(id)
      addToast('تم الحذف', 'success')
      setConfirmDel(null)
      setShowDetail(false)
      load()
    } catch (e) { addToast(e?.detail || 'خطأ', 'error') }
  }

  const printCert = (id) => api.print(`/work-completion-certs/${id}/print`)
  const openDetail = (c) => { setSelected(c); setShowDetail(true) }

  /* ════════════════════════════════════════════════════════ */
  return (
    <div style={{ direction: 'rtl', fontFamily: 'Tajawal, sans-serif', padding: '0 4px' }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 800, color: '#0C2340', margin: 0 }}>
            🏆 شهادات إتمام الأعمال
          </h1>
          <p style={{ fontSize: 12, color: '#9CA3AF', margin: '4px 0 0' }}>
            إصدار شهادات إتمام للمشاريع والعقود المنجزة
          </p>
        </div>
        <button onClick={() => setShowCreate(true)}
          style={{ background: '#0C2340', color: '#fff', border: 'none', borderRadius: 10,
            padding: '10px 22px', fontWeight: 700, fontSize: 14, cursor: 'pointer', fontFamily: 'inherit' }}>
          + شهادة إتمام جديدة
        </button>
      </div>

      {/* Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 20 }}>
        {[
          { label: 'إجمالي الشهادات', val: stats.total, color: '#0C2340' },
          { label: 'مسودات', val: stats.draft, color: '#6B7280' },
          { label: 'شهادات صادرة', val: stats.issued, color: '#15803D' },
          { label: 'قيمة المشاريع المنجزة', val: egp(stats.totalVal), color: '#B8892A' },
        ].map(s => (
          <div key={s.label} style={{ background: '#fff', borderRadius: 12, padding: '14px 18px',
            boxShadow: '0 1px 6px rgba(0,0,0,.06)', borderTop: `3px solid ${s.color}` }}>
            <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 4 }}>{s.label}</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: s.color }}>{s.val}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <select value={filterStatus} onChange={e => setFilter(e.target.value)}
          style={{ border: '1px solid #E5E7EB', borderRadius: 8, padding: '7px 12px',
            fontSize: 13, fontFamily: 'inherit', background: '#fff' }}>
          <option value="">كل الحالات</option>
          <option value="draft">مسودة</option>
          <option value="issued">صادرة</option>
        </select>

        <select defaultValue=""
          onChange={e => handleFromContract(e.target.value)}
          style={{ border: '1px solid #E5E7EB', borderRadius: 8, padding: '7px 12px',
            fontSize: 13, fontFamily: 'inherit', background: '#fff' }}>
          <option value="">← إنشاء من عقد توريد</option>
          {(Array.isArray(supplyContracts) ? supplyContracts : []).map(sc => (
            <option key={sc.id} value={sc.id}>{sc.code} — {sc.customer_name}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div style={{ background: '#fff', borderRadius: 14, boxShadow: '0 1px 8px rgba(0,0,0,.06)', overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: '#9CA3AF' }}>جاري التحميل…</div>
        ) : certs.length === 0 ? (
          <div style={{ padding: 60, textAlign: 'center' }}>
            <div style={{ fontSize: 42, marginBottom: 12 }}>🏆</div>
            <div style={{ fontSize: 15, fontWeight: 600, color: '#374151' }}>لا توجد شهادات إتمام</div>
            <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 4 }}>أنشئ شهادة جديدة أو حولها من عقد توريد</div>
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#0C2340', color: '#fff' }}>
                {['رقم الشهادة', 'اسم المشروع', 'العميل', 'التاريخ', 'قيمة العقد', 'ضمان', 'الحالة', ''].map(h => (
                  <th key={h} style={{ padding: '11px 14px', fontSize: 12, fontWeight: 700,
                    textAlign: 'right', whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {certs.map((c, i) => (
                <tr key={c.id}
                  onClick={() => openDetail(c)}
                  style={{ background: i % 2 === 0 ? '#FAFAFA' : '#fff', cursor: 'pointer', transition: 'background .15s' }}
                  onMouseEnter={e => e.currentTarget.style.background = '#FFFBF0'}
                  onMouseLeave={e => e.currentTarget.style.background = i % 2 === 0 ? '#FAFAFA' : '#fff'}>
                  <td style={{ padding: '10px 14px', fontWeight: 700, color: '#B8892A', fontSize: 13 }}>{c.code}</td>
                  <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600 }}>{c.project_name}</td>
                  <td style={{ padding: '10px 14px', fontSize: 13 }}>{c.customer_name}</td>
                  <td style={{ padding: '10px 14px', fontSize: 12 }}>{c.date}</td>
                  <td style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13 }}>{egp(c.contract_value)}</td>
                  <td style={{ padding: '10px 14px', fontSize: 12 }}>{c.warranty_months} شهر</td>
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
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Detail Modal */}
      {showDetail && selected && (
        <Modal title={`شهادة إتمام — ${selected.code}`} onClose={() => setShowDetail(false)} size="lg">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 16 }}>
            {[
              { label: 'رقم الشهادة', val: selected.code },
              { label: 'العميل', val: selected.customer_name },
              { label: 'اسم المشروع', val: selected.project_name },
              { label: 'موقع التنفيذ', val: selected.project_location || '—' },
              { label: 'تاريخ البدء', val: selected.start_date || '—' },
              { label: 'تاريخ الانتهاء', val: selected.end_date || '—' },
              { label: 'قيمة العقد', val: egp(selected.contract_value) },
              { label: 'فترة الضمان', val: `${selected.warranty_months} شهر` },
              { label: 'الحالة', val: <StatusPill status={selected.status} /> },
              { label: 'الفاتورة', val: selected.invoice_code || '—' },
              { label: 'العقد', val: selected.contract_code || '—' },
              { label: 'التاريخ', val: selected.date },
            ].map(it => (
              <div key={it.label} style={{ background: '#F9FAFB', borderRadius: 8, padding: '10px 14px' }}>
                <div style={{ fontSize: 10, color: '#9CA3AF', marginBottom: 3 }}>{it.label}</div>
                <div style={{ fontSize: 13, fontWeight: 700 }}>{it.val}</div>
              </div>
            ))}
          </div>

          {selected.scope_summary && (
            <div style={{ background: '#FFFBF0', border: '1px solid #FDE68A', borderRadius: 8,
              padding: '10px 14px', marginBottom: 14 }}>
              <div style={{ fontSize: 10, fontWeight: 800, color: '#92400E', marginBottom: 4 }}>نطاق الأعمال</div>
              <div style={{ fontSize: 13 }}>{selected.scope_summary}</div>
            </div>
          )}

          {selected.items?.length > 0 && (
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: '#6B7280', marginBottom: 8 }}>بنود الأعمال</div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ background: '#0C2340', color: '#fff' }}>
                    {['#', 'الوصف', 'الكمية', 'الوحدة', 'سعر الوحدة', 'الإجمالي'].map(h => (
                      <th key={h} style={{ padding: '8px 10px', textAlign: 'right', fontSize: 11 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {selected.items.map((it, i) => (
                    <tr key={it.id} style={{ background: i % 2 === 0 ? '#FAFAFA' : '#fff' }}>
                      <td style={{ padding: '7px 10px', color: '#9CA3AF' }}>{i + 1}</td>
                      <td style={{ padding: '7px 10px', fontWeight: 600 }}>{it.description}</td>
                      <td style={{ padding: '7px 10px' }}>{it.qty}</td>
                      <td style={{ padding: '7px 10px', color: '#6B7280' }}>{it.unit}</td>
                      <td style={{ padding: '7px 10px' }}>{egp(it.unit_price)}</td>
                      <td style={{ padding: '7px 10px', fontWeight: 700, color: '#B8892A' }}>{egp(it.total_price)}</td>
                    </tr>
                  ))}
                  <tr style={{ background: 'linear-gradient(90deg,#3d2600,#B8892A)', color: '#fff' }}>
                    <td colSpan={5} style={{ padding: '10px 12px', fontWeight: 700 }}>إجمالي الأعمال</td>
                    <td style={{ padding: '10px 12px', fontWeight: 900, fontSize: 15 }}>
                      {egp(selected.items.reduce((s, it) => s + it.total_price, 0))}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}

          {/* Warranty */}
          <div style={{ background: 'linear-gradient(135deg,#052015,#0a3a20)', borderRadius: 12,
            padding: '12px 20px', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 14 }}>
            <span style={{ fontSize: 24 }}>🛡️</span>
            <div>
              <div style={{ fontSize: 11, color: 'rgba(255,255,255,.6)' }}>فترة الضمان</div>
              <div style={{ fontSize: 18, fontWeight: 900, color: '#4ade80' }}>
                {selected.warranty_months} شهر
              </div>
            </div>
          </div>

          <ModalFooter>
            <button onClick={() => printCert(selected.id)}
              style={{ background: '#0C2340', color: '#fff', border: 'none', borderRadius: 8,
                padding: '9px 20px', fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit' }}>
              🖨️ طباعة
            </button>
            {selected.status === 'draft' && (
              <button onClick={() => handleIssue(selected.id)}
                style={{ background: '#15803D', color: '#fff', border: 'none', borderRadius: 8,
                  padding: '9px 20px', fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit' }}>
                ✅ إصدار الشهادة
              </button>
            )}
            {selected.status === 'draft' && (
              <button onClick={() => setConfirmDel(selected.id)}
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
        <Modal title="شهادة إتمام أعمال جديدة" onClose={() => { setShowCreate(false); resetForm() }} size="xl">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 16 }}>
            {/* Customer */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>العميل *</label>
              <select value={form.customer_id}
                onChange={e => setForm(f => ({ ...f, customer_id: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}>
                <option value="">اختر العميل</option>
                {(Array.isArray(customers) ? customers : []).map(c => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            {/* Date */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>تاريخ الشهادة *</label>
              <input type="date" value={form.date}
                onChange={e => setForm(f => ({ ...f, date: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
            </div>
            {/* Project Name */}
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>اسم المشروع *</label>
              <input type="text" value={form.project_name}
                onChange={e => setForm(f => ({ ...f, project_name: e.target.value }))}
                placeholder="اسم المشروع أو الأعمال المنجزة"
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
            </div>
            {/* Location */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>موقع التنفيذ</label>
              <input type="text" value={form.project_location}
                onChange={e => setForm(f => ({ ...f, project_location: e.target.value }))}
                placeholder="المدينة أو العنوان"
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
            </div>
            {/* Contract Value */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>قيمة العقد</label>
              <input type="number" min="0" step="0.01" value={form.contract_value}
                onChange={e => setForm(f => ({ ...f, contract_value: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
            </div>
            {/* Start / End */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>تاريخ البدء</label>
              <input type="date" value={form.start_date}
                onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>تاريخ الانتهاء</label>
              <input type="date" value={form.end_date}
                onChange={e => setForm(f => ({ ...f, end_date: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
            </div>
            {/* Warranty */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>فترة الضمان (شهر)</label>
              <input type="number" min="0" value={form.warranty_months}
                onChange={e => setForm(f => ({ ...f, warranty_months: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
            </div>
            {/* Contract / Invoice links */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>عقد توريد مرجعي</label>
              <select value={form.contract_id}
                onChange={e => setForm(f => ({ ...f, contract_id: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}>
                <option value="">بدون عقد</option>
                {(Array.isArray(supplyContracts) ? supplyContracts : []).map(sc => (
                  <option key={sc.id} value={sc.id}>{sc.code} — {sc.customer_name}</option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>فاتورة مرجعية</label>
              <select value={form.invoice_id}
                onChange={e => setForm(f => ({ ...f, invoice_id: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}>
                <option value="">بدون فاتورة</option>
                {(Array.isArray(invoices) ? invoices : []).map(inv => (
                  <option key={inv.id} value={inv.id}>{inv.code}</option>
                ))}
              </select>
            </div>
            {/* Scope */}
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>نطاق الأعمال المنجزة</label>
              <textarea value={form.scope_summary}
                onChange={e => setForm(f => ({ ...f, scope_summary: e.target.value }))}
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

          {/* Items */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: '#374151' }}>بنود الأعمال (اختياري)</span>
              <button onClick={addItem}
                style={{ background: '#EFF6FF', color: '#1D4ED8', border: 'none', borderRadius: 7,
                  padding: '5px 14px', fontSize: 12, fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit' }}>
                + بند
              </button>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: '#F3F4F6' }}>
                  {['الوصف', 'الكمية', 'الوحدة', 'سعر الوحدة', 'الإجمالي', ''].map(h => (
                    <th key={h} style={{ padding: '7px 10px', textAlign: 'right', fontWeight: 700,
                      color: '#6B7280', fontSize: 11 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {form.items.map((it, i) => {
                  const lTotal = (parseFloat(it.qty) || 0) * (parseFloat(it.unit_price) || 0)
                  return (
                    <tr key={i} style={{ borderBottom: '1px solid #F3F4F6' }}>
                      <td style={{ padding: '5px 6px' }}>
                        <input value={it.description}
                          onChange={e => setItem(i, 'description', e.target.value)}
                          placeholder="وصف البند"
                          style={{ width: '100%', border: '1px solid #E5E7EB', borderRadius: 6,
                            padding: '6px 8px', fontSize: 12, fontFamily: 'inherit' }}/>
                      </td>
                      <td style={{ padding: '5px 6px', width: 80 }}>
                        <input type="number" min="0" step="0.01" value={it.qty}
                          onChange={e => setItem(i, 'qty', e.target.value)}
                          style={{ width: '100%', border: '1px solid #E5E7EB', borderRadius: 6,
                            padding: '6px 8px', fontSize: 12, fontFamily: 'inherit' }}/>
                      </td>
                      <td style={{ padding: '5px 6px', width: 90 }}>
                        <input value={it.unit}
                          onChange={e => setItem(i, 'unit', e.target.value)}
                          style={{ width: '100%', border: '1px solid #E5E7EB', borderRadius: 6,
                            padding: '6px 8px', fontSize: 12, fontFamily: 'inherit' }}/>
                      </td>
                      <td style={{ padding: '5px 6px', width: 110 }}>
                        <input type="number" min="0" step="0.01" value={it.unit_price}
                          onChange={e => setItem(i, 'unit_price', e.target.value)}
                          style={{ width: '100%', border: '1px solid #E5E7EB', borderRadius: 6,
                            padding: '6px 8px', fontSize: 12, fontFamily: 'inherit' }}/>
                      </td>
                      <td style={{ padding: '5px 6px', fontWeight: 700, color: '#B8892A', width: 100, textAlign: 'left' }}>
                        {lTotal.toLocaleString('ar-EG', { minimumFractionDigits: 2 })}
                      </td>
                      <td style={{ padding: '5px 6px', width: 36 }}>
                        {form.items.length > 1 && (
                          <button onClick={() => removeItem(i)}
                            style={{ background: '#FEE2E2', border: 'none', borderRadius: 6,
                              padding: '4px 8px', cursor: 'pointer', fontSize: 13 }}>✕</button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            {formTotal > 0 && (
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
                <div style={{ background: 'linear-gradient(90deg,#3d2600,#B8892A)', color: '#fff',
                  borderRadius: 10, padding: '10px 20px', minWidth: 220 }}>
                  <div style={{ fontSize: 11, opacity: .75, marginBottom: 2 }}>إجمالي البنود</div>
                  <div style={{ fontSize: 20, fontWeight: 900 }}>{egp(formTotal)}</div>
                </div>
              </div>
            )}
          </div>

          <ModalFooter>
            <button onClick={handleCreate} disabled={saving}
              style={{ background: '#0C2340', color: '#fff', border: 'none', borderRadius: 8,
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
    </div>
  )
}
