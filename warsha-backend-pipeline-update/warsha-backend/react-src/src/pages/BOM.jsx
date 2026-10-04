/**
 * BOM (تفكيك) — Bill of Materials
 * Supports plate dimensions per BOM line:
 * - Normal material: qty + unit_cost
 * - Plate material:  count × (L×W) → area → kg via density → auto cost from stock price
 * - Bar material:    count × length → weight via weight_per_meter
 *
 * Viewer shows hierarchical structure: each equipment node as a header,
 * with its BOM lines indented beneath it.
 */
import { useState, useMemo, useEffect } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp } from '../utils/fmt'
import { PageSpinner } from '../components/ui/Spinner'
import ConfirmDialog from '../components/ui/ConfirmDialog'

const COST_TYPE_LABELS = {
  labor: 'عمالة',
  electricity: 'كهرباء',
  consumable: 'مستهلكات',
  overhead: 'تكاليف عامة',
}

const LEVEL_COLORS = {
  0: { bg: '#1D9E75', color: '#fff'    },
  1: { bg: '#E6F1FB', color: '#185FA5' },
  2: { bg: '#FAEEDA', color: '#854F0B' },
  3: { bg: '#FCEBEB', color: '#A32D2D' },
  4: { bg: '#f3f4f6', color: '#6b7280' },
}

