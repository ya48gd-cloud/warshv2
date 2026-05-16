/**
 * حركات المخزون — Stock Movements
 * Supports 3 withdrawal units:
 *   1. بالوزن/الكمية  — direct kg/unit entry
 *   2. بالقطعة        — pieces × weight_per_piece
 *   3. بالمساحة (ألواح فولاذ) — pieces × (L×W) → m² → kg via density
 *
 * For IN movements of plate material: enter dimensions + weight → compute & store density
 */
import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'

const DESTINATIONS = [
  { value: 'workshop',    label: 'الورشة' },
  { value: 'production',  label: 'خط الإنتاج' },
  { value: 'site',        label: 'موقع خارجي' },
  { value: 'maintenance', label: 'صيانة' },
  { value: 'scrap',       label: 'هالك' },
  { value: 'sale',        label: 'بيع' },
  { value: 'transfer',    label: 'تحويل مخزن' },
  { value: 'other',       label: 'أخرى' },
]
const SOURCES = [
  { value: 'purchase',   label: 'مشتريات (فاتورة مورد)' },
  { value: 'production', label: 'إنتاج داخلي' },
  { value: 'return',     label: 'مردود من الإنتاج' },
  { value: 'transfer',   label: 'تحويل من مخزن' },
  { value: 'adjustment', label: 'تسوية جرد' },
  { value: 'other',      label: 'أخرى' },
]

// ── Steel plate calculator ─────────────────────────────────────
function PlateDimRows({ dims, onChange, onAdd, onRemove }) {
  return (
    <div className="flex flex-col gap-2">
      {/* Header */}
      <div className="grid text-[11px] font-semibold text-erp-muted"
           style={{ gridTemplateColumns: '60px 1fr 1fr 80px auto' }}>
        <div className="text-center">م</div>
        <div className="text-center">الطول (سم)</div>
        <div className="text-center">العرض (سم)</div>
        <div className="text-center">المساحة (م²)</div>
        <div />
      </div>
      {dims.map((d, i) => {
        const area = d.length && d.width
          ? ((d.length / 100) * (d.width / 100)).toFixed(4)
          : '—'
        return (
          <div key={d.id} className="grid gap-1.5 items-center"
               style={{ gridTemplateColumns: '60px 1fr 1fr 80px auto' }}>
            <div className="text-center text-xs text-erp-muted font-semibold">{i + 1}</div>
            <input className="field-input text-sm text-center"
              type="number" step="0.1" min="0.1" placeholder="مثال: 100"
              value={d.length || ''}
              onChange={e => onChange(d.id, 'length', e.target.value)} />
            <input className="field-input text-sm text-center"
              type="number" step="0.1" min="0.1" placeholder="مثال: 300"
              value={d.width || ''}
              onChange={e => onChange(d.id, 'width', e.target.value)} />
            <div className="text-center text-xs font-semibold"
                 style={{ color: area !== '—' ? 'var(--teal)' : 'var(--muted)' }}>
              {area}
            </div>
            {dims.length > 1 && (
              <button className="btn btn-sm btn-danger text-xs" onClick={() => onRemove(d.id)}>✕</button>
            )}
          </div>
        )
      })}
      <button className="btn btn-sm text-xs self-start" onClick={onAdd}>+ إضافة صف أبعاد</button>
    </div>
  )
}

