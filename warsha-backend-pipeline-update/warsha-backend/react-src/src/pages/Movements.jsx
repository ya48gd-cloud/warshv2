/**
 * حركات المخزون — Stock Movements
 * الألواح وارد: عدد ألواح ↔ وزن إجمالي (محسوب تلقائياً)
 * الألواح صادر: بالوزن | لوح كامل (عدد) | مقاسات (طول×عرض)
 * الحديد وارد: عدد أسياخ ↔ وزن إجمالي
 * الحديد صادر: بالوزن | بالطول | بعدد الأسياخ
 */
import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'
import ConfirmDialog from '../components/ui/ConfirmDialog'

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

function InfoRow({ label, value, color }) {
  return (
    <div className="flex justify-between text-sm px-3 py-1.5 rounded"
         style={{ background: '#fff', color: color || 'var(--text)' }}>
      <span className="text-erp-muted">{label}</span>
      <span className="font-semibold">{value}</span>
    </div>
  )
}

function PlateDimRows({ dims, onChange, onAdd, onRemove }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="grid text-[11px] font-semibold text-erp-muted"
           style={{ gridTemplateColumns: '40px 1fr 1fr 90px auto' }}>
        <div className="text-center">م</div>
        <div className="text-center">الطول (سم)</div>
        <div className="text-center">العرض (سم)</div>
        <div className="text-center">المساحة (م²)</div>
        <div />
      </div>
      {dims.map((d, i) => {
        const area = d.length && d.width ? ((d.length/100)*(d.width/100)).toFixed(4) : '—'
        return (
          <div key={d.id} className="grid gap-1.5 items-center"
               style={{ gridTemplateColumns: '40px 1fr 1fr 90px auto' }}>
            <div className="text-center text-xs text-erp-muted font-semibold">{i+1}</div>
            <input className="field-input text-sm text-center" type="number" step="0.1" min="0.1"
              placeholder="100" value={d.length || ''} onChange={e => onChange(d.id,'length',e.target.value)} />
            <input className="field-input text-sm text-center" type="number" step="0.1" min="0.1"
              placeholder="300" value={d.width  || ''} onChange={e => onChange(d.id,'width', e.target.value)} />
            <div className="text-center text-xs font-semibold"
                 style={{ color: area!=='—' ? 'var(--teal)' : 'var(--muted)' }}>{area}</div>
            {dims.length > 1 && (
              <button className="btn btn-sm btn-danger text-xs" onClick={() => onRemove(d.id)}>✕</button>
            )}
          </div>
        )
      })}
      <button className="btn btn-sm text-xs self-start" onClick={onAdd}>+ صف أبعاد</button>
    </div>
  )
}

