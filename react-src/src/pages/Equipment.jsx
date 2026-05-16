/**
 * Equipment & Structure — شجرة المعدات والبنية
 * Split layout: يمين شجرة، يسار تفاصيل + tabs (المواصفات/المقاسات/BOM/CAD)
 */
import { useState, useCallback } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

const LEVEL_LABELS = { 0: 'L0', 1: 'L1', 2: 'L2', 3: 'L3', 4: 'L4' }
const LEVEL_COLORS = {
  0: { bg: '#1D9E75', color: '#fff' },
  1: { bg: '#E6F1FB', color: '#185FA5' },
  2: { bg: '#FAEEDA', color: '#854F0B' },
  3: { bg: '#FCEBEB', color: '#A32D2D' },
  4: { bg: '#f3f4f6', color: '#6b7280' },
}

// ── Tree node ──────────────────────────────────────────────────
function TreeNode({ node, selected, onSelect, onAddChild, onEdit, onDelete, write }) {
  const lv = node.level ?? 0
  const lc = LEVEL_COLORS[lv] || LEVEL_COLORS[4]
  const indent = lv * 18
  const hasCost = node.bom_total_cost && Number(node.bom_total_cost) > 0

  return (
    <>
      <div
        className={`flex items-center justify-between px-3 py-2 cursor-pointer border-b border-erp-border transition-colors ${selected?.id === node.id ? 'bg-teal-lt' : 'hover:bg-erp'}`}
        style={{ paddingRight: `${12 + indent}px` }}
        onClick={() => onSelect(node)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="inline-flex items-center justify-center text-[10px] font-bold px-1.5 py-0.5 rounded flex-shrink-0"
                style={{ background: lc.bg, color: lc.color, minWidth: 24 }}>
            {LEVEL_LABELS[lv] ?? `L${lv}`}
          </span>
          <div className="min-w-0">
            <div className="font-medium text-sm truncate">{node.name_ar}</div>
            <div className="font-mono text-[10px] text-erp-muted">{node.code}</div>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {hasCost && (
            <span className="text-xs font-semibold" style={{ color: 'var(--teal)' }}>
              ج {Number(node.bom_total_cost).toLocaleString()}
            </span>
          )}
          {write && (
            <div className="flex gap-1" onClick={e => e.stopPropagation()}>
              <button className="btn btn-sm text-[11px]" onClick={() => onAddChild(node)}>+ فرعي</button>
              <button className="btn btn-sm text-[11px]" onClick={() => onEdit(node)}>تعديل</button>
              <button className="btn btn-sm btn-danger text-[11px]" onClick={() => onDelete(node)}>حذف</button>
            </div>
          )}
        </div>
      </div>
      {node.children?.map(child => (
        <TreeNode key={child.id} node={child} selected={selected}
          onSelect={onSelect} onAddChild={onAddChild} onEdit={onEdit}
          onDelete={onDelete} write={write} />
      ))}
    </>
  )
}

// ── BOM tab ────────────────────────────────────────────────────
function BOMTab({ eq, write }) {
  const toast = useToast()
  const { data: bomData, loading, reload } = useApi(
    () => api.get(`/equipment/${eq.id}/bom/cost`),
    [eq.id]
  )
  const { data: materials } = useApi(() => api.inventory.materials())
  const [form, setForm] = useState({ qty: '1', unit_cost: '100', notes: '' })

  const [addLine, adding] = useAction(async () => {
    if (!form.material_id) { toast('اختر المادة', 'error'); return }
    await api.post('/equipment/bom', {
      equipment_id: eq.id,
      material_id: Number(form.material_id),
      qty: Number(form.qty),
      unit_cost: Number(form.unit_cost),
      notes: form.notes || null,
    })
    toast('تمت الإضافة')
    setForm({ qty: '1', unit_cost: '100', notes: '' })
    reload()
  })

  const [delLine] = useAction(async (id) => {
    await api.delete(`/equipment/bom/${id}`)
    toast('تم الحذف')
    reload()
  })

  if (loading) return <div className="p-6 text-center text-erp-muted animate-pulse">جاري التحميل…</div>

  const lines = bomData?.lines || []
  const totalCost = bomData?.total_cost || 0

  return (
    <div className="flex flex-col gap-4">
      {write && (
        <div className="card" style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
          <div className="text-sm font-semibold mb-3" style={{ color: 'var(--teal)' }}>+ إضافة سطر BOM</div>
          <div className="grid gap-3" style={{ gridTemplateColumns: '2fr 1fr 1fr 2fr auto' }}>
            <select className="field-select text-sm" value={form.material_id || ''}
              onChange={e => setForm(f => ({ ...f, material_id: e.target.value, unit_cost: materials?.find(m => m.id == e.target.value)?.unit_cost || f.unit_cost }))}>
              <option value="">— اختر المادة —</option>
              {materials?.map(m => <option key={m.id} value={m.id}>{m.name_ar} ({m.code})</option>)}
            </select>
            <input className="field-input text-sm" type="number" step="0.001" placeholder="الكمية"
              value={form.qty} onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} />
            <input className="field-input text-sm" type="number" step="0.01" placeholder="سعر الوحدة (ج)"
              value={form.unit_cost} onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))} />
            <input className="field-input text-sm" placeholder="ملاحظات (اختياري)"
              value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
            <button className="btn btn-primary btn-sm" onClick={addLine} disabled={adding}>
              {adding ? '…' : 'حفظ سطر BOM'}
            </button>
          </div>
        </div>
      )}

      {totalCost > 0 && (
        <div className="flex items-center justify-between px-4 py-3 rounded-erp"
             style={{ background: 'var(--teal-lt)' }}>
          <span className="text-sm text-erp-muted">تكلفة المواد الإجمالية</span>
          <span className="text-xl font-bold" style={{ color: 'var(--teal)' }}>{egp(totalCost)}</span>
        </div>
      )}

      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr>
              <th>المادة</th><th>الكود</th><th>الكمية</th><th>الوحدة</th>
              <th>سعر الوحدة</th><th>الإجمالي</th><th>ملاحظات</th>
              {write && <th>حذف</th>}
            </tr>
          </thead>
          <tbody>
            {!lines.length && (
              <tr><td colSpan={8} className="text-center text-erp-muted py-8">BOM فارغ — أضف مواد</td></tr>
            )}
            {lines.map(l => (
              <tr key={l.id}>
                <td className="font-medium">{l.material?.name_ar || '—'}</td>
                <td><span className="font-mono text-xs bg-erp px-1.5 py-0.5 rounded">{l.material?.code}</span></td>
                <td className="font-semibold">{Number(l.qty).toFixed(2)}</td>
                <td className="text-erp-muted text-xs">{l.material?.unit}</td>
                <td className="text-sm">{egp(l.unit_cost)}</td>
                <td className="font-semibold" style={{ color: 'var(--teal)' }}>{egp(l.total_cost)}</td>
                <td className="text-erp-muted text-xs">{l.notes || '—'}</td>
                {write && (
                  <td><button className="btn btn-sm btn-danger text-xs" onClick={() => delLine(l.id)}>🗑</button></td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Dimensions tab ─────────────────────────────────────────────
function DimensionsTab({ eq, write }) {
  const toast = useToast()
  const { data, loading, reload } = useApi(() => api.get(`/equipment/${eq.id}`), [eq.id])
  const [form, setForm] = useState({ dim_key: '', dim_value: '', unit: 'mm' })

  const [addDim, adding] = useAction(async () => {
    if (!form.dim_key || !form.dim_value) { toast('اسم وقيمة المقاس مطلوبان', 'error'); return }
    await api.post(`/equipment/${eq.id}/dimensions?dim_key=${encodeURIComponent(form.dim_key)}&dim_value=${encodeURIComponent(form.dim_value)}&unit=${encodeURIComponent(form.unit)}`, {})
    toast('تمت الإضافة')
    setForm({ dim_key: '', dim_value: '', unit: 'mm' })
    reload()
  })

  const dims = data?.dimensions || []

  return (
    <div className="flex flex-col gap-4">
      {write && (
        <div className="card">
          <div className="text-sm font-semibold mb-3">+ إضافة مقاس</div>
          <div className="flex gap-2">
            <input className="field-input flex-1" placeholder="اسم المقاس (مثال: الطول)" value={form.dim_key}
              onChange={e => setForm(f => ({ ...f, dim_key: e.target.value }))} />
            <input className="field-input flex-1" placeholder="القيمة" value={form.dim_value}
              onChange={e => setForm(f => ({ ...f, dim_value: e.target.value }))} />
            <select className="field-select" style={{ width: 80 }} value={form.unit}
              onChange={e => setForm(f => ({ ...f, unit: e.target.value }))}>
              {['mm', 'cm', 'm', 'kg', 'ton', 'kW', 'rpm', 'bar', 'pcs'].map(u => <option key={u}>{u}</option>)}
            </select>
            <button className="btn btn-primary btn-sm" onClick={addDim} disabled={adding}>
              {adding ? '…' : 'إضافة'}
            </button>
          </div>
        </div>
      )}
      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead><tr><th>المقاس</th><th>القيمة</th><th>الوحدة</th></tr></thead>
          <tbody>
            {!dims.length && (
              <tr><td colSpan={3} className="text-center text-erp-muted py-8">لا توجد مقاسات</td></tr>
            )}
            {dims.map(d => (
              <tr key={d.id}>
                <td className="font-medium">{d.dim_key}</td>
                <td className="font-semibold">{d.dim_value}</td>
                <td className="text-erp-muted text-xs">{d.unit}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── CAD Files tab ──────────────────────────────────────────────
function CADTab({ eq }) {
  const toast = useToast()
  const { data: files, reload } = useApi(() => api.get(`/cad/${eq.id}`), [eq.id])
  const [uploading, setUploading] = useState(false)

  const upload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('revision', 'Rev1')
      const token = localStorage.getItem('erp_token')
      const bases = ['/api/v1', 'http://localhost:8000/api/v1']
      let res
      for (const base of bases) {
        try {
          res = await fetch(`${base}/cad/upload/${eq.id}`, {
            method: 'POST', body: fd,
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          })
          if (res.ok) break
        } catch {}
      }
      if (res?.ok) { toast('تم رفع الملف'); reload() }
      else toast('فشل رفع الملف', 'error')
    } finally { setUploading(false); e.target.value = '' }
  }

  const del = async (id) => {
    if (!confirm('حذف الملف؟')) return
    const token = localStorage.getItem('erp_token')
    await fetch(`/api/v1/cad/${id}`, { method:'DELETE', headers:{Authorization:`Bearer ${token}`} })
    reload()
  }

  return (
    <div className="flex flex-col gap-4">
      <label className="card flex flex-col items-center justify-center py-8 gap-2 cursor-pointer"
             style={{ border: '2px dashed var(--border)', background: 'var(--bg)' }}>
        <span className="text-3xl">{uploading ? '⏳' : '📁'}</span>
        <div className="font-medium text-sm">{uploading ? 'جاري الرفع…' : 'اضغط لاختيار ملف CAD'}</div>
        <div className="text-xs text-erp-muted">DWG, DXF, STEP, PDF, PNG — حتى 50MB</div>
        <input type="file" accept=".dwg,.dxf,.step,.stp,.pdf,.png,.jpg" style={{display:'none'}} onChange={upload} disabled={uploading} />
      </label>

      {!files?.length ? (
        <div className="text-center text-xs text-erp-muted">لا توجد ملفات مرفوعة بعد.</div>
      ) : (
        <div className="flex flex-col gap-2">
          {files.map(f => (
            <div key={f.id} className="card-sm flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="text-xl">{f.filename?.endsWith('.pdf') ? '📄' : f.filename?.match(/\.(png|jpg)$/i) ? '🖼' : '📐'}</span>
                <div>
                  <div className="font-medium text-sm">{f.filename}</div>
                  <div className="text-[11px] text-erp-muted">{f.revision} — {(f.file_size/1024).toFixed(0)} KB</div>
                </div>
              </div>
              <div className="flex gap-2">
                <a href={`/api/v1/cad/download/${f.id}`} target="_blank" rel="noreferrer"
                   className="btn btn-sm text-xs">⬇ تنزيل</a>
                {f.filename?.match(/\.(pdf|png|jpg|jpeg)$/i) && (
                  <a href={`/api/v1/cad/download/${f.id}`} target="_blank" rel="noreferrer"
                     className="btn btn-sm text-xs" style={{color:'var(--blue)'}}>👁 معاينة</a>
                )}
                <button className="btn btn-sm btn-danger text-xs" onClick={() => del(f.id)}>🗑</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Detail panel ───────────────────────────────────────────────
function DetailPanel({ eq, write }) {
  const [tab, setTab] = useState('specs')
  const TABS = [
    { id: 'specs', label: 'المواصفات' },
    { id: 'dims',  label: 'المقاسات' },
    { id: 'bom',   label: 'BOM' },
    { id: 'cad',   label: 'CAD ملفات' },
  ]

  return (
    <div className="flex flex-col h-full">
      {/* Tabs */}
      <div className="flex border-b border-erp-border">
        {TABS.map(t => (
          <button key={t.id}
            className="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors"
            style={{
              borderBottomColor: tab === t.id ? 'var(--teal-md)' : 'transparent',
              color: tab === t.id ? 'var(--teal)' : 'var(--muted)',
            }}
            onClick={() => setTab(t.id)}
          >{t.label}</button>
        ))}
      </div>

      <div className="flex-1 overflow-auto p-4">
        {tab === 'specs' && (
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-3">
              {[
                ['الكود', eq.code],
                ['الاسم بالعربي', eq.name_ar],
                ['الاسم بالإنجليزي', eq.name_en],
                ['الوزن', eq.weight_kg ? `kg ${eq.weight_kg}` : '—'],
                ['رقم الرسم', eq.cad_drawing_no || '—'],
                ['المستوى', `L${eq.level ?? 0}`],
              ].map(([label, val]) => (
                <div key={label} className="card-sm">
                  <div className="text-xs text-erp-muted mb-1">{label}</div>
                  <div className="font-semibold text-sm">{val || '—'}</div>
                </div>
              ))}
            </div>
          </div>
        )}
        {tab === 'dims' && <DimensionsTab eq={eq} write={write} />}
        {tab === 'bom'  && <BOMTab eq={eq} write={write} />}
        {tab === 'cad'  && <CADTab eq={eq} />}
      </div>
    </div>
  )
}

// ── Main Equipment page ─────────────────────────────────────────
export default function Equipment() {
  const { canWrite } = useAuth()
  const toast = useToast()
  const write = canWrite('inventory')

  const { data: tree, loading, reload } = useApi(() => api.get('/equipment/tree/all').catch(() =>
    api.equipment.list().then(list => list)
  ))

  const [selected, setSelected] = useState(null)
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState({})
  const [deleteTarget, setDeleteTarget] = useState(null)

  const { data: allEqs } = useApi(() => api.equipment.list())

  const openAdd = () => {
    setForm({ level: 0 })
    setModal({ mode: 'add' })
  }

  const openAddChild = (parent) => {
    setForm({ parent_id: parent.id, level: (parent.level ?? 0) + 1, code: parent.code + '-' })
    setModal({ mode: 'add' })
  }

  const openEdit = (eq) => {
    setForm({ ...eq })
    setModal({ mode: 'edit', item: eq })
  }

  const [save, saving] = useAction(async () => {
    if (!form.name_ar?.trim()) { toast('الاسم بالعربي مطلوب', 'error'); return }
    if (!form.code?.trim())    { toast('الكود مطلوب', 'error'); return }
    const payload = {
      code: form.code, name_ar: form.name_ar, name_en: form.name_en || form.name_ar,
      description: form.description || null,
      parent_id: form.parent_id ? Number(form.parent_id) : null,
      level: Number(form.level ?? 0),
      weight_kg: form.weight_kg ? Number(form.weight_kg) : null,
      cad_drawing_no: form.cad_drawing_no || null,
    }
    if (modal?.mode === 'edit') await api.equipment.update(form.id, payload)
    else await api.equipment.create(payload)
    toast('تم الحفظ')
    setModal(null)
    reload()
  })

  const [doDelete] = useAction(async () => {
    await api.equipment.delete(deleteTarget.id)
    toast('تم الحذف')
    if (selected?.id === deleteTarget.id) setSelected(null)
    setDeleteTarget(null)
    reload()
  })

  if (loading) return <PageSpinner />

  // Normalise tree — backend may return flat list or tree
  const treeNodes = Array.isArray(tree) ? tree : (tree?.children || tree?.items || [])

  return (
    <div className="flex flex-col gap-4">
      {/* top toolbar */}
      <div className="flex justify-end">
        {write && (
          <button className="btn btn-primary btn-sm" onClick={openAdd}>+ إضافة معدة / مجموعة</button>
        )}
      </div>

      {/* Split layout */}
      <div className="grid gap-4" style={{ gridTemplateColumns: '1fr 1fr', minHeight: 540 }}>
        {/* Left: tree */}
        <div className="card p-0 overflow-hidden flex flex-col">
          <div className="px-4 py-2.5 border-b border-erp-border text-sm font-semibold" style={{ background: 'var(--bg)' }}>
            شجرة المعدات
          </div>
          <div className="flex-1 overflow-auto">
            {!treeNodes?.length ? (
              <div className="text-center text-erp-muted py-10 text-sm">لا توجد معدات — أضف أول معدة</div>
            ) : (
              treeNodes.map(node => (
                <TreeNode key={node.id} node={node} selected={selected}
                  onSelect={setSelected} onAddChild={openAddChild}
                  onEdit={openEdit} onDelete={setDeleteTarget} write={write} />
              ))
            )}
          </div>
        </div>

        {/* Right: detail */}
        <div className="card p-0 overflow-hidden flex flex-col">
          {!selected ? (
            <div className="flex flex-col items-center justify-center h-full text-erp-muted py-10 gap-2">
              <span className="text-4xl">⚙️</span>
              <div className="text-sm">اختر معدة</div>
              <div className="text-xs">اضغط على أي معدة في الشجرة</div>
            </div>
          ) : (
            <DetailPanel eq={selected} write={write} />
          )}
        </div>
      </div>

      {/* Add/Edit Modal */}
      <Modal open={!!modal} onClose={() => setModal(null)}
        title={modal?.mode === 'edit' ? 'تعديل معدة / مجموعة' : 'إضافة معدة / مجموعة'}>
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="الكود *" value={form.code || ''} onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="FG-500" />
            <Select label="المستوى" value={form.level ?? 0} onChange={e => setForm(f => ({ ...f, level: +e.target.value }))}
              options={[0,1,2,3,4].map(n => ({ value: n, label: `L${n} — ${['مجموعة رئيسية','وحدة','مكوّن','قطعة فرعية','قطعة دقيقة'][n]}` }))} />
          </FormGrid>
          <Input label="الاسم بالعربي *" value={form.name_ar || ''} onChange={e => setForm(f => ({ ...f, name_ar: e.target.value }))} placeholder="مجرشة أعلاف 5 طن/ساعة" />
          <Input label="الاسم بالإنجليزي" value={form.name_en || ''} onChange={e => setForm(f => ({ ...f, name_en: e.target.value }))} placeholder="5 Ton/hr Feed Crusher" />
          <FormGrid cols={2}>
            <Input label="الوزن (كجم)" type="number" step="0.001" value={form.weight_kg || ''} onChange={e => setForm(f => ({ ...f, weight_kg: e.target.value }))} placeholder="2800" />
            <Input label="رقم رسم CAD" value={form.cad_drawing_no || ''} onChange={e => setForm(f => ({ ...f, cad_drawing_no: e.target.value }))} placeholder="DWG-FG500-Rev2" />
          </FormGrid>
          {form.level > 0 && (
            <Select label="المعدة الأم"
              value={form.parent_id || ''}
              onChange={e => setForm(f => ({ ...f, parent_id: e.target.value ? +e.target.value : null }))}>
              <option value="">— بدون أم (جذر) —</option>
              {allEqs?.filter(e => e.id !== form.id).map(e => (
                <option key={e.id} value={e.id}>L{e.level} {e.code} — {e.name_ar}</option>
              ))}
            </Select>
          )}
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ المعدة'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog open={!!deleteTarget} onClose={() => setDeleteTarget(null)} onConfirm={doDelete}
        title="حذف معدة" message={`حذف "${deleteTarget?.name_ar}" وكل مكوناتها وملفات CAD؟`} danger />
    </div>
  )
}
