import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp, WO_STATUS, fmtDate, today } from '../utils/fmt'
import { StatusBadge } from '../components/ui/Badge'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import SearchInput from '../components/ui/SearchInput'
import { PageSpinner } from '../components/ui/Spinner'

const STATUS_OPTS = [
  { value: 'draft', label: 'مسودة' },
  { value: 'in_progress', label: 'جاري' },
  { value: 'done', label: 'منتهي' },
  { value: 'cancelled', label: 'ملغي' },
]

export default function WorkOrders() {
  const { canWrite } = useAuth()
  const toast = useToast()
  const write = canWrite('production')

  const { data: wos, loading, reload } = useApi(() => api.accounting.workOrders())
  const { data: eqs } = useApi(() => api.equipment.list())

  const [q, setQ] = useState('')
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState({})

  const filtered = useMemo(() => {
    if (!wos) return []
    const lq = q.toLowerCase()
    return !lq ? wos : wos.filter(w => w.code?.toLowerCase().includes(lq) || w.equipment?.toLowerCase().includes(lq))
  }, [wos, q])

  const openAdd = () => {
    setForm({ status: 'draft', start_date: today(), planned_cost: 0 })
    setModal({ mode: 'add' })
  }

  const openEdit = (item) => { setForm({ ...item }); setModal({ mode: 'edit', item }) }

  const [save, saving] = useAction(async () => {
    if (!form.code) { toast('الكود مطلوب', 'error'); return }
    if (modal?.mode === 'edit') await api.accounting.updateWO(form.id, form)
    else await api.accounting.createWO(form)
    toast('تم الحفظ')
    setModal(null)
    reload()
  })

  const [changeStatus] = useAction(async (id, status) => {
    await api.accounting.updateWOStatus(id, status)
    toast('تم تغيير الحالة')
    reload()
  })

  if (loading) return <PageSpinner />

  return (
    <div className="flex flex-col gap-4">
      <div className="card flex items-center gap-3 py-3">
        <div className="flex-1">
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالكود أو المعدة…" />
        </div>
        {write && <button className="btn btn-primary btn-sm" onClick={openAdd}>+ أمر جديد</button>}
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr>
                <th>الكود</th><th>المعدة</th><th>الحالة</th>
                <th>تاريخ البدء</th><th>التكلفة الفعلية</th><th>التقدم</th>
                {write && <th style={{ width: 120 }}>إجراءات</th>}
              </tr>
            </thead>
            <tbody>
              {!filtered.length && (
                <tr><td colSpan={7} className="text-center text-erp-muted py-10">لا توجد أوامر</td></tr>
              )}
              {filtered.map(wo => {
                const pct = Math.min(100, wo.cost_pct || 0)
                return (
                  <tr key={wo.id}>
                    <td><span className="font-mono text-xs font-medium">{wo.code}</span></td>
                    <td className="font-medium text-sm">{wo.equipment_name_ar || wo.equipment_name || wo.equipment || '—'}</td>
                    <td><StatusBadge map={WO_STATUS} value={wo.status} /></td>
                    <td className="text-erp-muted text-xs">{fmtDate(wo.start_date)}</td>
                    <td className="font-medium text-sm">{egp(wo.actual_cost)}</td>
                    <td>
                      <div className="w-24">
                        <div className="text-[10px] mb-0.5">{pct}%</div>
                        <div className="progress-track">
                          <div className="progress-fill" style={{ width: `${pct}%`, background: pct > 90 ? 'var(--red-md)' : 'var(--teal-md)' }} />
                        </div>
                      </div>
                    </td>
                    {write && (
                      <td>
                        <div className="flex gap-1 flex-wrap">
                          <button className="btn btn-sm text-xs" onClick={() => openEdit(wo)}>✏️</button>
                          <select
                            className="field-select text-xs py-0.5 px-1"
                            value={wo.status}
                            onChange={e => changeStatus(wo.id, e.target.value)}
                          >
                            {STATUS_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                          </select>
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

      <Modal open={!!modal} onClose={() => setModal(null)} title={modal?.mode === 'edit' ? 'تعديل أمر إنتاج' : 'أمر إنتاج جديد'}>
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="الكود *" value={form.code || ''} onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="WO-2025-001" />
            <Select label="الحالة" value={form.status || 'draft'} onChange={e => setForm(f => ({ ...f, status: e.target.value }))} options={STATUS_OPTS} />
          </FormGrid>
          <FormGrid cols={2}>
            <Select
              label="المعدة"
              value={form.equipment_id || ''}
              onChange={e => setForm(f => ({ ...f, equipment_id: +e.target.value }))}
            >
              <option value="">— اختر معدة —</option>
              {eqs?.map(eq => <option key={eq.id} value={eq.id}>{eq.name_ar} ({eq.code})</option>)}
            </Select>
            <Input label="تاريخ البدء" type="date" value={form.start_date || today()} onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} />
          </FormGrid>
          <Textarea label="الوصف" value={form.description || ''} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