// ── Plate dimension input ─────────────────────────────────────
function PlateInput({ material, form, setForm }) {
  const density    = material?.density_kg_m2
  const stockPrice = material?.unit_cost

  const areaM2 = useMemo(() => {
    const { plate_length_cm: l, plate_width_cm: w, dim_count: c } = form
    if (!l || !w) return null
    return ((l / 100) * (w / 100)) * Number(c || 1)
  }, [form.plate_length_cm, form.plate_width_cm, form.dim_count])

  const calcWeightKg = useMemo(() => {
    if (!areaM2 || !density) return null
    return areaM2 * density + Number(form.waste_g || 0) / 1000
  }, [areaM2, density, form.waste_g])

  const autoCost = useMemo(() => {
    if (!calcWeightKg || !stockPrice) return null
    return calcWeightKg * stockPrice
  }, [calcWeightKg, stockPrice])

  useEffect(() => {
    if (calcWeightKg !== null)
      setForm(f => ({ ...f, qty: calcWeightKg.toFixed(3), unit_cost: stockPrice || f.unit_cost }))
  }, [calcWeightKg]) // eslint-disable-line

  return (
    <div className="flex flex-col gap-2 p-3 rounded-erp"
         style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
      <div className="text-xs font-semibold" style={{ color: 'var(--teal)' }}>
        🔩 أبعاد اللوح — التكلفة تُحسب تلقائياً
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: '80px 1fr 1fr 1fr' }}>
        <div>
          <label className="field-label text-[11px]">العدد</label>
          <input className="field-input text-sm text-center" type="number" step="1" min="1"
            value={form.dim_count || 1}
            onChange={e => setForm(f => ({ ...f, dim_count: +e.target.value }))} />
        </div>
        <div>
          <label className="field-label text-[11px]">الطول (سم)</label>
          <input className="field-input text-sm text-center" type="number" step="0.1" min="0.1"
            value={form.plate_length_cm || ''}
            onChange={e => setForm(f => ({ ...f, plate_length_cm: e.target.value }))}
            placeholder="40" />
        </div>
        <div>
          <label className="field-label text-[11px]">العرض (سم)</label>
          <input className="field-input text-sm text-center" type="number" step="0.1" min="0.1"
            value={form.plate_width_cm || ''}
            onChange={e => setForm(f => ({ ...f, plate_width_cm: e.target.value }))}
            placeholder="30" />
        </div>
        <div>
          <label className="field-label text-[11px]">المساحة الكلية (م²)</label>
          <div className="field-input bg-erp text-sm text-center font-semibold"
               style={{ color: areaM2 ? 'var(--blue)' : 'var(--muted)' }}>
            {areaM2 ? areaM2.toFixed(4) : '—'}
          </div>
        </div>
      </div>
      <div>
        <label className="field-label text-[11px]">هالك/رايش (جرام)</label>
        <input className="field-input text-sm" type="number" step="1" min="0"
          value={form.waste_g || ''}
          onChange={e => setForm(f => ({ ...f, waste_g: e.target.value }))}
          placeholder="اختياري" />
      </div>
      {areaM2 && (
        <div className="grid gap-2" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
          <div className="p-2 rounded text-xs" style={{ background: '#fff' }}>
            <div className="text-erp-muted mb-0.5">كثافة المادة</div>
            <div className="font-semibold" style={{ color: density ? 'var(--teal)' : 'var(--red)' }}>
              {density ? `${Number(density).toFixed(2)} كجم/م²` : '⚠ غير محددة'}
            </div>
          </div>
          <div className="p-2 rounded text-xs" style={{ background: '#fff' }}>
            <div className="text-erp-muted mb-0.5">الوزن المحسوب</div>
            <div className="font-semibold" style={{ color: calcWeightKg ? 'var(--teal)' : 'var(--muted)' }}>
              {calcWeightKg ? `${calcWeightKg.toFixed(3)} كجم` : '—'}
            </div>
          </div>
          <div className="p-2 rounded text-xs" style={{ background: '#fff' }}>
            <div className="text-erp-muted mb-0.5">التكلفة التلقائية</div>
            <div className="font-bold" style={{ color: autoCost ? 'var(--teal)' : 'var(--muted)' }}>
              {autoCost ? egp(autoCost) : '—'}
            </div>
          </div>
        </div>
      )}
      {!density && (
        <div className="text-[11px] px-2 py-1.5 rounded"
             style={{ background: 'var(--amber-lt)', color: 'var(--amber)' }}>
          ⚠ هذه المادة ليس لها كثافة محسوبة بعد.
          سجّل حركة وارد بأبعاد اللوح أولاً لتُحسب الكثافة تلقائياً.
        </div>
      )}
      <div className="grid gap-2" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div>
          <label className="field-label text-[11px]">الوزن (كجم) — محسوب تلقائياً</label>
          <input className="field-input text-sm font-semibold" type="number" step="0.001"
            style={{ color: 'var(--teal)' }}
            value={form.qty || ''}
            onChange={e => setForm(f => ({ ...f, qty: e.target.value }))}
            placeholder="يُحسب تلقائياً" />
        </div>
        <div>
          <label className="field-label text-[11px]">سعر الوحدة (ج/كجم) — من المخزن</label>
          <input className="field-input text-sm font-semibold" type="number" step="0.01"
            style={{ color: 'var(--teal)' }}
            value={form.unit_cost || ''}
            onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))}
            placeholder={stockPrice ? String(stockPrice) : 'سعر المخزن'} />
        </div>
      </div>
    </div>
  )
}