export default function Movements() {
  const { canWrite } = useAuth()
  const write = canWrite('inventory')
  const toast = useToast()

  const { data: materials } = useApi(() => api.inventory.materials())
  const { data: workOrders } = useApi(() => api.accounting.workOrders().catch(() => []))

  const [matId, setMatId] = useState('')
  const { data: movs, loading, reload } = useApi(
    () => matId ? api.inventory.movements(matId) : Promise.resolve([]),
    [matId]
  )

  const selectedMat = useMemo(
    () => materials?.find(m => m.id == matId),
    [materials, matId]
  )

  // Is this a plate/steel material?
  const isPlate = selectedMat?.is_plate || selectedMat?.material_kind === 'plate' || false
  const isBar = selectedMat?.material_kind === 'bar'
  const density = selectedMat?.density_kg_m2   // kg/m²
  const thickness = selectedMat?.thickness_mm

  // ── Form state ──────────────────────────────────────────────
  const [modal, setModal] = useState(false)
  const emptyForm = () => ({
    movement_type:   'in',
    withdrawal_unit: 'weight',
    movement_date:   today(),
    qty:             '',
    unit_cost:       selectedMat?.unit_cost || '',
    pieces_count:    '',
    weight_per_piece: '',
    destination:     '',
    source:          '',
    work_order_id:   '',
    reference:       '',
    notes:           '',
    material_id:     matId || '',
    // plate IN
    plate_length_cm: '',
    plate_width_cm:  '',
    plate_weight_kg: '',
    bar_length_cm: '',
    // plate OUT multi-dim
    plateDims: [{ id: Date.now(), length: '', width: '' }],
    plate_pieces_per_dim: '1',
  })
  const [form, setForm] = useState(emptyForm())

  const openModal = () => {
    setForm({ ...emptyForm(), material_id: matId })
    setModal(true)
  }

  const f = (field, val) => setForm(prev => ({ ...prev, [field]: val }))

  // ── Plate OUT calculations ──────────────────────────────────
  const totalAreaM2 = useMemo(() => {
    if (form.movement_type !== 'out' || form.withdrawal_unit !== 'plate_area') return null
    return form.plateDims.reduce((sum, d) => {
      if (!d.length || !d.width) return sum
      const area = (d.length / 100) * (d.width / 100)
      return sum + area * Number(form.plate_pieces_per_dim || 1)
    }, 0)
  }, [form.movement_type, form.withdrawal_unit, form.plateDims, form.plate_pieces_per_dim])

  const calcKgFromArea = useMemo(() => {
    if (!totalAreaM2 || !density) return null
    return totalAreaM2 * density
  }, [totalAreaM2, density])

  // ── Plate IN density computation ────────────────────────────
  const computedDensity = useMemo(() => {
    if (form.movement_type !== 'in') return null
    const { plate_length_cm: l, plate_width_cm: w, plate_weight_kg: wt } = form
    if (!l || !w || !wt || Number(wt) <= 0) return null
    const area = (l / 100) * (w / 100)
    if (area <= 0) return null
    return (Number(wt) / area).toFixed(4)
  }, [form.movement_type, form.plate_length_cm, form.plate_width_cm, form.plate_weight_kg])

  // ── Piece calculations ──────────────────────────────────────
  const calcQtyPiece = useMemo(() => {
    if (form.withdrawal_unit !== 'piece') return null
    const { pieces_count: pc, weight_per_piece: wp } = form
    if (!pc || !wp) return null
    return (Number(pc) * Number(wp)).toFixed(3)
  }, [form.withdrawal_unit, form.pieces_count, form.weight_per_piece])

  const calcQtyBar = useMemo(() => {
    if (!['bar_length', 'bar_count'].includes(form.withdrawal_unit)) return null
    const wpm = Number(selectedMat?.weight_per_meter_kg || 0)
    if (!wpm) return null
    if (form.withdrawal_unit === 'bar_count') {
      const fullLength = Number(selectedMat?.bar_length_cm || 0) / 100
      if (!fullLength || !form.pieces_count) return null
      return (Number(form.pieces_count) * fullLength * wpm).toFixed(3)
    }
    if (!form.bar_length_cm) return null
    return ((Number(form.bar_length_cm) / 100) * wpm * Number(form.pieces_count || 1)).toFixed(3)
  }, [form.withdrawal_unit, form.bar_length_cm, form.pieces_count, selectedMat])

  // ── Save ────────────────────────────────────────────────────
  const [save, saving] = useAction(async () => {
    const matIdToUse = form.material_id || matId
    if (!matIdToUse)       { toast('اختر المادة',  'error'); return }
    if (!form.unit_cost)   { toast('أدخل سعر الوحدة', 'error'); return }

    let qty = Number(form.qty || 0)
    let payload = {
      material_id:   Number(matIdToUse),
      movement_type: form.movement_type,
      unit_cost:     Number(form.unit_cost),
      movement_date: form.movement_date || today(),
      reference:     form.reference || null,
      notes:         form.notes || null,
      work_order_id: form.work_order_id ? Number(form.work_order_id) : null,
      withdrawal_unit: form.withdrawal_unit,
      destination:   form.movement_type === 'out' ? form.destination : null,
      source:        form.movement_type === 'in'  ? form.source       : null,
    }

    if (form.movement_type === 'out') {
      if (form.withdrawal_unit === 'piece') {
        if (!form.pieces_count || !form.weight_per_piece) {
          toast('أدخل عدد القطع والوزن', 'error'); return
        }
        qty = Number(form.pieces_count) * Number(form.weight_per_piece)
        payload.pieces_count    = Number(form.pieces_count)
        payload.weight_per_piece = Number(form.weight_per_piece)

      } else if (form.withdrawal_unit === 'plate_area') {
        if (!totalAreaM2 || totalAreaM2 <= 0) {
          toast('أدخل أبعاد الألواح', 'error'); return
        }
        if (!density) {
          toast('المادة مش عندها كثافة محسوبة — سجّل وارد بالأبعاد أولاً', 'error'); return
        }
        qty = calcKgFromArea
        payload.withdrawal_unit = 'plate_area'
        payload.plate_area_m2   = totalAreaM2
        payload.plate_pieces    = Number(form.plate_pieces_per_dim || 1)

      } else if (form.withdrawal_unit === 'bar_length') {
        if (!form.bar_length_cm || Number(form.bar_length_cm) <= 0) {
          toast('أدخل طول الحديد المطلوب', 'error'); return
        }
        if (!selectedMat?.weight_per_meter_kg) {
          toast('الخامة ليس لها وزن متر محسوب', 'error'); return
        }
        qty = Number(calcQtyBar || 0)
        payload.withdrawal_unit = 'bar_length'
        payload.bar_length_cm = Number(form.bar_length_cm)
        payload.pieces_count = Number(form.pieces_count || 1)

      } else {
        // weight
        if (qty <= 0) { toast('أدخل الكمية', 'error'); return }
      }

    } else {
      // IN
      if (form.withdrawal_unit === 'plate_in') {
        // Plate sheet being added
        if (!form.plate_weight_kg || Number(form.plate_weight_kg) <= 0) {
          toast('أدخل وزن اللوح', 'error'); return
        }
        qty = Number(form.plate_weight_kg) * Number(form.pieces_count || 1)
        payload.plate_length_cm = form.plate_length_cm ? Number(form.plate_length_cm) : null
        payload.plate_width_cm  = form.plate_width_cm  ? Number(form.plate_width_cm)  : null
        payload.plate_weight_kg = Number(form.plate_weight_kg)
        payload.pieces_count = Number(form.pieces_count || 1)
        payload.withdrawal_unit = 'plate_in'
      } else if (form.withdrawal_unit === 'bar_count') {
        if (!form.pieces_count || Number(form.pieces_count) <= 0) {
          toast('أدخل عدد الأسياخ', 'error'); return
        }
        if (!selectedMat?.weight_per_meter_kg || !selectedMat?.bar_length_cm) {
          toast('الخامة ليس لها طول ووزن متر محفوظين', 'error'); return
        }
        qty = Number(calcQtyBar || 0)
        payload.withdrawal_unit = 'bar_count'
        payload.pieces_count = Number(form.pieces_count)
      } else {
        if (qty <= 0) { toast('أدخل الكمية', 'error'); return }
      }
    }

    payload.qty = qty
    await api.inventory.createMov(payload)
    toast('تمت الحركة بنجاح')
    setModal(false)
    reload()
  })

  // ── Dim management for plate OUT ────────────────────────────
  const addDim    = () => setForm(p => ({ ...p, plateDims: [...p.plateDims, { id: Date.now(), length: '', width: '' }] }))
  const removeDim = (id) => setForm(p => ({ ...p, plateDims: p.plateDims.filter(d => d.id !== id) }))
  const updateDim = (id, field, val) =>
    setForm(p => ({ ...p, plateDims: p.plateDims.map(d => d.id === id ? { ...d, [field]: val } : d) }))

  return (
    <div className="flex flex-col gap-4">
      {/* Top bar */}
      <div className="card flex items-center gap-3 py-3">
        <div className="flex-1">
          <label className="field-label mb-1">المادة</label>
          <select className="field-select w-full" value={matId}
            onChange={e => setMatId(e.target.value)}>
            <option value="">— اختر مادة لعرض حركاتها —</option>
            {materials?.map(m => (
              <option key={m.id} value={m.id}>
                {m.code} — {m.name_ar}
                {m.is_plate ? ` 🔩 (${m.thickness_mm || '?'}mm)` : ''}
              </option>
            ))}
          </select>
        </div>

        {selectedMat && (
          <div className="flex-shrink-0 flex flex-col items-end gap-0.5">
            <div className="px-3 py-1.5 rounded-erp text-sm"
                 style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
              <span className="font-bold">{Number(selectedMat.stock_qty || 0).toFixed(3)}</span>
              <span className="text-xs mr-1">{selectedMat.unit}</span>
            </div>
            {density && (
              <div className="text-[10px] text-erp-muted">
                كثافة: {Number(density).toFixed(2)} كجم/م²
                {thickness ? ` · ${thickness}mm` : ''}
              </div>
            )}
          </div>
        )}

        {write && (
          <button className="btn btn-primary btn-sm flex-shrink-0" onClick={openModal}>
            + تسجيل حركة
          </button>
        )}
      </div>

      {/* Movements table */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr>
                <th>التاريخ</th>
                <th>النوع</th>
                <th>الكمية (كجم)</th>
                <th>المساحة (م²)</th>
                <th>السعر</th>
                <th>الإجمالي</th>
                <th>الوجهة / المرجع</th>
                <th>الرصيد</th>
              </tr>
            </thead>
            <tbody>
              {loading && matId && (
                <tr><td colSpan={8} className="text-center py-10 animate-pulse text-erp-muted">جاري التحميل…</td></tr>
              )}
              {!matId && (
                <tr><td colSpan={8} className="text-center py-10 text-erp-muted">اختر مادة أولاً</td></tr>
              )}
              {matId && !loading && !movs?.length && (
                <tr><td colSpan={8} className="text-center py-10 text-erp-muted">لا توجد حركات</td></tr>
              )}
              {movs?.map(m => {
                const isIn    = m.movement_type === 'in'
                const total   = Number(m.qty || 0) * Number(m.unit_cost || 0)
                return (
                  <tr key={m.id}>
                    <td className="text-xs text-erp-muted">{fmtDate(m.movement_date)}</td>
                    <td>
                      <span className={`badge ${isIn ? 'badge-green' : 'badge-red'}`}>
                        {isIn ? 'وارد' : 'صادر'}
                      </span>
                      {m.withdrawal_unit === 'plate_area' && (
                        <span className="badge badge-blue mr-1 text-[10px]">لوح</span>
                      )}
                      {m.withdrawal_unit === 'piece' && (
                        <span className="badge badge-amber mr-1 text-[10px]">قطع</span>
                      )}
                    </td>
                    <td className="font-semibold font-mono">
                      {Number(m.qty || 0).toFixed(3)}
                      {m.pieces_count && (
                        <div className="text-[10px] text-erp-muted font-normal">
                          {m.pieces_count} قطعة × {Number(m.weight_per_piece || 0).toFixed(2)}
                        </div>
                      )}
                    </td>
                    <td className="text-xs text-erp-muted">
                      {m.plate_area_m2
                        ? <span className="font-semibold" style={{ color: 'var(--blue)' }}>
                            {Number(m.plate_area_m2).toFixed(4)} م²
                          </span>
                        : m.plate_length_cm
                          ? <span className="text-[11px]">
                              {m.plate_length_cm}×{m.plate_width_cm} سم
                            </span>
                          : '—'}
                    </td>
                    <td className="text-sm font-mono">{m.unit_cost ? egp(m.unit_cost) : '—'}</td>
                    <td className="font-semibold" style={{ color: isIn ? 'var(--green)' : 'var(--red)' }}>
                      {total > 0 ? egp(total) : '—'}
                    </td>
                    <td className="text-xs text-erp-muted">
                      {m.reference && <div className="font-mono">{m.reference}</div>}
                      {m.notes && <div className="text-[11px]">{m.notes}</div>}
                      {!m.reference && !m.notes && '—'}
                    </td>
                    <td className="font-medium font-mono">{Number(m.balance_after || 0).toFixed(3)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Modal ── */}
      <Modal open={modal} onClose={() => setModal(false)} title="تسجيل حركة مخزون">
        <div className="flex flex-col gap-3">
          {/* Material selector when no preselection */}
          {!matId && (
            <div>
              <label className="field-label">المادة *</label>
              <select className="field-select w-full" value={form.material_id || ''}
                onChange={e => f('material_id', e.target.value)}>
                <option value="">— اختر مادة —</option>
                {materials?.map(m => (
                  <option key={m.id} value={m.id}>
                    {m.name_ar} ({m.code})
                    {m.is_plate ? ` 🔩 ${m.thickness_mm || '?'}mm` : ''}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Material info chip */}
          {selectedMat && (
            <div className="flex items-center justify-between px-3 py-2 rounded-erp"
                 style={{ background: 'var(--bg)' }}>
              <div>
                <span className="font-medium text-sm">{selectedMat.code} — {selectedMat.name_ar}</span>
                {selectedMat.is_plate && (
                  <span className="badge badge-blue mr-2 text-[10px]">
                    🔩 فولاذ {selectedMat.thickness_mm ? selectedMat.thickness_mm + 'mm' : ''}
                  </span>
                )}
              </div>
              <span className="text-sm font-semibold" style={{ color: 'var(--teal)' }}>
                رصيد: {Number(selectedMat.stock_qty || 0).toFixed(3)} {selectedMat.unit}
              </span>
            </div>
          )}

          {/* Type + Date */}
          <FormGrid cols={2}>
            <div>
              <label className="field-label">النوع *</label>
              <select className="field-select w-full" value={form.movement_type}
                onChange={e => {
                  f('movement_type', e.target.value)
                  f('withdrawal_unit', e.target.value === 'in' ? (isPlate ? 'plate_in' : isBar ? 'bar_count' : 'weight') : 'weight')
                }}>
                <option value="in">↓ وارد</option>
                <option value="out">↑ صادر (سحب)</option>
              </select>
            </div>
            <Input label="التاريخ *" type="date"
              value={form.movement_date}
              onChange={e => f('movement_date', e.target.value)} />
          </FormGrid>

          {/* ════ IN: withdrawal mode ════ */}
          {form.movement_type === 'in' && (
            <>
              {/* Mode selector */}
              <div className="flex gap-4 px-3 py-2 rounded-erp" style={{ background: 'var(--bg)' }}>
                {[
                  { val: 'weight',   label: 'بالوزن / الكمية المباشرة' },
                  ...(isPlate ? [{ val: 'plate_in', label: 'ألواح: عدد ألواح أو وزن إجمالي' }] : []),
                  ...(isBar ? [{ val: 'bar_count', label: 'حديد: عدد أسياخ' }] : []),
                ].map(opt => (
                  <label key={opt.val} className="flex items-center gap-1.5 cursor-pointer text-sm">
                    <input type="radio" name="in_mode" value={opt.val}
                      checked={form.withdrawal_unit === opt.val}
                      onChange={() => f('withdrawal_unit', opt.val)} />
                    {opt.label}
                  </label>
                ))}
              </div>

              {form.withdrawal_unit === 'plate_in' ? (
                /* Plate IN: enter dimensions + weight → compute density */
                <div className="flex flex-col gap-3 px-3 py-3 rounded-erp"
                     style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
                  <div className="text-xs font-semibold" style={{ color: 'var(--teal)' }}>
                    🔩 أبعاد اللوح — النظام يحسب الكثافة تلقائياً
                  </div>
                  <FormGrid cols={2}>
                    <Input label="طول اللوح (سم)" type="number" step="0.1" min="0.1"
                      value={form.plate_length_cm}
                      onChange={e => f('plate_length_cm', e.target.value)} placeholder="مثال: 100" />
                    <Input label="عرض اللوح (سم)" type="number" step="0.1" min="0.1"
                      value={form.plate_width_cm}
                      onChange={e => f('plate_width_cm', e.target.value)} placeholder="مثال: 300" />
                  </FormGrid>
                  {form.plate_length_cm && form.plate_width_cm && (
                    <div className="text-xs flex justify-between px-2 py-1.5 rounded"
                         style={{ background: '#fff', color: 'var(--teal)' }}>
                      <span>مساحة اللوح</span>
                      <span className="font-bold">
                        {((form.plate_length_cm / 100) * (form.plate_width_cm / 100)).toFixed(4)} م²
                      </span>
                    </div>
                  )}
                  <Input label="وزن اللوح الكلي (كجم) *" type="number" step="0.001" min="0.001"
                    value={form.plate_weight_kg}
                    onChange={e => f('plate_weight_kg', e.target.value)} placeholder="مثال: 50" />
                  <Input label="عدد الألواح" type="number" step="1" min="1"
                    value={form.pieces_count}
                    onChange={e => f('pieces_count', e.target.value)} placeholder="1" />
                  {form.plate_weight_kg && (
                    <div className="text-xs flex justify-between px-2 py-1.5 rounded font-semibold"
                         style={{ background: '#fff', color: 'var(--teal)' }}>
                      <span>إجمالي الوزن الداخل</span>
                      <span>{(Number(form.plate_weight_kg) * Number(form.pieces_count || 1)).toFixed(3)} كجم</span>
                    </div>
                  )}
                  {computedDensity && (
                    <div className="text-xs flex justify-between px-2 py-1.5 rounded font-semibold"
                         style={{ background: '#fff', color: 'var(--teal)' }}>
                      <span>الكثافة المحسوبة (سيُحفظ مع المادة)</span>
                      <span>{computedDensity} كجم/م²</span>
                    </div>
                  )}
                  <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                    value={form.unit_cost}
                    onChange={e => f('unit_cost', e.target.value)} />
                </div>
              ) : form.withdrawal_unit === 'bar_count' ? (
                <div className="flex flex-col gap-2">
                  <FormGrid cols={2}>
                    <Input label="عدد الأسياخ *" type="number" step="1" min="1"
                      value={form.pieces_count} onChange={e => f('pieces_count', e.target.value)} placeholder="10" />
                    <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                      value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                  </FormGrid>
                  {calcQtyBar && (
                    <div className="flex justify-between text-sm px-3 py-2 rounded-erp"
                         style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
                      <span>إجمالي الوزن المحسوب</span>
                      <span className="font-bold">{calcQtyBar} كجم</span>
                    </div>
                  )}
                </div>
              ) : (
                /* Normal IN */
                <FormGrid cols={2}>
                  <Input label="الكمية (كجم) *" type="number" step="0.001" min="0.001"
                    value={form.qty} onChange={e => f('qty', e.target.value)} placeholder="0.000" />
                  <Input label="سعر الوحدة (ج) *" type="number" step="0.01"
                    value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                </FormGrid>
              )}

              {/* Source */}
              <div>
                <label className="field-label">مصدر الوارد</label>
                <select className="field-select w-full" value={form.source}
                  onChange={e => f('source', e.target.value)}>
                  <option value="">— اختر المصدر —</option>
                  {SOURCES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
              </div>
            </>
          )}

          {/* ════ OUT: withdrawal mode ════ */}
          {form.movement_type === 'out' && (
            <>
              {/* Mode selector */}
              <div className="px-3 py-2.5 rounded-erp" style={{ background: 'var(--amber-lt)' }}>
                <div className="text-xs font-semibold mb-2" style={{ color: 'var(--amber)' }}>نوع السحب</div>
                <div className="flex gap-4 flex-wrap">
                  {[
                    { val: 'weight',     label: 'بالوزن / الكمية' },
                    { val: 'piece',      label: 'بالقطعة' },
                    ...(isPlate ? [{ val: 'plate_area', label: '🔩 بالمساحة (ألواح)' }] : []),
                    ...(isBar ? [{ val: 'bar_length', label: 'حديد بالطول' }] : []),
                  ].map(opt => (
                    <label key={opt.val} className="flex items-center gap-1.5 cursor-pointer text-sm">
                      <input type="radio" name="withdrawal_unit" value={opt.val}
                        checked={form.withdrawal_unit === opt.val}
                        onChange={() => f('withdrawal_unit', opt.val)} />
                      {opt.label}
                    </label>
                  ))}
                </div>
              </div>

              {form.withdrawal_unit === 'weight' && (
                <FormGrid cols={2}>
                  <Input label="الكمية (كجم) *" type="number" step="0.001" min="0.001"
                    value={form.qty} onChange={e => f('qty', e.target.value)} placeholder="0.000" />
                  <Input label="سعر الوحدة (ج) *" type="number" step="0.01"
                    value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                </FormGrid>
              )}

              {form.withdrawal_unit === 'bar_length' && (
                <div className="flex flex-col gap-2">
                  <FormGrid cols={3}>
                    <Input label="الطول المطلوب (سم) *" type="number" step="0.1" min="0.1"
                      value={form.bar_length_cm} onChange={e => f('bar_length_cm', e.target.value)} placeholder="50" />
                    <Input label="عدد القطع" type="number" step="1" min="1"
                      value={form.pieces_count} onChange={e => f('pieces_count', e.target.value)} placeholder="1" />
                    <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                      value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                  </FormGrid>
                  {calcQtyBar && (
                    <div className="flex justify-between text-sm px-3 py-2 rounded-erp"
                         style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
                      <span>الوزن المحسوب</span>
                      <span className="font-bold">{calcQtyBar} كجم</span>
                    </div>
                  )}
                </div>
              )}

              {form.withdrawal_unit === 'piece' && (
                <div className="flex flex-col gap-2">
                  <FormGrid cols={2}>
                    <Input label="عدد القطع *" type="number" step="1" min="1"
                      value={form.pieces_count} onChange={e => f('pieces_count', e.target.value)} placeholder="5" />
                    <Input label="وزن القطعة (كجم) *" type="number" step="0.001"
                      value={form.weight_per_piece} onChange={e => f('weight_per_piece', e.target.value)} placeholder="3.000" />
                  </FormGrid>
                  {calcQtyPiece && (
                    <div className="flex justify-between text-sm px-3 py-2 rounded-erp"
                         style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
                      <span>الإجمالي المحسوب</span>
                      <span className="font-bold">{calcQtyPiece} كجم</span>
                    </div>
                  )}
                  <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                    value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                </div>
              )}

              {form.withdrawal_unit === 'plate_area' && (
                <div className="flex flex-col gap-3 p-3 rounded-erp"
                     style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
                  <div className="text-xs font-semibold" style={{ color: 'var(--teal)' }}>
                    🔩 سحب بالمساحة — أدخل أبعاد القطع المطلوبة
                  </div>

                  <div>
                    <label className="field-label mb-1.5">عدد القطع لكل مجموعة أبعاد</label>
                    <input className="field-input w-28 text-center" type="number" step="1" min="1"
                      value={form.plate_pieces_per_dim}
                      onChange={e => f('plate_pieces_per_dim', e.target.value)} />
                  </div>

                  <PlateDimRows
                    dims={form.plateDims}
                    onChange={updateDim}
                    onAdd={addDim}
                    onRemove={removeDim}
                  />

                  {totalAreaM2 !== null && (
                    <div className="flex flex-col gap-1 px-2 py-2 rounded"
                         style={{ background: '#fff' }}>
                      <div className="flex justify-between text-sm">
                        <span className="text-erp-muted">إجمالي المساحة</span>
                        <span className="font-bold" style={{ color: 'var(--blue)' }}>
                          {totalAreaM2.toFixed(4)} م²
                        </span>
                      </div>
                      {density ? (
                        <div className="flex justify-between text-sm">
                          <span className="text-erp-muted">الوزن المقابل (× {Number(density).toFixed(2)} كجم/م²)</span>
                          <span className="font-bold" style={{ color: 'var(--teal)' }}>
                            {calcKgFromArea ? calcKgFromArea.toFixed(3) : '—'} كجم
                          </span>
                        </div>
                      ) : (
                        <div className="text-xs text-red-600">⚠ لا توجد كثافة محسوبة — سجّل وارد بالأبعاد أولاً</div>
                      )}
                    </div>
                  )}

                  {density && (
                    <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                      value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                  )}
                </div>
              )}

              {/* Destination */}
              <div>
                <label className="field-label">الوجهة *</label>
                <select className="field-select w-full" value={form.destination}
                  onChange={e => f('destination', e.target.value)}>
                  <option value="">— اختر الوجهة —</option>
                  {DESTINATIONS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
                </select>
              </div>
            </>
          )}

          {/* Work order + reference (common) */}
          <FormGrid cols={2}>
            <div>
              <label className="field-label">رقم أمر الإنتاج</label>
              <select className="field-select w-full" value={form.work_order_id}
                onChange={e => f('work_order_id', e.target.value)}>
                <option value="">— بدون ربط —</option>
                {workOrders?.map(wo => <option key={wo.id} value={wo.id}>{wo.code}</option>)}
              </select>
            </div>
            <Input label="المرجع (فاتورة / أمر شراء)"
              value={form.reference} onChange={e => f('reference', e.target.value)}
              placeholder="PO-2024-001" />
          </FormGrid>

          <Textarea label="ملاحظات" rows={2}
            value={form.notes} onChange={e => f('notes', e.target.value)} />
        </div>

        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? '…' : 'تسجيل الحركة'}
          </button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