export default function Movements() {
  const { canWrite } = useAuth()
  const write = canWrite('inventory')
  const toast = useToast()

  const { data: materials } = useApi(() => api.inventory.materials())
  const { data: workOrders } = useApi(() => api.accounting.workOrders().catch(bgErr('أوامر الإنتاج', [])))

  const [matId, setMatId] = useState('')
  const { data: movs, loading, reload } = useApi(
    () => matId ? api.inventory.movements(matId) : Promise.resolve([]),
    [matId]
  )

  // بطاقة الرصيد في الشريط العلوي مرتبطة بالمادة المختارة من قائمة الفلترة فوق فقط
  const topBarMat = useMemo(() => materials?.find(m => m.id == matId), [materials, matId])

  // ── Form state ──────────────────────────────────────────────
  const [modal, setModal] = useState(false)
  const [confirmOut, setConfirmOut] = useState(null) // { payload, qty }
  const [form, setForm] = useState(() => ({
    movement_type: 'in', withdrawal_unit: 'weight', movement_date: today(),
    qty: '', unit_cost: '', pieces_count: '1', weight_per_piece: '',
    destination: '', source: '', work_order_id: '', reference: '', notes: '',
    material_id: '', plate_length_cm: '', plate_width_cm: '', plate_weight_kg: '',
    plateDims: [{ id: Date.now(), length: '', width: '' }], plate_pieces_per_dim: '1',
    bar_length_cm: '', waste_g: '',
  }))

  // المادة الفعلية بالنسبة للمودال: لو اتفتح من غير فلترة (matId فاضي) واختار
  // المستخدم مادة من داخل المودال نفسه، كل الحسابات (ألواح/حديد/الرصيد) لازم
  // تتبع اختيار المودال ده — قبل كده كانت الحسابات دي بتفضل مرتبطة بـ matId
  // الخارجي بس، فلو المستخدم فتح "+ تسجيل حركة" من غير ما يختار مادة فوق
  // الأول، كل قيم اللوح/الحديد المحسوبة والتحذيرات كانت بتختفي بصمت.
  const activeMatId = form.material_id || matId
  const selectedMat = useMemo(() => materials?.find(m => m.id == activeMatId), [materials, activeMatId])
  const isPlate  = selectedMat?.is_plate || selectedMat?.material_kind === 'plate' || false
  const isBar    = selectedMat?.material_kind === 'bar'
  const density  = selectedMat?.density_kg_m2
  const thickness = selectedMat?.thickness_mm

  const emptyForm = () => ({
    movement_type:   'in',
    withdrawal_unit: isPlate ? 'plate_in' : isBar ? 'bar_count' : 'weight',
    movement_date:   today(),
    qty:             '',
    unit_cost:       selectedMat?.unit_cost || '',
    pieces_count:    '1',
    weight_per_piece: '',
    destination:     '',
    source:          '',
    work_order_id:   '',
    reference:       '',
    notes:           '',
    material_id:     matId || '',
    // plate IN
    plate_length_cm: selectedMat?.plate_length_cm || '',
    plate_width_cm:  selectedMat?.plate_width_cm  || '',
    plate_weight_kg: selectedMat?.plate_weight_kg || '',
    // plate OUT dims
    plateDims: [{ id: Date.now(), length: '', width: '' }],
    plate_pieces_per_dim: '1',
    // bar
    bar_length_cm: '',
    waste_g: '',
  })
  const openModal = () => { setForm({ ...emptyForm(), material_id: matId }); setModal(true) }
  const f = (field, val) => setForm(prev => ({ ...prev, [field]: val }))

  // ── Calculations ─────────────────────────────────────────────

  // الألواح وارد: وزن إجمالي
  const plateInWeight = useMemo(() => {
    if (form.movement_type !== 'in' || form.withdrawal_unit !== 'plate_in') return null
    const w  = Number(form.plate_weight_kg || 0)
    const pc = Number(form.pieces_count || 1)
    if (!w) return null
    return (w * pc).toFixed(3)
  }, [form.movement_type, form.withdrawal_unit, form.plate_weight_kg, form.pieces_count])

  // الألواح وارد: كثافة محسوبة
  const plateInDensity = useMemo(() => {
    if (form.movement_type !== 'in' || form.withdrawal_unit !== 'plate_in') return null
    const { plate_length_cm: l, plate_width_cm: w, plate_weight_kg: wt } = form
    if (!l || !w || !wt || Number(wt) <= 0) return null
    const area = (Number(l)/100) * (Number(w)/100)
    return area > 0 ? (Number(wt)/area).toFixed(4) : null
  }, [form.movement_type, form.withdrawal_unit, form.plate_length_cm, form.plate_width_cm, form.plate_weight_kg])

  // الألواح صادر لوح كامل
  const plateOutFullWeight = useMemo(() => {
    if (form.movement_type !== 'out' || form.withdrawal_unit !== 'plate_count') return null
    const pc = Number(form.pieces_count || 0)
    const wt = Number(selectedMat?.plate_weight_kg || 0)
    return (pc && wt) ? (pc * wt).toFixed(3) : null
  }, [form.movement_type, form.withdrawal_unit, form.pieces_count, selectedMat])

  // الألواح صادر مقاسات
  const totalAreaM2 = useMemo(() => {
    if (form.movement_type !== 'out' || form.withdrawal_unit !== 'plate_area') return null
    return form.plateDims.reduce((sum, d) => {
      if (!d.length || !d.width) return sum
      return sum + (d.length/100)*(d.width/100) * Number(form.plate_pieces_per_dim||1)
    }, 0)
  }, [form.movement_type, form.withdrawal_unit, form.plateDims, form.plate_pieces_per_dim])

  const calcKgFromArea = useMemo(() => {
    if (!totalAreaM2 || !density) return null
    const waste = Number(form.waste_g || 0) / 1000
    return (totalAreaM2 * Number(density) + waste).toFixed(3)
  }, [totalAreaM2, density, form.waste_g])

  // الحديد
  const calcQtyBar = useMemo(() => {
    const wpm = Number(selectedMat?.weight_per_meter_kg || 0)
    if (!wpm) return null
    if (form.withdrawal_unit === 'bar_count') {
      const fullLength = Number(selectedMat?.bar_length_cm || 0) / 100
      const pc = Number(form.pieces_count || 0)
      return (pc && fullLength) ? (pc * fullLength * wpm).toFixed(3) : null
    }
    if (form.withdrawal_unit === 'bar_length') {
      const l = Number(form.bar_length_cm || 0) / 100
      const pc = Number(form.pieces_count || 1)
      const waste = Number(form.waste_g || 0) / 1000
      return l > 0 ? (l * wpm * pc + waste).toFixed(3) : null
    }
    return null
  }, [form.withdrawal_unit, form.bar_length_cm, form.pieces_count, form.waste_g, selectedMat])

  // ── Balance warning (OUT movements, real-time) ────────────────
  const computedOutQty = useMemo(() => {
    if (form.movement_type !== 'out') return 0
    if (form.withdrawal_unit === 'plate_count') return Number(plateOutFullWeight || 0)
    if (form.withdrawal_unit === 'plate_area')  return Number(calcKgFromArea || 0)
    if (form.withdrawal_unit === 'bar_length' || form.withdrawal_unit === 'bar_count') return Number(calcQtyBar || 0)
    if (form.withdrawal_unit === 'piece') return Number(form.pieces_count || 0) * Number(form.weight_per_piece || 0)
    return Number(form.qty || 0)
  }, [form.movement_type, form.withdrawal_unit, plateOutFullWeight, calcKgFromArea, calcQtyBar, form.qty, form.pieces_count, form.weight_per_piece])

  const stockQty = Number(selectedMat?.stock_qty || 0)
  const isOverBalance = form.movement_type === 'out' && computedOutQty > 0 && computedOutQty > stockQty

  // ── Confirmed post (after dialog) ─────────────────────────────
  const [doPost, posting] = useAction(async (payload) => {
    await api.inventory.createMov(payload)
    toast('تمت الحركة بنجاح')
    setModal(false); setConfirmOut(null); reload()
  })

  // ── عكس حركة (بدل حذفها) ───────────────────────────────────────
  const [confirmReverse, setConfirmReverse] = useState(null) // movement قيد التأكيد
  const [doReverse, reversing] = useAction(async (m) => {
    await api.inventory.deleteMovement(m.id)
    toast('تم عكس الحركة بنجاح')
    setConfirmReverse(null); reload()
  })

  // ── Save ──────────────────────────────────────────────────────
  const [save, saving] = useAction(async () => {
    const matIdToUse = form.material_id || matId
    if (!matIdToUse)     { toast('اختر المادة',     'error'); return }
    if (!form.unit_cost || Number(form.unit_cost) <= 0) { toast('أدخل سعر وحدة أكبر من صفر', 'error'); return }
    if (form.movement_type === 'out' && !form.destination) { toast('اختر الوجهة', 'error'); return }

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
      source:        form.movement_type === 'in'  ? form.source      : null,
    }

    if (form.movement_type === 'in') {
      if (form.withdrawal_unit === 'plate_in') {
        if (!form.plate_weight_kg || Number(form.plate_weight_kg) <= 0) {
          toast('أدخل وزن اللوح', 'error'); return
        }
        qty = Number(plateInWeight || 0)
        payload.plate_length_cm = form.plate_length_cm ? Number(form.plate_length_cm) : null
        payload.plate_width_cm  = form.plate_width_cm  ? Number(form.plate_width_cm)  : null
        payload.plate_weight_kg = Number(form.plate_weight_kg)
        payload.pieces_count    = Number(form.pieces_count || 1)
      } else if (form.withdrawal_unit === 'bar_count') {
        if (!form.pieces_count || Number(form.pieces_count) <= 0) {
          toast('أدخل عدد الأسياخ', 'error'); return
        }
        if (!selectedMat?.weight_per_meter_kg || !selectedMat?.bar_length_cm) {
          toast('المادة ليس لها بيانات طول ووزن', 'error'); return
        }
        qty = Number(calcQtyBar || 0)
        payload.pieces_count = Number(form.pieces_count)
      } else {
        if (qty <= 0) { toast('أدخل الكمية', 'error'); return }
      }

    } else { // OUT
      if (form.withdrawal_unit === 'plate_count') {
        if (!form.pieces_count || Number(form.pieces_count) <= 0) {
          toast('أدخل عدد الألواح', 'error'); return
        }
        if (!selectedMat?.plate_weight_kg) {
          toast('المادة ليس لها وزن لوح محفوظ', 'error'); return
        }
        qty = Number(plateOutFullWeight || 0)
        payload.pieces_count = Number(form.pieces_count)
        payload.withdrawal_unit = 'plate_count'

      } else if (form.withdrawal_unit === 'plate_area') {
        if (!totalAreaM2 || totalAreaM2 <= 0) { toast('أدخل أبعاد الألواح', 'error'); return }
        if (!density) { toast('المادة مش عندها كثافة — سجّل وارد بالأبعاد أولاً', 'error'); return }
        qty = Number(calcKgFromArea || 0)
        payload.withdrawal_unit = 'plate_area'
        payload.plate_area_m2   = totalAreaM2
        payload.plate_pieces    = Number(form.plate_pieces_per_dim || 1)
        if (form.waste_g) payload.waste_g = Number(form.waste_g)

      } else if (form.withdrawal_unit === 'bar_length') {
        if (!form.bar_length_cm || Number(form.bar_length_cm) <= 0) {
          toast('أدخل طول الحديد', 'error'); return
        }
        if (!selectedMat?.weight_per_meter_kg) {
          toast('المادة ليس لها وزن متر', 'error'); return
        }
        qty = Number(calcQtyBar || 0)
        payload.bar_length_cm = Number(form.bar_length_cm)
        payload.pieces_count  = Number(form.pieces_count || 1)
        if (form.waste_g) payload.waste_g = Number(form.waste_g)

      } else if (form.withdrawal_unit === 'bar_count') {
        if (!form.pieces_count || Number(form.pieces_count) <= 0) {
          toast('أدخل عدد الأسياخ', 'error'); return
        }
        qty = Number(calcQtyBar || 0)
        payload.pieces_count = Number(form.pieces_count)

      } else if (form.withdrawal_unit === 'piece') {
        if (!form.pieces_count || !form.weight_per_piece) {
          toast('أدخل عدد القطع والوزن', 'error'); return
        }
        qty = Number(form.pieces_count) * Number(form.weight_per_piece)
        payload.pieces_count     = Number(form.pieces_count)
        payload.weight_per_piece = Number(form.weight_per_piece)

      } else {
        if (qty <= 0) { toast('أدخل الكمية', 'error'); return }
      }
    }

    if (qty <= 0) { toast('الكمية يجب أن تكون أكبر من صفر', 'error'); return }
    payload.qty = qty

    // OUT movements: always show confirmation dialog (with balance warning if over stock)
    if (form.movement_type === 'out') {
      setConfirmOut({ payload, qty })
      return
    }

    await api.inventory.createMov(payload)
    toast('تمت الحركة بنجاح')
    setModal(false); reload()
  })

  const addDim    = () => setForm(p => ({ ...p, plateDims: [...p.plateDims, { id: Date.now(), length:'', width:'' }] }))
  const removeDim = (id) => setForm(p => ({ ...p, plateDims: p.plateDims.filter(d => d.id!==id) }))
  const updateDim = (id, field, val) =>
    setForm(p => ({ ...p, plateDims: p.plateDims.map(d => d.id===id ? { ...d, [field]:val } : d) }))

  return (
    <div className="flex flex-col gap-4">
      {/* Top bar */}
      <div className="card flex items-center gap-3 py-3">
        <div className="flex-1">
          <label className="field-label mb-1">المادة</label>
          <select className="field-select w-full" value={matId} onChange={e => setMatId(e.target.value)}>
            <option value="">— اختر مادة لعرض حركاتها —</option>
            {(materials || []).filter(m => (m.material_kind||'general') !== 'plate' && (m.material_kind||'general') !== 'bar').length > 0 && (
              <optgroup label="── مواد عامة ──">
                {materials?.filter(m => !m.is_plate && m.material_kind !== 'plate' && m.material_kind !== 'bar').map(m => (
                  <option key={m.id} value={m.id}>{m.code} — {m.name_ar}</option>
                ))}
              </optgroup>
            )}
            {materials?.some(m => m.is_plate || m.material_kind === 'plate') && (
              <optgroup label="── 🔩 الألواح ──">
                {materials?.filter(m => m.is_plate || m.material_kind === 'plate').map(m => (
                  <option key={m.id} value={m.id}>{m.code} — {m.name_ar} {m.thickness_mm ? `(${m.thickness_mm}mm)` : ''}</option>
                ))}
              </optgroup>
            )}
            {materials?.some(m => m.material_kind === 'bar') && (
              <optgroup label="── 📏 الحديد / السيخ ──">
                {materials?.filter(m => m.material_kind === 'bar').map(m => (
                  <option key={m.id} value={m.id}>{m.code} — {m.name_ar}</option>
                ))}
              </optgroup>
            )}
          </select>
        </div>

        {topBarMat && (
          <div className="flex-shrink-0 flex flex-col items-end gap-0.5">
            <div className="px-3 py-1.5 rounded-erp text-sm"
                 style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
              <span className="font-bold">{Number(topBarMat.stock_qty||0).toFixed(3)}</span>
              <span className="text-xs mr-1">{topBarMat.unit}</span>
            </div>
            {topBarMat.density_kg_m2 && (
              <div className="text-[10px] text-erp-muted">
                كثافة: {Number(topBarMat.density_kg_m2).toFixed(2)} كجم/م²
                {topBarMat.thickness_mm ? ` · ${topBarMat.thickness_mm}mm` : ''}
              </div>
            )}
            {topBarMat?.weight_per_meter_kg && (
              <div className="text-[10px] text-erp-muted">
                وزن المتر: {Number(topBarMat.weight_per_meter_kg).toFixed(4)} كجم/م
              </div>
            )}
          </div>
        )}

        {write && (
          <button className="btn btn-primary btn-sm flex-shrink-0" onClick={openModal}>+ تسجيل حركة</button>
        )}
      </div>

      {/* Movements table */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr>
                <th>التاريخ</th><th>النوع</th><th>الكمية (كجم)</th>
                <th>تفاصيل</th><th>السعر</th><th>الإجمالي</th>
                <th>الوجهة / المرجع</th><th>الرصيد</th>
                {write && <th></th>}
              </tr>
            </thead>
            <tbody>
              {loading && matId && (
                <tr><td colSpan={write ? 9 : 8} className="text-center py-10 animate-pulse text-erp-muted">جاري التحميل…</td></tr>
              )}
              {!matId && (
                <tr><td colSpan={write ? 9 : 8} className="text-center py-10 text-erp-muted">اختر مادة أولاً</td></tr>
              )}
              {matId && !loading && !movs?.length && (
                <tr><td colSpan={write ? 9 : 8} className="text-center py-10 text-erp-muted">لا توجد حركات</td></tr>
              )}
              {movs?.map(m => {
                const isIn = m.movement_type === 'in'
                return (
                  <tr key={m.id}>
                    <td className="text-xs text-erp-muted">{fmtDate(m.movement_date)}</td>
                    <td>
                      <span className={`badge ${isIn ? 'badge-green' : 'badge-red'}`}>
                        {isIn ? '↓ وارد' : '↑ صادر'}
                      </span>
                      {m.withdrawal_unit === 'plate_area'  && <span className="badge badge-blue mr-1 text-[10px]">مقاسات</span>}
                      {m.withdrawal_unit === 'plate_count' && <span className="badge badge-blue mr-1 text-[10px]">لوح كامل</span>}
                      {m.withdrawal_unit === 'plate_in'    && <span className="badge badge-blue mr-1 text-[10px]">وارد ألواح</span>}
                      {m.withdrawal_unit === 'bar_length'  && <span className="badge badge-amber mr-1 text-[10px]">بالطول</span>}
                      {m.withdrawal_unit === 'bar_count'   && <span className="badge badge-amber mr-1 text-[10px]">بالأسياخ</span>}
                      {m.withdrawal_unit === 'piece'       && <span className="badge badge-amber mr-1 text-[10px]">قطع</span>}
                      {m.state === 'reversed' && <span className="badge badge-gray mr-1 text-[10px]">معكوسة</span>}
                      {m.source_doc_type === 'reversal' && <span className="badge badge-gray mr-1 text-[10px]">حركة عكس</span>}
                    </td>
                    <td className="font-semibold font-mono">
                      {Number(m.qty||0).toFixed(3)}
                    </td>
                    <td className="text-xs text-erp-muted">
                      {m.pieces_count && !m.plate_area_m2 && !m.bar_length_cm && (
                        <div>{m.pieces_count} قطعة/سيخ{m.weight_per_piece ? ` × ${Number(m.weight_per_piece).toFixed(3)} كجم` : ''}</div>
                      )}
                      {m.plate_area_m2 && (
                        <div className="font-semibold" style={{ color: 'var(--blue)' }}>
                          {Number(m.plate_area_m2).toFixed(4)} م²
                          {m.plate_length_cm && m.plate_width_cm && (
                            <span className="text-erp-muted font-normal mr-1">
                              ({m.plate_length_cm}×{m.plate_width_cm} سم)
                            </span>
                          )}
                        </div>
                      )}
                      {m.plate_length_cm && !m.plate_area_m2 && (
                        <div style={{ color: 'var(--blue)' }}>{m.plate_length_cm}×{m.plate_width_cm} سم</div>
                      )}
                      {m.bar_length_cm && (
                        <div style={{ color: 'var(--amber)' }}>طول: {m.bar_length_cm} سم
                          {m.pieces_count && m.pieces_count > 1 ? ` × ${m.pieces_count}` : ''}
                        </div>
                      )}
                      {m.waste_g && <div className="text-[10px]">هالك: {m.waste_g} جم</div>}
                    </td>
                    <td className="text-sm font-mono">{m.unit_cost ? egp(m.unit_cost) : '—'}</td>
                    <td className="font-semibold" style={{ color: isIn ? 'var(--green)' : 'var(--red)' }}>
                      {egp(Number(m.qty||0) * Number(m.unit_cost||0))}
                    </td>
                    <td className="text-xs text-erp-muted">
                      {m.reference && <div className="font-mono">{m.reference}</div>}
                      {m.notes && <div className="text-[11px]">{m.notes}</div>}
                      {!m.reference && !m.notes && '—'}
                    </td>
                    <td className="font-medium font-mono">{Number(m.balance_after||0).toFixed(3)}</td>
                    {write && (
                      <td>
                        {m.state !== 'reversed' && m.source_doc_type !== 'reversal' && (
                          <button className="btn btn-sm text-xs" title="عكس الحركة"
                            onClick={() => setConfirmReverse(m)}>↩ عكس</button>
                        )}
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ════ Modal ════ */}
      <Modal open={modal} onClose={() => setModal(false)} title="تسجيل حركة مخزون">
        <div className="flex flex-col gap-3">

          {/* اختيار المادة لو مش محددة */}
          {!matId && (
            <div>
              <label className="field-label">المادة *</label>
              <select className="field-select w-full" value={form.material_id || ''}
                onChange={e => f('material_id', e.target.value)}>
                <option value="">— اختر مادة —</option>
                {materials?.map(m => (
                  <option key={m.id} value={m.id}>
                    {m.code} — {m.name_ar}
                    {m.is_plate ? ` 🔩 ${m.thickness_mm||'?'}mm` : ''}
                    {m.material_kind === 'bar' ? ' 📏' : ''}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* بطاقة المادة */}
          {selectedMat && (
            <div className="flex items-center justify-between px-3 py-2 rounded-erp" style={{ background: 'var(--bg)' }}>
              <div>
                <span className="font-medium text-sm">{selectedMat.code} — {selectedMat.name_ar}</span>
                {isPlate && <span className="badge badge-blue mr-2 text-[10px]">🔩 لوح {selectedMat.thickness_mm||'?'}mm</span>}
                {isBar   && <span className="badge badge-amber mr-2 text-[10px]">📏 حديد</span>}
              </div>
              <div className="flex flex-col items-end gap-0.5">
                <span className="text-sm font-semibold" style={{ color: 'var(--teal)' }}>
                  رصيد: {Number(selectedMat.stock_qty||0).toFixed(3)} {selectedMat.unit}
                </span>
                {isOverBalance && (
                  <span className="text-xs font-semibold" style={{ color: 'var(--red, #EF4444)' }}>
                    ⚠️ السحب ({computedOutQty.toFixed(3)}) يتجاوز الرصيد!
                  </span>
                )}
              </div>
            </div>
          )}

          {/* النوع + التاريخ */}
          <FormGrid cols={2}>
            <div>
              <label className="field-label">النوع *</label>
              <select className="field-select w-full" value={form.movement_type}
                onChange={e => {
                  const t = e.target.value
                  const def = t === 'in'
                    ? (isPlate ? 'plate_in' : isBar ? 'bar_count' : 'weight')
                    : (isPlate ? 'weight'   : isBar ? 'bar_length' : 'weight')
                  f('movement_type', t); f('withdrawal_unit', def)
                }}>
                <option value="in">↓ وارد</option>
                <option value="out">↑ صادر (سحب)</option>
              </select>
            </div>
            <Input label="التاريخ *" type="date" value={form.movement_date}
              onChange={e => f('movement_date', e.target.value)} />
          </FormGrid>

          {/* ════ WARED (IN) ════ */}
          {form.movement_type === 'in' && (
            <>
              {/* اختيار وضع الإدخال */}
              <div className="flex gap-4 flex-wrap px-3 py-2 rounded-erp" style={{ background: 'var(--bg)' }}>
                {[
                  { val: 'weight',    label: 'بالوزن المباشر' },
                  ...(isPlate ? [{ val: 'plate_in', label: '🔩 وارد ألواح (عدد / وزن)' }] : []),
                  ...(isBar   ? [{ val: 'bar_count', label: '📏 وارد حديد (عدد أسياخ)' }] : []),
                ].map(opt => (
                  <label key={opt.val} className="flex items-center gap-1.5 cursor-pointer text-sm">
                    <input type="radio" name="in_mode" value={opt.val}
                      checked={form.withdrawal_unit === opt.val}
                      onChange={() => f('withdrawal_unit', opt.val)} />
                    {opt.label}
                  </label>
                ))}
              </div>

              {/* ─── وارد ألواح ─── */}
              {form.withdrawal_unit === 'plate_in' && (
                <div className="flex flex-col gap-3 px-3 py-3 rounded-erp"
                     style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
                  <div className="text-xs font-semibold" style={{ color: 'var(--teal)' }}>
                    🔩 وارد ألواح — أدخل مقاس اللوح ووزنه وعدده
                  </div>
                  <div className="text-[11px] text-erp-muted px-1">
                    يمكن إدخال عدد الألواح أو الوزن الإجمالي — النظام يحسب الآخر تلقائياً
                  </div>
                  <FormGrid cols={2}>
                    <Input label="طول اللوح (سم)" type="number" step="0.1"
                      value={form.plate_length_cm} onChange={e => f('plate_length_cm', e.target.value)}
                      placeholder={selectedMat?.plate_length_cm || '100'} />
                    <Input label="عرض اللوح (سم)" type="number" step="0.1"
                      value={form.plate_width_cm} onChange={e => f('plate_width_cm', e.target.value)}
                      placeholder={selectedMat?.plate_width_cm || '300'} />
                  </FormGrid>
                  <Input label="وزن اللوح الواحد (كجم) *" type="number" step="0.001"
                    value={form.plate_weight_kg} onChange={e => f('plate_weight_kg', e.target.value)}
                    placeholder={selectedMat?.plate_weight_kg || '50'} />
                  <Input label="عدد الألواح" type="number" step="1" min="1"
                    value={form.pieces_count} onChange={e => f('pieces_count', e.target.value)} placeholder="1" />
                  {plateInWeight && (
                    <div className="flex flex-col gap-1">
                      <InfoRow label="إجمالي الوزن الداخل" value={`${plateInWeight} كجم`} color="var(--teal)" />
                      {plateInDensity && (
                        <InfoRow label="الكثافة المحسوبة (سيُحفظ مع المادة)" value={`${plateInDensity} كجم/م²`} color="var(--blue)" />
                      )}
                    </div>
                  )}
                  <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                    value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                </div>
              )}

              {/* ─── وارد حديد ─── */}
              {form.withdrawal_unit === 'bar_count' && (
                <div className="flex flex-col gap-2 px-3 py-3 rounded-erp"
                     style={{ background: '#FFFBF0', border: '1px solid var(--amber)' }}>
                  <div className="text-xs font-semibold" style={{ color: 'var(--amber)' }}>
                    📏 وارد حديد — يحسب الوزن من عدد الأسياخ تلقائياً
                  </div>
                  {selectedMat?.weight_per_meter_kg && (
                    <div className="text-[11px] text-erp-muted">
                      وزن المتر: {Number(selectedMat.weight_per_meter_kg).toFixed(4)} كجم/م ·
                      طول السيخ: {selectedMat.bar_length_cm} سم
                    </div>
                  )}
                  <FormGrid cols={2}>
                    <Input label="عدد الأسياخ *" type="number" step="1" min="1"
                      value={form.pieces_count} onChange={e => f('pieces_count', e.target.value)} placeholder="10" />
                    <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                      value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                  </FormGrid>
                  {calcQtyBar && <InfoRow label="إجمالي الوزن المحسوب" value={`${calcQtyBar} كجم`} color="var(--teal)" />}
                </div>
              )}

              {/* ─── وارد عادي ─── */}
              {form.withdrawal_unit === 'weight' && (
                <FormGrid cols={2}>
                  <Input label="الكمية (كجم) *" type="number" step="0.001"
                    value={form.qty} onChange={e => f('qty', e.target.value)} placeholder="0.000" />
                  <Input label="سعر الوحدة (ج) *" type="number" step="0.01"
                    value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                </FormGrid>
              )}

              <div>
                <label className="field-label">مصدر الوارد</label>
                <select className="field-select w-full" value={form.source} onChange={e => f('source', e.target.value)}>
                  <option value="">— اختر المصدر —</option>
                  {SOURCES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
              </div>
            </>
          )}

          {/* ════ SADER (OUT) ════ */}
          {form.movement_type === 'out' && (
            <>
              {/* اختيار نوع السحب */}
              <div className="px-3 py-2.5 rounded-erp" style={{ background: 'var(--amber-lt)' }}>
                <div className="text-xs font-semibold mb-2" style={{ color: 'var(--amber)' }}>نوع السحب</div>
                <div className="flex gap-4 flex-wrap">
                  {[
                    { val: 'weight',      label: 'بالوزن' },
                    { val: 'piece',       label: 'بالقطعة' },
                    ...(isPlate ? [
                      { val: 'plate_count', label: '🔩 لوح كامل' },
                      { val: 'plate_area',  label: '🔩 بالمقاسات' },
                    ] : []),
                    ...(isBar ? [
                      { val: 'bar_length', label: '📏 بالطول' },
                      { val: 'bar_count',  label: '📏 بعدد الأسياخ' },
                    ] : []),
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

              {/* بالوزن */}
              {form.withdrawal_unit === 'weight' && (
                <FormGrid cols={2}>
                  <Input label="الكمية (كجم) *" type="number" step="0.001"
                    value={form.qty} onChange={e => f('qty', e.target.value)} placeholder="0.000" />
                  <Input label="سعر الوحدة (ج) *" type="number" step="0.01"
                    value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                </FormGrid>
              )}

              {/* بالقطعة */}
              {form.withdrawal_unit === 'piece' && (
                <div className="flex flex-col gap-2">
                  <FormGrid cols={2}>
                    <Input label="عدد القطع *" type="number" step="1" min="1"
                      value={form.pieces_count} onChange={e => f('pieces_count', e.target.value)} placeholder="5" />
                    <Input label="وزن القطعة (كجم) *" type="number" step="0.001"
                      value={form.weight_per_piece} onChange={e => f('weight_per_piece', e.target.value)} placeholder="3.000" />
                  </FormGrid>
                  {form.pieces_count && form.weight_per_piece && (
                    <InfoRow label="الوزن الإجمالي"
                      value={`${(Number(form.pieces_count)*Number(form.weight_per_piece)).toFixed(3)} كجم`}
                      color="var(--teal)" />
                  )}
                  <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                    value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                </div>
              )}

              {/* لوح كامل */}
              {form.withdrawal_unit === 'plate_count' && (
                <div className="flex flex-col gap-2 p-3 rounded-erp"
                     style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
                  <div className="text-xs font-semibold" style={{ color: 'var(--teal)' }}>
                    🔩 سحب ألواح كاملة — وزن اللوح الكامل: {selectedMat?.plate_weight_kg || '؟'} كجم
                  </div>
                  <Input label="عدد الألواح *" type="number" step="1" min="1"
                    value={form.pieces_count} onChange={e => f('pieces_count', e.target.value)} placeholder="2" />
                  {plateOutFullWeight && (
                    <InfoRow label="إجمالي الوزن" value={`${plateOutFullWeight} كجم`} color="var(--teal)" />
                  )}
                  <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                    value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                </div>
              )}

              {/* بالمقاسات */}
              {form.withdrawal_unit === 'plate_area' && (
                <div className="flex flex-col gap-3 p-3 rounded-erp"
                     style={{ background: 'var(--teal-lt)', border: '1px solid var(--teal-md)' }}>
                  <div className="text-xs font-semibold" style={{ color: 'var(--teal)' }}>
                    🔩 سحب بالمقاسات — أدخل الطول والعرض لكل قطعة
                  </div>
                  <div>
                    <label className="field-label mb-1">عدد القطع لكل مجموعة أبعاد</label>
                    <input className="field-input w-24 text-center" type="number" step="1" min="1"
                      value={form.plate_pieces_per_dim} onChange={e => f('plate_pieces_per_dim', e.target.value)} />
                  </div>
                  <PlateDimRows dims={form.plateDims} onChange={updateDim} onAdd={addDim} onRemove={removeDim} />
                  <Input label="هالك/رايش (جرام — اختياري)" type="number" step="1" min="0"
                    value={form.waste_g} onChange={e => f('waste_g', e.target.value)} placeholder="0" />
                  {totalAreaM2 !== null && (
                    <div className="flex flex-col gap-1">
                      <InfoRow label="إجمالي المساحة"
                        value={`${totalAreaM2.toFixed(4)} م²`} color="var(--blue)" />
                      {density
                        ? <InfoRow label={`الوزن (× ${Number(density).toFixed(2)} كجم/م²)`}
                            value={calcKgFromArea ? `${calcKgFromArea} كجم` : '—'} color="var(--teal)" />
                        : <div className="text-xs px-2" style={{ color: 'var(--red)' }}>⚠ لا توجد كثافة — سجّل وارد بالأبعاد أولاً</div>
                      }
                    </div>
                  )}
                  {density && (
                    <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                      value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                  )}
                </div>
              )}

              {/* حديد بالطول */}
              {form.withdrawal_unit === 'bar_length' && (
                <div className="flex flex-col gap-2 p-3 rounded-erp"
                     style={{ background: '#FFFBF0', border: '1px solid var(--amber)' }}>
                  <div className="text-xs font-semibold" style={{ color: 'var(--amber)' }}>
                    📏 سحب بالطول — وزن المتر: {selectedMat?.weight_per_meter_kg ? Number(selectedMat.weight_per_meter_kg).toFixed(4) : '؟'} كجم/م
                  </div>
                  <FormGrid cols={2}>
                    <Input label="الطول المطلوب (سم) *" type="number" step="0.1"
                      value={form.bar_length_cm} onChange={e => f('bar_length_cm', e.target.value)} placeholder="50" />
                    <Input label="عدد القطع" type="number" step="1" min="1"
                      value={form.pieces_count} onChange={e => f('pieces_count', e.target.value)} placeholder="1" />
                  </FormGrid>
                  <Input label="هالك/رايش (جرام — اختياري)" type="number" step="1" min="0"
                    value={form.waste_g} onChange={e => f('waste_g', e.target.value)} placeholder="0" />
                  {calcQtyBar && <InfoRow label="الوزن المحسوب" value={`${calcQtyBar} كجم`} color="var(--teal)" />}
                  <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                    value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                </div>
              )}

              {/* حديد بعدد الأسياخ */}
              {form.withdrawal_unit === 'bar_count' && (
                <div className="flex flex-col gap-2 p-3 rounded-erp"
                     style={{ background: '#FFFBF0', border: '1px solid var(--amber)' }}>
                  <div className="text-xs font-semibold" style={{ color: 'var(--amber)' }}>
                    📏 سحب بعدد الأسياخ
                  </div>
                  <FormGrid cols={2}>
                    <Input label="عدد الأسياخ *" type="number" step="1" min="1"
                      value={form.pieces_count} onChange={e => f('pieces_count', e.target.value)} placeholder="3" />
                    <Input label="سعر الوحدة (ج/كجم) *" type="number" step="0.01"
                      value={form.unit_cost} onChange={e => f('unit_cost', e.target.value)} />
                  </FormGrid>
                  {calcQtyBar && <InfoRow label="الوزن المحسوب" value={`${calcQtyBar} كجم`} color="var(--teal)" />}
                </div>
              )}

              {/* الوجهة */}
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

          {/* ربط بأمر إنتاج + مرجع */}
          <FormGrid cols={2}>
            <div>
              <label className="field-label">رقم أمر الإنتاج</label>
              <select className="field-select w-full" value={form.work_order_id}
                onChange={e => f('work_order_id', e.target.value)}>
                <option value="">— بدون ربط —</option>
                {workOrders?.map(wo => <option key={wo.id} value={wo.id}>{wo.code}</option>)}
              </select>
            </div>
            <Input label="المرجع" value={form.reference}
              onChange={e => f('reference', e.target.value)} placeholder="PO-2024-001" />
          </FormGrid>

          <Textarea label="ملاحظات" rows={2}
            value={form.notes} onChange={e => f('notes', e.target.value)} />
        </div>

        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving || posting}>
            {saving ? '…' : 'تسجيل الحركة'}
          </button>
        </ModalFooter>
      </Modal>

      {/* ════ Confirm OUT withdrawal ════ */}
      <ConfirmDialog
        open={!!confirmOut}
        onCancel={() => setConfirmOut(null)}
        onConfirm={() => doPost(confirmOut.payload)}
        title="تأكيد سحب المخزون"
        confirmLabel="تأكيد السحب"
        danger={isOverBalance}
        message={confirmOut
          ? `${isOverBalance
              ? `⚠️ تحذير: الكمية المطلوبة (${Number(confirmOut.qty).toFixed(3)} كجم) تتجاوز الرصيد الحالي (${stockQty.toFixed(3)} كجم). هل تريد المتابعة؟`
              : `هل تريد سحب ${Number(confirmOut.qty).toFixed(3)} كجم من المخزون؟`}`
          : ''}
      />

      {/* ════ Confirm reverse movement ════ */}
      <ConfirmDialog
        open={!!confirmReverse}
        onCancel={() => setConfirmReverse(null)}
        onConfirm={() => doReverse(confirmReverse)}
        title="تأكيد عكس الحركة"
        confirmLabel={reversing ? '…' : 'تأكيد العكس'}
        danger
        message={confirmReverse
          ? `هل تريد عكس هذه الحركة (${confirmReverse.movement_type === 'in' ? 'وارد' : 'صادر'} ${Number(confirmReverse.qty || 0).toFixed(3)} كجم)؟ سيتم إنشاء حركة عكسية تعيد الرصيد لحالته قبلها، وتبقى الحركة الأصلية في السجل بحالة "معكوسة".`
          : ''}
      />
    </div>
  )
}
