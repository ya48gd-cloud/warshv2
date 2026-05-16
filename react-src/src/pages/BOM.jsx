/**
 * BOM (تفكيك) — Bill of Materials
 * Supports plate dimensions per BOM line:
 * - Normal material: qty + unit_cost
 * - Plate material:  count × (L×W) → area → kg via density → auto cost from stock price
 */
import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp } from '../utils/fmt'
import { PageSpinner } from '../components/ui/Spinner'

const LEVEL_COLORS = {
  0: { bg: '#1D9E75', color: '#fff'    },
  1: { bg: '#E6F1FB', color: '#185FA5' },
  2: { bg: '#FAEEDA', color: '#854F0B' },
  3: { bg: '#FCEBEB', color: '#A32D2D' },
  4: { bg: '#f3f4f6', color: '#6b7280' },
}

// ── Plate dimension input for a BOM line ─────────────────────
function PlateInput({ material, form, setForm }) {
  const density     = material?.density_kg_m2
  const stockPrice  = material?.unit_cost

  // Compute: area = count × L × W (in m²)  →  weight = area × density
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

  // Sync calculated values into form
  useMemo(() => {
    if (calcWeightKg !== null) {
      setForm(f => ({ ...f, qty: calcWeightKg.toFixed(3), unit_cost: stockPrice || f.unit_cost }))
    }
  }, [calcWeightKg]) // eslint-disable-line

  return (
    <div className="flex flex-col gap-2 p-3 rounded-erp"
         style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
      <div className="text-xs font-semibold" style={{ color: 'var(--teal)' }}>
        🔩 أبعاد اللوح — التكلفة تُحسب تلقائياً
      </div>

      {/* Dimensions */}
      <div className="grid gap-2" style={{ gridTemplateColumns: '80px 1fr 1fr 1fr' }}>
        <div>
          <label className="field-label text-[11px]">العدد</label>
          <input className="field-input text-sm text-center" type="number" step="1" min="1"
            value={form.dim_count || 1}
            onChange={e => setForm(f => ({ ...f, dim_count: +e.target.value }))}
            placeholder="4" />
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

      {/* Results */}
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

      {/* Manual override */}
      <div className="grid gap-2" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div>
          <label className="field-label text-[11px]">الوزن (كجم) — محسوب تلقائياً</label>
          <input className="field-input text-sm font-semibold"
            type="number" step="0.001"
            style={{ color: 'var(--teal)' }}
            value={form.qty || ''}
            onChange={e => setForm(f => ({ ...f, qty: e.target.value }))}
            placeholder="يُحسب تلقائياً" />
        </div>
        <div>
          <label className="field-label text-[11px]">سعر الوحدة (ج/كجم) — من المخزن</label>
          <input className="field-input text-sm font-semibold"
            type="number" step="0.01"
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
  const wpm = Number(material?.weight_per_meter_kg || 0)
  const stockPrice = material?.unit_cost
  const weight = useMemo(() => {
    if (!wpm || !form.bar_length_cm) return null
    return ((Number(form.bar_length_cm) / 100) * wpm * Number(form.dim_count || 1)) + Number(form.waste_g || 0) / 1000
  }, [wpm, form.bar_length_cm, form.dim_count, form.waste_g])

  useMemo(() => {
    if (weight !== null) {
      setForm(f => ({ ...f, qty: weight.toFixed(3), unit_cost: stockPrice || f.unit_cost }))
    }
  }, [weight]) // eslint-disable-line

  return (
    <div className="flex flex-col gap-2 p-3 rounded-erp"
         style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
      <div className="text-xs font-semibold" style={{ color: 'var(--teal)' }}>
        حديد بالطول — الوزن والتكلفة يحسبان تلقائياً
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: '1fr 1fr 1fr 1fr' }}>
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
          <div className="field-input bg-erp text-sm text-center font-semibold" style={{ color: weight ? 'var(--teal)' : 'var(--muted)' }}>
            {weight ? `${weight.toFixed(3)} كجم` : '—'}
          </div>
        </div>
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <input className="field-input text-sm font-semibold" type="number" step="0.001"
          value={form.qty || ''} onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} />
        <input className="field-input text-sm font-semibold" type="number" step="0.01"
          value={form.unit_cost || ''} onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))} />
      </div>
    </div>
  )
}

