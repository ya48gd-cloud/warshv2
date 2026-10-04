/**
 * المخازن المنطقية (Warehouses) — محطة 10
 * الـ8 مخازن الأساسية بتتعمل تلقائيًا لكل ورشة، ينفع تضيف مخازن مخصّصة كمان.
 */
import { useState } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import Modal, { ModalFooter } from '../components/ui/Modal'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { Input, Select } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'
import { egp } from '../utils/fmt'

const TYPE_LABELS = {
  raw: 'خامات', spare: 'قطع غيار', imported: 'معدات مستوردة', wip: 'تحت التصنيع',
  finished_goods: 'منتج تام', remnant: 'نسالة', scrap: 'هالك', returns: 'مرتجعات', general: 'عام',
}

export default function Warehouses() {
  const { canWrite } = useAuth()
  const write = canWrite('inventory')
  const toast = useToast()

  const [includeInactive, setIncludeInactive] = useState(false)
  const { data: warehouses, loading, reload } = useApi(
    () => api.warehouses.list(includeInactive), [includeInactive]
  )
  // رصيد كل مخزن: عدد الأصناف + القيمة الإجمالية (كمية × سعر الوحدة)
  const { data: summary, reload: reloadSummary } = useApi(
    () => api.warehouses.stockSummary(includeInactive).catch(() => []), [includeInactive]
  )
  const summaryById = Object.fromEntries((summary || []).map(s => [s.id, s]))
  const grandValue = (summary || []).reduce((t, s) => t + Number(s.total_value || 0), 0)

  // عرض أرصدة مخزن معيّن
  const [stockWh, setStockWh] = useState(null)
  const { data: whStock, loading: whStockLoading } = useApi(
    () => stockWh ? api.warehouses.stock(stockWh.id) : Promise.resolve(null), [stockWh?.id]
  )

  const [modal, setModal] = useState(null)   // null | 'new' | warehouse object (edit)
  const [form, setForm] = useState({})
  const [confirmDelete, setConfirmDelete] = useState(null)   // warehouse object قيد الحذف

  const openNew = () => { setForm({ code: '', name_ar: '', warehouse_type: 'general' }); setModal('new') }
  const openEdit = (w) => { setForm({ code: w.code, name_ar: w.name_ar, warehouse_type: w.warehouse_type }); setModal(w) }

  const [submit, submitting] = useAction(async () => {
    if (!form.code?.trim() || !form.name_ar?.trim()) { toast('أدخل الكود والاسم', 'error'); return }
    try {
      if (modal === 'new') {
        await api.warehouses.create({ ...form, code: form.code.trim(), name_ar: form.name_ar.trim() })
        toast('تم إنشاء المخزن')
      } else {
        await api.warehouses.update(modal.id, { ...form, code: form.code.trim(), name_ar: form.name_ar.trim() })
        toast('تم تحديث المخزن')
      }
      setModal(null)
      reload(); reloadSummary()
    } catch (e) {
      toast(e.message || 'تعذّرت العملية', 'error')
    }
  })

  const [remove, removing] = useAction(async (w) => {
    try {
      await api.warehouses.remove(w.id)
      toast('تم حذف المخزن')
      reload(); reloadSummary()
    } catch (e) {
      toast(e.message || 'تعذّر الحذف', 'error')
    }
  })

  const [togglingId, setTogglingId] = useState(null)   // منع الضغط المزدوج على زر التفعيل/التعطيل
  const toggle = async (w) => {
    if (togglingId) return
    setTogglingId(w.id)
    try {
      await api.warehouses.toggleActive(w.id)
      toast(w.is_active ? 'تم تعطيل المخزن' : 'تم تفعيل المخزن')
      reload(); reloadSummary()
    } catch (e) {
      toast(e.message || 'تعذّر التغيير', 'error')
    } finally {
      setTogglingId(null)
    }
  }

  if (loading && !warehouses) return <PageSpinner />

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">🏬 المخازن المنطقية</h1>
        {write && <button className="btn btn-primary" onClick={openNew}>➕ مخزن جديد</button>}
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={includeInactive} onChange={e => setIncludeInactive(e.target.checked)} />
        عرض المخازن المعطّلة كمان
      </label>

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table text-sm">
            <thead>
              <tr><th>الكود</th><th>الاسم</th><th>النوع</th><th>عدد الأصناف</th><th>قيمة المخزون</th><th>أساسي؟</th><th>الحالة</th>{write && <th></th>}</tr>
            </thead>
            <tbody>
              {(warehouses || []).map(w => (
                <tr key={w.id}>
                  <td className="font-mono text-xs">{w.code}</td>
                  <td>
                    <button className="font-medium hover:underline" style={{ color: 'var(--teal)' }}
                      title="عرض أرصدة المخزن" onClick={() => setStockWh(w)}>{w.name_ar}</button>
                  </td>
                  <td>{TYPE_LABELS[w.warehouse_type] || w.warehouse_type}</td>
                  <td>
                    <button className="btn btn-sm text-xs" title="عرض أرصدة المخزن" onClick={() => setStockWh(w)}>
                      📦 {summaryById[w.id]?.items_count ?? '…'} صنف
                    </button>
                  </td>
                  <td className="font-mono text-xs">{summaryById[w.id] ? egp(summaryById[w.id].total_value) : '…'}</td>
                  <td>{w.is_default ? <span className="badge badge-gray">أساسي</span> : '—'}</td>
                  <td>
                    <span className={`badge ${w.is_active ? 'badge-green' : 'badge-red'}`}>
                      {w.is_active ? 'نشط' : 'معطّل'}
                    </span>
                  </td>
                  {write && (
                    <td className="flex gap-1">
                      <button className="btn btn-sm text-xs" title="تعديل" onClick={() => openEdit(w)}>✏️</button>
                      <button className="btn btn-sm text-xs" title={w.is_active ? 'تعطيل' : 'تفعيل'} disabled={togglingId === w.id} onClick={() => toggle(w)}>
                        {w.is_active ? '⏸️' : '▶️'}
                      </button>
                      {!w.is_default && (
                        <button className="btn btn-sm text-xs btn-danger" title="حذف"
                          onClick={() => setConfirmDelete(w)} disabled={removing}>🗑️</button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
              {(!warehouses || warehouses.length === 0) && (
                <tr><td colSpan={write ? 8 : 7} className="text-center text-erp-muted py-4">لا توجد مخازن</td></tr>
              )}
              {(warehouses || []).length > 0 && summary && (
                <tr style={{ background: 'var(--bg)' }}>
                  <td colSpan={4} className="font-semibold text-xs">إجمالي قيمة المخزون في كل المخازن</td>
                  <td className="font-mono text-xs font-bold">{egp(grandValue)}</td>
                  <td colSpan={write ? 3 : 2}></td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <ConfirmDialog
        open={!!confirmDelete}
        title="تأكيد حذف المخزن"
        message={confirmDelete ? `هل تريد حذف مخزن "${confirmDelete.name_ar}" (${confirmDelete.code}) نهائيًا؟ لا يمكن التراجع عن هذا الإجراء.` : ''}
        confirmLabel="حذف نهائي"
        danger
        onConfirm={() => { remove(confirmDelete); setConfirmDelete(null) }}
        onCancel={() => setConfirmDelete(null)}
      />

      <Modal open={!!stockWh} onClose={() => setStockWh(null)} size="lg"
        title={`📦 أرصدة ${stockWh?.name_ar || ''}`}>
        {whStockLoading || !whStock ? <PageSpinner /> : (
          <div className="flex flex-col gap-3">
            <div className="flex gap-4 text-sm">
              <span>عدد الأصناف: <b>{whStock.items_count}</b></span>
              <span>القيمة الإجمالية: <b>{egp(whStock.total_value)}</b></span>
            </div>
            <div className="overflow-x-auto" style={{ maxHeight: '60vh' }}>
              <table className="erp-table text-sm">
                <thead>
                  <tr><th>الكود</th><th>المادة</th><th>الكمية</th><th>الوحدة</th><th>سعر الوحدة</th><th>القيمة</th></tr>
                </thead>
                <tbody>
                  {whStock.items.map(i => (
                    <tr key={i.material_id}>
                      <td className="font-mono text-xs">{i.code}</td>
                      <td>{i.name_ar}</td>
                      <td className="font-mono font-semibold" style={{ color: i.qty < 0 ? 'var(--red)' : undefined }}>{Number(i.qty).toFixed(3)}</td>
                      <td className="text-xs text-erp-muted">{i.unit}</td>
                      <td className="text-xs">{egp(i.unit_cost)}</td>
                      <td className="font-mono text-xs">{egp(i.value)}</td>
                    </tr>
                  ))}
                  {whStock.items.length === 0 && (
                    <tr><td colSpan={6} className="text-center text-erp-muted py-6">لا يوجد رصيد في هذا المخزن</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <ModalFooter>
          <button className="btn" onClick={() => setStockWh(null)}>إغلاق</button>
        </ModalFooter>
      </Modal>

      <Modal open={!!modal} onClose={() => setModal(null)} title={modal === 'new' ? '➕ مخزن جديد' : `✏️ تعديل ${modal?.name_ar || ''}`}>
        <div className="flex flex-col gap-3">
          <Input label="الكود" value={form.code || ''} disabled={modal !== 'new' && modal?.is_default}
            onChange={e => setForm(f => ({ ...f, code: e.target.value.toUpperCase() }))} />
          <Input label="الاسم" value={form.name_ar || ''}
            onChange={e => setForm(f => ({ ...f, name_ar: e.target.value }))} />
          <Select label="النوع" value={form.warehouse_type || 'general'}
            onChange={e => setForm(f => ({ ...f, warehouse_type: e.target.value }))}>
            {Object.entries(TYPE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={submit} disabled={submitting}>
            {submitting ? '…' : '💾 حفظ'}
          </button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