function BarInput({ material, form, setForm }) {
  const wpm        = Number(material?.weight_per_meter_kg || 0)
  const stockPrice = material?.unit_cost
  const weight = useMemo(() => {
    if (!wpm || !form.bar_length_cm) return null
    return ((Number(form.bar_length_cm) / 100) * wpm * Number(form.dim_count || 1)) + Number(form.waste_g || 0) / 1000
  }, [wpm, form.bar_length_cm, form.dim_count, form.waste_g])

  useEffect(() => {
    if (weight !== null)
      setForm(f => ({ ...f, qty: weight.toFixed(3), unit_cost: stockPrice || f.unit_cost }))
  }, [weight]) // eslint-disable-line

  return (
    <div className="flex flex-col gap-2 p-3 rounded-erp"
         style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
      <div className="text-xs font-semibold" style={{ color: 'var(--teal)' }}>
        حديد بالطول — الوزن والتكلفة يحسبان تلقائياً
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))' }}>
        <div>
          <label className="field-label text-[11px]">عدد القطع</label>
          <input className="field-input text-sm text-center" type="number" step="1" min="1"
            value={form.dim_count || 1}
            onChange={e => setForm(f => ({ ...f, dim_count: +e.target.value }))} />
        </div>
        <div>
          <label className="field-label text-[11px]">طول القطعة (سم)</label>
          <input className="field-input text-sm text-center" type="number" step="0.1" min="0.1"
            value={form.bar_length_cm || ''}
            onChange={e => setForm(f => ({ ...f, bar_length_cm: e.target.value }))}
            placeholder="50" />
        </div>
        <div>
          <label className="field-label text-[11px]">هالك (جرام)</label>
          <input className="field-input text-sm text-center" type="number" step="1" min="0"
            value={form.waste_g || ''}
            onChange={e => setForm(f => ({ ...f, waste_g: e.target.value }))} />
        </div>
        <div>
          <label className="field-label text-[11px]">الوزن</label>
          <div className="field-input bg-erp text-sm text-center font-semibold"
               style={{ color: weight ? 'var(--teal)' : 'var(--muted)' }}>
            {weight ? `${weight.toFixed(3)} كجم` : '—'}
          </div>
        </div>
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <input className="field-input text-sm font-semibold" type="number" step="0.001" min="0"
          placeholder="الوزن (كجم)" title="الوزن (كجم)" aria-label="الوزن (كجم)"
          value={form.qty || ''} onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} />
        <input className="field-input text-sm font-semibold" type="number" step="0.01" min="0"
          placeholder="سعر الوحدة (ج/كجم)" title="سعر الوحدة (ج/كجم)" aria-label="سعر الوحدة (ج/كجم)"
          value={form.unit_cost || ''} onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))} />
      </div>
    </div>
  )
}

