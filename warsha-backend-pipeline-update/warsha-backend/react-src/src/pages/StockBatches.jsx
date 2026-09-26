/**
 * دفعات المخزون (Lot Tracking) — محطة 8
 * استلام دفعات جديدة (بيحدّث رصيد المادة تلقائيًا) + صرف من دفعة محددة
 * (بيحترم فحص المحجوز لأوامر الإنتاج) + تتبع/تسوية الرصيد + تنبيهات انتهاء الصلاحية
 */
import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { Input, Select, Textarea } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'

const STATUS_LABELS = {
  active: 'نشطة', consumed: 'مستهلكة بالكامل', expired: 'منتهية الصلاحية', returned: 'مرتجعة',
}
const STATUS_BADGE = {
  active: 'badge-green', consumed: 'badge-gray', expired: 'badge-red', returned: 'badge-amber',
}

export default function StockBatches() {
  const { canWrite } = useAuth()
  const write = canWrite('inventory')
  const toast = useToast()

  const [materialFilter, setMaterialFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const { data: materials } = useApi(() => api.inventory.materials())
  const { data: suppliers } = useApi(() => api.purchases.suppliers())
  const { data: batches, loading, reload } = useApi(
    () => api.batches.list({ ...(materialFilter ? { material_id: materialFilter } : {}), ...(statusFilter ? { status: statusFilter } : {}) }),
    [materialFilter, statusFilter]
  )
  const { data: trace } = useApi(
    () => materialFilter ? api.batches.traceability(materialFilter) : Promise.resolve(null),
    [materialFilter]
  )
  const { data: expiring } = useApi(() => api.batches.expiryAlerts(30))

  // ── إنشاء دُفعة جديدة ──────────────────────────────────────────────
  const [createModal, setCreateModal] = useState(false)
  const [form, setForm] = useState({})
  const resetForm = () => setForm({ received_date: today(), qty_received: '', unit_cost: '' })

  const [submitCreate, creating] = useAction(async () => {
    if (!form.material_id) { toast('اختر المادة', 'error'); return }
    if (!form.lot_number?.trim()) { toast('أدخل رقم الدُفعة', 'error'); return }
    if (!form.qty_received || Number(form.qty_received) <= 0) { toast('أدخل كمية صحيحة', 'error'); return }
    if (form.unit_cost && Number(form.unit_cost) < 0) { toast('سعر الوحدة لا يمكن أن يكون سالباً', 'error'); return }
    if (form.received_date && form.expiry_date && form.expiry_date < form.received_date) {
      toast('تاريخ الصلاحية لازم يكون بعد تاريخ الاستلام', 'error'); return
    }
    await api.batches.create({
      material_id: Number(form.material_id),
      lot_number: form.lot_number.trim(),
      supplier_id: form.supplier_id ? Number(form.supplier_id) : null,
      received_date: form.received_date || today(),
      qty_received: Number(form.qty_received),
      unit_cost: Number(form.unit_cost) || 0,
      expiry_date: form.expiry_date || null,
      notes: form.notes || null,
    })
    toast('تم استلام الدُفعة وتحديث الرصيد')
    setCreateModal(false)
    reload()
  })

  // ── صرف من دُفعة ───────────────────────────────────────────────────
  const [issueModal, setIssueModal] = useState(null)
  const [issueQty, setIssueQty] = useState('')
  const [issueRef, setIssueRef] = useState('')
  const [confirmIssue, setConfirmIssue] = useState(null)   // {batch, qty, reference} قيد التأكيد

  const [submitIssue, issuing] = useAction(async ({ batch, qty, reference }) => {
    try {
      await api.batches.issue(batch.id, { qty: Number(qty), reference: reference || null })
      toast('تم الصرف من الدُفعة')
      setIssueModal(null); setIssueQty(''); setIssueRef('')
      reload()
    } catch (e) {
      toast(e.message || 'تعذّر الصرف', 'error')
    }
  })

  const requestIssue = () => {
    if (!issueQty || Number(issueQty) <= 0) { toast('أدخل كمية صحيحة', 'error'); return }
    if (Number(issueQty) > Number(issueModal.qty_remaining)) {
      toast(`الكمية المطلوبة أكبر من المتبقي في الدُفعة (${issueModal.qty_remaining})`, 'error'); return
    }
    setConfirmIssue({ batch: issueModal, qty: issueQty, reference: issueRef })
  }

  const matOptions = useMemo(
    () => (materials || []).map(m => ({ value: m.id, label: `${m.code ? m.code + ' — ' : ''}${m.name_ar}` })),
    [materials]
  )

  if (loading) return <PageSpinner />

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">📦 دفعات المخزون (Lot Tracking)</h1>
        {write && (
          <button className="btn btn-primary" onClick={() => { resetForm(); setCreateModal(true) }}>
            ➕ استلام دُفعة جديدة
          </button>
        )}
      </div>

      {expiring && expiring.count > 0 && (
        <div className="card p-3" style={{ background: 'var(--red-lt)', borderColor: 'var(--red)' }}>
          <div className="font-semibold text-sm" style={{ color: 'var(--red)' }}>
            ⚠️ {expiring.count} دُفعة قريبة من انتهاء الصلاحية (خلال 30 يوم)
          </div>
        </div>
      )}

      <div className="flex gap-2 items-end">
        <Select label="فلترة بالمادة" value={materialFilter} onChange={e => setMaterialFilter(e.target.value)}
          options={[{ value: '', label: 'كل المواد' }, ...matOptions]} />
        <Select label="الحالة" value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
          options={[
            { value: '', label: 'نشطة (افتراضي)' },
            { value: 'active', label: 'نشطة' },
            { value: 'consumed', label: 'مستهلكة بالكامل' },
            { value: 'expired', label: 'منتهية الصلاحية' },
          ]} />
      </div>

      {trace && trace.system_stock !== trace.total_remaining_batches && (
        <div className="card p-3" style={{ background: 'var(--red-lt)', borderColor: 'var(--red)' }}>
          <div className="font-semibold text-sm" style={{ color: 'var(--red)' }}>
            ⚠️ فرق في تسوية «{trace.material_name}» — رصيد النظام: {trace.system_stock}، إجمالي الدُفعات: {trace.total_remaining_batches}
          </div>
        </div>
      )}
      {trace && trace.system_stock === trace.total_remaining_batches && (
        <div className="text-xs text-erp-muted">✓ رصيد «{trace.material_name}» متطابق مع مجموع دُفعاته</div>
      )}

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table text-sm">
            <thead>
              <tr>
                <th>المادة</th><th>رقم الدُفعة</th><th>الاستلام</th><th>الوارد</th>
                <th>المتبقي</th><th>تكلفة الوحدة</th><th>الصلاحية</th><th>الحالة</th>
                {write && <th></th>}
              </tr>
            </thead>
            <tbody>
              {(batches || []).map(b => (
                <tr key={b.id}>
                  <td>{b.material_name} <span className="text-xs text-erp-muted">({b.material_code})</span></td>
                  <td className="font-mono text-xs">{b.lot_number}</td>
                  <td>{fmtDate(b.received_date)}</td>
                  <td>{b.qty_received} {b.material_unit}</td>
                  <td className="font-semibold">{b.qty_remaining} {b.material_unit}</td>
                  <td>{egp(b.unit_cost)}</td>
                  <td style={{ color: b.is_expired ? 'var(--red)' : undefined }}>
                    {b.expiry_date ? fmtDate(b.expiry_date) : '—'}
                  </td>
                  <td><span className={`badge ${STATUS_BADGE[b.status] || 'badge-gray'}`}>{STATUS_LABELS[b.status] || b.status}</span></td>
                  {write && (
                    <td>
                      {b.status === 'active' && b.qty_remaining > 0 && (
                        <button className="btn btn-sm text-xs" onClick={() => setIssueModal(b)}>📤 صرف</button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
              {(!batches || batches.length === 0) && (
                <tr><td colSpan={write ? 8 : 7} className="text-center text-erp-muted py-4">لا توجد دُفعات</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* مودال استلام دُفعة جديدة */}
      <Modal open={createModal} onClose={() => setCreateModal(false)} title="➕ استلام دُفعة جديدة">
        <div className="flex flex-col gap-3">
          <Select label="المادة" value={form.material_id || ''}
            onChange={e => setForm(f => ({ ...f, material_id: e.target.value }))}
            options={[{ value: '', label: '— اختر —' }, ...matOptions]} />
          <Input label="رقم الدُفعة (Lot)" value={form.lot_number || ''}
            onChange={e => setForm(f => ({ ...f, lot_number: e.target.value }))} />
          <div className="flex gap-2">
            <Input label="تاريخ الاستلام" type="date" value={form.received_date || today()}
              onChange={e => setForm(f => ({ ...f, received_date: e.target.value }))} />
            <Input label="تاريخ الصلاحية (اختياري)" type="date" value={form.expiry_date || ''}
              onChange={e => setForm(f => ({ ...f, expiry_date: e.target.value }))} />
          </div>
          <div className="flex gap-2">
            <Input label="الكمية المستلمة" type="number" step="0.001" value={form.qty_received || ''}
              onChange={e => setForm(f => ({ ...f, qty_received: e.target.value }))} />
            <Input label="تكلفة الوحدة" type="number" step="0.01" value={form.unit_cost || ''}
              onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))} />
          </div>
          <Select label="المورد (اختياري)" value={form.supplier_id || ''}
            onChange={e => setForm(f => ({ ...f, supplier_id: e.target.value }))}
            options={[{ value: '', label: '— بدون —' }, ...(suppliers || []).map(s => ({ value: s.id, label: s.name }))]} />
          <Textarea label="ملاحظات" value={form.notes || ''}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
          <div className="text-xs text-erp-muted">
            هيتم تحديث رصيد المادة العام تلقائيًا وإنشاء حركة "وارد" مربوطة بالدُفعة دي.
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setCreateModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={submitCreate} disabled={creating}>
            {creating ? '…' : '💾 حفظ الاستلام'}
          </button>
        </ModalFooter>
      </Modal>

      {/* مودال الصرف من دُفعة */}
      <Modal open={!!issueModal} onClose={() => setIssueModal(null)} title={`📤 صرف من دُفعة ${issueModal?.lot_number || ''}`}>
        <div className="flex flex-col gap-3">
          <div className="text-sm text-erp-muted">
            المتبقي في الدُفعة: <b>{issueModal?.qty_remaining} {issueModal?.material_unit}</b>
          </div>
          <Input label="الكمية المطلوب صرفها" type="number" step="0.001" value={issueQty}
            onChange={e => setIssueQty(e.target.value)} />
          <Input label="مرجع (اختياري)" value={issueRef} onChange={e => setIssueRef(e.target.value)} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setIssueModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={requestIssue}>📤 صرف</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!confirmIssue}
        title="تأكيد الصرف من الدُفعة"
        message={confirmIssue
          ? `هل تريد صرف ${confirmIssue.qty} ${confirmIssue.batch.material_unit || ''} من دُفعة "${confirmIssue.batch.lot_number}"؟ لا يمكن التراجع عن هذا الإجراء إلا بعكس الحركة يدويًا من شاشة حركات المخزون.`
          : ''}
        confirmLabel={issuing ? '…' : 'تأكيد الصرف'}
        danger
        onConfirm={() => { submitIssue(confirmIssue); setConfirmIssue(null) }}
        onCancel={() => setConfirmIssue(null)}
      />
    </div>
  )
}
