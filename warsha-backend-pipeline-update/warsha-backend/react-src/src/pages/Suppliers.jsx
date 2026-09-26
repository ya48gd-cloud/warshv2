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
import PaymentAttachment from '../components/ui/PaymentAttachment'

// ── بروفايل المورد (كشف حساب) ────────────────────────────────
function SupplierProfile({ supplier, onBack }) {
  const toast = useToast()
  const { canWrite } = useAuth()
  const write = canWrite('inventory')
  const { data: statement, loading, reload } = useApi(
    () => api.get(`/purchases/suppliers/${supplier.id}/statement`),
    [supplier.id]
  )
  const [payModal, setPayModal] = useState(false)
  const [payForm, setPayForm] = useState({ amount: '', payment_date: today(), payment_method: 'cash', reference: '', notes: '', wht_amount: '' })
  const [payFile, setPayFile] = useState(null)

  const [recordPay, paying] = useAction(async () => {
    if (!payForm.amount || Number(payForm.amount) <= 0) { toast('أدخل مبلغ الدفعة', 'error'); return }
    const inv = statement?.invoices?.find(i => i.remaining > 0)
    const result = await api.purchases.recordSupplierPayment({
      supplier_id:    sup.id,
      invoice_id:     inv ? inv.id : null,
      amount:         Number(payForm.amount),
      payment_date:   payForm.payment_date || today(),
      payment_method: payForm.payment_method || 'cash',
      reference:      payForm.reference || null,
      notes:          payForm.notes || null,
      wht_amount:     Number(payForm.wht_amount) || 0,
    })
    // رفع إثبات الدفع إن وُجد
    if (payFile && result?.supplier_payment_id) {
      try { await api.uploadAttachment('supplier_payment', result.supplier_payment_id, payFile, 'إثبات دفعة') }
      catch (_) {}
    }
    if (result?.excess_onaccount > 0) {
      toast(`تم تسجيل الدفعة — ${egp(result.excess_onaccount)} زيادة عن الفاتورة تم ترحيلها كرصيد تحت الحساب`)
    } else {
      toast('تم تسجيل الدفعة')
    }
    setPayModal(false)
    setPayForm({ amount: '', payment_date: today(), payment_method: 'cash', reference: '', notes: '', wht_amount: '' })
    setPayFile(null); reload()
  })

  if (loading) return <PageSpinner />
  if (!statement) return null

  const sup = statement.supplier || supplier
  const invoices = statement.invoices || []
  const payments = statement.payments || []
  const balanceDue = statement.balance_due || 0
  const paymentMethodLabels = { cash: 'نقدي', transfer: 'تحويل بنكي', cheque: 'شيك', bill: 'كمبيالة' }

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <button className="btn btn-sm" onClick={onBack}>← العودة</button>
          <h2 className="text-base font-semibold">{sup.name}</h2>
          <span className="font-mono text-xs text-erp-muted">{sup.code}</span>
        </div>
        {write && (
          <button className="btn btn-primary btn-sm" onClick={() => setPayModal(true)}>💸 تسجيل دفعة</button>
        )}
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          { label: 'إجمالي الفواتير',   val: egp(statement.total_invoices || 0), color: 'var(--blue)'  },
          { label: 'إجمالي المدفوع',    val: egp(statement.total_paid || 0),     color: 'var(--green)' },
          ...(statement.onaccount_paid > 0 ? [
            { label: 'رصيد تحت الحساب', val: egp(statement.onaccount_paid), color: 'var(--blue)' },
          ] : []),
          { label: balanceDue >= 0 ? 'المتبقي المستحق' : 'رصيد لصالحنا (زيادة)',
            val: egp(Math.abs(balanceDue)),
            color: balanceDue > 0 ? 'var(--red)' : 'var(--green)' },
          { label: 'شروط الدفع',        val: sup.payment_terms || '—',           color: 'var(--text)'  },
        ].map(k => (
          <div key={k.label} className="card text-center py-3">
            <div className="text-lg font-bold" style={{ color: k.color }}>{k.val}</div>
            <div className="text-xs text-erp-muted mt-1">{k.label}</div>
          </div>
        ))}
      </div>

      {/* معلومات المورد */}
      <div className="card">
        <div className="text-sm font-semibold mb-3">بيانات المورد</div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
          {sup.phone        && <div><span className="text-erp-muted">الهاتف:</span> {sup.phone}</div>}
          {sup.address      && <div><span className="text-erp-muted">العنوان:</span> {sup.address}</div>}
          {sup.tax_id       && <div><span className="text-erp-muted">الرقم الضريبي:</span> {sup.tax_id}</div>}
          {sup.supplier_type && <div><span className="text-erp-muted">النوع:</span> {sup.supplier_type}</div>}
          {sup.is_import != null && (
            <div>
              <span className="text-erp-muted">تصنيف الاستلام:</span>{' '}
              <span className={`badge ${sup.is_import ? 'badge-blue' : 'badge-green'}`}>
                {sup.is_import ? 'مستورد → IMPORTED' : 'محلي → RAW'}
              </span>
            </div>
          )}
          {sup.bank_account && <div><span className="text-erp-muted">الحساب البنكي:</span> {sup.bank_account}</div>}
          {sup.iban         && <div><span className="text-erp-muted">IBAN:</span> <span className="font-mono text-xs">{sup.iban}</span></div>}
          {sup.notes        && <div className="col-span-full"><span className="text-erp-muted">ملاحظات:</span> {sup.notes}</div>}
        </div>
      </div>

      {/* الفواتير */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">
          الفواتير ({invoices.length})
        </div>
        {!invoices.length ? (
          <div className="p-8 text-center text-erp-muted text-sm">لا توجد فواتير</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="erp-table">
              <thead>
                <tr>
                  <th>رقم الفاتورة</th>
                  <th>التاريخ</th>
                  <th>الاستحقاق</th>
                  <th>الإجمالي</th>
                  <th>المدفوع</th>
                  <th>المتبقي</th>
                  <th>الحالة</th>
                </tr>
              </thead>
              <tbody>
                {invoices.map(inv => (
                  <tr key={inv.id}>
                    <td><span className="font-mono text-xs">{inv.code}</span></td>
                    <td className="text-xs text-erp-muted">{fmtDate(inv.date)}</td>
                    <td className="text-xs">{fmtDate(inv.due_date) || '—'}</td>
                    <td className="font-semibold">{egp(inv.total)}</td>
                    <td style={{ color: 'var(--green)' }}>{egp(inv.paid_amount)}</td>
                    <td style={{ color: inv.remaining > 0 ? 'var(--red)' : 'var(--green)', fontWeight: 600 }}>
                      {egp(inv.remaining)}
                    </td>
                    <td>
                      <span className={`badge ${inv.status === 'paid' ? 'badge-green' : inv.status === 'partial' ? 'badge-blue' : 'badge-amber'}`}>
                        {inv.status_ar || inv.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* سجل الدفعات */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">
          سجل الدفعات ({payments.length})
        </div>
        {!payments.length ? (
          <div className="p-8 text-center text-erp-muted text-sm">لا توجد دفعات مسجلة</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="erp-table">
              <thead>
                <tr>
                  <th>التاريخ</th>
                  <th>المبلغ</th>
                  <th>الفاتورة</th>
                  <th>طريقة الدفع</th>
                  <th>المرجع</th>
                  <th>ملاحظات</th>
                  <th>إثبات الدفع</th>
                </tr>
              </thead>
              <tbody>
                {payments.map(p => (
                  <tr key={p.id}>
                    <td className="text-xs text-erp-muted">{fmtDate(p.payment_date)}</td>
                    <td className="font-semibold" style={{ color: 'var(--green)' }}>{egp(p.amount)}</td>
                    <td>
                      {p.invoice_id
                        ? <span className="font-mono text-xs">{p.invoice_code || `#${p.invoice_id}`}</span>
                        : <span className="badge badge-blue">تحت الحساب</span>}
                    </td>
                    <td className="text-xs">{paymentMethodLabels[p.payment_method] || p.payment_method || '—'}</td>
                    <td className="text-xs font-mono">{p.reference || '—'}</td>
                    <td className="text-xs text-erp-muted">{p.notes || '—'}</td>
                    <td><PaymentAttachment entityType="supplier_payment" entityId={p.id} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pay Modal */}
      {payModal && (
        <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && setPayModal(false)}>
          <div className="modal-box" style={{ maxWidth: 400 }}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold">💸 تسجيل دفعة للمورد</h2>
              <button onClick={() => setPayModal(false)} className="text-erp-muted text-xl">×</button>
            </div>
            <div className="flex flex-col gap-3">
              <div className="text-xs text-erp-muted">
                المتبقي الإجمالي: <span className="font-bold" style={{ color: 'var(--red)' }}>{egp(balanceDue)}</span>
              </div>
              <div>
                <label className="field-label">المبلغ (ج) *</label>
                <input className="field-input w-full" type="number" step="0.01" min="0.01"
                  value={payForm.amount} onChange={e => setPayForm(f => ({ ...f, amount: e.target.value }))} />
              </div>
              <div>
                <label className="field-label">تاريخ الدفعة *</label>
                <input className="field-input w-full" type="date"
                  value={payForm.payment_date || today()}
                  onChange={e => setPayForm(f => ({ ...f, payment_date: e.target.value }))} />
              </div>
              {!invoices.some(i => i.remaining > 0) ? (
                <div className="text-xs" style={{ color: 'var(--green,#0F6E56)' }}>
                  ℹ️ لا توجد فواتير مستحقة — ستُسجَّل هذه الدفعة "تحت الحساب" لصالح المورد.
                </div>
              ) : (
                <div className="text-xs" style={{ color: 'var(--green,#0F6E56)' }}>
                  ℹ️ لو المبلغ أكبر من المتبقي على أقرب فاتورة مستحقة، سيتم تسديد الفاتورة بالكامل وترحيل الفرق كرصيد "تحت الحساب" لصالح المورد.
                </div>
              )}
              <Select label="طريقة الدفع"
                value={payForm.payment_method || 'cash'}
                onChange={e => setPayForm(f => ({ ...f, payment_method: e.target.value }))}>
                <option value="cash">نقدي</option>
                <option value="transfer">تحويل بنكي</option>
                <option value="cheque">شيك</option>
                <option value="bill">كمبيالة</option>
              </Select>
              <Input label="رقم المرجع (رقم الشيك / رقم التحويل)"
                value={payForm.reference || ''}
                onChange={e => setPayForm(f => ({ ...f, reference: e.target.value }))}
                placeholder="اختياري" />
              <Input label="ضريبة الخصم تحت الحساب (WHT)"
                type="number" step="0.01"
                value={payForm.wht_amount}
                onChange={e => setPayForm(f => ({ ...f, wht_amount: e.target.value }))}
                placeholder="اختياري" />
              <div>
                <label className="field-label">ملاحظات</label>
                <input className="field-input w-full" value={payForm.notes}
                  onChange={e => setPayForm(f => ({ ...f, notes: e.target.value }))} />
              </div>
              <div>
                <div className="text-xs text-erp-muted mb-1">إثبات الدفع (اختياري — صورة)</div>
                <input type="file" accept="image/*"
                  onChange={e => setPayFile(e.target.files?.[0] || null)}
                  style={{ fontSize: 13 }} />
                {payFile && <div className="text-xs text-erp-muted mt-1">📎 {payFile.name}</div>}
              </div>
            </div>
            <div className="flex gap-2 justify-end mt-5 pt-4 border-t border-erp-border">
              <button className="btn" onClick={() => setPayModal(false)}>إلغاء</button>
              <button className="btn btn-primary" onClick={recordPay} disabled={paying}>{paying ? '…' : 'حفظ'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── الصفحة الرئيسية للموردين ──────────────────────────────────
export default function Suppliers() {
  const { canWrite } = useAuth()
  const write = canWrite('inventory')
  const toast = useToast()
  const { data: suppliers, loading, reload } = useApi(() => api.purchases.suppliers())
  const [q, setQ] = useState('')
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState({})
  const [delTarget, setDelTarget] = useState(null)
  const [profileSupplier, setProfileSupplier] = useState(null)

  const filtered = useMemo(() => {
    if (!suppliers) return []
    const lq = q.toLowerCase()
    return !lq ? suppliers : suppliers.filter(s =>
      s.name?.toLowerCase().includes(lq) ||
      s.code?.toLowerCase().includes(lq) ||
      s.phone?.toLowerCase().includes(lq)
    )
  }, [suppliers, q])

  const openAdd = async () => {
    setForm({ is_active: true })
    setModal({ mode: 'add' })
    try { const r = await api.purchases.nextSupplierCode(); setForm(f => ({ ...f, code: r.code })) } catch (_) {}
  }
  const openEdit = (s) => {
    setForm({ ...s })
    setModal({ mode: 'edit', item: s })
  }

  const [save, saving] = useAction(async () => {
    if (!form.name || !form.code) { toast('اسم ورمز المورد مطلوبان', 'error'); return }
    if (modal?.mode === 'edit') await api.purchases.updateSupplier(form.id, form)
    else await api.purchases.createSupplier(form)
    toast('تم الحفظ'); setModal(null); reload()
  })

  const [del, deleting] = useAction(async () => {
    await api.purchases.deleteSupplier(delTarget.id)
    toast('تم الحذف'); setDelTarget(null); reload()
  })

  if (loading) return <PageSpinner />

  if (profileSupplier) {
    return <SupplierProfile supplier={profileSupplier} onBack={() => setProfileSupplier(null)} />
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="card flex items-center gap-3 py-3">
        <div className="flex-1"><SearchInput value={q} onChange={setQ} placeholder="بحث بالاسم أو الرمز أو الهاتف…" /></div>
        {write && <button className="btn btn-primary btn-sm" onClick={openAdd}>+ مورد جديد</button>}
      </div>

      {/* Summary KPIs */}
      {suppliers?.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="card py-3 text-center">
            <div className="text-xl font-bold" style={{ color: 'var(--teal)' }}>{suppliers.length}</div>
            <div className="text-xs text-erp-muted mt-1">إجمالي الموردين</div>
          </div>
          <div className="card py-3 text-center">
            <div className="text-xl font-bold" style={{ color: 'var(--green)' }}>
              {suppliers.filter(s => s.is_active).length}
            </div>
            <div className="text-xs text-erp-muted mt-1">نشط</div>
          </div>
          <div className="card py-3 text-center">
            <div className="text-lg font-bold" style={{ color: 'var(--red)' }}>
              {egp(suppliers.reduce((s, x) => s + (x.balance || 0), 0))}
            </div>
            <div className="text-xs text-erp-muted mt-1">إجمالي الرصيد المستحق</div>
          </div>
          <div className="card py-3 text-center">
            <div className="text-xl font-bold" style={{ color: 'var(--amber)' }}>
              {suppliers.filter(s => (s.balance || 0) > 0).length}
            </div>
            <div className="text-xs text-erp-muted mt-1">موردين بأرصدة</div>
          </div>
        </div>
      )}

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr>
                <th>الرمز</th>
                <th>الاسم</th>
                <th>الهاتف</th>
                <th>النوع</th>
                <th>شروط الدفع</th>
                <th>الرصيد</th>
                <th>الحالة</th>
                <th>إجراءات</th>
              </tr>
            </thead>
            <tbody>
              {!filtered.length && (
                <tr><td colSpan={8} className="text-center text-erp-muted py-10">لا يوجد موردون</td></tr>
              )}
              {filtered.map(s => {
                const TYPES = { regular: 'عادي', strategic: 'استراتيجي', local: 'محلي', foreign: 'أجنبي' }
                const TERMS = { cash: 'نقدي', net_15: '15 يوم', net_30: '30 يوم', net_60: '60 يوم', net_90: '90 يوم' }
                return (
                <tr key={s.id}>
                  <td><span className="font-mono text-xs badge badge-blue">{s.code}</span></td>
                  <td className="font-medium">{s.name}</td>
                  <td className="text-erp-muted text-sm">{s.phone || '—'}</td>
                  <td className="text-xs">
                    {TYPES[s.supplier_type] || s.supplier_type || '—'}
                    {s.is_import != null && (
                      <span className={`badge ${s.is_import ? 'badge-blue' : 'badge-green'}`} style={{ marginInlineStart: 4 }}>
                        {s.is_import ? 'مستورد' : 'محلي'}
                      </span>
                    )}
                  </td>
                  <td className="text-xs">{TERMS[s.payment_terms] || s.payment_terms || '—'}</td>
                  <td>
                    <span className="font-semibold" style={{ color: (s.balance || 0) > 0 ? 'var(--red)' : 'var(--green)' }}>
                      {egp(s.balance)}
                    </span>
                  </td>
                  <td>
                    <span className={`badge ${s.is_active ? 'badge-green' : 'badge-red'}`}>
                      {s.is_active ? 'نشط' : 'موقوف'}
                    </span>
                  </td>
                  <td>
                    <div className="flex gap-1">
                      <button className="btn btn-sm text-xs"
                        style={{ color: 'var(--teal)', borderColor: 'var(--teal-lt)', background: 'var(--teal-lt)' }}
                        onClick={() => setProfileSupplier(s)}>
                        بروفايل
                      </button>
                      {write && (
                        <>
                          <button className="btn btn-sm text-xs" onClick={() => openEdit(s)}>✏️</button>
                          <button className="btn btn-sm text-xs" style={{ color: 'var(--red)' }} onClick={() => setDelTarget(s)}>🗑️</button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              )})}
            </tbody>
          </table>
        </div>
      </div>

      <Modal open={!!modal} onClose={() => setModal(null)} title={modal?.mode === 'edit' ? 'تعديل مورد' : 'مورد جديد'}>
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            {modal?.mode === 'edit'
              ? <Input label="الرمز" value={form.code || ''} readOnly
                  style={{ background: 'var(--bg-alt)', cursor: 'not-allowed', opacity: 0.7 }} />
              : <Input label="الرمز (اختياري — يُولَّد تلقائياً)" value={form.code || ''}
                  onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="SUP-0001" />
            }
            <Input label="الاسم *" value={form.name || ''} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="الهاتف" value={form.phone || ''} onChange={e => setForm(f => ({ ...f, phone: e.target.value }))} />
            <Input label="البريد الإلكتروني" type="email" value={form.email || ''} onChange={e => setForm(f => ({ ...f, email: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="جهة الاتصال" value={form.contact_person || ''} onChange={e => setForm(f => ({ ...f, contact_person: e.target.value }))} placeholder="اسم الشخص المسؤول" />
            <Input label="الرقم الضريبي" value={form.tax_id || ''} onChange={e => setForm(f => ({ ...f, tax_id: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Select label="شروط الدفع" value={form.payment_terms || ''} onChange={e => setForm(f => ({ ...f, payment_terms: e.target.value }))}>
              <option value="">— اختر —</option>
              <option value="cash">نقدي فوري</option>
              <option value="net_15">صافي 15 يوم</option>
              <option value="net_30">صافي 30 يوم</option>
              <option value="net_60">صافي 60 يوم</option>
              <option value="net_90">صافي 90 يوم</option>
            </Select>
            <Select label="نوع المورد" value={form.supplier_type || ''} onChange={e => setForm(f => ({ ...f, supplier_type: e.target.value }))}>
              <option value="">— اختر —</option>
              <option value="regular">عادي</option>
              <option value="strategic">استراتيجي</option>
              <option value="local">محلي</option>
              <option value="foreign">أجنبي</option>
            </Select>
          </FormGrid>
          <FormGrid cols={2}>
            <Select
              label="تصنيف الاستلام (محلي/مستورد)"
              value={form.is_import === true ? 'true' : form.is_import === false ? 'false' : ''}
              onChange={e => setForm(f => ({
                ...f,
                is_import: e.target.value === '' ? null : e.target.value === 'true',
              }))}
            >
              <option value="">تلقائي (حسب نوع المورد أعلاه)</option>
              <option value="false">محلي — استلام يروح مخزن RAW</option>
              <option value="true">مستورد — استلام يروح مخزن IMPORTED</option>
            </Select>
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="الحساب البنكي" value={form.bank_account || ''} onChange={e => setForm(f => ({ ...f, bank_account: e.target.value }))} placeholder="اسم البنك ورقم الحساب" />
            <Input label="IBAN" value={form.iban || ''} onChange={e => setForm(f => ({ ...f, iban: e.target.value }))} placeholder="EG..." />
          </FormGrid>
          <FormGrid cols={2}>
            <Select label="الحالة" value={form.is_active ? 'active' : 'inactive'} onChange={e => setForm(f => ({ ...f, is_active: e.target.value === 'active' }))}>
              <option value="active">نشط</option>
              <option value="inactive">موقوف</option>
            </Select>
          </FormGrid>
          <Input label="العنوان" value={form.address || ''} onChange={e => setForm(f => ({ ...f, address: e.target.value }))} />
          <Textarea label="ملاحظات" value={form.notes || ''} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} rows={2} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!delTarget}
        onClose={() => setDelTarget(null)}
        onConfirm={del}
        title="حذف المورد"
        message={`حذف "${delTarget?.name}"?`}
        danger
      />
    </div>
  )
}
