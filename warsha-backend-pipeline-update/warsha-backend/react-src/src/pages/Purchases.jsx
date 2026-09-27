import { useState, useMemo, useRef } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import { StatusBadge } from '../components/ui/Badge'
import Modal, { ModalFooter } from '../components/ui/Modal'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import SearchInput from '../components/ui/SearchInput'
import { PageSpinner } from '../components/ui/Spinner'

// ملحوظة: الباك-إند لا يُصدر أبداً حالة "overdue" (فقط draft/received/paid/partial)
// — أُزيلت من هنا لأنها كانت خياراً ميتاً في فلتر الحالة (يرجع نتائج فارغة دائماً).
const PURCHASE_STATUS = {
  draft:    { label: 'مسودة',    badge: 'badge-gray'  },
  received: { label: 'مستلمة',   badge: 'badge-blue'  },
  paid:     { label: 'مدفوعة',   badge: 'badge-green' },
  partial:  { label: 'جزئي',     badge: 'badge-amber' },
}

const EMPTY_LINE = { description: '', qty: '', unit: 'قطعة', unit_cost: '', material_id: '', update_stock: true, update_cost: true }

export default function Purchases() {
  const { canWrite } = useAuth()
  const write = canWrite('inventory')
  const toast = useToast()
  const { data: invoices, loading, reload } = useApi(() => api.purchases.invoices())
  const { data: suppliers } = useApi(() => api.purchases.suppliers())
  const { data: materials } = useApi(() => api.inventory.materials())
  const [q, setQ] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [modal, setModal] = useState(null)   // 'add' | 'view' | 'payment'
  const [form, setForm] = useState({})
  const [lines, setLines] = useState([{ ...EMPTY_LINE }])
  const [viewInv, setViewInv] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [payForm, setPayForm] = useState({ amount: '', notes: '', wht_amount: '' })
  const [confirmDelete, setConfirmDelete] = useState(null)
  const fileRef = useRef()

  const filtered = useMemo(() => {
    if (!invoices) return []
    let result = invoices
    if (statusFilter) result = result.filter(x => x.status === statusFilter)
    const lq = q.toLowerCase()
    if (lq) result = result.filter(x =>
      x.internal_code?.toLowerCase().includes(lq) ||
      x.code?.toLowerCase().includes(lq) ||
      x.supplier_name?.toLowerCase().includes(lq)
    )
    return result
  }, [invoices, q, statusFilter])

  const subtotal = lines.reduce((s, l) => s + ((+l.qty || 0) * (+l.unit_cost || 0)), 0)
  const taxAmt = subtotal * ((+form.tax_pct || 0) / 100)
  const total = subtotal + taxAmt - (+form.discount_amt || 0)

  const openAdd = () => {
    setEditingId(null)
    setForm({ date: today(), tax_pct: '0', discount_amt: '0' })
    setLines([{ ...EMPTY_LINE }])
    setModal('add')
  }

  const openEdit = async (inv) => {
    const detail = await api.purchases.getInvoice(inv.id)
    setEditingId(detail.id)
    setForm({
      supplier_id: detail.supplier_id,
      date: detail.date,
      due_date: detail.due_date || '',
      code: detail.code || '',
      tax_pct: String(detail.tax_pct ?? 0),
      discount_amt: String(detail.discount_amt ?? 0),
      notes: detail.notes || '',
    })
    setLines(
      detail.lines?.length
        ? detail.lines.map(l => ({
            description: l.description, qty: String(l.qty), unit: l.unit,
            unit_cost: String(l.unit_cost), material_id: l.material_id || '',
            update_stock: !!l.update_stock,
            update_cost: l.update_cost !== false,
          }))
        : [{ ...EMPTY_LINE }]
    )
    setModal('add')
  }

  const openView = async (inv) => {
    const detail = await api.purchases.getInvoice(inv.id)
    setViewInv(detail)
    setModal('view')
  }

  const addLine = () => setLines(ls => [...ls, { ...EMPTY_LINE }])
  const removeLine = (i) => setLines(ls => ls.filter((_, idx) => idx !== i))
  const updateLine = (i, field, val) => setLines(ls => ls.map((l, idx) => idx === i ? { ...l, [field]: val } : l))

  const [save, saving] = useAction(async () => {
    if (!form.supplier_id) { toast('يجب اختيار المورد', 'error'); return }
    if (!lines.some(l => l.description && l.qty && l.unit_cost)) {
      toast('أضف سطراً واحداً على الأقل', 'error'); return
    }
    const payload = {
      ...form,
      supplier_id: +form.supplier_id,
      due_date: form.due_date || null,
      tax_pct: +form.tax_pct || 0,
      discount_amt: +form.discount_amt || 0,
      lines: lines.filter(l => l.description && l.qty && l.unit_cost).map(l => ({
        ...l,
        qty: +l.qty,
        unit_cost: +l.unit_cost,
        material_id: l.material_id ? +l.material_id : null,
        update_stock: !!l.update_stock,
      })),
    }
    let inv
    if (editingId) {
      inv = await api.purchases.updateInvoice(editingId, payload)
    } else {
      inv = await api.purchases.createInvoice(payload)
    }

    if (fileRef.current?.files?.length) {
      await api.purchases.uploadAttachment(inv.id, fileRef.current.files[0])
    }

    toast(editingId ? 'تم تحديث فاتورة الشراء ✅' : 'تم حفظ فاتورة الشراء ✅')
    setEditingId(null)
    setModal(null); reload()
  })

  const payFileRef = useRef(null)

  const [pay, paying] = useAction(async () => {
    if (!payForm.amount) { toast('أدخل المبلغ', 'error'); return }
    const res = await api.purchases.recordPayment(viewInv.id, {
      amount: +payForm.amount,
      payment_date: payForm.payment_date || today(),
      notes: payForm.notes,
      wht_amount: +payForm.wht_amount || 0,
    })
    if (payFileRef.current?.files?.length && res?.supplier_payment_id) {
      try {
        await api.uploadAttachment('supplier_payment', res.supplier_payment_id, payFileRef.current.files[0])
      } catch (e) {
        toast(`تم تسجيل الدفعة، لكن فشل رفع إثبات الدفع: ${e.message}`, 'error')
        setModal(null); setPayForm({ amount: '', notes: '', wht_amount: '' }); reload()
        return
      }
    }
    if (payFileRef.current) payFileRef.current.value = ''
    toast('تم تسجيل الدفعة')
    setModal(null); setPayForm({ amount: '', notes: '', wht_amount: '' }); reload()
  })

  const [handleDelete, deleting] = useAction(async (id) => {
    await api.purchases.deleteInvoice(id)
    toast('تم حذف الفاتورة')
    reload()
  })

  const [uploadAtt, uploading] = useAction(async (invId, file) => {
    await api.purchases.uploadAttachment(invId, file)
    toast('تم رفع المرفق'); reload()
  })

  if (loading) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      {/* ── Toolbar ── */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div className="flex-1 min-w-48">
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالكود أو المورد…" />
        </div>
        <select
          className="erp-input text-sm"
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          style={{ minWidth: 130 }}
        >
          <option value="">كل الحالات</option>
          {Object.entries(PURCHASE_STATUS).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>
        {write && (
          <button className="btn btn-primary btn-sm" onClick={openAdd}>
            + فاتورة شراء جديدة
          </button>
        )}
      </div>

      {/* ── KPIs ── */}
      {invoices?.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: 'إجمالي الفواتير', val: egp(invoices.reduce((s,x)=>s+(x.total||0),0)), color: 'var(--teal)' },
            { label: 'مدفوع', val: egp(invoices.reduce((s,x)=>s+(x.paid_amount||0),0)), color: 'var(--green)' },
            { label: 'مستحق', val: egp(invoices.reduce((s,x)=>s+(Math.max(0,(x.total||0)-(x.paid_amount||0))),0)), color: 'var(--red)' },
            { label: 'عدد الفواتير', val: invoices.length, color: 'var(--amber)' },
          ].map(k => (
            <div key={k.label} className="card py-3 text-center">
              <div className="text-lg font-bold" style={{ color: k.color }}>{k.val}</div>
              <div className="text-xs text-erp-muted mt-1">{k.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* ── Table ── */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr>
                <th>الكود الداخلي</th>
                <th>كود المورد</th>
                <th>المورد</th>
                <th>التاريخ</th>
                <th>الإجمالي</th>
                <th>المدفوع</th>
                <th>المتبقي</th>
                <th>مرفقات</th>
                <th>الحالة</th>
                {write && <th>إجراءات</th>}
              </tr>
            </thead>
            <tbody>
              {!filtered.length && (
                <tr>
                  <td colSpan={10} className="text-center text-erp-muted py-10">
                    لا توجد فواتير شراء
                  </td>
                </tr>
              )}
              {filtered.map(inv => {
                const remaining = (inv.total || 0) - (inv.paid_amount || 0)
                return (
                  <tr key={inv.id}>
                    <td>
                      <span className="font-mono text-xs font-semibold" style={{ color: 'var(--teal)' }}>
                        {inv.internal_code || '—'}
                      </span>
                    </td>
                    <td>
                      <span className="font-mono text-xs text-erp-muted">{inv.code || '—'}</span>
                    </td>
                    <td className="font-medium">{inv.supplier_name || '—'}</td>
                    <td className="text-xs text-erp-muted">{fmtDate(inv.date)}</td>
                    <td className="font-semibold">{egp(inv.total)}</td>
                    <td style={{ color: 'var(--green)' }}>{egp(inv.paid_amount)}</td>
                    <td
                      style={{ color: remaining > 0 ? 'var(--red)' : 'var(--green)' }}
                      className="font-semibold"
                    >
                      {egp(remaining)}
                    </td>
                    <td>
                      {inv.attachment_count > 0
                        ? <span className="badge badge-blue">📎 {inv.attachment_count}</span>
                        : <span className="text-erp-muted text-xs">—</span>
                      }
                    </td>
                    <td><StatusBadge map={PURCHASE_STATUS} value={inv.status} /></td>
                    {write && (
                      <td>
                        <div className="flex gap-1 flex-wrap">
                          <button className="btn btn-sm text-xs" onClick={() => openView(inv)} title="عرض">👁️</button>
                          {(inv.paid_amount || 0) === 0 && (
                            <button className="btn btn-sm text-xs" onClick={() => openEdit(inv)} title="تعديل">✏️</button>
                          )}
                          <button
                            className="btn btn-sm text-xs"
                            title="طباعة"
                            onClick={() => api.purchases.printInvoice(inv.id)}
                          >🖨️</button>
                          {remaining > 0.01 && (
                            <button
                              className="btn btn-sm text-xs"
                              style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}
                              onClick={() => {
                                setViewInv(inv)
                                setPayForm({ amount: remaining.toFixed(2), notes: '', wht_amount: '' })
                                setModal('payment')
                              }}
                            >💰 دفع</button>
                          )}
                          <label className="btn btn-sm text-xs cursor-pointer" title="رفع مرفق">
                            📎
                            <input
                              type="file"
                              className="hidden"
                              accept="image/*"
                              onChange={e => e.target.files[0] && uploadAtt(inv.id, e.target.files[0])}
                            />
                          </label>
                          <button
                            className="btn btn-sm text-xs btn-danger"
                            title="حذف"
                            onClick={() => setConfirmDelete(inv.id)}
                          >🗑️</button>
                        </div>
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Add Invoice Modal ── */}
      <Modal open={modal === 'add'} onClose={() => { setModal(null); setEditingId(null) }} title={editingId ? 'تعديل فاتورة شراء' : 'فاتورة شراء جديدة'} size="xl">
        <div className="flex flex-col gap-3">
          <FormGrid cols={3}>
            <Select
              label="المورد *"
              value={form.supplier_id || ''}
              onChange={e => setForm(f => ({ ...f, supplier_id: e.target.value }))}
              required
            >
              <option value="" disabled>— اختر المورد —</option>
              {suppliers?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
            <Input
              label="تاريخ الفاتورة"
              type="date"
              value={form.date || today()}
              onChange={e => setForm(f => ({ ...f, date: e.target.value }))}
            />
            <Input
              label="تاريخ الاستحقاق"
              type="date"
              value={form.due_date || ''}
              onChange={e => setForm(f => ({ ...f, due_date: e.target.value }))}
            />
          </FormGrid>
          <FormGrid cols={3}>
            <Input
              label="رقم فاتورة المورد (اختياري)"
              value={form.code || ''}
              onChange={e => setForm(f => ({ ...f, code: e.target.value }))}
              placeholder="رقم فاتورة المورد الخارجي"
            />
            <Input
              label="ضريبة %"
              type="number"
              step="0.1"
              value={form.tax_pct || '0'}
              onChange={e => setForm(f => ({ ...f, tax_pct: e.target.value }))}
            />
            <Input
              label="خصم (ج)"
              type="number"
              value={form.discount_amt || '0'}
              onChange={e => setForm(f => ({ ...f, discount_amt: e.target.value }))}
            />
          </FormGrid>

          {/* Lines */}
          <div className="border border-erp-border rounded-erp overflow-hidden">
            <div className="px-3 py-2 bg-erp text-xs font-semibold flex items-center justify-between">
              <span>بنود الفاتورة</span>
              <button className="btn btn-sm text-xs" onClick={addLine}>+ سطر</button>
            </div>
            <div className="overflow-x-auto">
              <table className="erp-table text-xs">
                <thead>
                  <tr>
                    <th>الوصف *</th>
                    <th>المادة (اختياري)</th>
                    <th>الكمية</th>
                    <th>الوحدة</th>
                    <th>سعر الوحدة</th>
                    <th>الإجمالي</th>
                    <th>تحديث مخزون</th>
                    <th>تحديث السعر</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => (
                    <tr key={i}>
                      <td>
                        <input
                          className="erp-input text-xs w-full"
                          value={l.description}
                          onChange={e => updateLine(i, 'description', e.target.value)}
                          placeholder="وصف البند"
                        />
                      </td>
                      <td>
                        <select
                          className="erp-input text-xs w-full"
                          value={l.material_id}
                          onChange={e => {
                            const mat = materials?.find(m => m.id === +e.target.value)
                            updateLine(i, 'material_id', e.target.value)
                            if (mat && !l.description) updateLine(i, 'description', mat.name_ar || mat.name)
                            if (mat && mat.unit) updateLine(i, 'unit', mat.unit)
                            if (mat && !l.unit_cost) {
                              const price = mat.last_purchase_price || mat.unit_cost
                              if (price) updateLine(i, 'unit_cost', String(price))
                            }
                          }}
                        >
                          <option value="">—</option>
                          {materials?.map(m => (
                            <option key={m.id} value={m.id}>{m.name_ar || m.name}</option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          className="erp-input text-xs w-20"
                          type="number"
                          step="0.01"
                          value={l.qty}
                          onChange={e => updateLine(i, 'qty', e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          className="erp-input text-xs w-16"
                          value={l.unit}
                          onChange={e => updateLine(i, 'unit', e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          className="erp-input text-xs w-24"
                          type="number"
                          step="0.01"
                          value={l.unit_cost}
                          onChange={e => updateLine(i, 'unit_cost', e.target.value)}
                        />
                      </td>
                      <td className="font-semibold text-xs">
                        {egp((+l.qty||0)*(+l.unit_cost||0))}
                      </td>
                      <td className="text-center">
                        <input
                          type="checkbox"
                          checked={l.update_stock}
                          onChange={e => updateLine(i, 'update_stock', e.target.checked)}
                        />
                      </td>
                      <td className="text-center">
                        <input
                          type="checkbox"
                          checked={l.update_cost !== false}
                          onChange={e => updateLine(i, 'update_cost', e.target.checked)}
                          title="هل يتم تحديث سعر تكلفة المادة في المخزون بسعر هذه الفاتورة؟"
                        />
                      </td>
                      <td>
                        <button
                          className="text-erp-muted hover:text-red text-xs"
                          onClick={() => removeLine(i)}
                        >✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Totals */}
          <div className="flex justify-end">
            <div className="text-sm space-y-1 text-left" style={{ minWidth: 220 }}>
              <div className="flex justify-between gap-4 text-erp-muted">
                <span>المجموع قبل الضريبة:</span><span>{egp(subtotal)}</span>
              </div>
              {+form.tax_pct > 0 && (
                <div className="flex justify-between gap-4 text-erp-muted">
                  <span>الضريبة ({form.tax_pct}%):</span><span>{egp(taxAmt)}</span>
                </div>
              )}
              {+form.discount_amt > 0 && (
                <div className="flex justify-between gap-4" style={{ color: 'var(--red)' }}>
                  <span>الخصم:</span><span>- {egp(+form.discount_amt)}</span>
                </div>
              )}
              <div
                className="flex justify-between gap-4 font-bold text-base border-t border-erp-border pt-1"
                style={{ color: 'var(--teal)' }}
              >
                <span>الإجمالي:</span><span>{egp(total)}</span>
              </div>
            </div>
          </div>

          {/* File attachment */}
          <div>
            <div className="text-xs font-medium text-erp-muted mb-1">إرفاق صورة/PDF الفاتورة (اختياري)</div>
            <input ref={fileRef} type="file" accept="image/*" className="text-xs" />
          </div>

          <Textarea
            label="ملاحظات"
            value={form.notes || ''}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
            rows={2}
          />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => { setModal(null); setEditingId(null) }}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? '…' : (editingId ? 'حفظ التعديلات' : 'حفظ الفاتورة')}
          </button>
        </ModalFooter>
      </Modal>

      {/* ── View Invoice Modal ── */}
      <Modal
        open={modal === 'view'}
        onClose={() => setModal(null)}
        title={`فاتورة شراء — ${viewInv?.internal_code || viewInv?.code}`}
        size="lg"
      >
        {viewInv && (
          <div className="flex flex-col gap-3 text-sm">
            <div className="grid grid-cols-2 gap-2 text-erp-muted">
              {viewInv.internal_code && (
                <div>الكود الداخلي: <strong className="text-erp-text font-mono" style={{ color: 'var(--teal)' }}>{viewInv.internal_code}</strong></div>
              )}
              {viewInv.code && (
                <div>كود المورد: <strong className="text-erp-text font-mono">{viewInv.code}</strong></div>
              )}
              <div>المورد: <strong className="text-erp-text">{viewInv.supplier_name || '—'}</strong></div>
              <div>التاريخ: <strong className="text-erp-text">{fmtDate(viewInv.date)}</strong></div>
              <div>الاستحقاق: <strong className="text-erp-text">{fmtDate(viewInv.due_date)}</strong></div>
              <div>الحالة: <StatusBadge map={PURCHASE_STATUS} value={viewInv.status} /></div>
            </div>

            {viewInv.lines?.length > 0 && (
              <div className="border border-erp-border rounded-erp overflow-hidden">
                <table className="erp-table text-xs">
                  <thead>
                    <tr>
                      <th>الوصف</th>
                      <th>الكمية</th>
                      <th>الوحدة</th>
                      <th>سعر الوحدة</th>
                      <th>الإجمالي</th>
                    </tr>
                  </thead>
                  <tbody>
                    {viewInv.lines.map((l, i) => (
                      <tr key={i}>
                        <td>{l.description}</td>
                        <td>{l.qty}</td>
                        <td>{l.unit}</td>
                        <td>{egp(l.unit_cost)}</td>
                        <td className="font-semibold">{egp(l.total_cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="flex justify-between font-bold" style={{ color: 'var(--teal)' }}>
              <span>الإجمالي</span><span>{egp(viewInv.total)}</span>
            </div>
            <div className="flex justify-between" style={{ color: 'var(--green)' }}>
              <span>المدفوع</span><span>{egp(viewInv.paid_amount)}</span>
            </div>
            <div className="flex justify-between font-bold" style={{ color: 'var(--red)' }}>
              <span>المتبقي</span>
              <span>{egp((viewInv.total||0) - (viewInv.paid_amount||0))}</span>
            </div>

            {viewInv.attachments?.length > 0 && (
              <div>
                <div className="text-xs font-semibold mb-1">المرفقات</div>
                <div className="flex gap-2 flex-wrap">
                  {viewInv.attachments.map(a => (
                    <button key={a.id} type="button" onClick={() => api.openFile(`/purchases/attachments/${a.id}/download`)}
                      className="badge badge-blue text-xs cursor-pointer">
                      📎 {a.filename}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="flex gap-2 justify-end flex-wrap">
              <button
                className="btn btn-sm text-xs"
                onClick={() => api.purchases.printInvoice(viewInv.id)}
              >🖨️ طباعة</button>
              {write && (viewInv.total||0) - (viewInv.paid_amount||0) > 0.01 && (
                <button className="btn btn-primary btn-sm" onClick={() => {
                  setPayForm({ amount: ((viewInv.total||0)-(viewInv.paid_amount||0)).toFixed(2), notes: '', wht_amount: '' })
                  setModal('payment')
                }}>💰 تسجيل دفعة</button>
              )}
            </div>
          </div>
        )}
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إغلاق</button>
        </ModalFooter>
      </Modal>

      {/* ── Payment Modal ── */}
      <Modal open={modal === 'payment'} onClose={() => setModal(null)} title="تسجيل دفعة" size="sm">
        <div className="flex flex-col gap-3">
          <Input
            label="المبلغ (ج) *"
            type="number"
            step="0.01"
            value={payForm.amount}
            onChange={e => setPayForm(f => ({ ...f, amount: e.target.value }))}
          />
          <Input
            label="تاريخ الدفع *"
            type="date"
            value={payForm.payment_date || today()}
            onChange={e => setPayForm(f => ({ ...f, payment_date: e.target.value }))}
          />
          <Input
            label="ضريبة الخصم تحت الحساب (WHT)"
            type="number"
            step="0.01"
            value={payForm.wht_amount}
            onChange={e => setPayForm(f => ({ ...f, wht_amount: e.target.value }))}
          />
          <Textarea
            label="ملاحظات"
            value={payForm.notes}
            onChange={e => setPayForm(f => ({ ...f, notes: e.target.value }))}
            rows={2}
          />
          <div>
            <label className="field-label">إثبات الدفع (صورة، اختياري)</label>
            <input ref={payFileRef} type="file" accept="image/*" className="text-xs" />
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={pay} disabled={paying}>
            {paying ? '…' : 'تأكيد الدفع'}
          </button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!confirmDelete}
        message="هل تريد حذف هذه الفاتورة؟"
        onConfirm={() => { handleDelete(confirmDelete); setConfirmDelete(null) }}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  )
}
