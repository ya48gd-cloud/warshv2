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

const STATUS_AR = { draft: 'مسودة', issued: 'صادر', applied: 'مُطبَّق', cancelled: 'ملغي' }
const STATUS_COLOR = {
  draft:     { bg: '#F3F4F6', color: '#6B7280' },
  issued:    { bg: '#DCFCE7', color: '#15803D' },
  applied:   { bg: '#DBEAFE', color: '#1D4ED8' },
  cancelled: { bg: '#FEE2E2', color: '#991B1B' },
}

function StatusPill({ status }) {
  const s = STATUS_COLOR[status] || STATUS_COLOR.draft
  return (
    <span style={{
      background: s.bg, color: s.color,
      fontSize: 11, fontWeight: 700,
      padding: '2px 10px', borderRadius: 99,
    }}>
      {STATUS_AR[status] || status}
    </span>
  )
}

const today = () => new Date().toISOString().slice(0, 10)

const emptyLine = () => ({ description: '', qty: 1, unit: 'قطعة', unit_price: '', sort_order: 0 })

/* ─── Component ──────────────────────────────────────────── */
export default function CreditNotes() {
  const addToast = useToast()
  const [notes, setNotes]         = useState([])
  const [loading, setLoading]     = useState(true)
  const [selected, setSelected]   = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [showDetail, setShowDetail] = useState(false)
  const [editingId, setEditingId]   = useState(null)
  const [confirmDel, setConfirmDel] = useState(null)
  const [saving, setSaving]       = useState(false)
  const [filterStatus, setFilter] = useState('')

  // data for create form
  const { data: customers = [] } = useApi(() => api.customers.list())
  const { data: invoices  = [] } = useApi(() => api.sales.invoices())

  const [form, setForm] = useState({
    customer_id: '', invoice_id: '', date: today(),
    reason: '', return_type: 'partial',
    tax_pct: 0, discount_amt: 0, notes: '',
    lines: [emptyLine()],
  })

  /* ── fetch ── */
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const p = filterStatus ? { status: filterStatus } : {}
      const data = await api.creditNotes.list(p)
      setNotes(Array.isArray(data) ? data : [])
    } catch { addToast('خطأ في تحميل الإشعارات', 'error') }
    finally { setLoading(false) }
  }, [filterStatus])

  useEffect(() => { load() }, [load])

  /* ── computed totals for form ── */
  const formSubtotal = form.lines.reduce((s, l) =>
    s + (parseFloat(l.qty || 0) * parseFloat(l.unit_price || 0)), 0)
  const formTax    = formSubtotal * (parseFloat(form.tax_pct) / 100)
  const formTotal  = formSubtotal - parseFloat(form.discount_amt || 0) + formTax

  /* ── dashboard stats ── */
  const stats = {
    total:    notes.length,
    draft:    notes.filter(n => n.status === 'draft').length,
    issued:   notes.filter(n => n.status === 'issued').length,
    totalVal: notes.filter(n => n.status === 'issued').reduce((s, n) => s + (n.total || 0), 0),
  }

  /* ── line helpers ── */
  const setLine = (i, field, val) => {
    const lines = form.lines.map((l, idx) =>
      idx === i ? { ...l, [field]: val } : l
    )
    setForm(f => ({ ...f, lines }))
  }
  const addLine    = () => setForm(f => ({ ...f, lines: [...f.lines, emptyLine()] }))
  const removeLine = (i) => setForm(f => ({ ...f, lines: f.lines.filter((_, idx) => idx !== i) }))

  /* ── openEdit ── */
  const openEdit = async (cn) => {
    try {
      const detail = await api.creditNotes.get(cn.id)
      setForm({
        customer_id:  detail.customer_id || '',
        invoice_id:   detail.invoice_id || '',
        date:         detail.date || today(),
        reason:       detail.reason || '',
        return_type:  detail.return_type || 'partial',
        tax_pct:      detail.tax_pct || 0,
        discount_amt: detail.discount_amt || 0,
        notes:        detail.notes || '',
        lines: (detail.lines || []).map(l => ({
          description: l.description,
          qty:         l.qty,
          unit:        l.unit || 'قطعة',
          unit_price:  l.unit_price,
        })),
      })
      setEditingId(cn.id)
      setShowCreate(true)
    } catch { addToast('خطأ في تحميل الإشعار', 'error') }
  }

  /* ── create / update ── */
  const handleCreate = async () => {
    if (!form.customer_id) return addToast('اختر العميل', 'error')
    if (!form.lines.length) return addToast('أضف بنداً واحداً على الأقل', 'error')
    if (form.lines.some(l => !l.description?.trim())) return addToast('أدخل وصف لكل البنود', 'error')
    setSaving(true)
    try {
      const payload = {
        customer_id:  parseInt(form.customer_id),
        invoice_id:   form.invoice_id ? parseInt(form.invoice_id) : null,
        date:         form.date,
        reason:       form.reason,
        return_type:  form.return_type,
        tax_pct:      parseFloat(form.tax_pct) || 0,
        discount_amt: parseFloat(form.discount_amt) || 0,
        notes:        form.notes,
        lines: form.lines.map((l, i) => ({
          description: l.description,
          qty:         parseFloat(l.qty) || 1,
          unit:        l.unit || 'قطعة',
          unit_price:  parseFloat(l.unit_price) || 0,
          sort_order:  i,
        })),
      }
      if (editingId) {
        await api.creditNotes.update(editingId, payload)
        addToast('تم تعديل الإشعار', 'success')
      } else {
        await api.creditNotes.create(payload)
        addToast('تم إنشاء الإشعار', 'success')
      }
      setShowCreate(false)
      setEditingId(null)
      resetForm()
      load()
    } catch (e) {
      addToast(e?.detail || 'خطأ في الحفظ', 'error')
    } finally { setSaving(false) }
  }

  const resetForm = () => setForm({
    customer_id: '', invoice_id: '', date: today(),
    reason: '', return_type: 'partial',
    tax_pct: 0, discount_amt: 0, notes: '',
    lines: [emptyLine()],
  })

  /* ── issue ── */
  const handleIssue = async (id) => {
    try {
      await api.creditNotes.issue(id)
      addToast('تم إصدار الإشعار وخصم المبلغ من رصيد العميل', 'success')
      setShowDetail(false)
      load()
    } catch (e) { addToast(e?.detail || 'خطأ', 'error') }
  }

  /* ── cancel ── */
  const handleCancel = async (id) => {
    try {
      await api.creditNotes.cancel(id)
      addToast('تم إلغاء الإشعار', 'info')
      setShowDetail(false)
      load()
    } catch (e) { addToast(e?.detail || 'خطأ', 'error') }
  }

  /* ── delete ── */
  const handleDelete = async (id) => {
    try {
      await api.creditNotes.delete(id)
      addToast('تم الحذف', 'success')
      setConfirmDel(null)
      setShowDetail(false)
      load()
    } catch (e) { addToast(e?.detail || 'خطأ في الحذف', 'error') }
  }

  /* ── from invoice ── */
  const handleFromInvoice = async (invId) => {
    if (!invId) return
    try {
      await api.creditNotes.fromInvoice(invId)
      addToast('تم إنشاء إشعار مرتجع كامل من الفاتورة', 'success')
      load()
    } catch (e) { addToast(e?.detail || 'خطأ', 'error') }
  }

  /* ── print ── */
  const printNote = (id) =>
    api.print(`/credit-notes/${id}/print`)

  /* ── open detail ── */
  const openDetail = (n) => { setSelected(n); setShowDetail(true) }

  /* ════════════════════════════════════════════════════════ */
  return (
    <div style={{ direction: 'rtl', fontFamily: 'Tajawal, sans-serif', padding: '0 4px' }}>

      {/* ── Header ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 800, color: '#0C2340', margin: 0 }}>
            📋 إشعارات دائنة / مرتجعات المبيعات
          </h1>
          <p style={{ fontSize: 12, color: '#9CA3AF', margin: '4px 0 0' }}>
            إدارة مرتجعات العملاء وإشعارات الدائن
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          style={{ background: '#A32D2D', color: '#fff', border: 'none', borderRadius: 10,
            padding: '10px 22px', fontWeight: 700, fontSize: 14, cursor: 'pointer',
            fontFamily: 'inherit' }}>
          + إشعار دائن جديد
        </button>
      </div>

      {/* ── Stats ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 20 }}>
        {[
          { label: 'إجمالي الإشعارات', val: stats.total, color: '#0C2340' },
          { label: 'مسودات', val: stats.draft, color: '#6B7280' },
          { label: 'إشعارات صادرة', val: stats.issued, color: '#15803D' },
          { label: 'قيمة الإشعارات الصادرة', val: egp(stats.totalVal), color: '#A32D2D' },
        ].map(s => (
          <div key={s.label} style={{ background: '#fff', borderRadius: 12, padding: '14px 18px',
            boxShadow: '0 1px 6px rgba(0,0,0,.06)', borderTop: `3px solid ${s.color}` }}>
            <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 4 }}>{s.label}</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: s.color }}>{s.val}</div>
          </div>
        ))}
      </div>

      {/* ── Filters ── */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <select value={filterStatus} onChange={e => setFilter(e.target.value)}
          style={{ border: '1px solid #E5E7EB', borderRadius: 8, padding: '7px 12px',
            fontSize: 13, fontFamily: 'inherit', background: '#fff' }}>
          <option value="">كل الحالات</option>
          <option value="draft">مسودة</option>
          <option value="issued">صادر</option>
          <option value="applied">مُطبَّق</option>
          <option value="cancelled">ملغي</option>
        </select>

        <select
          onChange={e => handleFromInvoice(e.target.value)}
          defaultValue=""
          style={{ border: '1px solid #E5E7EB', borderRadius: 8, padding: '7px 12px',
            fontSize: 13, fontFamily: 'inherit', background: '#fff' }}>
          <option value="">← مرتجع كامل من فاتورة</option>
          {(Array.isArray(invoices) ? invoices : []).map(inv => (
            <option key={inv.id} value={inv.id}>
              {inv.code} — {inv.customer_name}
            </option>
          ))}
        </select>
      </div>

      {/* ── Table ── */}
      <div style={{ background: '#fff', borderRadius: 14, boxShadow: '0 1px 8px rgba(0,0,0,.06)', overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: '#9CA3AF' }}>جاري التحميل…</div>
        ) : notes.length === 0 ? (
          <div style={{ padding: 60, textAlign: 'center' }}>
            <div style={{ fontSize: 42, marginBottom: 12 }}>📋</div>
            <div style={{ fontSize: 15, fontWeight: 600, color: '#374151' }}>لا توجد إشعارات دائنة</div>
            <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 4 }}>أنشئ إشعاراً دائناً جديداً أو ارجع من فاتورة</div>
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#0C2340', color: '#fff' }}>
                {['رقم الإشعار', 'العميل', 'الفاتورة المرجعية', 'التاريخ', 'نوع المرتجع', 'الإجمالي', 'الحالة', ''].map(h => (
                  <th key={h} style={{ padding: '11px 14px', fontSize: 12, fontWeight: 700,
                    textAlign: 'right', whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {notes.map((n, i) => (
                <tr key={n.id}
                  onClick={() => openDetail(n)}
                  style={{ background: i % 2 === 0 ? '#FAFAFA' : '#fff',
                    cursor: 'pointer', transition: 'background .15s' }}
                  onMouseEnter={e => e.currentTarget.style.background = '#FFF0F0'}
                  onMouseLeave={e => e.currentTarget.style.background = i % 2 === 0 ? '#FAFAFA' : '#fff'}>
                  <td style={{ padding: '10px 14px', fontWeight: 700, color: '#A32D2D', fontSize: 13 }}>{n.code}</td>
                  <td style={{ padding: '10px 14px', fontSize: 13 }}>{n.customer_name}</td>
                  <td style={{ padding: '10px 14px', fontSize: 12, color: '#6B7280' }}>{n.invoice_code || '—'}</td>
                  <td style={{ padding: '10px 14px', fontSize: 12 }}>{n.date}</td>
                  <td style={{ padding: '10px 14px', fontSize: 12 }}>
                    {n.return_type === 'full' ? '🔴 كامل' : '🟡 جزئي'}
                  </td>
                  <td style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13 }}>{egp(n.total)}</td>
                  <td style={{ padding: '10px 14px' }}><StatusPill status={n.status} /></td>
                  <td style={{ padding: '10px 14px' }} onClick={e => e.stopPropagation()}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button onClick={() => printNote(n.id)}
                        title="طباعة"
                        style={{ background: '#F3F4F6', border: 'none', borderRadius: 6,
                          padding: '5px 10px', cursor: 'pointer', fontSize: 14 }}>🖨️</button>
                      {n.status === 'draft' && (
                        <button onClick={() => openEdit(n)}
                          title="تعديل"
                          style={{ background: '#DBEAFE', border: 'none', borderRadius: 6,
                            padding: '5px 10px', cursor: 'pointer', fontSize: 14 }}>✏️</button>
                      )}
                      {n.status === 'draft' && (
                        <button onClick={() => setConfirmDel(n.id)}
                          title="حذف"
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

      {/* ═══ Detail Modal ═══ */}
      {showDetail && selected && (
        <Modal title={`إشعار دائن — ${selected.code}`} onClose={() => setShowDetail(false)} size="lg">
          {/* Info Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 20 }}>
            {[
              { label: 'رقم الإشعار', val: selected.code },
              { label: 'العميل', val: selected.customer_name },
              { label: 'الفاتورة المرجعية', val: selected.invoice_code || '—' },
              { label: 'التاريخ', val: selected.date },
              { label: 'نوع المرتجع', val: selected.return_type === 'full' ? 'مرتجع كامل' : 'مرتجع جزئي' },
              { label: 'الحالة', val: <StatusPill status={selected.status} /> },
              { label: 'سبب الإشعار', val: selected.reason || '—' },
              { label: 'ضريبة', val: `${selected.tax_pct}%` },
              { label: 'خصم', val: egp(selected.discount_amt) },
            ].map(it => (
              <div key={it.label} style={{ background: '#F9FAFB', borderRadius: 8, padding: '10px 14px' }}>
                <div style={{ fontSize: 10, color: '#9CA3AF', marginBottom: 3 }}>{it.label}</div>
                <div style={{ fontSize: 13, fontWeight: 700 }}>{it.val}</div>
              </div>
            ))}
          </div>

          {/* Lines Table */}
          {selected.lines?.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: '#6B7280', marginBottom: 8 }}>بنود الإشعار</div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ background: '#0C2340', color: '#fff' }}>
                    {['#', 'الوصف', 'الكمية', 'الوحدة', 'سعر الوحدة', 'الإجمالي'].map(h => (
                      <th key={h} style={{ padding: '8px 10px', textAlign: 'right', fontSize: 11 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {selected.lines.map((l, i) => (
                    <tr key={l.id} style={{ background: i % 2 === 0 ? '#FAFAFA' : '#fff' }}>
                      <td style={{ padding: '7px 10px', color: '#9CA3AF' }}>{i + 1}</td>
                      <td style={{ padding: '7px 10px', fontWeight: 600 }}>{l.description}</td>
                      <td style={{ padding: '7px 10px' }}>{l.qty}</td>
                      <td style={{ padding: '7px 10px', color: '#6B7280' }}>{l.unit}</td>
                      <td style={{ padding: '7px 10px' }}>{egp(l.unit_price)}</td>
                      <td style={{ padding: '7px 10px', fontWeight: 700, color: '#A32D2D' }}>{egp(l.total_price)}</td>
                    </tr>
                  ))}
                  {/* Grand total */}
                  <tr style={{ background: 'linear-gradient(90deg,#5c0a0a,#A32D2D)', color: '#fff' }}>
                    <td colSpan={5} style={{ padding: '10px 12px', fontWeight: 700, fontSize: 13 }}>
                      إجمالي الإشعار
                    </td>
                    <td style={{ padding: '10px 12px', fontWeight: 900, fontSize: 15 }}>
                      {egp(selected.total)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}

          {selected.notes && (
            <div style={{ background: '#FFF7F0', border: '1px solid #FED7AA', borderRadius: 8,
              padding: '10px 14px', fontSize: 13, marginBottom: 16 }}>
              <b>ملاحظات:</b> {selected.notes}
            </div>
          )}

          <ModalFooter>
            <button onClick={() => printNote(selected.id)}
              style={{ background: '#0C2340', color: '#fff', border: 'none', borderRadius: 8,
                padding: '9px 20px', fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit' }}>
              🖨️ طباعة
            </button>
            {selected.status === 'draft' && (
              <button onClick={() => handleIssue(selected.id)}
                style={{ background: '#15803D', color: '#fff', border: 'none', borderRadius: 8,
                  padding: '9px 20px', fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit' }}>
                ✅ إصدار الإشعار
              </button>
            )}
            {selected.status === 'draft' && (
              <button onClick={() => setConfirmDel(selected.id)}
                style={{ background: '#FEE2E2', color: '#991B1B', border: 'none', borderRadius: 8,
                  padding: '9px 20px', fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit' }}>
                🗑️ حذف
              </button>
            )}
            {selected.status === 'issued' && (
              <button onClick={() => handleCancel(selected.id)}
                style={{ background: '#FEF3C7', color: '#92400E', border: 'none', borderRadius: 8,
                  padding: '9px 20px', fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit' }}>
                ❌ إلغاء
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

      {/* ═══ Create Modal ═══ */}
      {showCreate && (
        <Modal title={editingId ? 'تعديل إشعار دائن' : 'إشعار دائن جديد'} onClose={() => { setShowCreate(false); setEditingId(null); resetForm() }} size="xl">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 16 }}>
            {/* Customer */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>
                العميل *
              </label>
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
            {/* Invoice */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>
                الفاتورة المرجعية (اختياري)
              </label>
              <select value={form.invoice_id}
                onChange={e => setForm(f => ({ ...f, invoice_id: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}>
                <option value="">بدون فاتورة</option>
                {(Array.isArray(invoices) ? invoices : []).map(inv => (
                  <option key={inv.id} value={inv.id}>{inv.code} — {inv.customer_name}</option>
                ))}
              </select>
            </div>
            {/* Date */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>التاريخ *</label>
              <input type="date" value={form.date}
                onChange={e => setForm(f => ({ ...f, date: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
            </div>
            {/* Return type */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>نوع المرتجع</label>
              <select value={form.return_type}
                onChange={e => setForm(f => ({ ...f, return_type: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}>
                <option value="partial">مرتجع جزئي</option>
                <option value="full">مرتجع كامل</option>
              </select>
            </div>
            {/* Reason */}
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>سبب الإشعار</label>
              <input type="text" value={form.reason}
                onChange={e => setForm(f => ({ ...f, reason: e.target.value }))}
                placeholder="مثال: بضاعة تالفة، خطأ في الفاتورة…"
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
            </div>
            {/* Tax + Discount */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>ضريبة %</label>
              <input type="number" min="0" max="100" step="0.5" value={form.tax_pct}
                onChange={e => setForm(f => ({ ...f, tax_pct: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>خصم</label>
              <input type="number" min="0" step="0.01" value={form.discount_amt}
                onChange={e => setForm(f => ({ ...f, discount_amt: e.target.value }))}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit' }}/>
            </div>
            {/* Notes */}
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={{ fontSize: 12, fontWeight: 700, color: '#374151', display: 'block', marginBottom: 5 }}>ملاحظات</label>
              <textarea value={form.notes}
                onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                rows={2}
                style={{ width: '100%', border: '1px solid #D1D5DB', borderRadius: 8,
                  padding: '8px 12px', fontSize: 13, fontFamily: 'inherit', resize: 'vertical' }}/>
            </div>
          </div>

          {/* Lines */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: '#374151' }}>بنود الإشعار</span>
              <button onClick={addLine}
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
                {form.lines.map((l, i) => {
                  const lTotal = (parseFloat(l.qty) || 0) * (parseFloat(l.unit_price) || 0)
                  return (
                    <tr key={i} style={{ borderBottom: '1px solid #F3F4F6' }}>
                      <td style={{ padding: '5px 6px' }}>
                        <input value={l.description}
                          onChange={e => setLine(i, 'description', e.target.value)}
                          placeholder="وصف الصنف"
                          style={{ width: '100%', border: '1px solid #E5E7EB', borderRadius: 6,
                            padding: '6px 8px', fontSize: 12, fontFamily: 'inherit' }}/>
                      </td>
                      <td style={{ padding: '5px 6px', width: 80 }}>
                        <input type="number" min="0" step="0.01" value={l.qty}
                          onChange={e => setLine(i, 'qty', e.target.value)}
                          style={{ width: '100%', border: '1px solid #E5E7EB', borderRadius: 6,
                            padding: '6px 8px', fontSize: 12, fontFamily: 'inherit' }}/>
                      </td>
                      <td style={{ padding: '5px 6px', width: 90 }}>
                        <input value={l.unit}
                          onChange={e => setLine(i, 'unit', e.target.value)}
                          style={{ width: '100%', border: '1px solid #E5E7EB', borderRadius: 6,
                            padding: '6px 8px', fontSize: 12, fontFamily: 'inherit' }}/>
                      </td>
                      <td style={{ padding: '5px 6px', width: 110 }}>
                        <input type="number" min="0" step="0.01" value={l.unit_price}
                          onChange={e => setLine(i, 'unit_price', e.target.value)}
                          style={{ width: '100%', border: '1px solid #E5E7EB', borderRadius: 6,
                            padding: '6px 8px', fontSize: 12, fontFamily: 'inherit' }}/>
                      </td>
                      <td style={{ padding: '5px 6px', fontWeight: 700, color: '#A32D2D', width: 100, textAlign: 'left' }}>
                        {lTotal.toLocaleString('ar-EG', { minimumFractionDigits: 2 })}
                      </td>
                      <td style={{ padding: '5px 6px', width: 36 }}>
                        {form.lines.length > 1 && (
                          <button onClick={() => removeLine(i)}
                            style={{ background: '#FEE2E2', border: 'none', borderRadius: 6,
                              padding: '4px 8px', cursor: 'pointer', fontSize: 13 }}>✕</button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            {/* Running total */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
              <div style={{ background: '#A32D2D', color: '#fff', borderRadius: 10,
                padding: '10px 20px', minWidth: 220 }}>
                <div style={{ fontSize: 11, opacity: .75, marginBottom: 2 }}>إجمالي الإشعار</div>
                <div style={{ fontSize: 20, fontWeight: 900 }}>{egp(formTotal)}</div>
              </div>
            </div>
          </div>

          <ModalFooter>
            <button onClick={handleCreate} disabled={saving}
              style={{ background: '#A32D2D', color: '#fff', border: 'none', borderRadius: 8,
                padding: '9px 24px', fontWeight: 700, cursor: saving ? 'not-allowed' : 'pointer',
                opacity: saving ? .7 : 1, fontFamily: 'inherit' }}>
              {saving ? 'جاري الحفظ…' : '💾 حفظ الإشعار'}
            </button>
            <button onClick={() => { setShowCreate(false); setEditingId(null); resetForm() }}
              style={{ background: '#F3F4F6', color: '#374151', border: 'none', borderRadius: 8,
                padding: '9px 20px', fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
              إلغاء
            </button>
          </ModalFooter>
        </Modal>
      )}

      {/* ═══ Confirm Delete ═══ */}
      <ConfirmDialog
        open={!!confirmDel}
        title="حذف الإشعار"
        message="هل أنت متأكد من حذف هذا الإشعار؟ لا يمكن التراجع."
        confirmLabel="حذف"
        confirmVariant="danger"
        onConfirm={() => handleDelete(confirmDel)}
        onCancel={() => setConfirmDel(null)}
      />
    </div>
  )
}