// ── Main BOM page ──────────────────────────────────────────────
export default function BOM() {
  const { canWrite } = useAuth()
  const write = canWrite('inventory')
  const toast = useToast()

  const { data: allEqs }   = useApi(() => api.equipment.list())
  const { data: materials } = useApi(() => api.inventory.materials())

  const [viewEqId, setViewEqId] = useState('')
  const [addEqId,  setAddEqId]  = useState('')

  const { data: bomData, loading: bomLoading, reload: reloadBom } = useApi(
    () => viewEqId
      ? api.get(`/equipment/${viewEqId}/bom/cost`).catch(() => api.equipment.bom(viewEqId).then(lines => ({ lines, total_cost: lines.reduce((s, l) => s + Number(l.total_cost || 0), 0) })))
      : Promise.resolve(null),
    [viewEqId]
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
  const isBarMode = selectedMat?.material_kind === 'bar'

  // Auto-fill unit_cost and detect plate mode on material change
  const onMatChange = (matId) => {
    const mat = materials?.find(m => m.id == matId)
    setForm(f => ({
      ...f,
      material_id:   matId,
      unit_cost:     mat?.unit_cost ? String(mat.unit_cost) : f.unit_cost,
      is_plate_mode: !!mat?.is_plate,
      dim_count:     1,
      plate_length_cm: '',
      plate_width_cm:  '',
      bar_length_cm: '',
      waste_g: '',
      qty: (mat?.is_plate || mat?.material_kind === 'bar') ? '' : '1',
    }))
  }

  // Computed total for normal mode
  const lineTotal = useMemo(() => {
    if (!form.qty || !form.unit_cost) return null
    return Number(form.qty) * Number(form.unit_cost)
  }, [form.qty, form.unit_cost])

  const [addLine, adding] = useAction(async () => {
    if (!addEqId)           { toast('اختر المعدة / المجموعة', 'error'); return }
    if (!form.material_id)  { toast('اختر المادة',             'error'); return }
    if (!form.qty || Number(form.qty) <= 0) { toast('الكمية (الوزن) مطلوبة', 'error'); return }

    const payload = {
      equipment_id: Number(addEqId),
      material_id:  Number(form.material_id),
      qty:          Number(form.qty),
      unit_cost:    Number(form.unit_cost || selectedMat?.unit_cost || 0),
      notes:        form.notes || null,
    }
    // Plate dimensions
    if (isPlateMode && form.plate_length_cm && form.plate_width_cm) {
      payload.dim_count      = Number(form.dim_count || 1)
      payload.dim_length_cm  = Number(form.plate_length_cm)
      payload.dim_width_cm   = Number(form.plate_width_cm)
      payload.dim_area_m2    = ((form.plate_length_cm / 100) * (form.plate_width_cm / 100)) * Number(form.dim_count || 1)
      payload.calc_weight_kg = Number(form.qty)
    }
    if (isBarMode && form.bar_length_cm) {
      payload.dim_count = Number(form.dim_count || 1)
      payload.bar_length_cm = Number(form.bar_length_cm)
      payload.calc_weight_kg = Number(form.qty)
    }
    if (form.waste_g) payload.waste_g = Number(form.waste_g)

    await api.post('/equipment/bom', payload)
    toast('تمت إضافة سطر BOM')
    setForm(emptyForm())
    if (viewEqId === addEqId) reloadBom()
  })

  const [delLine] = useAction(async (id) => {
    await api.delete(`/equipment/bom/${id}`)
    toast('تم الحذف')
    reloadBom()
  })

  const lines     = bomData?.lines     || []
  const totalCost = bomData?.total_cost || 0

  return (
    <div className="flex flex-col gap-4">

      {/* ── Add BOM line ── */}
      {write && (
        <div className="card">
          <div className="text-sm font-semibold mb-4">+ إضافة سطر BOM</div>

          <div className="grid gap-3 mb-3" style={{ gridTemplateColumns: '1fr 1fr' }}>
            <div>
              <label className="field-label">المعدة / المجموعة *</label>
              <select className="field-select w-full" value={addEqId}
                onChange={e => setAddEqId(e.target.value)}>
                <option value="">— اختر —</option>
                {allEqs?.map(eq => (
                  <option key={eq.id} value={eq.id}>L{eq.level} {eq.code} — {eq.name_ar}</option>
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

          {/* Plate mode or normal */}
          {form.material_id && (
            isPlateMode ? (
              <PlateInput
                material={selectedMat}
                form={form}
                setForm={setForm}
              />
            ) : isBarMode ? (
              <BarInput
                material={selectedMat}
                form={form}
                setForm={setForm}
              />
            ) : (
              <div className="grid gap-3" style={{ gridTemplateColumns: '1fr 1fr 1fr auto' }}>
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
              {isBarMode && 'وضع الحديد: الوزن والتكلفة تُحسبان من الطول ووزن المتر'}
            </div>
            <button className="btn btn-primary" onClick={addLine} disabled={adding}>
              {adding ? '…' : 'حفظ سطر BOM'}
            </button>
          </div>
        </div>
      )}

      {/* ── BOM viewer ── */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-erp-border flex items-center justify-between gap-4"
             style={{ background: 'var(--bg)' }}>
          <div className="text-sm font-semibold">عرض تفكيك معدة</div>
          <div className="flex-1 max-w-md">
            <select className="field-select w-full" value={viewEqId}
              onChange={e => setViewEqId(e.target.value)}>
              <option value="">— اختر معدة / مجموعة —</option>
              {allEqs?.map(eq => (
                <option key={eq.id} value={eq.id}>{eq.code} — {eq.name_ar}</option>
              ))}
            </select>
          </div>
          {totalCost > 0 && (
            <div className="text-sm font-semibold flex-shrink-0" style={{ color: 'var(--teal)' }}>
              تكلفة BOM: {egp(totalCost)}
            </div>
          )}
        </div>

        {bomLoading ? (
          <div className="p-10 text-center text-erp-muted animate-pulse">جاري التحميل…</div>
        ) : !viewEqId ? (
          <div className="p-10 text-center text-erp-muted text-sm">اختر معدة لعرض BOM</div>
        ) : !lines.length ? (
          <div className="p-10 text-center text-erp-muted text-sm">BOM فارغ لهذه المعدة</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="erp-table">
              <thead>
                <tr>
                  <th>المستوى</th>
                  <th>الكود</th>
                  <th>الاسم</th>
                  <th>الوزن/الكمية</th>
                  <th>الأبعاد (سم)</th>
                  <th>المساحة (م²)</th>
                  <th>سعر الوحدة</th>
                  <th>تكلفة BOM</th>
                  {write && <th>حذف</th>}
                </tr>
              </thead>
              <tbody>
                {lines.map(l => {
                  const lv  = l.equipment?.level ?? 0
                  const lc  = LEVEL_COLORS[lv] || LEVEL_COLORS[4]
                  const mat = l.material || {}
                  const hasDims = l.dim_length_cm && l.dim_width_cm
                  const hasBar = l.bar_length_cm
                  return (
                    <tr key={l.id}>
                      <td>
                        <span className="inline-flex items-center justify-center text-[10px] font-bold px-1.5 py-0.5 rounded"
                              style={{ background: lc.bg, color: lc.color, minWidth: 28 }}>
                          L{lv}
                        </span>
                      </td>
                      <td>
                        <span className="font-mono text-xs bg-erp px-1.5 py-0.5 rounded">{mat.code || '—'}</span>
                        {mat.is_plate && <span className="badge badge-blue mr-1 text-[10px]">لوح</span>}
                        {mat.material_kind === 'bar' && <span className="badge badge-amber mr-1 text-[10px]">حديد</span>}
                      </td>
                      <td className="font-medium">{mat.name_ar || '—'}</td>
                      <td className="font-semibold font-mono">
                        {Number(l.qty).toFixed(3)}
                        <span className="text-erp-muted text-[10px] mr-1">{mat.unit}</span>
                      </td>
                      <td className="text-xs text-erp-muted">
                        {hasDims ? (
                          <div>
                            <div>{l.dim_count || 1} × {l.dim_length_cm}×{l.dim_width_cm}</div>
                          </div>
                        ) : hasBar ? (
                          <div>{l.dim_count || 1} × {l.bar_length_cm} سم</div>
                        ) : '—'}
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
                          <button className="btn btn-sm btn-danger text-xs" onClick={() => delLine(l.id)}>🗑</button>
                        </td>
                      )}
                    </tr>
                  )
                })}
                {/* Total row */}
                {totalCost > 0 && (
                  <tr style={{ background: 'var(--teal-lt)' }}>
                    <td colSpan={write ? 7 : 6} className="text-left font-semibold text-sm px-4">
                      تكلفة المواد الإجمالية
                    </td>
                    <td className="font-bold text-base" style={{ color: 'var(--teal)' }}>
                      {egp(totalCost)}
                    </td>
                    {write && <td />}
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