// ── Hierarchical BOM viewer ───────────────────────────────────
// Groups lines by equipment, renders equipment as section headers
// with their lines indented beneath, maintaining the tree order.
function HierarchicalBOMTable({ lines, allEqs, write, onDelete }) {
  // Build ordered list of unique equipment ids preserving tree order
  const eqMap = useMemo(() => {
    const m = {}
    ;(allEqs || []).forEach(e => { m[e.id] = e })
    return m
  }, [allEqs])

  // Group lines by equipment_id
  const byEq = useMemo(() => {
    const g = {}
    lines.forEach(l => {
      const eid = l.equipment_id
      if (!g[eid]) g[eid] = []
      g[eid].push(l)
    })
    return g
  }, [lines])

  // Get ordered equipment ids via depth-first tree traversal (parent then its children),
  // so sub-equipment always appears directly beneath its actual parent — not just grouped by level.
  const orderedEqIds = useMemo(() => {
    const ids = Object.keys(byEq).map(Number)
    const all = allEqs || []
    const byParent = {}
    all.forEach(e => {
      const pid = e.parent_id ?? null
      if (!byParent[pid]) byParent[pid] = []
      byParent[pid].push(e)
    })

    const ordered = []
    const visit = (pid) => {
      ;(byParent[pid] || []).forEach(e => {
        if (ids.includes(e.id)) ordered.push(e.id)
        visit(e.id)
      })
    }
    visit(null)

    // append any ids not found in allEqs (e.g. roots not under null parent) at the end
    ids.forEach(id => { if (!ordered.includes(id)) ordered.push(id) })
    return ordered
  }, [byEq, allEqs])

  if (!lines.length) return (
    <div className="p-10 text-center text-erp-muted text-sm">BOM فارغ لهذه المعدة</div>
  )

  return (
    <div className="overflow-x-auto">
      <table className="erp-table">
        <thead>
          <tr>
            <th>المستوى / المادة</th>
            <th>الكود</th>
            <th>الوزن/الكمية</th>
            <th>الأبعاد</th>
            <th>المساحة (م²)</th>
            <th>سعر الوحدة</th>
            <th>تكلفة BOM</th>
            {write && <th>حذف</th>}
          </tr>
        </thead>
        <tbody>
          {orderedEqIds.map(eid => {
            const eq   = eqMap[eid]
            const lv   = eq?.level ?? 0
            const lc   = LEVEL_COLORS[lv] || LEVEL_COLORS[4]
            const eqLines = byEq[eid] || []
            const eqCost  = eqLines.reduce((s, l) => s + Number(l.total_cost || 0), 0)
            const indent  = lv * 16

            return [
              // Equipment header row
              <tr key={`eq-${eid}`} style={{ background: lc.bg + '22' }}>
                <td colSpan={write ? 8 : 7} style={{ paddingRight: `${8 + indent}px` }}>
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center justify-center text-[10px] font-bold px-1.5 py-0.5 rounded"
                          style={{ background: lc.bg, color: lc.color, minWidth: 28 }}>
                      L{lv}
                    </span>
                    <span className="font-semibold text-sm">{eq?.name_ar || `معدة #${eid}`}</span>
                    <span className="font-mono text-xs text-erp-muted">{eq?.code}</span>
                    {eqCost > 0 && (
                      <span className="mr-auto text-xs font-semibold" style={{ color: 'var(--teal)' }}>
                        {egp(eqCost)}
                      </span>
                    )}
                  </div>
                </td>
              </tr>,
              // BOM lines under this equipment
              ...eqLines.map(l => {
                const mat     = l.material || {}
                const hasDims = l.dim_length_cm && l.dim_width_cm
                const hasBar  = l.bar_length_cm
                return (
                  <tr key={l.id} style={{ background: '#fff' }}>
                    <td style={{ paddingRight: `${24 + indent}px` }}>
                      <div className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full flex-shrink-0"
                              style={{ background: lc.bg, opacity: 0.5 }} />
                        <span className="font-medium text-sm">{mat.name_ar || '—'}</span>
                        {mat.is_plate && <span className="badge badge-blue text-[10px]">لوح</span>}
                        {mat.material_kind === 'bar' && <span className="badge badge-amber text-[10px]">حديد</span>}
                      </div>
                    </td>
                    <td>
                      <span className="font-mono text-xs bg-erp px-1.5 py-0.5 rounded">{mat.code || '—'}</span>
                    </td>
                    <td className="font-semibold font-mono">
                      {Number(l.qty).toFixed(3)}
                      <span className="text-erp-muted text-[10px] mr-1">{mat.unit}</span>
                    </td>
                    <td className="text-xs text-erp-muted">
                      {hasDims ? `${l.dim_count || 1} × ${l.dim_length_cm}×${l.dim_width_cm} سم` :
                       hasBar  ? `${l.dim_count || 1} × ${l.bar_length_cm} سم` : '—'}
                      {l.waste_g ? <div className="text-[10px]">هالك: {l.waste_g} جم</div> : null}
                    </td>
                    <td className="text-xs" style={{ color: l.dim_area_m2 ? 'var(--blue)' : 'var(--muted)' }}>
                      {l.dim_area_m2 ? Number(l.dim_area_m2).toFixed(4) : '—'}
                    </td>
                    <td className="text-sm">{egp(l.unit_cost)}</td>
                    <td className="font-semibold" style={{ color: Number(l.total_cost) > 0 ? 'var(--teal)' : 'var(--muted)' }}>
                      {Number(l.total_cost) > 0 ? egp(l.total_cost) : '—'}
                    </td>
                    {write && (
                      <td>
                        <button className="btn btn-sm btn-danger text-xs" onClick={() => onDelete(l.id)}>🗑</button>
                      </td>
                    )}
                  </tr>
                )
              })
            ]
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main BOM page ──────────────────────────────────────────────
export default function BOM() {
  const { canWrite } = useAuth()
  const write = canWrite('inventory')
  const toast = useToast()

  // All equipment (all levels) for dropdowns and hierarchy display
  const { data: allEqs }   = useApi(() => api.equipment.listAll())
  const { data: materials } = useApi(() => api.inventory.materials())

  const [viewEqId, setViewEqId] = useState('')
  const [addEqId,  setAddEqId]  = useState('')
  const [viewLength, setViewLength] = useState('')

  const viewEq = useMemo(() => allEqs?.find(e => String(e.id) === String(viewEqId)), [allEqs, viewEqId])
  const isMetricView = viewEq?.length_min != null
  // طول خارج النطاق (أو أثناء الكتابة) كان بيضرب الـ API مع كل ضغطة زر ويرجّع 400 → toast خطأ متكرر.
  // نتحقق محلياً بنفس قاعدة الباك-إند (length_min <= L <= length_max) قبل الاستدعاء.
  const viewLengthNum = Number(viewLength)
  const viewLengthInvalid = isMetricView && viewLength !== '' && (
    !Number.isFinite(viewLengthNum) || viewLengthNum <= 0 ||
    (viewEq?.length_min != null && viewLengthNum < Number(viewEq.length_min)) ||
    (viewEq?.length_max != null && viewLengthNum > Number(viewEq.length_max))
  )

  useEffect(() => {
    setViewLength(viewEq?.length_min != null ? String(viewEq.length_min) : '')
  }, [viewEqId])

  const { data: bomData, loading: bomLoading, reload: reloadBom } = useApi(
    () => {
      if (!viewEqId) return Promise.resolve(null)
      if (isMetricView && viewLength !== '' && !viewLengthInvalid) {
        return api.get(`/equipment/${viewEqId}/bom/parametric-length?length=${encodeURIComponent(viewLength)}`)
      }
      return api.get(`/equipment/${viewEqId}/bom/cost`).catch(() =>
        api.equipment.bom(viewEqId).then(lines => ({
          lines,
          total_cost: lines.reduce((s, l) => s + Number(l.total_cost || 0), 0)
        })))
    },
    [viewEqId, isMetricView, viewLength, viewLengthInvalid]
  )

  const emptyForm = () => ({
    qty: '1', unit_cost: '', notes: '',
    material_id: '', is_plate_mode: false,
    dim_count: 1, plate_length_cm: '', plate_width_cm: '',
    bar_length_cm: '', waste_g: '',
  })
  const [form, setForm] = useState(emptyForm())

  const selectedMat = useMemo(
    () => materials?.find(m => m.id == form.material_id),
    [materials, form.material_id]
  )

  const isPlateMode = form.is_plate_mode || selectedMat?.is_plate
  const isBarMode   = selectedMat?.material_kind === 'bar'

  const onMatChange = (matId) => {
    const mat = materials?.find(m => m.id == matId)
    setForm(f => ({
      ...f,
      material_id:     matId,
      unit_cost:       mat?.unit_cost ? String(mat.unit_cost) : f.unit_cost,
      is_plate_mode:   !!mat?.is_plate,
      dim_count:       1,
      plate_length_cm: '',
      plate_width_cm:  '',
      bar_length_cm:   '',
      waste_g:         '',
      qty: (mat?.is_plate || mat?.material_kind === 'bar') ? '' : '1',
    }))
  }

  const lineTotal = useMemo(() => {
    if (!form.qty || !form.unit_cost) return null
    return Number(form.qty) * Number(form.unit_cost)
  }, [form.qty, form.unit_cost])

  const [addLine, adding] = useAction(async () => {
    if (!addEqId)          { toast('اختر المعدة / المجموعة', 'error'); return }
    if (!form.material_id) { toast('اختر المادة',             'error'); return }
    if (!form.qty || Number(form.qty) <= 0) { toast('الكمية (الوزن) مطلوبة', 'error'); return }
    if (form.unit_cost && Number(form.unit_cost) < 0) { toast('سعر الوحدة لا يمكن أن يكون سالباً', 'error'); return }
    if ((isPlateMode || isBarMode) && (!Number.isInteger(Number(form.dim_count || 1)) || Number(form.dim_count || 1) < 1)) {
      toast('العدد يجب أن يكون رقماً صحيحاً أكبر من صفر', 'error'); return
    }
    if (form.waste_g && Number(form.waste_g) < 0) { toast('الهالك لا يمكن أن يكون سالباً', 'error'); return }
    if (isPlateMode && ((form.plate_length_cm && Number(form.plate_length_cm) <= 0) || (form.plate_width_cm && Number(form.plate_width_cm) <= 0))) {
      toast('أبعاد اللوح يجب أن تكون أكبر من صفر', 'error'); return
    }
    if (isBarMode && form.bar_length_cm && Number(form.bar_length_cm) <= 0) { toast('طول القطعة يجب أن يكون أكبر من صفر', 'error'); return }

    const payload = {
      equipment_id: Number(addEqId),
      material_id:  Number(form.material_id),
      qty:          Number(form.qty),
      unit_cost:    Number(form.unit_cost || selectedMat?.unit_cost || 0),
      notes:        form.notes || null,
    }
    if (isPlateMode && form.plate_length_cm && form.plate_width_cm) {
      payload.dim_count      = Number(form.dim_count || 1)
      payload.dim_length_cm  = Number(form.plate_length_cm)
      payload.dim_width_cm   = Number(form.plate_width_cm)
      payload.dim_area_m2    = ((form.plate_length_cm / 100) * (form.plate_width_cm / 100)) * Number(form.dim_count || 1)
      payload.calc_weight_kg = Number(form.qty)
    }
    if (isBarMode && form.bar_length_cm) {
      payload.dim_count      = Number(form.dim_count || 1)
      payload.bar_length_cm  = Number(form.bar_length_cm)
      payload.calc_weight_kg = Number(form.qty)
    }
    if (form.waste_g) payload.waste_g = Number(form.waste_g)

    await api.post('/equipment/bom', payload)
    toast('تمت إضافة سطر BOM')
    setForm(emptyForm())
    // العارض يعرض الشجرة كاملة (المعدة الرئيسية + كل مكوّناتها) — إضافة سطر لمكوّن فرعي
    // كانت بتسيب الجدول قديم لأن الشرط كان مساواة المعدة الرئيسية فقط.
    if (viewEqId) reloadBom()
  })

  const [confirmBomId, setConfirmBomId] = useState(null)
  const [delLine, deleting] = useAction(async (id) => {
    await api.delete(`/equipment/bom/${id}`)
    toast('تم الحذف')
    reloadBom()
  })

  const lines       = bomData?.lines        || []
  const totalCost   = bomData?.total_cost   || 0
  const materialCost = bomData?.material_cost || 0
  const fixedCost   = bomData?.fixed_cost   || 0
  const costLines   = bomData?.cost_lines   || []
  const materialsSummary = bomData?.materials_summary || []

  // Build grouped option list for "add" dropdown: L0 > L1 (indented) > L2 ...
  const eqOptions = useMemo(() => {
    if (!allEqs) return []
    return allEqs.map(eq => ({
      ...eq,
      label: '\u00A0'.repeat(eq.level * 4) + `L${eq.level} ${eq.code} — ${eq.name_ar}`
    }))
  }, [allEqs])

  return (
    <div className="flex flex-col gap-4">

      {/* ── Add BOM line ── */}
      {write && (
        <div className="card">
          <div className="text-sm font-semibold mb-4">+ إضافة سطر BOM</div>

          <div className="grid gap-3 mb-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
            <div>
              <label className="field-label">المعدة / الوحدة / المكوّن *</label>
              <select className="field-select w-full" value={addEqId}
                onChange={e => setAddEqId(e.target.value)}>
                <option value="">— اختر —</option>
                {eqOptions.map(eq => (
                  <option key={eq.id} value={eq.id}>{eq.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="field-label">المادة *</label>
              <select className="field-select w-full" value={form.material_id}
                onChange={e => onMatChange(e.target.value)}>
                <option value="">— اختر —</option>
                {materials?.map(m => (
                  <option key={m.id} value={m.id}>
                    {m.name_ar} ({m.code}){m.is_plate ? ' لوح' : m.material_kind === 'bar' ? ' حديد' : ''}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {form.material_id && (
            isPlateMode ? (
              <PlateInput material={selectedMat} form={form} setForm={setForm} />
            ) : isBarMode ? (
              <BarInput material={selectedMat} form={form} setForm={setForm} />
            ) : (
              <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
                <div>
                  <label className="field-label">الكمية ({selectedMat?.unit || 'وحدة'}) *</label>
                  <input className="field-input" type="number" step="0.001" min="0.001"
                    value={form.qty}
                    onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} />
                </div>
                <div>
                  <label className="field-label">سعر الوحدة (ج) *</label>
                  <input className="field-input" type="number" step="0.01" min="0"
                    value={form.unit_cost}
                    onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))} />
                </div>
                <div>
                  <label className="field-label">الإجمالي</label>
                  <div className="field-input bg-erp font-semibold"
                       style={{ color: lineTotal ? 'var(--teal)' : 'var(--muted)' }}>
                    {lineTotal ? egp(lineTotal) : '—'}
                  </div>
                </div>
                <div>
                  <label className="field-label">ملاحظات</label>
                  <input className="field-input" placeholder="اختياري"
                    value={form.notes}
                    onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
                </div>
              </div>
            )
          )}

          <div className="flex items-center justify-between mt-3">
            <div className="text-xs text-erp-muted">
              {isPlateMode && 'وضع الألواح: الوزن والتكلفة تُحسبان تلقائياً من الأبعاد والكثافة'}
              {isBarMode   && 'وضع الحديد: الوزن والتكلفة تُحسبان من الطول ووزن المتر'}
            </div>
            <button className="btn btn-primary" onClick={addLine} disabled={adding}>
              {adding ? '…' : 'حفظ سطر BOM'}
            </button>
          </div>
        </div>
      )}

      {/* ── BOM viewer ── */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-erp-border flex items-center justify-between gap-4 flex-wrap"
             style={{ background: 'var(--bg)' }}>
          <div className="text-sm font-semibold">عرض تفكيك معدة (كامل الشجرة)</div>
          <div className="flex-1 max-w-md">
            <select className="field-select w-full" value={viewEqId}
              onChange={e => setViewEqId(e.target.value)}>
              <option value="">— اختر معدة رئيسية —</option>
              {/* Show only L0 root equipment for the viewer — it pulls all descendants */}
              {allEqs?.filter(e => !e.parent_id).map(eq => (
                <option key={eq.id} value={eq.id}>{eq.code} — {eq.name_ar}</option>
              ))}
            </select>
          </div>
          {isMetricView && (
            <div className="flex items-center gap-2 flex-shrink-0">
              <span className="text-xs text-erp-muted whitespace-nowrap">الطول (م)</span>
              <input
                type="number" step="0.1"
                min={viewEq.length_min} max={viewEq.length_max}
                className="field-input text-sm" style={{ width: 90 }}
                value={viewLength}
                onChange={e => setViewLength(e.target.value)}
              />
              <span className="text-xs whitespace-nowrap" style={{ color: viewLengthInvalid ? 'var(--red)' : undefined }}>
                ({viewEq.length_min} - {viewEq.length_max}){viewLengthInvalid ? ' — الطول خارج النطاق، يُعرض BOM القياسي' : ''}
              </span>
            </div>
          )}
          {viewEqId && (
            <button className="btn btn-sm flex-shrink-0" onClick={() => api.equipment.printBOM(viewEqId, isMetricView && !viewLengthInvalid ? viewLength : null)}>
              🖨️ تقرير تصنيع
            </button>
          )}
          {totalCost > 0 && (
            <div className="text-sm font-semibold flex-shrink-0 text-end" style={{ color: 'var(--teal)' }}>
              تكلفة BOM الكلية: {egp(totalCost)}
              {fixedCost > 0 && (
                <div className="text-xs font-normal text-erp-muted">
                  (مواد {egp(materialCost)} + تكاليف ثابتة {egp(fixedCost)})
                </div>
              )}
            </div>
          )}
        </div>

        {bomLoading ? (
          <div className="p-10 text-center text-erp-muted animate-pulse">جاري التحميل…</div>
        ) : !viewEqId ? (
          <div className="p-10 text-center text-erp-muted text-sm">اختر معدة لعرض BOM الكامل</div>
        ) : (
          <HierarchicalBOMTable
            lines={lines}
            allEqs={allEqs}
            write={write}
            onDelete={(id) => setConfirmBomId(id)}
          />
        )}

        {/* Total row */}
        {totalCost > 0 && lines.length > 0 && (
          <div className="border-t border-erp-border" style={{ background: 'var(--teal-lt)' }}>
            <div className="px-4 py-2 flex items-center justify-between text-sm">
              <span className="text-erp-muted">تكلفة المواد</span>
              <span className="font-semibold">{egp(materialCost)}</span>
            </div>
            {costLines.map(cl => (
              <div key={cl.id} className="px-4 py-2 flex items-center justify-between text-sm">
                <span className="text-erp-muted">{COST_TYPE_LABELS[cl.cost_type] || cl.cost_type} — {cl.description}</span>
                <span className="font-semibold">{egp(cl.total_cost)}</span>
              </div>
            ))}
            <div className="px-4 py-3 border-t border-erp-border flex items-center justify-between">
              <span className="font-semibold text-sm">إجمالي تكلفة المعدة (مواد + تكاليف ثابتة)</span>
              <span className="font-bold text-lg" style={{ color: 'var(--teal)' }}>{egp(totalCost)}</span>
            </div>
          </div>
        )}
      </div>

      {/* ── ملخص المواد المطلوبة (إجمالي ألواح/أسياخ/قطع) ── */}
      {materialsSummary.length > 0 && (
        <div className="card p-0 overflow-x-auto">
          <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold" style={{ background: 'var(--bg)' }}>
            ملخص المواد المطلوبة (الإجمالي)
          </div>
          <table className="erp-table">
            <thead>
              <tr>
                <th>المادة</th>
                <th>الكود</th>
                <th>الإجمالي المطلوب</th>
              </tr>
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
                      <>
                        {m.precise.precise_value} {m.precise.precise_unit}
                        <span className="text-erp-muted font-normal"> (من {m.unit_label} {m.precise.unit_size} {m.precise.precise_unit} — الباقي {m.precise.remainder} {m.precise.precise_unit} نسالة)</span>
                      </>
                    ) : m.whole_count != null ? (
                      <>
                        {m.whole_count} {m.unit_label} كامل
                        {m.fraction_pct > 0 && (
                          <span className="text-erp-muted font-normal"> + {m.fraction_pct}% من {m.unit_label} إضافي</span>
                        )}
                      </>
                    ) : (
                      <>{m.count} {m.unit_label}</>
                    )}
                    {m.remnant_covered > 0 && (
                      <div className="text-xs text-erp-muted font-normal">
                        (منها {m.remnant_covered} {(m.unit_label || '').replace('كامل', '').trim()} متاح من النسالة)
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={!!confirmBomId}
        message="هل تريد حذف هذا السطر من BOM نهائياً؟"
        onConfirm={() => { if (!deleting) delLine(confirmBomId); setConfirmBomId(null) }}
        onCancel={() => setConfirmBomId(null)}
      />
    </div>
  )
}
