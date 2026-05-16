/**
 * Invoices — الفواتير
 * Matches original UI: form with line items (الوصف/الكمية/الوحدة/سعر الوحدة/الإجمالي)
 * + Totals section (إجمالي / الإجمالي النهائي after tax/discount)
 * List view + detail/create view
 */
import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import SearchInput from '../components/ui/SearchInput'
import { PageSpinner } from '../components/ui/Spinner'

const UNITS = ['pcs', 'kg', 'ton', 'م', 'م²', 'م³', 'لتر', 'ساعة', 'رحلة', 'مجموعة']

// ── Invoice Form (create/edit with line items) ─────────────────
function InvoiceForm({ customers, onSave, onCancel, initial }) {
  const toast = useToast()
  const [form, setForm] = useState({
    code:          initial?.code || '',
    customer_id:   initial?.customer_id || '',
    issue_date:    initial?.issue_date || today(),
    due_date:      initial?.due_date || '',
    tax_pct:       initial?.tax_pct ?? 0,
    discount_pct:  initial?.discount_pct ?? 0,
    payment_terms: initial?.payment_terms || 'الدفع خلال 30 يوم من الاستلام',
    notes:         initial?.notes || '',
  })
  const [lines, setLines] = useState(initial?.lines || [])
  const [saving, setSaving] = useState(false)

  const addLine = () => setLines(ls => [
    ...ls,
    { id: Date.now(), description: '', quantity: 1, unit: 'pcs', unit_price: 0 }
  ])

  const removeLine = (id) => setLines(ls => ls.filter(l => l.id !== id))

  const updateLine = (id, field, value) =>
    setLines(ls => ls.map(l => l.id === id ? { ...l, [field]: value } : l))

  const subtotal  = lines.reduce((s, l) => s + Number(l.quantity || 0) * Number(l.unit_price || 0), 0)
  const taxAmt    = subtotal * Number(form.tax_pct || 0) / 100
  const discAmt   = subtotal * Number(form.discount_pct || 0) / 100
  const total     = subtotal + taxAmt - discAmt

  const handleSave = async () => {
    if (!form.customer_id) { toast('اختر العميل', 'error'); return }
    if (!form.code?.trim()) { toast('الكود مطلوب', 'error'); return }
    if (!lines.length) { toast('أضف بنداً واحداً على الأقل', 'error'); return }
    setSaving(true)
    try {
      await onSave({
        ...form,
        customer_id:  Number(form.customer_id),
        tax_pct:      Number(form.tax_pct || 0),
        discount_pct: Number(form.discount_pct || 0),
        subtotal, tax_amount: taxAmt, discount_amt: discAmt, total,
        lines: lines.map(l => ({
          description: l.description,
          quantity:    Number(l.quantity || 0),
          unit:        l.unit || 'pcs',
          unit_price:  Number(l.unit_price || 0),
          sort_order:  lines.indexOf(l) + 1,
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
            onChange={e => setForm(f => ({ ...f, customer_id: e.target.value }))}>
            <option value="">— اختر عميل —</option>
            {customers?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
          <Input label="التاريخ *" type="date" value={form.issue_date}
            onChange={e => setForm(f => ({ ...f, issue_date: e.target.value }))} />
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
          <Textarea label="شروط الدفع" rows={1} value={form.payment_terms}
            onChange={e => setForm(f => ({ ...f, payment_terms: e.target.value }))} />
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
          <button className="btn btn-primary btn-sm" onClick={addLine}>+ إضافة بند</button>
        </div>

        {/* Lines header */}
        <div className="grid text-xs font-semibold text-erp-muted mb-2"
             style={{ gridTemplateColumns: '3fr 1fr 1fr 1.5fr 1.5fr auto' }}>
          <div>الوصف</div>
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
          {lines.map(l => {
            const lineTotal = Number(l.quantity || 0) * Number(l.unit_price || 0)
            return (
              <div key={l.id} className="grid gap-2 items-center"
                   style={{ gridTemplateColumns: '3fr 1fr 1fr 1.5fr 1.5fr auto' }}>
                <input className="field-input text-sm"
                  placeholder="الوصف"
                  value={l.description}
                  onChange={e => updateLine(l.id, 'description', e.target.value)} />
                <input className="field-input text-sm text-center"
                  type="number" step="0.001" min="0"
                  value={l.quantity}
                  onChange={e => updateLine(l.id, 'quantity', e.target.value)} />
                <select className="field-select text-sm"
                  value={l.unit}
                  onChange={e => updateLine(l.id, 'unit', e.target.value)}>
                  {UNITS.map(u => <option key={u}>{u}</option>)}
                </select>
                <input className="field-input text-sm text-center"
                  type="number" step="0.01" min="0"
                  value={l.unit_price}
                  onChange={e => updateLine(l.id, 'unit_price', e.target.value)} />
                <div className="field-input bg-erp text-sm text-center font-semibold"
                     style={{ color: 'var(--teal)' }}>
                  {egp(lineTotal)}
                </div>
                <button className="btn btn-sm btn-danger text-xs"
                  onClick={() => removeLine(l.id)}>✕</button>
              </div>
            )
          })}
        </div>
      </div>

      {/* Totals */}
      {lines.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-3">الإجماليات</div>
          <div className="flex flex-col gap-2 max-w-xs mr-auto">
            {[
              ['الإجمالي', egp(subtotal)],
              form.tax_pct > 0     && [`ضريبة ${form.tax_pct}%`, egp(taxAmt)],
              form.discount_pct > 0 && [`خصم ${form.discount_pct}%`, `-${egp(discAmt)}`],
            ].filter(Boolean).map(([label, val]) => (
              <div key={label} className="flex items-center justify-between text-sm">
                <span className="text-erp-muted">{label}</span>
                <span className="font-medium">{val}</span>
              </div>
            ))}
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
          🖨️ {saving ? '…' : 'حفظ الفاتورة'}
        </button>
        <button className="btn" onClick={onCancel}>إلغاء</button>
      </div>
    </div>
  )
}

// ── Invoices list ──────────────────────────────────────────────
export default function Invoices() {
  const { canWrite } = useAuth()
  const write = canWrite('sales')
  const toast = useToast()

  const { data: invoices, loading, reload } = useApi(() => api.sales.invoices())
  const { data: customers } = useApi(() => api.customers.list())

  const [q, setQ] = useState('')
  const [view, setView] = useState('list')   // 'list' | 'create'
  const [payModal, setPayModal] = useState(null)
  const [payForm, setPayForm] = useState({ amount: '', payment_date: today() })

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
    total:       (invoices || []).reduce((s, i) => s + Number(i.total_amount || 0), 0),
    paid:        (invoices || []).filter(i => i.status === 'paid').reduce((s, i) => s + Number(i.total_amount || 0), 0),
    outstanding: (invoices || []).filter(i => i.status !== 'paid').reduce((s, i) => s + Number(i.total_amount || 0) - Number(i.paid_amount || 0), 0),
  }), [invoices])

  const handleCreate = async (data) => {
    await api.sales.createInvoice(data)
    toast('تم حفظ الفاتورة')
    setView('list')
    reload()
  }

  const [recordPay, paying] = useAction(async () => {
    if (!payForm.amount || Number(payForm.amount) <= 0) { toast('أدخل المبلغ', 'error'); return }
    await api.sales.recordPayment(payModal.id, {
      amount:       Number(payForm.amount),
      payment_date: payForm.payment_date || today(),
    })
    toast('تم تسجيل الدفع')
    setPayModal(null)
    setPayForm({ amount: '', payment_date: today() })
    reload()
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
              {write && <th style={{ width: 80 }}></th>}
            </tr>
          </thead>
          <tbody>
            {!filtered.length && (
              <tr><td colSpan={8} className="text-center text-erp-muted py-10">لا توجد فواتير</td></tr>
            )}
            {filtered.map(inv => {
              const statusMap = {
                draft:   { label: 'مسودة',    cls: 'badge-gray'  },
                unpaid:  { label: 'غير مدفوعة', cls: 'badge-red' },
                partial: { label: 'جزئي',     cls: 'badge-amber' },
                paid:    { label: 'مدفوعة',   cls: 'badge-green' },
              }
              const st = statusMap[inv.status] || { label: inv.status, cls: 'badge-gray' }
              const outstanding = Number(inv.total_amount || 0) - Number(inv.paid_amount || 0)
              return (
                <tr key={inv.id}>
                  <td className="font-mono text-xs font-medium">{inv.code}</td>
                  <td className="font-medium">{inv.customer_name || inv.customer?.name || '—'}</td>
                  <td className="text-xs text-erp-muted">{fmtDate(inv.issue_date)}</td>
                  <td className="text-xs" style={{ color: inv.status === 'overdue' ? 'var(--red)' : undefined }}>
                    {fmtDate(inv.due_date)}
                  </td>
                  <td className="font-semibold">{egp(inv.total_amount)}</td>
                  <td style={{ color: 'var(--green)' }}>{egp(inv.paid_amount || 0)}</td>
                  <td><span className={`badge ${st.cls}`}>{st.label}</span></td>
                  {write && (
                    <td>
                      {inv.status !== 'paid' && outstanding > 0 && (
                        <button className="btn btn-sm text-xs"
                          style={{ color: 'var(--green)', borderColor: 'var(--green-lt)' }}
                          onClick={() => {
                            setPayModal(inv)
                            setPayForm({ amount: String(outstanding.toFixed(2)), payment_date: today() })
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
      </div>

      {/* Payment modal */}
      <Modal open={!!payModal} onClose={() => setPayModal(null)}
        title={`تسجيل دفع — ${payModal?.code}`} size="sm">
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between px-3 py-2 rounded-erp"
               style={{ background: 'var(--teal-lt)' }}>
            <span className="text-sm text-erp-muted">المبلغ المستحق</span>
            <span className="font-bold" style={{ color: 'var(--teal)' }}>
              {egp(Number(payModal?.total_amount || 0) - Number(payModal?.paid_amount || 0))}
            </span>
          </div>
          <Input label="المبلغ المدفوع (ج) *" type="number" step="0.01"
            value={payForm.amount}
            onChange={e => setPayForm(f => ({ ...f, amount: e.target.value }))} />
          <Input label="تاريخ الدفع" type="date"
            value={payForm.payment_date}
            onChange={e => setPayForm(f => ({ ...f, payment_date: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setPayModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={recordPay} disabled={paying}
            style={{ background: 'var(--green)', borderColor: 'var(--green)' }}>
            {paying ? '…' : '💵 تسجيل'}
          </button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
