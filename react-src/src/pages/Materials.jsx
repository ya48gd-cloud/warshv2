/**
 * Materials — المواد والخامات
 * Supports plate/steel materials with density tracking
 */
import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp } from '../utils/fmt'
import SearchInput from '../components/ui/SearchInput'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, FormGrid } from '../components/ui/Field'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

const UNITS = ['كجم', 'طن', 'متر', 'م²', 'م³', 'عدد', 'لتر', 'مجموعة']
const MATERIAL_KINDS = [
  { value: 'general', label: 'عام' },
  { value: 'plate', label: 'ألواح' },
  { value: 'bar', label: 'حديد / سيخ' },
  { value: 'consumable', label: 'مستهلكات' },
]

const kindLabel = (kind, isPlate) =>
  MATERIAL_KINDS.find(k => k.value === (kind || (isPlate ? 'plate' : 'general')))?.label || 'عام'

export default function Materials() {
  const { canWrite } = useAuth()
  const toast = useToast()
  const write = canWrite('inventory')

  const { data: materials, loading, reload } = useApi(() => api.inventory.materials())
  const { data: categories } = useApi(() => api.inventory.categories().catch(() => []))

  const [q, setQ] = useState('')
  const [filterLow, setFilterLow] = useState(false)
  const [modal, setModal] = useState(null)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [form, setForm] = useState({})

  const filtered = useMemo(() => {
    if (!materials) return []
    const lq = q.toLowerCase()
    let list = materials
    if (filterLow) list = list.filter(m => Number(m.stock_qty) <= Number(m.reorder_level))
    if (lq) list = list.filter(m =>
      m.name_ar?.toLowerCase().includes(lq) ||
      m.name_en?.toLowerCase().includes(lq) ||
      m.code?.toLowerCase().includes(lq)
    )
    return list
  }, [materials, q, filterLow])

  const lowCount = useMemo(
    () => (materials || []).filter(m => Number(m.stock_qty) <= Number(m.reorder_level)).length,
    [materials]
  )

  const getCatName = (m) => {
    const cat = categories?.find(c => c.id === m.category_id)
    return cat?.name_ar || m.category?.name_ar || '—'
  }

  const openAdd  = () => { setForm({ unit: 'كجم', reorder_level: 0, material_kind: 'general', is_plate: false }); setModal({ mode: 'add' }) }
  const openEdit = (item) => { setForm({ ...item }); setModal({ mode: 'edit', item }) }

  const [save, saving] = useAction(async () => {
    if (!form.name_ar?.trim()) { toast('الاسم بالعربي مطلوب', 'error'); return }
    if (!form.code?.trim())    { toast('الكود مطلوب',          'error'); return }
    if (!form.category_id)     { toast('اختر الفئة',           'error'); return }

    const payload = {
      code:          form.code,
      name_ar:       form.name_ar,
      name_en:       form.name_en || form.name_ar,
      unit:          form.unit || 'كجم',
      unit_cost:     Number(form.unit_cost || 0),
      reorder_level: Number(form.reorder_level || 0),
      category_id:   Number(form.category_id),
      supplier:      form.supplier || null,
      material_kind: form.material_kind || (form.is_plate ? 'plate' : 'general'),
      is_plate:      form.material_kind === 'plate' || !!form.is_plate,
      thickness_mm:  form.material_kind === 'plate' && form.thickness_mm ? Number(form.thickness_mm) : null,
      plate_length_cm: form.material_kind === 'plate' && form.plate_length_cm ? Number(form.plate_length_cm) : null,
      plate_width_cm:  form.material_kind === 'plate' && form.plate_width_cm ? Number(form.plate_width_cm) : null,
      plate_weight_kg: form.material_kind === 'plate' && form.plate_weight_kg ? Number(form.plate_weight_kg) : null,
      bar_length_cm: form.material_kind === 'bar' && form.bar_length_cm ? Number(form.bar_length_cm) : null,
      bar_weight_kg: form.material_kind === 'bar' && form.bar_weight_kg ? Number(form.bar_weight_kg) : null,
    }
    if (modal?.mode === 'edit') await api.inventory.updateMat(form.id, payload)
    else await api.inventory.createMat(payload)
    toast(modal?.mode === 'edit' ? 'تم التحديث' : 'تمت الإضافة')
    setModal(null)
    reload()
  })

  const [doDelete] = useAction(async () => {
    await api.inventory.deleteMat(deleteTarget.id)
    toast('تم الحذف')
    setDeleteTarget(null)
    reload()
  })

  if (loading) return <PageSpinner />

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div className="flex-1 min-w-48">
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالكود أو الاسم…" />
        </div>
        <button
          className="btn btn-sm"
          style={filterLow ? { background: 'var(--red-lt)', color: 'var(--red)', borderColor: 'var(--red-md)' } : {}}
          onClick={() => setFilterLow(f => !f)}
        >
          ⚠ منخفض ({lowCount})
        </button>
        <div className="text-xs text-erp-muted">{filtered.length} مادة</div>
        {write && <button className="btn btn-primary btn-sm" onClick={openAdd}>+ مادة جديدة</button>}
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr>
                <th>الكود</th>
                <th>الاسم بالعربي</th>
                <th>الفئة</th>
                <th>الوحدة</th>
                <th>المخزون</th>
                <th>حد الطلب</th>
                <th>سعر الوحدة</th>
                <th>النوع</th>
                <th>بيانات الوزن</th>
                {write && <th style={{ width: 90 }}>إجراءات</th>}
              </tr>
            </thead>
            <tbody>
              {!filtered.length && (
                <tr><td colSpan={10} className="text-center text-erp-muted py-10">
                  {q || filterLow ? 'لا توجد نتائج' : 'لا توجد مواد — أضف أول مادة'}
                </td></tr>
              )}
              {filtered.map(m => {
                const low = Number(m.stock_qty) <= Number(m.reorder_level)
                return (
                  <tr key={m.id}>
                    <td>
                      <span className="font-mono text-xs bg-erp px-1.5 py-0.5 rounded">{m.code}</span>
                      {m.is_plate && <span className="badge badge-blue mr-1 text-[10px]">لوح {m.thickness_mm || '?'}mm</span>}
                    </td>
                    <td>
                      <div className="font-medium">{m.name_ar}</div>
                      {m.name_en && m.name_en !== m.name_ar && (
                        <div className="text-[11px] text-erp-muted">{m.name_en}</div>
                      )}
                    </td>
                    <td className="text-erp-muted text-xs">{getCatName(m)}</td>
                    <td className="text-erp-muted text-xs">{m.unit}</td>
                    <td>
                      <span className="font-semibold" style={{ color: low ? 'var(--red)' : 'var(--text)' }}>
                        {Number(m.stock_qty || 0).toFixed(3)}
                      </span>
                      {low && <span className="badge badge-red mr-1 text-[10px]">منخفض</span>}
                    </td>
                    <td className="text-erp-muted text-xs">{Number(m.reorder_level || 0).toFixed(2)}</td>
                    <td className="text-sm">{egp(m.unit_cost)}</td>
                    <td className="text-xs">
                      <span className="badge badge-blue text-[10px]">{kindLabel(m.material_kind, m.is_plate)}</span>
                    </td>
                    <td className="text-xs">
                      {m.material_kind === 'bar' && m.weight_per_meter_kg ? (
                        <span className="font-semibold" style={{ color: 'var(--teal)' }}>
                          {Number(m.weight_per_meter_kg).toFixed(3)} كجم/م
                        </span>
                      ) : m.density_kg_m2 ? (
                        <span className="font-semibold" style={{ color: 'var(--blue)' }}>
                          {Number(m.density_kg_m2).toFixed(2)} كجم/م²
                        </span>
                      ) : <span className="text-erp-muted">—</span>}
                    </td>
                    {write && (
                      <td>
                        <div className="flex gap-1">
                          <button className="btn btn-sm text-xs" onClick={() => openEdit(m)}>✏️</button>
                          <button className="btn btn-sm btn-danger text-xs" onClick={() => setDeleteTarget(m)}>🗑</button>
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

      {/* Add/Edit Modal */}
      <Modal open={!!modal} onClose={() => setModal(null)}
        title={modal?.mode === 'edit' ? 'تعديل مادة' : 'مادة جديدة'}>
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="الكود *" value={form.code || ''}
              onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="ST-37-6MM" />
            <Select label="الفئة *" value={form.category_id || ''}
              onChange={e => setForm(f => ({ ...f, category_id: +e.target.value }))}>
              <option value="">— اختر فئة —</option>
              {(categories || []).map(c => <option key={c.id} value={c.id}>{c.name_ar}</option>)}
            </Select>
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="الاسم بالعربي *" value={form.name_ar || ''}
              onChange={e => setForm(f => ({ ...f, name_ar: e.target.value }))}
              placeholder="لوح فولاذ ST-37 سماكة 6mm" />
            <Input label="الاسم بالإنجليزي" value={form.name_en || ''}
              onChange={e => setForm(f => ({ ...f, name_en: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={3}>
            <Select label="الوحدة" value={form.unit || 'كجم'}
              onChange={e => setForm(f => ({ ...f, unit: e.target.value }))} options={UNITS} />
            <Input label="حد إعادة الطلب" type="number" step="0.001"
              value={form.reorder_level ?? 0}
              onChange={e => setForm(f => ({ ...f, reorder_level: e.target.value }))} />
            <Input label="سعر الوحدة (ج/كجم)" type="number" step="0.01"
              value={form.unit_cost || ''}
              onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))} />
          </FormGrid>
          <Input label="المورد" value={form.supplier || ''}
            onChange={e => setForm(f => ({ ...f, supplier: e.target.value }))} />

          <Select label="نوع الخامة" value={form.material_kind || (form.is_plate ? 'plate' : 'general')}
            onChange={e => setForm(f => ({
              ...f,
              material_kind: e.target.value,
              is_plate: e.target.value === 'plate',
              unit: e.target.value === 'consumable' ? (f.unit || 'عدد') : (f.unit || 'كجم'),
            }))}>
            {MATERIAL_KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
          </Select>

          {/* Steel/plate/bar data */}
          <div className="border border-erp-border rounded-erp p-3">
            <div className="font-medium text-sm mb-2">بيانات الوزن والتقطيع</div>
            {(form.material_kind === 'plate' || form.is_plate) && (
              <div className="flex flex-col gap-2 mt-2">
                <FormGrid cols={2}>
                  <Input label="سماكة اللوح (mm)" type="number" step="0.1"
                    value={form.thickness_mm || ''}
                    onChange={e => setForm(f => ({ ...f, thickness_mm: e.target.value }))}
                    placeholder="مثال: 6" />
                  <Input label="وزن اللوح الكامل (كجم)" type="number" step="0.001"
                    value={form.plate_weight_kg || ''}
                    onChange={e => setForm(f => ({ ...f, plate_weight_kg: e.target.value }))}
                    placeholder="مثال: 50" />
                  <Input label="طول اللوح الكامل (سم)" type="number" step="0.1"
                    value={form.plate_length_cm || ''}
                    onChange={e => setForm(f => ({ ...f, plate_length_cm: e.target.value }))}
                    placeholder="مثال: 600" />
                  <Input label="عرض اللوح الكامل (سم)" type="number" step="0.1"
                    value={form.plate_width_cm || ''}
                    onChange={e => setForm(f => ({ ...f, plate_width_cm: e.target.value }))}
                    placeholder="مثال: 150" />
                  <div>
                    <label className="field-label">الكثافة المحسوبة (كجم/م²)</label>
                    <div className="field-input bg-erp text-erp-muted text-sm">
                      {form.plate_length_cm && form.plate_width_cm && form.plate_weight_kg
                        ? <span style={{ color: 'var(--teal)', fontWeight: 600 }}>
                            {(Number(form.plate_weight_kg) / ((Number(form.plate_length_cm) / 100) * (Number(form.plate_width_cm) / 100))).toFixed(4)}
                          </span>
                        : form.density_kg_m2
                          ? <span style={{ color: 'var(--teal)', fontWeight: 600 }}>{Number(form.density_kg_m2).toFixed(4)}</span>
                          : 'أدخل مقاس ووزن اللوح الكامل'}
                    </div>
                  </div>
                </FormGrid>
                <div className="text-[11px] text-erp-muted px-1">
                  يتم استخدام بيانات اللوح الكامل في الوارد والصادر والـ BOM لحساب الوزن والسعر تلقائياً.
                </div>
              </div>
            )}
            {form.material_kind === 'bar' && (
              <div className="flex flex-col gap-2 mt-2">
                <FormGrid cols={3}>
                  <Input label="طول السيخ الكامل (سم)" type="number" step="0.1"
                    value={form.bar_length_cm || ''}
                    onChange={e => setForm(f => ({ ...f, bar_length_cm: e.target.value }))}
                    placeholder="مثال: 600" />
                  <Input label="وزن السيخ الكامل (كجم)" type="number" step="0.001"
                    value={form.bar_weight_kg || ''}
                    onChange={e => setForm(f => ({ ...f, bar_weight_kg: e.target.value }))}
                    placeholder="مثال: 60" />
                  <div>
                    <label className="field-label">وزن المتر</label>
                    <div className="field-input bg-erp text-sm font-semibold" style={{ color: 'var(--teal)' }}>
                      {form.bar_length_cm && form.bar_weight_kg
                        ? `${(Number(form.bar_weight_kg) / (Number(form.bar_length_cm) / 100)).toFixed(4)} كجم/م`
                        : form.weight_per_meter_kg
                          ? `${Number(form.weight_per_meter_kg).toFixed(4)} كجم/م`
                          : '—'}
                    </div>
                  </div>
                </FormGrid>
                <div className="text-[11px] text-erp-muted px-1">
                  مثال: سيخ 600 سم وزنه 60 كجم = 10 كجم/م، وسحب 50 سم يحسب 5 كجم.
                </div>
              </div>
            )}
            {form.material_kind === 'consumable' && (
              <div className="text-xs text-erp-muted">
                المستهلكات تدخل في التكلفة فقط ولا تُضاف إلى وزن المعدة المحسوب من الـ BOM.
              </div>
            )}
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog open={!!deleteTarget} onClose={() => setDeleteTarget(null)} onConfirm={doDelete}
        title="حذف مادة" message={`حذف "${deleteTarget?.name_ar}"؟`} danger />
    </div>
  )
}
