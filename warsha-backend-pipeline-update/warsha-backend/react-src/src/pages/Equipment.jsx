/**
 * Equipment & Structure — شجرة المعدات والبنية
 * Split layout: يمين شجرة، يسار تفاصيل + tabs (المواصفات/المقاسات/BOM/CAD)
 */
import { useState, useCallback, useEffect } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp, fmtDate } from '../utils/fmt'
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
function TreeNode({ node, selected, onSelect, onAddChild, onEdit, onDelete, onDuplicate, write }) {
  const lv = node.level ?? 0
  const lc = LEVEL_COLORS[lv] || LEVEL_COLORS[4]
  const indent = lv * 18
  const hasCost = node.bom_total_cost && Number(node.bom_total_cost) > 0
  const hasChildren = node.children?.length > 0
  // الفرعيات مطوية تلقائيًا — إلا لو إحدى الفرعيات هي المعدة المختارة حاليًا (نوسّع المسار لها فقط)
  const containsSelected = useCallback(function check(n) {
    if (!n.children) return false
    return n.children.some(c => c.id === selected?.id || check(c))
  }, [selected])
  const [expanded, setExpanded] = useState(() => hasChildren && containsSelected(node))

  return (
    <>
      <div
        className={`flex items-center justify-between px-3 py-2 cursor-pointer border-b border-erp-border transition-colors ${selected?.id === node.id ? 'bg-teal-lt' : 'hover:bg-erp'}`}
        style={{ paddingRight: `${12 + indent}px` }}
        onClick={() => onSelect(node)}
      >
        <div className="flex items-center gap-2 min-w-0">
          {hasChildren && (
            <button
              className="flex-shrink-0 text-erp-muted hover:text-erp-text"
              style={{ width: 16, fontSize: 11, transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}
              onClick={e => { e.stopPropagation(); setExpanded(v => !v) }}
              title={expanded ? 'إخفاء الفرعيات' : `عرض الفرعيات (${node.children.length})`}
            >▶</button>
          )}
          <span className="inline-flex items-center justify-center text-[10px] font-bold px-1.5 py-0.5 rounded flex-shrink-0"
                style={{ background: lc.bg, color: lc.color, minWidth: 24 }}>
            {LEVEL_LABELS[lv] ?? `L${lv}`}
          </span>
          <div className="min-w-0">
            <div className="font-medium text-sm truncate">
              {node.name_ar}
              {hasChildren && !expanded && <span className="text-erp-muted text-[11px]"> ({node.children.length} فرعي)</span>}
            </div>
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
              <button className="btn btn-sm text-[11px]" onClick={() => onDuplicate(node)} title="نسخ المعدة مع كود جديد">📋 نسخ</button>
              <button className="btn btn-sm text-[11px]" onClick={() => onEdit(node)}>تعديل</button>
              <button className="btn btn-sm btn-danger text-[11px]" onClick={() => onDelete(node)}>حذف</button>
            </div>
          )}
        </div>
      </div>
      {expanded && node.children?.map(child => (
        <TreeNode key={child.id} node={child} selected={selected}
          onSelect={onSelect} onAddChild={onAddChild} onEdit={onEdit}
          onDelete={onDelete} onDuplicate={onDuplicate} write={write} />
      ))}
    </>
  )
}

