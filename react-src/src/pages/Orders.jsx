/**
 * Orders — الطلبات
 * Standalone table: كود/عميل/الوصف/الإجمالي/الحالة/التاريخ
 * Matches original screenshot exactly
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

const STATUS_MAP = {
  pending:     { label: 'معلق',   badge: 'badge-amber' },
  in_progress: { label: 'جاري',   badge: 'badge-blue'  },
  delivered:   { label: 'مُسلَّم', badge: 'badge-green' },
  cancelled:   { label: 'ملغي',   badge: 'badge-red'   },
}

export default function Orders() {
  const { canWrite } = useAuth()
  const write = canWrite('sales')
  const toast = useToast()

  // Fetch ALL orders across all customers
  const { data: orders, loading, reload } = useApi(() =>
    api.get('/customers/orders/all').catch(() =>
      // fallback: fetch all customers then their orders
      api.customers.list().then(async custs => {
        const all = await Promise.all(
          (custs || []).map(c =>
            api.customers.orders(c.id)
              .then(os => os.map(o => ({ ...o, customer_name: c.name, customer_code: c.code })))
              .catch(() => [])
          )
        )
        return all.flat()
      })
    )
  )

  const { data: customers } = useApi(() => api.customers.list())

  const [q, setQ] = useState('')
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState({})

  const filtered = useMemo(() => {
    if (!orders) return []
    const lq = q.toLowerCase()
    return !lq ? orders : orders.filter(o =>
      o.code?.toLowerCase().includes(lq) ||
      o.customer_name?.toLowerCase().includes(lq) ||
      o.description?.toLowerCase().includes(lq)
    )
  }, [orders, q])

  const openAdd = () => {
    setForm({ status: 'pending', order_date: today(), quantity: 1, unit_price: 0 })
    setModal({ mode: 'add' })
  }

  const [save, saving] = useAction(async () => {
    if (!form.customer_id) { toast('اختر العميل', 'error'); return }
    if (!form.code?.trim()) { toast('الكود مطلوب', 'error'); return }
    await api.customers.createOrder({
      customer_id: Number(form.customer_id),
      code:         form.code,
      description:  form.description || '',
      quantity:     Number(form.quantity || 1),
      unit_price:   Number(form.unit_price || 0),
      order_date:   form.order_date || today(),
      status:       form.status || 'pending',
      notes:        form.notes || null,
    })
    toast('تم إنشاء الطلب')
    setModal(null)
    reload()
  })

  if (loading) return <PageSpinner />

  return (
    <div className="flex flex-col gap-4">
      <div className="card flex items-center gap-3 py-3">
        <div className="flex-1">
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالكود أو العميل أو الوصف…" />
        </div>
        <div className="text-xs text-erp-muted">الطلبات ({filtered.length})</div>
        {write && (
          <button className="btn btn-primary btn-sm" onClick={openAdd}>+ إضافة</button>
        )}
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr>
              <th>الكود</th>
              <th>العميل</th>
              <th>الوصف</th>
              <th>الإجمالي</th>
              <th>الحالة</th>
              <th>التاريخ</th>
            </tr>
          </thead>
          <tbody>
            {!filtered.length && (
              <tr>
                <td colSpan={6} className="text-center text-erp-muted py-10">
                  لا توجد طلبات
                </td>
              </tr>
            )}
            {filtered.map(o => {
              const total  = o.total_amount ?? (Number(o.quantity || 1) * Number(o.unit_price || 0))
              const status = STATUS_MAP[o.status] || { label: o.status, badge: 'badge-gray' }
              return (
                <tr key={o.id}>
                  <td className="font-mono text-xs font-medium">{o.code}</td>
                  <td className="font-medium">{o.customer_name || o.customer?.name || '—'}</td>
                  <td className="text-sm text-erp-muted">{o.description || '—'}</td>
                  <td className="font-semibold">{egp(total)}</td>
                  <td><span className={`badge ${status.badge}`}>{status.label}</span></td>
                  <td className="text-xs text-erp-muted">{fmtDate(o.order_date)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Add Order Modal */}
      <Modal open={!!modal} onClose={() => setModal(null)} title="طلب جديد">
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="الكود *" value={form.code || ''}
              onChange={e => setForm(f => ({ ...f, code: e.target.value }))}
              placeholder="ORD-2025-001" />
            <Select label="العميل *" value={form.customer_id || ''}
              onChange={e => setForm(f => ({ ...f, customer_id: e.target.value }))}>
              <option value="">— اختر عميل —</option>
              {customers?.map(c => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
          </FormGrid>
          <Textarea label="الوصف *" rows={2} value={form.description || ''}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            placeholder="مجرشة FG-500 مع تركيب" />
          <FormGrid cols={3}>
            <Input label="الكمية" type="number" step="1" min="1"
              value={form.quantity || 1}
              onChange={e => setForm(f => ({ ...f, quantity: e.target.value }))} />
            <Input label="سعر الوحدة (ج)" type="number" step="0.01"
              value={form.unit_price || ''}
              onChange={e => setForm(f => ({ ...f, unit_price: e.target.value }))} />
            <div>
              <label className="field-label">الإجمالي</label>
              <div className="field-input bg-erp font-semibold" style={{ color: 'var(--teal)' }}>
                {egp(Number(form.quantity || 1) * Number(form.unit_price || 0))}
              </div>
            </div>
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="تاريخ الطلب" type="date"
              value={form.order_date || today()}
              onChange={e => setForm(f => ({ ...f, order_date: e.target.value }))} />
            <Select label="الحالة" value={form.status || 'pending'}
              onChange={e => setForm(f => ({ ...f, status: e.target.value }))}
              options={Object.entries(STATUS_MAP).map(([v, { label }]) => ({ value: v, label }))} />
          </FormGrid>
          <Textarea label="ملاحظات" rows={2} value={form.notes || ''}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? '…' : 'حفظ الطلب'}
          </button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
