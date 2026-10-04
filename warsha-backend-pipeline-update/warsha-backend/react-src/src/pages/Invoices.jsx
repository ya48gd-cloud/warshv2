/**
 * Invoices — الفواتير
 * Matches original UI: form with line items (الوصف/الكمية/الوحدة/سعر الوحدة/الإجمالي)
 * + Totals section (إجمالي / الإجمالي النهائي after tax/discount)
 * List view + detail/create view
 */
import { useState, useMemo, useEffect } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import SearchInput from '../components/ui/SearchInput'
import { PageSpinner } from '../components/ui/Spinner'
import CatalogPickerModal from '../components/ui/CatalogPicker'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import ActionMenu from '../components/ui/ActionMenu'

const UNITS = ['قطعة', 'كجم', 'طن', 'متر', 'م²', 'م³', 'لتر', 'ساعة', 'رحلة', 'مجموعة']

// ── Invoice Form (create/edit with line items) ─────────────────
function InvoiceForm({ customers, onSave, onCancel, initial }) {
  const toast = useToast()
  const [form, setForm] = useState({
    code:         initial?.code || '',
    // سيتعبّى تلقائياً من السيرفر إذا إنشاء جديد
    customer_id:  initial?.customer_id || '',
    location_id:  initial?.location_id || '',
    date:         initial?.date || today(),       // fixed: was issue_date
    due_date:     initial?.due_date || '',
    tax_pct:      initial?.tax_pct ?? 0,
    discount_pct: initial?.discount_pct ?? 0,
    terms:        initial?.terms || '',
    notes:        initial?.notes || '',
  })
  const [lines, setLines] = useState(
    initial?.lines?.length
      ? initial.lines.map(l => ({ ...l, _id: l.id ?? Date.now() + Math.random(), qty: l.qty ?? l.quantity ?? 1 }))
      : [{ _id: Date.now(), item_type: '', model_no: '', description: '', qty: 1, unit: 'قطعة', unit_price: 0, notes: '' }]
  )
  const [saving, setSaving] = useState(false)
  const [catalogOpen, setCatalogOpen] = useState(false)

  // توليد كود الفاتورة تلقائياً عند الإنشاء
  useEffect(() => {
    if (!initial) {
      api.sales.nextInvoiceCode().then(r => setForm(f => ({ ...f, code: r.code }))).catch(bgErr('كود الفاتورة التالي', () => {}))
    }
  }, [])

  // مواقع العميل المختار
  const [locations, setLocations] = useState([])
  useEffect(() => {
    if (!form.customer_id) { setLocations([]); return }
    api.customers.locations(form.customer_id).then(r => setLocations(Array.isArray(r) ? r : [])).catch(bgErr('عناوين العميل', () => setLocations([])))
  }, [form.customer_id])

  const handleCatalogSelect = (item) => {
    setLines(ls => [...ls, {
      _id:         Date.now() + Math.random(),
      item_type:   item.item_type || '',
      model_no:    item.model_no  || '',
      description: item.description || item.name,
      qty:         1,
      unit:        item.unit || 'قطعة',
      unit_price:  item.unit_price,
      notes:       `تكلفة: ${item.cost_price} | هامش: ${item.margin_pct}%`,
    }])
  }

  const addLine = () => setLines(ls => [
    ...ls,
    { _id: Date.now() + Math.random(), item_type: '', model_no: '', description: '', qty: 1, unit: 'قطعة', unit_price: 0, notes: '' }
  ])
  const removeLine = (_id) => setLines(ls => ls.filter(l => l._id !== _id))
  const updateLine = (_id, field, value) =>
    setLines(ls => ls.map(l => l._id === _id ? { ...l, [field]: value } : l))

  // ── Totals (same formula as backend: discount first, then tax on discounted) ──
  const subtotal  = lines.reduce((s, l) => s + (Number(l.qty) || 0) * (Number(l.unit_price) || 0), 0)
  const discAmt   = subtotal * (Number(form.discount_pct) || 0) / 100
  const taxable   = subtotal - discAmt
  const taxAmt    = taxable * (Number(form.tax_pct) || 0) / 100
  const total     = taxable + taxAmt

  const handleSave = async () => {
    if (!form.customer_id) { toast('اختر العميل', 'error'); return }
    if (!lines.length) { toast('أضف بنداً واحداً على الأقل', 'error'); return }
    if (lines.some(l => !l.description?.trim())) { toast('أدخل وصف لكل البنود', 'error'); return }
    if (form.date && form.due_date && form.due_date < form.date) {
      toast('تاريخ الاستحقاق لازم يكون بعد تاريخ الفاتورة', 'error'); return
    }
    // نفس قواعد الباك إند (_validate_doc_lines): كمية موجبة، سعر غير سالب، نسب 0-100
    if (lines.some(l => !(Number(l.qty) > 0))) { toast('كمية كل بند يجب أن تكون أكبر من صفر', 'error'); return }
    if (lines.some(l => l.unit_price === '' || l.unit_price == null || isNaN(Number(l.unit_price)) || Number(l.unit_price) < 0)) {
      toast('سعر الوحدة لازم يكون رقم صفر أو أكثر لكل البنود', 'error'); return
    }
    for (const [name, v] of [['الضريبة', form.tax_pct], ['الخصم', form.discount_pct]]) {
      const n = Number(v) || 0
      if (n < 0 || n > 100) { toast(`نسبة ${name} يجب أن تكون بين 0 و100`, 'error'); return }
    }
    if (initial && Number(initial.paid_amount || 0) > 0 && total + 0.005 < Number(initial.paid_amount)) {
      toast(`الإجمالي الجديد (${egp(total)}) أقل من المبلغ المسدد على الفاتورة (${egp(initial.paid_amount)})`, 'error'); return
    }
    if (saving) return
    setSaving(true)
    try {
      await onSave({
        code:         form.code,
        customer_id:  Number(form.customer_id),
        location_id:  form.location_id ? Number(form.location_id) : null,
        // الحفاظ على روابط الفاتورة الأصلية عند التعديل — الباك إند بيكتب
        // quotation_id من الطلب مباشرة، فإرسالها فاضية كان بيقطع ربط عرض السعر
        ...(initial ? {
          quotation_id:  initial.quotation_id ?? null,
          work_order_id: initial.work_order_id ?? null,
        } : {}),
        date:         form.date || today(),        // fixed: was issue_date spread via ...form
        due_date:     form.due_date || null,
        tax_pct:      Number(form.tax_pct) || 0,
        discount_pct: Number(form.discount_pct) || 0,
        notes:        form.notes || null,
        terms:        form.terms || null,
        lines: lines.map((l, i) => ({
          item_type:   l.item_type || null,
          model_no:    l.model_no || null,
          description: l.description,
          qty:         Number(l.qty) || 1,         // fixed: was "quantity"
          unit:        l.unit || 'قطعة',
          unit_price:  Number(l.unit_price) || 0,
          notes:       l.notes || null,
          sort_order:  i,
          // الحفاظ على صورة البند (لو موجودة) عند التعديل بدل ما تتمسح
          ...(l.image_data ? { image_data: l.image_data } : {}),
        })),
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {/* Invoice data */}
      <div className="card">
        <div className="text-sm font-semibold mb-4">بيانات الفاتورة</div>
        <div className="grid gap-3" style={{ gridTemplateColumns: '1fr 1fr' }}>
          <Input label="الكود *" value={form.code}
            onChange={e => setForm(f => ({ ...f, code: e.target.value }))}
            placeholder="INV-2024-001" />
          <Select label="العميل *" value={form.customer_id}
            onChange={e => setForm(f => ({ ...f, customer_id: e.target.value, location_id: '' }))}>
            <option value="">— اختر عميل —</option>
            {customers?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
          {locations.length > 0 && (
            <Select label="الموقع / الفرع" value={form.location_id}
              onChange={e => setForm(f => ({ ...f, location_id: e.target.value }))}>
              <option value="">— بدون موقع محدد —</option>
              {locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            </Select>
          )}
          <Input label="التاريخ *" type="date" value={form.date}
            onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
          <Input label="تاريخ الاستحقاق" type="date" value={form.due_date}
            onChange={e => setForm(f => ({ ...f, due_date: e.target.value }))} />
          <Input label="نسبة الضريبة (%)" type="number" step="0.5" min="0" max="100"
            value={form.tax_pct}
            onChange={e => setForm(f => ({ ...f, tax_pct: e.target.value }))} />
          <Input label="نسبة الخصم (%)" type="number" step="0.5" min="0" max="100"
            value={form.discount_pct}
            onChange={e => setForm(f => ({ ...f, discount_pct: e.target.value }))} />
        </div>
        <div className="mt-3">
          <Textarea label="شروط الدفع" rows={1} value={form.terms}
            onChange={e => setForm(f => ({ ...f, terms: e.target.value }))} />
        </div>
        <div className="mt-3">
          <Textarea label="ملاحظات" rows={2} value={form.notes}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
        </div>
      </div>

      {/* Line items */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm font-semibold">البنود</div>
          <div className="flex gap-2">
            <button className="btn btn-sm" onClick={() => setCatalogOpen(true)}>🔍 من الكتالوج</button>
            <button className="btn btn-primary btn-sm" onClick={addLine}>+ إضافة بند</button>
          </div>
        </div>

        {/* Lines header */}
        <div className="grid text-xs font-semibold text-erp-muted mb-2"
             style={{ gridTemplateColumns: '80px 90px 2fr 70px 80px 110px 1.2fr auto', gap: '6px' }}>
          <div>النوع</div>
          <div>الموديل</div>
          <div>الوصف *</div>
          <div className="text-center">الكمية</div>
          <div className="text-center">الوحدة</div>
          <div className="text-center">سعر الوحدة</div>
          <div className="text-center">الإجمالي</div>
          <div />
        </div>

        {!lines.length && (
          <div className="text-center py-6 text-erp-muted text-sm border-2 border-dashed border-erp-border rounded-erp">
            لا توجد بنود — اضغط "إضافة بند"
          </div>
        )}

        <div className="flex flex-col gap-2">
          {lines.map((l, i) => {
            const lineTotal = (Number(l.qty) || 0) * (Number(l.unit_price) || 0)
            return (
              <div key={l._id} className="grid items-center gap-1"
                   style={{ gridTemplateColumns: '80px 90px 2fr 70px 80px 110px 1.2fr auto', gap: '6px' }}>
                <input className="field-input text-sm" placeholder="النوع"
                  value={l.item_type || ''}
                  onChange={e => updateLine(l._id, 'item_type', e.target.value)} />
                <input className="field-input text-sm" placeholder="الموديل"
                  value={l.model_no || ''}
                  onChange={e => updateLine(l._id, 'model_no', e.target.value)} />
                <input className="field-input text-sm"
                  placeholder={`المواصفات ${i + 1}`}
                  value={l.description || ''}
                  onChange={e => updateLine(l._id, 'description', e.target.value)} />
                <input className="field-input text-sm text-center"
                  type="number" step="0.001" min="0"
                  value={l.qty}
                  onChange={e => updateLine(l._id, 'qty', e.target.value)} />
                <select className="field-select text-sm"
                  value={l.unit}
                  onChange={e => updateLine(l._id, 'unit', e.target.value)}>
                  {UNITS.map(u => <option key={u}>{u}</option>)}
                </select>
                <input className="field-input text-sm text-center"
                  type="number" step="0.01" min="0"
                  value={l.unit_price}
                  onChange={e => updateLine(l._id, 'unit_price', e.target.value)} />
                <div className="text-sm font-semibold text-right" style={{ color: 'var(--teal)' }}>
                  {egp(lineTotal)}
                </div>
                <button className="btn btn-sm btn-danger text-xs"
                  onClick={() => removeLine(l._id)}
                  disabled={lines.length === 1}>✕</button>
              </div>
            )
          })}
        </div>
      </div>

      {/* Totals — matches backend formula */}
      {lines.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-3">الإجماليات</div>
          <div className="flex flex-col gap-2 max-w-xs mr-auto">
            <div className="flex items-center justify-between text-sm">
              <span className="text-erp-muted">الإجمالي الفرعي</span>
              <span className="font-medium">{egp(subtotal)}</span>
            </div>
            {Number(form.discount_pct) > 0 && (
              <div className="flex items-center justify-between text-sm" style={{ color: 'var(--red)' }}>
                <span>خصم ({form.discount_pct}%)</span>
                <span>− {egp(discAmt)}</span>
              </div>
            )}
            {Number(form.tax_pct) > 0 && (
              <div className="flex items-center justify-between text-sm text-erp-muted">
                <span>ضريبة ({form.tax_pct}%) على {egp(taxable)}</span>
                <span>+ {egp(taxAmt)}</span>
              </div>
            )}
            <div className="border-t border-erp-border pt-2 flex items-center justify-between">
              <span className="font-bold" style={{ color: 'var(--teal)' }}>الإجمالي النهائي</span>
              <span className="text-xl font-bold" style={{ color: 'var(--teal)' }}>{egp(total)}</span>
            </div>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3">
        <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
          💾 {saving ? '…' : 'حفظ الفاتورة'}
        </button>
        <button className="btn" onClick={onCancel}>إلغاء</button>
      </div>

      <CatalogPickerModal
        open={catalogOpen}
        onClose={() => setCatalogOpen(false)}
        onSelect={handleCatalogSelect}
      />
    </div>
  )
}

// ── Invoices list ──────────────────────────────────────────────
export default function Invoices({ highlightId = null } = {}) {
  const { canWrite } = useAuth()
  const write = canWrite('sales')
  const toast = useToast()

  const { data: invoices, loading, reload } = useApi(() => api.sales.invoices())
  const { data: customers } = useApi(() => api.customers.list())

  const [q, setQ] = useState('')
  const [view, setView] = useState('list')   // 'list' | 'create' | 'edit'
  const [editingInvoice, setEditingInvoice] = useState(null)
  const [copyingId, setCopyingId] = useState(null)
  const [payModal, setPayModal] = useState(null)
  const [payForm, setPayForm] = useState({ amount: '', payment_date: today(), payment_type: 'cash', reference: '' })
  const [confirmDelete, setConfirmDelete] = useState(null)

  // محطة 4: جدولة أقساط الفاتورة
  const [scheduleModal, setScheduleModal] = useState(null)      // الفاتورة المفتوحة عليها المودال
  const [scheduleExisting, setScheduleExisting] = useState(null) // الخطة الحالية (لو موجودة) — null = بيتحمّل، [] = مفيش
  const [scheduleRows, setScheduleRows] = useState([])           // صفوف التعديل/الإنشاء
  const [scheduleMode, setScheduleMode] = useState('view')       // 'view' | 'edit'

  // لما نيجي من شاشة الأقساط على فاتورة معينة، نفلتر القائمة عليها مباشرة
  useEffect(() => {
    if (!highlightId || !Array.isArray(invoices)) return
    const target = invoices.find(i => String(i.id) === String(highlightId))
    if (target?.code) setQ(target.code)
  }, [highlightId, invoices])

  const filtered = useMemo(() => {
    if (!invoices) return []
    const lq = q.toLowerCase()
    return !lq ? invoices : invoices.filter(inv =>
      inv.code?.toLowerCase().includes(lq) ||
      inv.customer_name?.toLowerCase().includes(lq) ||
      inv.customer?.name?.toLowerCase().includes(lq)
    )
  }, [invoices, q])

  const totals = useMemo(() => ({
    total:       (invoices || []).reduce((s, i) => s + Number(i.total || 0), 0),
    paid:        (invoices || []).reduce((s, i) => s + Number(i.paid_amount || 0), 0),
    outstanding: (invoices || []).reduce((s, i) => s + Number(i.total || 0) - Number(i.paid_amount || 0), 0),
  }), [invoices])

  const handleCreate = async (data) => {
    try {
      await api.sales.createInvoice(data)
      toast('تم حفظ الفاتورة')
      setView('list')
      reload()
    } catch (e) {
      toast(e.message || 'تعذّر حفظ الفاتورة', 'error')
    }
  }

  const handleUpdate = async (data) => {
    try {
      await api.sales.updateInvoice(editingInvoice.id, data)
      toast('تم تحديث الفاتورة')
      setEditingInvoice(null)
      setView('list')
      reload()
    } catch (e) {
      toast(e.message || 'تعذّر تحديث الفاتورة', 'error')
    }
  }

  const [handleDelete, deleting] = useAction(async (id) => {
    await api.sales.deleteInvoice(id)
    toast('تم حذف الفاتورة')
    reload()
  })

  const copyInvoice = async (inv) => {
    if (copyingId) return
    setCopyingId(inv.id)
    try {
      await api.sales.createInvoice({
        code:         '',
        customer_id:  inv.customer_id,
        date:         today(),
        tax_pct:      inv.tax_pct || 0,
        discount_pct: inv.discount_pct || 0,
        notes:        `نسخة من: ${inv.code}`,
        lines: (inv.lines || []).map(l => ({
          description: l.description,
          qty:         l.qty,
          unit:        l.unit,
          unit_price:  l.unit_price,
          item_type:   l.item_type   || '',
          model_no:    l.model_no    || '',
          notes:       l.notes       || '',
          sort_order:  l.sort_order  || 0,
        })),
      })
      toast('✅ تم نسخ الفاتورة — تحقق من القائمة')
      reload()
    } catch (e) { toast(e.message || 'فشل النسخ', 'error') }
    finally { setCopyingId(null) }
  }

  const [recordPay, paying] = useAction(async () => {
    if (!payForm.amount || !(Number(payForm.amount) > 0)) { toast('أدخل مبلغ صحيح أكبر من صفر', 'error'); return }
    const outstanding = Number(payModal?.total || 0) - Number(payModal?.paid_amount || 0)
    if (Number(payForm.amount) > outstanding + 0.01) {
      toast(`المبلغ (${egp(Number(payForm.amount))}) يتجاوز الرصيد المستحق (${egp(outstanding)})`, 'error'); return
    }
    const res = await api.sales.recordPayment(payModal.id, {
      amount:        Number(payForm.amount),
      payment_date:  payForm.payment_date || today(),
      payment_type:  payForm.payment_type || 'cash',
      reference:     payForm.reference || null,
    })
    const touched = res?.installments_updated || []
    if (touched.length > 0) {
      const sm = { paid: 'مدفوع', partial: 'جزئي', pending: 'لم يستحق', overdue: 'متأخر' }
      const summary = touched.map(t => `${t.label || ('قسط ' + t.installment_no)} (${sm[t.status] || t.status})`).join('، ')
      toast(`تم تسجيل الدفع — تم توزيعه تلقائيًا على: ${summary}`)
    } else {
      toast('تم تسجيل الدفع')
    }
    setPayModal(null)
    setPayForm({ amount: '', payment_date: today(), payment_type: 'cash', reference: '' })
    reload()
  })

  const openScheduleModal = async (inv) => {
    setScheduleModal(inv)
    setScheduleExisting(null)
    setScheduleMode('view')
    try {
      const rows = await api.sales.getPaymentSchedule(inv.id)
      setScheduleExisting(rows || [])
      if (!rows || rows.length === 0) {
        setScheduleMode('edit')
        setScheduleRows([{ label: 'دفعة 1', due_date: today(), amount: String(inv.total || '') }])
      }
    } catch (e) {
      toast(e.message || 'تعذّر تحميل خطة الأقساط', 'error')
      setScheduleExisting([])
      setScheduleMode('edit')
      setScheduleRows([{ label: 'دفعة 1', due_date: today(), amount: String(inv.total || '') }])
    }
  }

  const startEditSchedule = () => {
    const base = (scheduleExisting && scheduleExisting.length)
      ? scheduleExisting.map(r => ({ label: r.label || '', due_date: r.due_date, amount: String(r.amount_due) }))
      : [{ label: 'دفعة 1', due_date: today(), amount: String(scheduleModal?.total || '') }]
    setScheduleRows(base)
    setScheduleMode('edit')
  }

  const addScheduleRow = () => setScheduleRows(rs => [...rs, { label: `دفعة ${rs.length + 1}`, due_date: today(), amount: '' }])
  const removeScheduleRow = (idx) => setScheduleRows(rs => rs.filter((_, i) => i !== idx))
  const updateScheduleRow = (idx, field, value) =>
    setScheduleRows(rs => rs.map((r, i) => i === idx ? { ...r, [field]: value } : r))

  const scheduleRowsTotal = useMemo(
    () => scheduleRows.reduce((s, r) => s + (Number(r.amount) || 0), 0),
    [scheduleRows]
  )

  const [saveSchedule, savingSchedule] = useAction(async () => {
    if (scheduleRows.length === 0) { toast('أضف قسط واحد على الأقل', 'error'); return }
    if (scheduleRows.some(r => !r.due_date)) { toast('حدد تاريخ استحقاق لكل قسط', 'error'); return }
    if (scheduleRows.some(r => !(Number(r.amount) > 0))) { toast('مبلغ كل قسط لازم يكون أكبر من صفر', 'error'); return }
    const invTotal = Number(scheduleModal?.total || 0)
    if (Math.abs(scheduleRowsTotal - invTotal) > 0.02) {
      toast(`مجموع الأقساط (${egp(scheduleRowsTotal)}) لازم يساوي إجمالي الفاتورة (${egp(invTotal)})`, 'error')
      return
    }
    const hasExisting = scheduleExisting && scheduleExisting.length > 0
    await api.sales.createPaymentSchedule(scheduleModal.id, {
      installments: scheduleRows.map(r => ({
        label: r.label || null,
        due_date: r.due_date,
        amount: Number(r.amount),
      })),
    }, hasExisting)
    toast('تم حفظ خطة الأقساط')
    const rows = await api.sales.getPaymentSchedule(scheduleModal.id)
    setScheduleExisting(rows || [])
    setScheduleMode('view')
  })

  if (loading) return <PageSpinner />

  if (view === 'create') {
    return (
      <InvoiceForm
        customers={customers}
        onSave={handleCreate}
        onCancel={() => setView('list')}
      />
    )
  }

  if (view === 'edit' && editingInvoice) {
    return (
      <InvoiceForm
        customers={customers}
        initial={editingInvoice}
        onSave={handleUpdate}
        onCancel={() => { setEditingInvoice(null); setView('list') }}
      />
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* KPI row */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'إجمالي الفواتير',  val: egp(totals.total),       color: 'var(--blue)'  },
          { label: 'المحصّل',           val: egp(totals.paid),        color: 'var(--green)' },
          { label: 'المستحق للتحصيل',  val: egp(totals.outstanding), color: 'var(--red)'   },
        ].map(k => (
          <div key={k.label} className="card text-center py-3">
            <div className="text-xs text-erp-muted mb-1">{k.label}</div>
            <div className="text-lg font-bold" style={{ color: k.color }}>{k.val}</div>
          </div>
        ))}
      </div>

      <div className="card flex items-center gap-3 py-3">
        <div className="flex-1">
          <SearchInput value={q} onChange={setQ} placeholder="بحث برقم الفاتورة أو العميل…" />
        </div>
        <button className="btn btn-sm text-xs" title="تصدير Excel"
          onClick={() => api.export.invoices()}>📥 Excel</button>
        {write && (
          <button className="btn btn-primary btn-sm" onClick={() => setView('create')}>
            + فاتورة جديدة
          </button>
        )}
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr>
              <th>الكود</th>
              <th>العميل</th>
              <th>التاريخ</th>
              <th>الاستحقاق</th>
              <th>الإجمالي</th>
              <th>المدفوع</th>
              <th>الحالة</th>
              <th style={{ width: 110 }}></th>
            </tr>
          </thead>
          <tbody>
            {!filtered.length && (
              <tr><td colSpan={8} className="text-center text-erp-muted py-10">لا توجد فواتير</td></tr>
            )}
            {filtered.map(inv => {
              const statusMap = {
                draft:   { label: 'مسودة',      cls: 'badge-gray'  },
                unpaid:  { label: 'غير مدفوعة', cls: 'badge-red'   },
                partial: { label: 'جزئي',        cls: 'badge-amber' },
                paid:    { label: 'مدفوعة',      cls: 'badge-green' },
                overdue: { label: 'متأخرة',      cls: 'badge-red'   },
                cancelled: { label: 'ملغية',     cls: 'badge-gray'  },
              }
              const st = statusMap[inv.status] || { label: inv.status, cls: 'badge-gray' }
              const outstanding = Number(inv.total || 0) - Number(inv.paid_amount || 0)
              return (
                <tr key={inv.id}>
                  <td className="font-mono text-xs font-medium">{inv.code}</td>
                  <td className="font-medium">{inv.customer_name || inv.customer?.name || '—'}</td>
                  <td className="text-xs text-erp-muted">{fmtDate(inv.date)}</td>
                  <td className="text-xs" style={{ color: inv.status === 'overdue' ? 'var(--red)' : undefined }}>
                    {fmtDate(inv.due_date)}
                  </td>
                  <td className="font-semibold">{egp(inv.total)}</td>
                  <td style={{ color: 'var(--green)' }}>{egp(inv.paid_amount || 0)}</td>
                  <td><span className={`badge ${st.cls}`}>{st.label}</span></td>
                  <td>
                    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                      {write && inv.status !== 'paid' && outstanding > 0 && (
                        <button className="btn btn-sm text-xs"
                          style={{ color: 'var(--green)', borderColor: 'var(--green-lt)' }}
                          onClick={() => {
                            setPayModal(inv)
                            setPayForm({ amount: String(outstanding.toFixed(2)), payment_date: today(), payment_type: 'cash', reference: '' })
                          }}>
                          💵 دفع
                        </button>
                      )}
                      <button className="btn btn-sm text-xs" title={inv.has_payment_schedule ? 'عندها خطة أقساط' : 'جدولة الأقساط'}
                        style={{
                          color: 'var(--teal)',
                          borderColor: inv.has_payment_schedule ? 'var(--teal)' : 'var(--teal-lt)',
                          background: inv.has_payment_schedule ? 'var(--teal-lt)' : undefined,
                          fontWeight: inv.has_payment_schedule ? 700 : 400,
                        }}
                        onClick={() => openScheduleModal(inv)}>
                        📅 أقساط{inv.has_payment_schedule ? ' ✓' : ''}
                      </button>
                      {/* إصلاح ازدحام الـ UX: الأفعال الثانوية (طباعة/نسخ/تعديل/حذف)
                          اتجمعوا في قائمة واحدة بدل ما يتكرروا كأزرار منفصلة في كل صف */}
                      <ActionMenu items={[
                        { label: 'طباعة الفاتورة', icon: '🖨️', onClick: () => api.sales.printInvoice(inv.id) },
                        write && { label: 'نسخ الفاتورة', icon: '📋', disabled: copyingId === inv.id,
                          onClick: () => copyInvoice(inv) },
                        write && inv.status !== 'paid' && { label: 'تعديل الفاتورة', icon: '✏️',
                          onClick: () => { setEditingInvoice(inv); setView('edit') } },
                        write && { label: 'حذف الفاتورة', icon: '🗑️', danger: true,
                          onClick: () => setConfirmDelete(inv.id) },
                      ]} />
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Payment modal */}
      <Modal open={!!payModal} onClose={() => setPayModal(null)}
        title={`تسجيل دفع — ${payModal?.code}`} size="sm">
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between px-3 py-2 rounded-erp"
               style={{ background: 'var(--teal-lt)' }}>
            <span className="text-sm text-erp-muted">المبلغ المستحق</span>
            <span className="font-bold" style={{ color: 'var(--teal)' }}>
              {egp(Number(payModal?.total || 0) - Number(payModal?.paid_amount || 0))}
            </span>
          </div>
          <Input label="المبلغ المدفوع (ج) *" type="number" step="0.01"
            value={payForm.amount}
            onChange={e => setPayForm(f => ({ ...f, amount: e.target.value }))} />
          <Input label="تاريخ الدفع" type="date"
            value={payForm.payment_date}
            onChange={e => setPayForm(f => ({ ...f, payment_date: e.target.value }))} />
          <Select label="طريقة الدفع"
            value={payForm.payment_type || 'cash'}
            onChange={e => setPayForm(f => ({ ...f, payment_type: e.target.value }))}>
            <option value="cash">نقدي</option>
            <option value="transfer">تحويل بنكي</option>
            <option value="cheque">شيك</option>
            <option value="bill">كمبيالة</option>
          </Select>
          <Input label="رقم المرجع (رقم الشيك / رقم التحويل)"
            value={payForm.reference || ''}
            onChange={e => setPayForm(f => ({ ...f, reference: e.target.value }))}
            placeholder="اختياري" />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setPayModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={recordPay} disabled={paying}
            style={{ background: 'var(--green)', borderColor: 'var(--green)' }}>
            {paying ? '…' : '💵 تسجيل'}
          </button>
  
        </ModalFooter>
      </Modal>

      <ConfirmDialog open={!!confirmDelete} title="حذف الفاتورة" confirmLabel="حذف" danger
        message="هل تريد حذف هذه الفاتورة نهائياً؟"
        onConfirm={() => { handleDelete(confirmDelete); setConfirmDelete(null) }}
        onCancel={() => setConfirmDelete(null)} />

      {/* محطة 4: جدولة أقساط الفاتورة */}
      <Modal open={!!scheduleModal} onClose={() => setScheduleModal(null)}
        title={`جدولة أقساط — ${scheduleModal?.code}`}>
        {scheduleExisting === null ? (
          <div className="text-sm text-erp-muted p-4 text-center">جارِ التحميل…</div>
        ) : scheduleMode === 'view' ? (
          <div className="flex flex-col gap-3">
            <table className="erp-table text-sm">
              <thead>
                <tr>
                  <th>#</th><th>البيان</th><th>الاستحقاق</th><th>المستحق</th><th>المتبقي</th><th>الحالة</th>
                  {write && <th></th>}
                </tr>
              </thead>
              <tbody>
                {scheduleExisting.map(row => {
                  // أقدم قسط لسه مستحق (اللي هيتوزع عليه الدفع فعليًا أولاً حسب FIFO محطة 5)
                  const oldestOpen = scheduleExisting.find(r => r.remaining_amount > 0)
                  const isOldestOpen = oldestOpen && oldestOpen.id === row.id
                  return (
                    <tr key={row.id}>
                      <td>{row.installment_no}</td>
                      <td>{row.label || '—'}</td>
                      <td style={{ color: row.status === 'overdue' ? 'var(--red)' : undefined }}>{fmtDate(row.due_date)}</td>
                      <td>{egp(row.amount_due)}</td>
                      <td style={{ color: row.remaining_amount > 0 ? undefined : 'var(--green)' }}>{egp(row.remaining_amount)}</td>
                      <td><span className={`badge ${row.status === 'paid' ? 'badge-green' : row.status === 'overdue' ? 'badge-red' : row.status === 'partial' ? 'badge-amber' : 'badge-gray'}`}>
                        {row.status_ar}
                      </span></td>
                      {write && (
                        <td>
                          {row.remaining_amount > 0 && (
                            <button className="btn btn-sm text-xs"
                              style={{ color: 'var(--green)', borderColor: 'var(--green-lt)' }}
                              title={isOldestOpen ? 'دفع هذا القسط' : `هيتوزع تلقائيًا على أقدم قسط مستحق أولاً (${oldestOpen.label || 'قسط ' + oldestOpen.installment_no}) قبل القسط ده`}
                              onClick={() => {
                                setScheduleModal(null)
                                setPayModal(scheduleModal)
                                setPayForm({ amount: String(Number(row.remaining_amount || 0).toFixed(2)), payment_date: today(), payment_type: 'cash', reference: '' })
                                if (!isOldestOpen) {
                                  toast(`هيتوزع تلقائيًا على أقدم قسط مستحق أولاً (${oldestOpen.label || 'قسط ' + oldestOpen.installment_no})، مش بالضرورة القسط ده مباشرة`, 'warn')
                                }
                              }}>
                              💵 دفع
                            </button>
                          )}
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
            {write && (
              <button className="btn" onClick={startEditSchedule}>
                {scheduleExisting.length ? '✏️ تعديل / استبدال الخطة' : '➕ إنشاء خطة أقساط'}
              </button>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between px-3 py-2 rounded-erp"
                 style={{ background: 'var(--teal-lt)' }}>
              <span className="text-sm text-erp-muted">إجمالي الفاتورة</span>
              <span className="font-bold" style={{ color: 'var(--teal)' }}>{egp(scheduleModal?.total || 0)}</span>
            </div>
            {scheduleRows.map((row, idx) => (
              <div key={idx} className="flex gap-2 items-end" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
                <Input label="البيان" value={row.label}
                  onChange={e => updateScheduleRow(idx, 'label', e.target.value)} />
                <Input label="تاريخ الاستحقاق" type="date" value={row.due_date}
                  onChange={e => updateScheduleRow(idx, 'due_date', e.target.value)} />
                <Input label="المبلغ (ج)" type="number" step="0.01" value={row.amount}
                  onChange={e => updateScheduleRow(idx, 'amount', e.target.value)} />
                <button className="btn btn-sm btn-danger" style={{ marginBottom: 4 }}
                  onClick={() => removeScheduleRow(idx)} disabled={scheduleRows.length === 1}>🗑️</button>
              </div>
            ))}
            <button className="btn btn-sm" onClick={addScheduleRow}>➕ إضافة قسط</button>

            <div className="flex items-center justify-between px-3 py-2 rounded-erp"
                 style={{ background: Math.abs(scheduleRowsTotal - Number(scheduleModal?.total || 0)) > 0.02 ? 'var(--red-lt)' : 'var(--green-lt)' }}>
              <span className="text-sm">مجموع الأقساط</span>
              <span className="font-bold">{egp(scheduleRowsTotal)} / {egp(scheduleModal?.total || 0)}</span>
            </div>
          </div>
        )}
        <ModalFooter>
          <button className="btn" onClick={() => setScheduleModal(null)}>إغلاق</button>
          {scheduleMode === 'edit' && (
            <button className="btn btn-primary" onClick={saveSchedule} disabled={savingSchedule}
              style={{ background: 'var(--teal)', borderColor: 'var(--teal)' }}>
              {savingSchedule ? '…' : '💾 حفظ الخطة'}
            </button>
          )}
        </ModalFooter>
      </Modal>
    </div>
  )
}