// ── Length calculator panel (معدات مترية) ───────────────────────
function LengthCalculatorPanel({ eq }) {
  const min = Number(eq.length_min)
  const max = Number(eq.length_max)
  const [length, setLength] = useState(min)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    if (collapsed) return
    setLoading(true)
    setError(null)
    const t = setTimeout(() => {
      api.equipment.bomParametricLength(eq.id, length)
        .then(res => { setResult(res); setLoading(false) })
        .catch(err => { setError(err?.message || 'تعذر الحساب'); setResult(null); setLoading(false) })
    }, 300)
    return () => clearTimeout(t)
  }, [eq.id, length, collapsed])

  const materialsSummary = result?.materials_summary || []

  return (
    <div className="card" style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
      <div className="flex items-center justify-between mb-3" style={{ cursor: 'pointer' }} onClick={() => setCollapsed(c => !c)}>
        <div className="text-sm font-semibold" style={{ color: 'var(--teal)' }}>
          🧮 حاسبة التكلفة لطول محدد (من {min} إلى {max} م)
        </div>
        <span className="text-xs px-2 py-0.5 rounded" style={{ background: 'rgba(20,184,166,0.15)', color: 'var(--teal)' }}>
          {collapsed ? '▼ عرض' : '▲ طي'}
        </span>
      </div>
      {collapsed && result && (
        <div className="text-xs" style={{ color: 'var(--teal)' }}>
          عند {length}م — إجمالي: <span className="font-bold">{result.total_cost?.toLocaleString()} ج.م</span>
        </div>
      )}
      {!collapsed && (
        <>
          <div className="flex items-center gap-3 mb-3">
            <input type="range" min={min} max={max} step="0.1" value={length}
              onChange={e => setLength(Number(e.target.value))} className="flex-1" />
            <input type="number" min={min} max={max} step="0.1" className="field-input text-sm" style={{ width: 100 }}
              value={length} onChange={e => setLength(Number(e.target.value))} />
            <span className="text-sm text-erp-muted">متر</span>
          </div>

          {error && (
            <div className="text-sm font-semibold p-3 rounded-erp" style={{ background: 'var(--red-lt, #fcebeb)', color: 'var(--red, #a32d2d)' }}>
              {error}
            </div>
          )}

          {!error && result && (
            <>
              <div className="grid grid-cols-3 gap-3 mb-3">
                <div className="card-sm">
                  <div className="text-xs text-erp-muted mb-1">تكلفة المواد</div>
                  <div className="font-semibold text-sm">{egp(result.material_cost)}</div>
                </div>
                <div className="card-sm">
                  <div className="text-xs text-erp-muted mb-1">التكاليف الثابتة</div>
                  <div className="font-semibold text-sm">{egp(result.fixed_cost)}</div>
                </div>
                <div className="card-sm" style={{ background: 'var(--teal-lt)' }}>
                  <div className="text-xs text-erp-muted mb-1">الإجمالي</div>
                  <div className="font-bold text-sm" style={{ color: 'var(--teal)' }}>{egp(result.total_cost)}</div>
                </div>
              </div>

              {materialsSummary.length > 0 && (
                <div className="card p-0 overflow-hidden">
                  <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold" style={{ background: 'var(--bg)' }}>
                    ملخص المواد عند طول {length} م
                  </div>
                  <table className="erp-table">
                    <thead>
                      <tr><th>المادة</th><th>الكود</th><th>الإجمالي المطلوب</th></tr>
                    </thead>
                    <tbody>
                      {materialsSummary.map(m => (
                        <tr key={m.material_id}>
                          <td className="font-medium text-sm">{m.material_name}</td>
                          <td><span className="font-mono text-xs bg-erp px-1.5 py-0.5 rounded">{m.material_code}</span></td>
                          <td className="font-semibold">
                            {m.oversized_warning ? (
                              <span style={{ color: '#B91C1C' }} title={m.oversized_warning}>⚠️ {m.count} {m.unit_label}</span>
                            ) : m.precise ? (
                              <>{m.precise.precise_value} {m.precise.precise_unit}
                                <span className="text-erp-muted font-normal"> (من {m.unit_label} {m.precise.unit_size} {m.precise.precise_unit} — الباقي {m.precise.remainder} {m.precise.precise_unit} نسالة)</span>
                              </>
                            ) : m.whole_count != null ? (
                              <>{m.whole_count} {m.unit_label} كامل
                                {m.fraction_pct > 0 && (
                                  <span className="text-erp-muted font-normal"> + {m.fraction_pct}% من {m.unit_label} إضافي</span>
                                )}
                              </>
                            ) : (
                              <>{m.count} {m.unit_label}</>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
          {loading && <div className="text-xs text-erp-muted mt-2">جاري الحساب…</div>}
        </>
      )}
    </div>
  )
}

// ── BOM tab ────────────────────────────────────────────────────
function BOMAddForm({ eq, materials, onSaved }) {
  const toast = useToast()
  const isMetric = eq.length_min != null && eq.length_max != null
  const [form, setForm] = useState({ qty: '1', unit_cost: '', notes: '', line_type: 'fixed' })

  const selectedMat = materials?.find(m => m.id == form.material_id)
  const isBar   = selectedMat && Number(selectedMat.weight_per_meter_kg) > 0
  const isPlate = selectedMat && Number(selectedMat.density_kg_m2) > 0
  const isVar   = form.line_type === 'variable'

  // preview qty for variable lines
  const previewQty = (L) => {
    if (!isVar) return null
    const l = Number(form.bar_length_cm)
    const w = Number(selectedMat?.weight_per_meter_kg)
    const dl = Number(form.dim_length_cm)
    const dw = Number(form.dim_width_cm)
    const dc = Number(form.dim_count) || 1
    const dens = Number(selectedMat?.density_kg_m2)
    if (isBar && l && w) {
      const barL = form.scale_dim_length ? l * L : l * L
      return ((barL / 100) * w * dc).toFixed(3) + ' ' + (selectedMat?.unit || '')
    }
    if (isPlate && dl && dw && dens) {
      const effL = form.scale_dim_length ? dl * L : dl
      const area = (effL / 100) * (dw / 100) * (dc * L)
      return (area * dens).toFixed(3) + ' كجم'
    }
    return (Number(form.qty) * L).toFixed(3) + ' ' + (selectedMat?.unit || '')
  }

  const [addLine, adding] = useAction(async () => {
    if (!form.material_id) { toast('اختر المادة أولاً', 'error'); return }
    if (!form.unit_cost || Number(form.unit_cost) <= 0) { toast('أدخل سعر الوحدة', 'error'); return }
    const payload = {
      equipment_id: eq.id,
      material_id: Number(form.material_id),
      qty: Number(form.qty) || 1,
      unit_cost: Number(form.unit_cost),
      notes: form.notes || null,
      line_type: form.line_type,
      scale_dim_length: !!form.scale_dim_length,
      dim_length_cm: form.dim_length_cm ? Number(form.dim_length_cm) : null,
      dim_width_cm:  form.dim_width_cm  ? Number(form.dim_width_cm)  : null,
      dim_count:     form.dim_count     ? Number(form.dim_count)     : null,
      bar_length_cm: form.bar_length_cm ? Number(form.bar_length_cm) : null,
    }
    await api.post('/equipment/bom', payload)
    toast('تمت الإضافة')
    setForm({ qty: '1', unit_cost: '', notes: '', line_type: form.line_type })
    onSaved()
  })

  return (
    <div className="card" style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-semibold" style={{ color: 'var(--teal)' }}>+ إضافة سطر BOM</div>
        {isMetric && (
          <div className="text-xs px-2 py-1 rounded" style={{ background: 'rgba(20,184,166,0.15)', color: 'var(--teal)' }}>
            معدة مترية (من {eq.length_min} إلى {eq.length_max} م)
          </div>
        )}
      </div>

      {/* Row 1: material + type */}
      <div className="grid gap-3 mb-3" style={{ gridTemplateColumns: '3fr 1fr' }}>
        <select className="field-select text-sm" value={form.material_id || ''}
          onChange={e => {
            const mat = materials?.find(m => m.id == e.target.value)
            setForm(f => ({ ...f, material_id: e.target.value,
              unit_cost: mat?.unit_cost != null ? String(mat.unit_cost) : f.unit_cost,
              bar_length_cm: '', dim_length_cm: '', dim_width_cm: '', dim_count: '' }))
          }}>
          <option value="">— اختر المادة —</option>
          {materials?.map(m => <option key={m.id} value={m.id}>{m.name_ar} ({m.code}) — {m.unit}</option>)}
        </select>
        <select className="field-select text-sm" value={form.line_type}
          onChange={e => setForm(f => ({ ...f, line_type: e.target.value }))}>
          <option value="fixed">ثابت (لا يتغير)</option>
          {isMetric && <option value="variable">متغير لكل متر ↕</option>}
        </select>
      </div>

      {/* Type hint */}
      {isMetric && (
        <div className="text-xs mb-3 px-2 py-1.5 rounded" style={{
          background: isVar ? 'rgba(20,184,166,0.08)' : 'rgba(0,0,0,0.04)',
          color: isVar ? 'var(--teal)' : 'var(--fg-muted)'
        }}>
          {isVar
            ? '↕ متغير: الكميات/الأبعاد التي تدخلها تمثّل المعدّل لكل متر واحد من طول المعدة — سيتم ضربها في L عند الحساب'
            : '■ ثابت: الكمية لا تتغير بتغيّر طول المعدة (مثال: موتور، بلي، قاعدة)'}
        </div>
      )}

      {/* Fields: bar material */}
      {isBar && (
        <div className="grid gap-3 mb-3" style={{ gridTemplateColumns: isVar ? '1fr 1fr 1fr' : '1fr 1fr' }}>
          <div>
            <div className="text-xs text-erp-muted mb-1">
              {isVar ? 'طول الماسورة/السيخ لكل متر من المعدة (سم)' : 'طول القطعة (سم)'}
            </div>
            <input className="field-input text-sm w-full" type="number" step="0.1"
              placeholder={isVar ? 'مثال: 100 = متر كامل لكل متر L' : 'مثال: 50 = نصف متر'}
              value={form.bar_length_cm || ''} onChange={e => setForm(f => ({ ...f, bar_length_cm: e.target.value }))} />
          </div>
          {isVar && (
            <div>
              <div className="text-xs text-erp-muted mb-1">عدد الأسياخ/المواسير لكل متر</div>
              <input className="field-input text-sm w-full" type="number" step="1" placeholder="1"
                value={form.dim_count || ''} onChange={e => setForm(f => ({ ...f, dim_count: e.target.value }))} />
            </div>
          )}
          <div>
            <div className="text-xs text-erp-muted mb-1">سعر الوحدة (ج.م / كجم)</div>
            <input className="field-input text-sm w-full" type="number" step="0.01"
              value={form.unit_cost} onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))} />
          </div>
        </div>
      )}

      {/* Fields: plate material */}
      {isPlate && !isBar && (
        <div className="grid gap-3 mb-3" style={{ gridTemplateColumns: isVar ? '1fr 1fr 1fr 1fr 1fr' : '1fr 1fr 1fr 1fr' }}>
          <div>
            <div className="text-xs text-erp-muted mb-1">
              {isVar && form.scale_dim_length ? 'طول القطعة (سم/م) — سيُضرب في L' : 'طول القطعة (سم)'}
            </div>
            <input className="field-input text-sm w-full" type="number" step="0.1"
              value={form.dim_length_cm || ''} onChange={e => setForm(f => ({ ...f, dim_length_cm: e.target.value }))} />
          </div>
          <div>
            <div className="text-xs text-erp-muted mb-1">عرض القطعة (سم) — ثابت دائماً</div>
            <input className="field-input text-sm w-full" type="number" step="0.1"
              value={form.dim_width_cm || ''} onChange={e => setForm(f => ({ ...f, dim_width_cm: e.target.value }))} />
          </div>
          <div>
            <div className="text-xs text-erp-muted mb-1">{isVar ? 'عدد القطع لكل متر' : 'عدد القطع'}</div>
            <input className="field-input text-sm w-full" type="number" step="1"
              value={form.dim_count || ''} onChange={e => setForm(f => ({ ...f, dim_count: e.target.value }))} />
          </div>
          <div>
            <div className="text-xs text-erp-muted mb-1">سعر الوحدة (ج.م / كجم)</div>
            <input className="field-input text-sm w-full" type="number" step="0.01"
              value={form.unit_cost} onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))} />
          </div>
          {isVar && (
            <div className="flex flex-col justify-end">
              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <input type="checkbox" checked={!!form.scale_dim_length}
                  onChange={e => setForm(f => ({ ...f, scale_dim_length: e.target.checked }))} />
                <span>طول القطعة = L<br/><span className="text-erp-muted">(هيكل / غطاء)</span></span>
              </label>
            </div>
          )}
        </div>
      )}

      {/* Fields: general material (not bar, not plate) */}
      {!isBar && !isPlate && (
        <div className="grid gap-3 mb-3" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
          <div>
            <div className="text-xs text-erp-muted mb-1">
              {isVar ? 'الكمية لكل متر واحد من طول المعدة' : 'الكمية الثابتة'}
            </div>
            <input className="field-input text-sm w-full" type="number" step="0.001"
              value={form.qty} onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} />
          </div>
          <div>
            <div className="text-xs text-erp-muted mb-1">سعر الوحدة (ج.م)</div>
            <input className="field-input text-sm w-full" type="number" step="0.01"
              value={form.unit_cost} onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))} />
          </div>
          <div>
            <div className="text-xs text-erp-muted mb-1">ملاحظات</div>
            <input className="field-input text-sm w-full"
              value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
          </div>
        </div>
      )}

      {/* Notes for bar/plate */}
      {(isBar || isPlate) && (
        <div className="mb-3">
          <div className="text-xs text-erp-muted mb-1">ملاحظات (اختياري)</div>
          <input className="field-input text-sm w-full"
            value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
        </div>
      )}

      {/* Preview for variable metric lines */}
      {isVar && isMetric && selectedMat && (
        <div className="flex gap-3 mb-3 text-xs" style={{ color: 'var(--teal)' }}>
          <span className="font-semibold">معاينة:</span>
          <span>عند L={eq.length_min}م ← {previewQty(Number(eq.length_min))}</span>
          <span>|</span>
          <span>عند L={eq.length_max}م ← {previewQty(Number(eq.length_max))}</span>
        </div>
      )}

      <button className="btn btn-primary btn-sm" onClick={addLine} disabled={adding}>
        {adding ? '…جاري الحفظ' : 'حفظ سطر BOM'}
      </button>
    </div>
  )
}

