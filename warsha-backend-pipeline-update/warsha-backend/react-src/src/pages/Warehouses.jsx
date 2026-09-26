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

  const [modal, setModal] = useState(null)   // null | 'new' | warehouse object (edit)
  const [form, setForm] = useState({})
  const [confirmDelete, setConfirmDelete] = useState(null)   // warehouse object قيد الحذف

  const openNew = () => { setForm({ code: '', name_ar: '', warehouse_type: 'general' }); setModal('new') }
  const openEdit = (w) => { setForm({ code: w.code, name_ar: w.name_ar, warehouse_type: w.warehouse_type }); setModal(w) }

  const [submit, submitting] = useAction(async () => {
    if (!form.code?.trim() || !form.name_ar?.trim()) { toast('أدخل الكود والاسم', 'error'); return }
    try {
      if (modal === 'new') {
        await api.warehouses.create(form)
        toast('تم إنشاء المخزن')
      } else {
        await api.warehouses.update(modal.id, form)
        toast('تم تحديث المخزن')
      }
      setModal(null)
      reload()
    } catch (e) {
      toast(e.message || 'تعذّرت العملية', 'error')
    }
  })

  const [remove, removing] = useAction(async (w) => {
    try {
      await api.warehouses.remove(w.id)
      toast('تم حذف المخزن')
      reload()
    } catch (e) {
      toast(e.message || 'تعذّر الحذف', 'error')
    }
  })

  const toggle = async (w) => {
    try {
      await api.warehouses.toggleActive(w.id)
      reload()
    } catch (e) {
      toast(e.message || 'تعذّر التغيير', 'error')
    }
  }

  if (loading) return <PageSpinner />

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
              <tr><th>الكود</th><th>الاسم</th><th>النوع</th><th>أساسي؟</th><th>الحالة</th>{write && <th></th>}</tr>
            </thead>
            <tbody>
              {(warehouses || []).map(w => (
                <tr key={w.id}>
                  <td className="font-mono text-xs">{w.code}</td>
                  <td>{w.name_ar}</td>
                  <td>{TYPE_LABELS[w.warehouse_type] || w.warehouse_type}</td>
                  <td>{w.is_default ? <span className="badge badge-gray">أساسي</span> : '—'}</td>
                  <td>
                    <span className={`badge ${w.is_active ? 'badge-green' : 'badge-red'}`}>
                      {w.is_active ? 'نشط' : 'معطّل'}
                    </span>
                  </td>
                  {write && (
                    <td className="flex gap-1">
                      <button className="btn btn-sm text-xs" title="تعديل" onClick={() => openEdit(w)}>✏️</button>
                      <button className="btn btn-sm text-xs" title={w.is_active ? 'تعطيل' : 'تفعيل'} onClick={() => toggle(w)}>
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
                <tr><td colSpan={write ? 6 : 5} className="text-center text-erp-muted py-4">لا توجد مخازن</td></tr>
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
