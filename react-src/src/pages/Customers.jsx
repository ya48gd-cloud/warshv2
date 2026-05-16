/**
 * Customers — سجل العملاء
 * Matches original UI: table with كود/اسم/تليفون/رصيد + بروفايل/تعديل/دفعة buttons
 * Profile view: KPIs + طلبات + فواتير + مدفوعات
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
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

// ── Customer profile page ──────────────────────────────────────
function CustomerProfile({ customer, onBack, write }) {
  const toast = useToast()
  const { data: statement, loading } = useApi(
    () => api.get(`/customers/${customer.id}/statement`),
    [customer.id]
  )
  const [payModal, setPayModal] = useState(false)
  const [payForm, setPayForm] = useState({ amount: '', payment_date: today(), notes: '' })

  const [recordPay, paying] = useAction(async () => {
    if (!payForm.amount || Number(payForm.amount) <= 0) { toast('أدخل مبلغ الدفعة', 'error'); return }
    await api.post('/customers/payments', {
      customer_id: customer.id,
      amount: Number(payForm.amount),
      payment_type: 'cash',
      payment_date: payForm.payment_date || today(),
      notes: payForm.notes || null,
    })
    toast('تم تسجيل الدفعة')
    setPayModal(false)
    setPayForm({ amount: '', payment_date: today(), notes: '' })
  })

  const orders   = statement?.orders   || []
  const invoices = statement?.invoices  || []
  const payments = statement?.payments  || []
  const totalOwed    = statement?.total_owed    || customer.balance || 0
  const totalPaid    = statement?.total_paid    || 0
  const totalOrdered = statement?.total_ordered || 0

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button className="btn btn-sm" onClick={onBack}>← العودة</button>
          <h2 className="text-base font-semibold">{customer.name}</h2>
        </div>
        <div className="flex gap-2">
          {write && (
            <button className="btn btn-primary btn-sm" onClick={() => setPayModal(true)}>+ دفعة</button>
          )}
          <button className="btn btn-sm">+ طلب</button>
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: 'إجمالي المطلوب', val: egp(totalOrdered), sub: 'طلبات + فواتير', color: 'var(--blue)' },
          { label: 'إجمالي المدفوع', val: egp(totalPaid),    sub: '',               color: 'var(--green)' },
          { label: 'الرصيد المستحق', val: egp(totalOwed),    sub: 'مستحق للتحصيل',  color: Number(totalOwed) > 0 ? 'var(--red)' : 'var(--green)' },
          { label: 'التليفون',       val: customer.phone || '—', sub: customer.city || '', color: 'var(--text)' },
        ].map(k => (
          <div key={k.label} className="card">
            <div className="text-xs text-erp-muted mb-1">{k.label}</div>
            <div className="text-xl font-bold" style={{ color: k.color }}>{k.val}</div>
            {k.sub && <div className="text-[11px] text-erp-muted mt-0.5">{k.sub}</div>}
          </div>
        ))}
      </div>

      {loading ? <PageSpinner /> : (
        <div className="grid gap-4" style={{ gridTemplateColumns: '1fr 1fr' }}>
          {/* Orders */}
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
              الطلبات ({orders.length})
            </div>
            {!orders.length ? (
              <div className="p-6 text-center text-erp-muted text-sm">لا توجد طلبات.</div>
            ) : (
              <table className="erp-table">
                <thead>
                  <tr><th>الكود</th><th>الوصف</th><th>الكمية</th><th>السعر</th><th>الإجمالي</th><th>التاريخ</th><th>الحالة</th></tr>
                </thead>
                <tbody>
                  {orders.map(o => {
                    const statusMap = { pending:'معلق', in_progress:'جاري', delivered:'مُسلَّم', cancelled:'ملغي' }
                    const statusColor = { pending:'badge-amber', in_progress:'badge-blue', delivered:'badge-green', cancelled:'badge-red' }
                    return (
                      <tr key={o.id}>
                        <td className="font-mono text-xs">{o.code}</td>
                        <td className="text-sm">{o.description}</td>
                        <td>{o.quantity}</td>
                        <td className="text-sm">{egp(o.unit_price)}</td>
                        <td className="font-semibold">{egp(o.total_amount ?? (o.quantity * o.unit_price))}</td>
                        <td className="text-xs text-erp-muted">{fmtDate(o.order_date)}</td>
                        <td><span className={`badge ${statusColor[o.status] || 'badge-gray'}`}>{statusMap[o.status] || o.status}</span></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>

          {/* Invoices */}
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
              الفواتير ({invoices.length})
            </div>
            {!invoices.length ? (
              <div className="p-6 text-center text-erp-muted text-sm">لا توجد فواتير.</div>
            ) : (
              <table className="erp-table">
                <thead>
                  <tr><th>الكود</th><th>الإجمالي</th><th>المدفوع</th><th>الحالة</th><th>التاريخ</th></tr>
                </thead>
                <tbody>
                  {invoices.map(inv => (
                    <tr key={inv.id}>
                      <td className="font-mono text-xs">{inv.code}</td>
                      <td className="font-semibold">{egp(inv.total_amount)}</td>
                      <td style={{ color: 'var(--green)' }}>{egp(inv.paid_amount)}</td>
                      <td><span className={`badge ${inv.status === 'paid' ? 'badge-green' : inv.status === 'partial' ? 'badge-amber' : 'badge-red'}`}>
                        {inv.status === 'paid' ? 'مدفوعة' : inv.status === 'partial' ? 'جزئي' : 'غير مدفوعة'}
                      </span></td>
                      <td className="text-xs text-erp-muted">{fmtDate(inv.issue_date)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Payments */}
          <div className="card p-0 overflow-hidden" style={{ gridColumn: '1 / -1' }}>
            <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
              المدفوعات ({payments.length})
            </div>
            {!payments.length ? (
              <div className="p-6 text-center text-erp-muted text-sm">لا توجد مدفوعات.</div>
            ) : (
              <table className="erp-table">
                <thead>
                  <tr><th>التاريخ</th><th>المبلغ</th><th>ملاحظات</th></tr>
                </thead>
                <tbody>
                  {payments.map(p => (
                    <tr key={p.id}>
                      <td className="text-xs text-erp-muted">{fmtDate(p.payment_date)}</td>
                      <td className="font-bold" style={{ color: 'var(--green)' }}>{egp(p.amount)}</td>
                      <td className="text-erp-muted text-xs">{p.notes || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* Payment modal */}
      <Modal open={payModal} onClose={() => setPayModal(false)}
        title={`تسجيل دفعة — ${customer.name}`} size="sm">
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between px-3 py-2 rounded-erp"
               style={{ background: 'var(--red-lt)' }}>
            <span className="text-sm text-erp-muted">الرصيد المستحق</span>
            <span className="font-bold" style={{ color: 'var(--red)' }}>{egp(totalOwed)}</span>
          </div>
          <Input label="المبلغ المدفوع (ج) *" type="number" step="0.01"
            value={payForm.amount}
            onChange={e => setPayForm(f => ({ ...f, amount: e.target.value }))}
            placeholder="0.00" />
          <Input label="تاريخ الدفعة" type="date"
            value={payForm.payment_date}
            onChange={e => setPayForm(f => ({ ...f, payment_date: e.target.value }))} />
          <Textarea label="ملاحظات" rows={2}
            value={payForm.notes}
            onChange={e => setPayForm(f => ({ ...f, notes: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setPayModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={recordPay} disabled={paying}
            style={{ background: 'var(--green)', borderColor: 'var(--green)' }}>
            {paying ? '…' : '💵 تسجيل الدفعة'}
          </button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ── Main Customers list ────────────────────────────────────────
export default function Customers() {
  const { canWrite } = useAuth()
  const toast = useToast()
  const write = canWrite('customers')

  const { data: customers, loading, reload } = useApi(() => api.customers.list())
  const [q, setQ] = useState('')
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState({})
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [profile, setProfile] = useState(null)

  const filtered = useMemo(() => {
    if (!customers) return []
    const lq = q.toLowerCase()
    return !lq ? customers : customers.filter(c =>
      c.name?.toLowerCase().includes(lq) ||
      c.code?.toLowerCase().includes(lq) ||
      c.phone?.includes(lq)
    )
  }, [customers, q])

  const openAdd  = () => { setForm({}); setModal({ mode: 'add' }) }
  const openEdit = (c) => { setForm({ ...c }); setModal({ mode: 'edit', item: c }) }

  const [save, saving] = useAction(async () => {
    if (!form.name?.trim()) { toast('الاسم مطلوب', 'error'); return }
    if (!form.code?.trim()) { toast('الكود مطلوب', 'error'); return }
    const payload = {
      code: form.code, name: form.name,
      phone: form.phone || null, city: form.city || null,
      address: form.address || null, tax_id: form.tax_id || null,
      credit_limit: Number(form.credit_limit || 0),
      notes: form.notes || null,
    }
    if (modal?.mode === 'edit') await api.customers.update(form.id, payload)
    else await api.customers.create(payload)
    toast('تم الحفظ')
    setModal(null)
    reload()
  })

  const [doDelete] = useAction(async () => {
    await api.customers.delete(deleteTarget.id)
    toast('تم الحذف')
    setDeleteTarget(null)
    reload()
  })

  if (loading) return <PageSpinner />

  // Show profile view
  if (profile) {
    const fullCustomer = customers?.find(c => c.id === profile.id) || profile
    return <CustomerProfile customer={fullCustomer} onBack={() => setProfile(null)} write={write} />
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="card flex items-center gap-3 py-3">
        <div className="flex-1">
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالاسم أو الكود أو التليفون…" />
        </div>
        <div className="text-xs text-erp-muted">العملاء ({filtered.length})</div>
        {write && <button className="btn btn-primary btn-sm" onClick={openAdd}>+ إضافة</button>}
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr>
              <th>الكود</th>
              <th>الاسم</th>
              <th>التليفون</th>
              <th>الرصيد</th>
              <th style={{ width: 180 }}></th>
            </tr>
          </thead>
          <tbody>
            {!filtered.length && (
              <tr><td colSpan={5} className="text-center text-erp-muted py-10">لا يوجد عملاء</td></tr>
            )}
            {filtered.map(c => {
              const bal = Number(c.balance || 0)
              return (
                <tr key={c.id}>
                  <td className="font-mono text-xs">{c.code}</td>
                  <td className="font-medium">{c.name}</td>
                  <td className="font-mono text-sm">{c.phone || '—'}</td>
                  <td className="font-semibold text-sm"
                      style={{ color: bal > 0 ? 'var(--red)' : bal < 0 ? 'var(--green)' : 'var(--muted)' }}>
                    {bal === 0 ? 'صفر' : `عليه ${egp(Math.abs(bal))}`}
                  </td>
                  <td>
                    <div className="flex gap-1.5">
                      <button className="btn btn-sm text-xs"
                        style={{ color: 'var(--teal)', borderColor: 'var(--teal-lt)', background: 'var(--teal-lt)' }}
                        onClick={() => setProfile(c)}>
                        بروفايل
                      </button>
                      {write && (
                        <>
                          <button className="btn btn-sm text-xs" onClick={() => openEdit(c)}>تعديل</button>
                          <button className="btn btn-sm text-xs"
                            style={{ color: 'var(--blue)', borderColor: 'var(--blue-lt)' }}
                            onClick={() => {/* open payment modal */}}>
                            دفعة
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <Modal open={!!modal} onClose={() => setModal(null)}
        title={modal?.mode === 'edit' ? 'تعديل عميل' : 'عميل جديد'}>
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="الكود *" value={form.code || ''}
              onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="C-001" />
            <Input label="الاسم *" value={form.name || ''}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="التليفون" value={form.phone || ''}
              onChange={e => setForm(f => ({ ...f, phone: e.target.value }))} placeholder="02xxxxxxxx" />
            <Input label="المدينة" value={form.city || ''}
              onChange={e => setForm(f => ({ ...f, city: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="الرقم الضريبي" value={form.tax_id || ''}
              onChange={e => setForm(f => ({ ...f, tax_id: e.target.value }))} />
            <Input label="حد الائتمان (ج)" type="number" value={form.credit_limit || ''}
              onChange={e => setForm(f => ({ ...f, credit_limit: e.target.value }))} />
          </FormGrid>
          <Textarea label="العنوان" rows={2} value={form.address || ''}
            onChange={e => setForm(f => ({ ...f, address: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog open={!!deleteTarget} onClose={() => setDeleteTarget(null)} onConfirm={doDelete}
        title="حذف عميل" message={`حذف "${deleteTarget?.name}"؟`} danger />
    </div>
  )
}