function BOMTab({ eq, write }) {
  const toast = useToast()
  const { data: bomLines, loading, reload } = useApi(
    () => api.equipment.bom(eq.id),
    [eq.id]
  )
  const { data: materials } = useApi(() => api.inventory.materials())
  const [confirmBomDel, setConfirmBomDel] = useState(null)
  const [editingLine, setEditingLine] = useState(null) // line id being edited
  const [editForm, setEditForm] = useState({})

  const startEdit = (l) => {
    setEditingLine(l.id)
    setEditForm({ qty: String(l.qty), unit_cost: String(l.unit_cost), notes: l.notes || '' })
  }

  const [saveEdit, savingEdit] = useAction(async () => {
    await api.patch(`/equipment/bom/${editingLine}`, {
      qty: Number(editForm.qty),
      unit_cost: Number(editForm.unit_cost),
      notes: editForm.notes || null,
    })
    toast('تم الحفظ')
    setEditingLine(null)
    reload()
  })

  const [delLine] = useAction(async (id) => {
    await api.delete(`/equipment/bom/${id}`)
    toast('تم الحذف')
    setConfirmBomDel(null)
    reload()
  })

  if (loading) return <div className="p-6 text-center text-erp-muted animate-pulse">جاري التحميل…</div>

  const lines = bomLines || []
  const totalCost = lines.reduce((s, l) => s + Number(l.material?.unit_cost ?? l.unit_cost ?? 0) * Number(l.qty || 0), 0)

  return (
    <div className="flex flex-col gap-4">
      {write && <BOMAddForm eq={eq} materials={materials} onSaved={reload} />}

      {totalCost > 0 && (
        <div className="flex items-center justify-between px-4 py-3 rounded-erp"
             style={{ background: 'var(--teal-lt)' }}>
          <span className="text-sm text-erp-muted">
            {eq.length_min != null
              ? <>تكلفة BOM عند الحد الأدنى للطول <span className="text-xs px-1.5 py-0.5 rounded ml-1" style={{ background: '#fef3c7', color: '#92400e', border: '1px solid #fcd34d' }}>⚠ الأسعار حية، لكن الكميات غير مضروبة في الطول — استخدم الحاسبة للطول المطلوب</span></>
              : 'تكلفة BOM الإجمالية'}
          </span>
          <span className="text-xl font-bold" style={{ color: 'var(--teal)' }}>{egp(totalCost)}</span>
        </div>
      )}

      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr>
              <th>المادة</th><th>الكود</th><th>الكمية</th><th>الوحدة</th>
              <th>سعر الوحدة</th><th>الإجمالي</th><th>النوع</th><th>ملاحظات</th>
              {write && <th>إجراء</th>}
            </tr>
          </thead>
          <tbody>
            {!lines.length && (
              <tr><td colSpan={9} className="text-center text-erp-muted py-8">BOM فارغ — أضف مواد</td></tr>
            )}
            {lines.map(l => (
              editingLine === l.id ? (
                <tr key={l.id} style={{ background: 'var(--teal-lt)' }}>
                  <td className="font-medium" colSpan={2}>{l.material?.name_ar || '—'}</td>
                  <td><input className="field-input text-sm" style={{ width: 80 }} type="number" step="0.001"
                    value={editForm.qty} onChange={e => setEditForm(f => ({ ...f, qty: e.target.value }))} /></td>
                  <td className="text-erp-muted text-xs">{l.material?.unit}</td>
                  <td><input className="field-input text-sm" style={{ width: 90 }} type="number" step="0.01"
                    value={editForm.unit_cost} onChange={e => setEditForm(f => ({ ...f, unit_cost: e.target.value }))} /></td>
                  <td className="text-erp-muted text-xs">—</td>
                  <td><span className="text-xs px-1.5 py-0.5 rounded" style={{ background: l.line_type === 'variable' ? 'rgba(20,184,166,0.15)' : 'var(--bg)', color: l.line_type === 'variable' ? 'var(--teal)' : 'var(--fg-muted)' }}>{l.line_type === 'variable' ? 'متغير/م' : 'ثابت'}</span></td>
                  <td><input className="field-input text-sm" style={{ width: 120 }}
                    value={editForm.notes} onChange={e => setEditForm(f => ({ ...f, notes: e.target.value }))} /></td>
                  <td className="flex gap-1">
                    <button className="btn btn-sm btn-primary text-xs" onClick={saveEdit} disabled={savingEdit}>✓</button>
                    <button className="btn btn-sm text-xs" onClick={() => setEditingLine(null)}>✕</button>
                  </td>
                </tr>
              ) : (
              <tr key={l.id}>
                <td className="font-medium">{l.material?.name_ar || '—'}</td>
                <td><span className="font-mono text-xs bg-erp px-1.5 py-0.5 rounded">{l.material?.code}</span></td>
                <td className="font-semibold">
                  {Number(l.qty).toFixed(2)}
                  {l.line_type === 'variable' && <span className="text-erp-muted font-normal text-xs">/م</span>}
                </td>
                <td className="text-erp-muted text-xs">{l.material?.unit}</td>
                <td className="text-sm">
                  {egp(l.material?.unit_cost ?? l.unit_cost)}
                  {l.material?.unit_cost != null && Math.abs(l.material.unit_cost - l.unit_cost) > 0.001 && (
                    <span title={`السعر المخزون: ${egp(l.unit_cost)}`} style={{ fontSize: 10, marginRight: 4, color: '#f59e0b', cursor: 'help' }}>⚠</span>
                  )}
                </td>
                <td className="font-semibold" style={{ color: 'var(--teal)' }}>
                  {egp((l.material?.unit_cost ?? l.unit_cost) * l.qty)}
                </td>
                <td>
                  {l.line_type === 'variable'
                    ? <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'rgba(20,184,166,0.15)', color: 'var(--teal)' }}>متغير/م</span>
                    : <span className="text-xs px-1.5 py-0.5 rounded bg-erp text-erp-muted">ثابت</span>}
                </td>
                <td className="text-erp-muted text-xs">{l.notes || '—'}</td>
                {write && (
                  <td className="flex gap-1">
                    <button className="btn btn-sm text-xs" onClick={() => startEdit(l)}>✏️</button>
                    <button className="btn btn-sm btn-danger text-xs" onClick={() => setConfirmBomDel(l)}>🗑</button>
                  </td>
                )}
              </tr>
              )
            ))}
          </tbody>
        </table>
      </div>
      <ConfirmDialog
        open={!!confirmBomDel}
        onClose={() => setConfirmBomDel(null)}
        onConfirm={() => delLine(confirmBomDel?.id)}
        title="حذف سطر BOM"
        message={`حذف "${confirmBomDel?.material?.name_ar || 'هذا السطر'}" من BOM؟`}
        danger
      />
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
              {['مم', 'سم', 'متر', 'كجم', 'طن', 'كيلوواط', 'لفة/دقيقة', 'بار', 'قطعة'].map(u => <option key={u}>{u}</option>)}
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
  const [confirmCadDel, setConfirmCadDel] = useState(null)

  const upload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('revision', 'Rev1')
      const token = sessionStorage.getItem('erp_token')
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
    const token = sessionStorage.getItem('erp_token')
    try {
      await fetch(`/api/v1/cad/${id}`, { method:'DELETE', headers:{Authorization:`Bearer ${token}`} })
      toast('تم حذف الملف')
    } catch { toast('فشل الحذف', 'error') }
    setConfirmCadDel(null)
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
                <button type="button" onClick={() => api.download(`/cad/download/${f.id}`, f.filename)}
                   className="btn btn-sm text-xs">⬇ تنزيل</button>
                {f.filename?.match(/\.(pdf|png|jpg|jpeg)$/i) && (
                  <button type="button" onClick={() => api.openFile(`/cad/download/${f.id}`)}
                     className="btn btn-sm text-xs" style={{color:'var(--blue)'}}>👁 معاينة</button>
                )}
                <button className="btn btn-sm btn-danger text-xs" onClick={() => setConfirmCadDel(f)}>🗑</button>
              </div>
            </div>
          ))}
        </div>
      )}
      <ConfirmDialog
        open={!!confirmCadDel}
        onClose={() => setConfirmCadDel(null)}
        onConfirm={() => del(confirmCadDel?.id)}
        title="حذف ملف CAD"
        message={`حذف الملف "${confirmCadDel?.filename}"؟`}
        danger
      />
    </div>
  )
}

// ── Cost Lines tab ─────────────────────────────────────────────
const COST_TYPE_LABELS = {
  labor: 'عمالة',
  electricity: 'كهرباء',
  consumable: 'مستهلكات',
  overhead: 'تكاليف عامة',
}

function CostLinesTab({ eq, write }) {
  const toast = useToast()
  const { data: lines, loading, reload } = useApi(
    () => api.get(`/equipment/${eq.id}/cost-lines`),
    [eq.id]
  )
  const [form, setForm] = useState({ cost_type: 'labor', description: '', cost_per_unit: '' })
  const [confirmDel, setConfirmDel] = useState(null)

  const [addLine, adding] = useAction(async () => {
    if (!form.description.trim()) { toast('الوصف مطلوب', 'error'); return }
    if (!form.cost_per_unit || Number(form.cost_per_unit) <= 0) { toast('أدخل تكلفة أكبر من صفر', 'error'); return }
    await api.post(`/equipment/${eq.id}/cost-lines`, {
      equipment_id: eq.id,
      cost_type: form.cost_type,
      description: form.description,
      cost_per_unit: Number(form.cost_per_unit),
    })
    toast('تمت الإضافة')
    setForm({ cost_type: 'labor', description: '', cost_per_unit: '' })
    reload()
  })

  const [delLine] = useAction(async (id) => {
    await api.delete(`/equipment/cost-lines/${id}`)
    toast('تم الحذف')
    setConfirmDel(null)
    reload()
  })

  if (loading) return <div className="p-6 text-center text-erp-muted animate-pulse">جاري التحميل…</div>

  const rows = Array.isArray(lines) ? lines : []
  const totalCost = rows.reduce((s, l) => s + Number(l.cost_per_unit || 0), 0)
  const calcUnitLabel = eq.calc_unit === 'piece' ? 'قطعة'
    : eq.calc_unit === 'meter' ? 'متر'
    : eq.calc_unit === 'ton_hr' ? 'طن/ساعة'
    : eq.calc_unit === 'kw' ? 'كيلوواط'
    : (eq.calc_unit || 'وحدة')

  return (
    <div className="flex flex-col gap-4">
      {/* summary */}
      <div className="card-sm flex items-center justify-between" style={{ background: 'var(--teal-lt)' }}>
        <div className="text-sm text-erp-muted">
          وحدة الحساب: <span className="font-bold" style={{ color: 'var(--teal)' }}>{calcUnitLabel}</span>
          {eq.base_qty && Number(eq.base_qty) !== 1 && (
            <span className="mr-2 text-xs">(الكمية الأساسية: {eq.base_qty})</span>
          )}
        </div>
        <div className="font-semibold text-sm" style={{ color: 'var(--teal)' }}>
          إجمالي التكاليف الثابتة / وحدة: {egp(totalCost)}
        </div>
      </div>

      {/* add form */}
      {write && (
        <div className="card" style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
          <div className="text-sm font-semibold mb-3" style={{ color: 'var(--teal)' }}>+ إضافة سطر تكلفة</div>
          <div className="grid gap-3" style={{ gridTemplateColumns: '1fr 2fr 1fr auto' }}>
            <select className="field-select text-sm" value={form.cost_type}
              onChange={e => setForm(f => ({ ...f, cost_type: e.target.value }))}>
              {Object.entries(COST_TYPE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <input className="field-input text-sm" placeholder="الوصف (مثال: أجرة عامل تركيب)"
              value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
            <input className="field-input text-sm" type="number" step="0.01" placeholder={`التكلفة / ${calcUnitLabel}`}
              value={form.cost_per_unit} onChange={e => setForm(f => ({ ...f, cost_per_unit: e.target.value }))} />
            <button className="btn btn-primary btn-sm" onClick={addLine} disabled={adding}>
              {adding ? '…' : 'إضافة'}
            </button>
          </div>
        </div>
      )}

      {/* table */}
      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr>
              <th>النوع</th><th>الوصف</th>
              <th>التكلفة / {calcUnitLabel}</th>
              {write && <th>حذف</th>}
            </tr>
          </thead>
          <tbody>
            {!rows.length && (
              <tr><td colSpan={4} className="text-center text-erp-muted py-8">لا توجد تكاليف — أضف سطراً</td></tr>
            )}
            {rows.map(l => (
              <tr key={l.id}>
                <td>
                  <span className="inline-block px-2 py-0.5 rounded text-xs font-semibold"
                        style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
                    {COST_TYPE_LABELS[l.cost_type] || l.cost_type}
                  </span>
                </td>
                <td className="font-medium">{l.description}</td>
                <td className="font-semibold" style={{ color: 'var(--teal)' }}>{egp(l.cost_per_unit)}</td>
                {write && (
                  <td><button className="btn btn-sm btn-danger text-xs" onClick={() => setConfirmDel(l)}>🗑</button></td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={!!confirmDel}
        onClose={() => setConfirmDel(null)}
        onConfirm={() => delLine(confirmDel?.id)}
        title="حذف سطر تكلفة"
        message={`حذف "${confirmDel?.description}"؟`}
        danger
      />
    </div>
  )
}


// ── Detail panel ───────────────────────────────────────────────
const CALC_UNIT_LABELS = {
  piece:  'قطعة',
  meter:  'متر',
  ton_hr: 'طن/ساعة',
  kw:     'كيلوواط',
}

function DetailPanel({ eq, write }) {
  const [tab, setTab] = useState('specs')
  const TABS = [
    { id: 'specs', label: 'المواصفات' },
    { id: 'dims',  label: 'المقاسات' },
    { id: 'bom',   label: 'BOM' },
    { id: 'cad',   label: 'CAD ملفات' },
    { id: 'costs', label: 'التكاليف' },
  ]

  return (
    <div className="flex flex-col h-full">
      {/* Tabs */}
      <div className="flex border-b border-erp-border overflow-x-auto">
        {TABS.map(t => (
          <button key={t.id}
            className="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap"
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
                ['الوزن', eq.weight_kg ? `${eq.weight_kg} كجم` : '—'],
                ['رقم الرسم', eq.cad_drawing_no || '—'],
                ['المستوى', `L${eq.level ?? 0}`],
                ['الرقم التسلسلي', eq.serial_number || '—'],
                ['الحالة', { active: 'نشطة', maintenance: 'صيانة', retired: 'متقاعدة', damaged: 'معطلة' }[eq.equip_status] || eq.equip_status || '—'],
                ['تاريخ الشراء', fmtDate(eq.purchase_date) || '—'],
                ['قيمة الشراء', eq.purchase_value ? egp(eq.purchase_value) : '—'],
                ['وحدة الحساب', CALC_UNIT_LABELS[eq.calc_unit] || eq.calc_unit || 'قطعة'],
                ['الكمية الأساسية', eq.base_qty ? String(eq.base_qty) : '1'],
                ...(eq.length_min != null && eq.length_max != null
                  ? [['نطاق الطول (الصنف)', `من ${eq.length_min} إلى ${eq.length_max} متر`]]
                  : []),
              ].map(([label, val]) => (
                <div key={label} className="card-sm">
                  <div className="text-xs text-erp-muted mb-1">{label}</div>
                  <div className="font-semibold text-sm">{val || '—'}</div>
                </div>
              ))}
            </div>
            {eq.description && (
              <div className="card-sm">
                <div className="text-xs text-erp-muted mb-1">الوصف</div>
                <div className="text-sm" style={{ whiteSpace: 'pre-wrap' }}>{eq.description}</div>
              </div>
            )}
          </div>
        )}
        {tab === 'dims'  && <DimensionsTab eq={eq} write={write} />}
        {tab === 'bom'   && (
          <div className="flex flex-col gap-4">
            {eq.length_min != null && eq.length_max != null && <LengthCalculatorPanel eq={eq} />}
            <BOMTab eq={eq} write={write} />
          </div>
        )}
        {tab === 'cad'   && <CADTab eq={eq} />}
        {tab === 'costs' && <CostLinesTab eq={eq} write={write} />}
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
  const [catModal, setCatModal] = useState(false)
  const [newCatName, setNewCatName] = useState('')
  const [filterCat, setFilterCat] = useState('')

  const { data: allEqs, reload: reloadEqs } = useApi(() => api.equipment.listAll().catch(bgErr('قائمة المعدات', [])))
  const { data: categories, reload: reloadCats } = useApi(() => api.equipment.categories().catch(bgErr('فئات المعدات', [])))

  // قائمة مسطّحة احتياطية من الشجرة لو list-all رجع فاضي
  const flatFromTree = (nodes, acc = []) => {
    for (const n of (Array.isArray(nodes) ? nodes : [])) {
      acc.push({ id: n.id, code: n.code, name_ar: n.name_ar, level: n.level, parent_id: n.parent_id })
      if (n.children?.length) flatFromTree(n.children, acc)
    }
    return acc
  }
  const parentOptions = (Array.isArray(allEqs) && allEqs.length)
    ? allEqs
    : flatFromTree(Array.isArray(tree) ? tree : (tree?.children || []))

  const openAdd = () => {
    setForm({ level: 0 })
    setModal({ mode: 'add' })
  }

  const openAddChild = (parent) => {
    setForm({
      parent_id: parent.id,
      level: (parent.level ?? 0) + 1,
      code: parent.code + '-',
    })
    setModal({ mode: 'add', parentName: parent.name_ar })
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
      category_id: form.category_id ? Number(form.category_id) : null,
      level: Number(form.level ?? 0),
      weight_kg: form.weight_kg ? Number(form.weight_kg) : null,
      cad_drawing_no: form.cad_drawing_no || null,
      serial_number: form.serial_number || null,
      equip_status: form.equip_status || 'active',
      purchase_date: form.purchase_date || null,
      purchase_value: form.purchase_value ? Number(form.purchase_value) : null,
      calc_unit: form.calc_unit || 'piece',
      base_qty: form.base_qty ? Number(form.base_qty) : 1,
      length_min: form.calc_unit === 'meter' && form.length_min !== '' && form.length_min != null ? Number(form.length_min) : null,
      length_max: form.calc_unit === 'meter' && form.length_max !== '' && form.length_max != null ? Number(form.length_max) : null,
    }
    if (modal?.mode === 'edit') await api.equipment.update(form.id, payload)
    else await api.equipment.create(payload)
    toast('تم الحفظ')
    setModal(null)
    reload()
    reloadEqs()
  })

  const [doDelete] = useAction(async () => {
    await api.equipment.delete(deleteTarget.id)
    toast('تم الحذف')
    if (selected?.id === deleteTarget.id) setSelected(null)
    setDeleteTarget(null)
    reload()
    reloadEqs()
  })

  const [doDuplicate, duplicating] = useAction(async (eq) => {
    const newCode = window.prompt(`أدخل كود المعدة الجديدة (نسخة من "${eq.name_ar}"):`, `${eq.code}-COPY`)
    if (!newCode) return
    const created = await api.equipment.duplicate(eq.id, { code: newCode })
    toast('تم نسخ المعدة')
    reload()
    reloadEqs()
    return created
  })

  const [addCategory, addingCat] = useAction(async () => {
    const name = newCatName.trim()
    if (!name) { toast('اكتب اسم التصنيف', 'error'); return }
    const created = await api.equipment.createCategory({ name_ar: name, name_en: name })
    toast('تمت إضافة التصنيف')
    setNewCatName('')
    await reloadCats()
    if (created?.id && modal) setForm(f => ({ ...f, category_id: created.id }))
  })

  if (loading) return <PageSpinner />

  // Normalise tree — backend may return flat list or tree
  let treeNodes = Array.isArray(tree) ? tree : (tree?.children || tree?.items || [])
  if (filterCat) treeNodes = treeNodes.filter(n => String(n.category_id) === String(filterCat))

  return (
    <div className="flex flex-col gap-4">
      {/* top toolbar */}
      <div className="flex justify-between items-center gap-2 flex-wrap">
        <select className="field-select" style={{ maxWidth: 200, height: 32, padding: '0 8px', fontSize: 13 }}
          value={filterCat} onChange={e => setFilterCat(e.target.value)}>
          <option value="">كل التصنيفات</option>
          {(categories || []).map(c => (
            <option key={c.id} value={c.id}>{c.name_ar}{c.equipment_count != null ? ` (${c.equipment_count})` : ''}</option>
          ))}
        </select>
        <div className="flex gap-2">
          {write && (
            <button className="btn btn-sm" onClick={() => setCatModal(true)}>+ تصنيف</button>
          )}
          {write && (
            <button className="btn btn-primary btn-sm" onClick={openAdd}>+ إضافة معدة / مجموعة</button>
          )}
        </div>
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
                  onEdit={openEdit} onDelete={setDeleteTarget} onDuplicate={doDuplicate} write={write} />
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
        title={modal?.mode === 'edit' ? 'تعديل معدة / مجموعة' : modal?.parentName ? `+ فرعي لـ: ${modal.parentName}` : 'إضافة معدة / مجموعة'}>
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="الكود *" value={form.code || ''} onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="FG-500" />
            <Select label="المستوى" value={form.level ?? 0} onChange={e => setForm(f => ({ ...f, level: +e.target.value }))}
              options={[0,1,2,3,4].map(n => ({ value: n, label: `L${n} — ${['مجموعة رئيسية','وحدة','مكوّن','قطعة فرعية','قطعة دقيقة'][n]}` }))} />
          </FormGrid>
          <Input label="الاسم بالعربي *" value={form.name_ar || ''} onChange={e => setForm(f => ({ ...f, name_ar: e.target.value }))} placeholder="مجرشة أعلاف 5 طن/ساعة" />
          <Input label="الاسم بالإنجليزي" value={form.name_en || ''} onChange={e => setForm(f => ({ ...f, name_en: e.target.value }))} placeholder="5 Ton/hr Feed Crusher" />
          <div className="form-field">
            <label className="field-label">الوصف / المواصفات الفنية</label>
            <textarea className="input" rows={4} style={{ resize: 'vertical' }}
              value={form.description || ''} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              placeholder={'مواصفات فنية تُستخدم تلقائياً عند اختيار هذه المعدة من الكتالوج في عروض الأسعار\nمثال:\n- قدرة الموتور: 15 حصان\n- معدل الإنتاج: 5 طن/ساعة\n- الأبعاد: 2×1.2×1.5 م'} />
          </div>
          <Select label="التصنيف" value={form.category_id != null ? String(form.category_id) : ''}
            onChange={e => setForm(f => ({ ...f, category_id: e.target.value ? +e.target.value : null }))}>
            <option value="">— بدون تصنيف —</option>
            {(categories || []).map(c => (
              <option key={c.id} value={c.id}>{c.name_ar}</option>
            ))}
          </Select>
          <FormGrid cols={2}>
            <Input label="الوزن (كجم)" type="number" step="0.001" value={form.weight_kg || ''} onChange={e => setForm(f => ({ ...f, weight_kg: e.target.value }))} placeholder="2800" />
            <Input label="رقم رسم CAD" value={form.cad_drawing_no || ''} onChange={e => setForm(f => ({ ...f, cad_drawing_no: e.target.value }))} placeholder="DWG-FG500-Rev2" />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="الرقم التسلسلي" value={form.serial_number || ''} onChange={e => setForm(f => ({ ...f, serial_number: e.target.value }))} placeholder="SN-2024-00123" />
            <Select label="حالة المعدة" value={form.equip_status || 'active'} onChange={e => setForm(f => ({ ...f, equip_status: e.target.value }))}>
              <option value="active">نشطة</option>
              <option value="maintenance">تحت الصيانة</option>
              <option value="retired">متقاعدة</option>
              <option value="damaged">معطلة</option>
            </Select>
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="تاريخ الشراء" type="date" value={form.purchase_date || ''} onChange={e => setForm(f => ({ ...f, purchase_date: e.target.value }))} />
            <Input label="قيمة الشراء (ج)" type="number" step="0.01" value={form.purchase_value || ''} onChange={e => setForm(f => ({ ...f, purchase_value: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Select label="وحدة الحساب (calc_unit)" value={form.calc_unit || 'piece'} onChange={e => setForm(f => ({ ...f, calc_unit: e.target.value }))}>
              <option value="piece">قطعة</option>
              <option value="meter">متر (مترية — سير/بريمة)</option>
              <option value="ton_hr">طن/ساعة (مواصفة)</option>
              <option value="kw">كيلوواط (مواصفة)</option>
            </Select>
            <Input label="الكمية الأساسية للـ BOM" type="number" step="0.001" min="0.001"
              value={form.base_qty ?? 1}
              onChange={e => setForm(f => ({ ...f, base_qty: e.target.value }))}
              placeholder="1" />
          </FormGrid>
          {form.calc_unit === 'meter' && (
            <FormGrid cols={2}>
              <Input label="أقل طول للصنف (م)" type="number" step="0.1" min="0"
                value={form.length_min ?? ''}
                onChange={e => setForm(f => ({ ...f, length_min: e.target.value }))}
                placeholder="2" />
              <Input label="أقصى طول للصنف (م)" type="number" step="0.1" min="0"
                value={form.length_max ?? ''}
                onChange={e => setForm(f => ({ ...f, length_max: e.target.value }))}
                placeholder="4" />
            </FormGrid>
          )}
          {form.level > 0 && (
            <Select label="المعدة الأم *"
              value={form.parent_id != null ? String(form.parent_id) : ''}
              onChange={e => setForm(f => ({ ...f, parent_id: e.target.value ? +e.target.value : null }))}>
              <option value="">— بدون أم (جذر) —</option>
              {(Array.isArray(parentOptions) ? parentOptions : []).filter(e => e.id !== form.id).map(e => (
                <option key={e.id} value={String(e.id)}>
                  {'— '.repeat(e.level || 0)}L{e.level} {e.code} — {e.name_ar}
                </option>
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

      {/* مودال إدارة التصنيفات */}
      {catModal && (
        <Modal open onClose={() => { setCatModal(false); setNewCatName('') }} title="إدارة تصنيفات المعدات">
          <div className="flex flex-col gap-4">
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <Input label="اسم التصنيف الجديد" value={newCatName}
                  onChange={e => setNewCatName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') addCategory() }}
                  placeholder="مثال: مجارش، خلاطات، سيور نقالة…" />
              </div>
              <button className="btn btn-primary" disabled={addingCat} onClick={addCategory}>
                {addingCat ? '...' : 'إضافة'}
              </button>
            </div>
            <div className="border-t pt-3" style={{ borderColor: 'var(--border)' }}>
              <div className="text-xs text-erp-muted mb-2">التصنيفات الموجودة ({(categories || []).length})</div>
              <div className="flex flex-col gap-1 max-h-72 overflow-y-auto">
                {(categories || []).map(c => (
                  <div key={c.id} className="flex items-center justify-between py-1.5 px-2 rounded"
                    style={{ background: 'var(--surface-2)' }}>
                    <span className="text-sm">{c.name_ar}
                      <span className="text-[11px] text-erp-muted mr-2">({c.equipment_count ?? 0} معدة)</span>
                    </span>
                    <button className="btn btn-sm" title="حذف التصنيف"
                      style={{ color: 'var(--red)' }}
                      onClick={async () => {
                        try {
                          await api.equipment.deleteCategory(c.id)
                          toast('تم حذف التصنيف'); await reloadCats()
                        } catch (err) {
                          toast(err?.message || 'تعذّر حذف التصنيف', 'error')
                        }
                      }}>🗑</button>
                  </div>
                ))}
                {!(categories || []).length && (
                  <div className="text-sm text-erp-muted text-center py-4">لا توجد تصنيفات بعد</div>
                )}
              </div>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
