import { useState, useMemo, useEffect, useRef } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp, WO_STATUS, fmtDate, today } from '../utils/fmt'
import { StatusBadge } from '../components/ui/Badge'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import SearchInput from '../components/ui/SearchInput'
import { PageSpinner } from '../components/ui/Spinner'

const STATUS_OPTS = [
  { value: 'draft',       label: 'مسودة',  color: '#94A3B8' },
  { value: 'in_progress', label: 'جاري',   color: '#3B82F6' },
  { value: 'done',        label: 'منتهي',  color: '#22C55E' },
  { value: 'cancelled',   label: 'ملغي',   color: '#EF4444' },
]

/* تحقق مشترك لنموذج إنشاء/تعديل أمر الإنتاج (نفس قواعد الباك-إند + قيود منطقية مفقودة هناك).
   يرجّع رسالة خطأ عربية أو null. */
function validateWoForm(form, allEqs, fallbackEqId) {
  if (form.planned_cost !== '' && form.planned_cost != null && Number(form.planned_cost) < 0) {
    return 'التكلفة المخططة لا يمكن أن تكون سالبة'
  }
  if (form.quantity != null && form.quantity !== '' && !(Number(form.quantity) > 0)) {
    return 'الكمية المنتَجة يجب أن تكون أكبر من صفر'
  }
  if (form.start_date && form.planned_end_date && form.planned_end_date < form.start_date) {
    return 'تاريخ الانتهاء المخطط لازم يكون بعد تاريخ البدء'
  }
  const eq = (allEqs || []).find(e => e.id === Number(form.equipment_id || fallbackEqId))
  if (eq?.length_min != null && form.planned_length != null && form.planned_length !== '') {
    const L = Number(form.planned_length)
    if (!(L > 0)) return 'طول التصنيع يجب أن يكون أكبر من صفر'
    if (L < Number(eq.length_min) || (eq.length_max != null && L > Number(eq.length_max))) {
      return `طول التصنيع خارج النطاق المسموح (${eq.length_min} — ${eq.length_max})`
    }
  }
  return null
}

/* تواريخ فاضية ('') كانت بتتبعت كما هي → Optional[date] يرفضها بخطأ 422 خام */
function normalizeWoDates(payload) {
  const out = { ...payload }
  ;['start_date', 'end_date', 'planned_end_date'].forEach(k => { if (out[k] === '') out[k] = null })
  return out
}

function StatusPill({ value }) {
  const s = STATUS_OPTS.find(o => o.value === value) || STATUS_OPTS[0]
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 10px', borderRadius: 20, fontSize: 12, fontWeight: 600,
      background: s.color + '18', color: s.color, border: `1px solid ${s.color}40`
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: s.color, display: 'inline-block' }}/>
      {s.label}
    </span>
  )
}

/* ── MetricBomPreview — inline cost preview for metric equipment ── */
function MetricBomPreview({ equipmentId, length }) {
  const [result, setResult]   = useState(null)
  const [loading, setLoading] = useState(false)
  const debounce = useRef(null)

  useEffect(() => {
    if (!equipmentId || !length) { setResult(null); return }
    clearTimeout(debounce.current)
    debounce.current = setTimeout(async () => {
      setLoading(true)
      try {
        const r = await api.equipment.bomParametricLength(equipmentId, length)
        setResult(r)
      } catch { setResult(null) }
      finally { setLoading(false) }
    }, 500)
    return () => clearTimeout(debounce.current)
  }, [equipmentId, length])

  if (!equipmentId || !length) return null

  return (
    <div style={{ marginTop: 8, padding: '8px 12px', borderRadius: 8, background: '#f0fdf4', border: '1px solid #bbf7d0', fontSize: 12 }}>
      {loading && <span style={{ color: '#64748b' }}>⏳ جاري حساب تكلفة BOM...</span>}
      {!loading && result && (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', color: '#15803d' }}>
          <span>📦 {result.lines?.length ?? 0} مادة</span>
          <span style={{ fontWeight: 700 }}>تكلفة BOM المتوقعة: {Number(result.total_cost).toLocaleString('ar-EG', { minimumFractionDigits: 2 })} ج.م</span>
        </div>
      )}
    </div>
  )
}

/* ── Work Order Profile (detail view with full production mgmt) ── */
/* ── CutIssueModal — صرف مادة لوح/حديد عبر مخطط تقطيع بدل صرف ساذج ──
   يفتح بدل "📤 صرف" العادي لو المادة لوح أو حديد، يحسب أفضل تقطيع من
   النسالة/المخزون المتاح، ويصرف وينقّص الحجز في نداء واحد عبر
   /inventory/cutting-plan/commit (reservation_id) — نفس مسار الصرف
   الموحَّد المستخدم في باقي النظام، فلا يوجد تعارض أو خصم مزدوج. */
function CutIssueModal({ res, mat, wo, onClose, onDone }) {
  const toast = useToast()
  const isPlate = !!mat && (mat.is_plate || mat.material_kind === 'plate')
  // ملاحظة: لازم نستخدم reserved_qty (المحجوز فعلياً) لا required_qty (المطلوب من BOM) —
  // لو حصل نقص عند الحجز (reserved_qty < required_qty)، الصرف الفعلي (سواء العادي عبر
  // issue-by-reservation أو تقطيع/صرف هذه الشاشة) بيتقيّد بـ reserved_qty - issued_qty
  // من الباك-إند دايماً. لو استخدمنا required_qty هنا هنولّد خطة تقطيع أكبر من المتاح
  // فعلاً في الحجز، فيتقطّع/يُستهلك من المخزون أكتر مما هو محجوز لهذا الأمر تحديداً.
  const remaining = res ? Number(res.reserved_qty || 0) - Number(res.issued_qty || 0) : 0

  const [pieces, setPieces] = useState([])
  const [result, setResult] = useState(null)
  const [wasteActions, setWasteActions] = useState({})
  const [calcError, setCalcError] = useState('')
  const [newStock, setNewStock] = useState({ length_cm: '', width_cm: '', qty: 1, source: 'remnant' })

  useEffect(() => {
    if (!res || !mat) return
    setResult(null); setCalcError(''); setWasteActions({})
    if (isPlate) {
      const side = Math.round(Math.sqrt(Math.max(remaining, 0.01)) * 100)
      setPieces([{ label: 'A', width: side, height: side, qty: 1 }])
    } else {
      const lenCm = mat.weight_per_meter_kg ? Math.round((remaining / mat.weight_per_meter_kg) * 100) : 100
      setPieces([{ label: 'A', length: lenCm, qty: 1 }])
    }
  }, [res?.id, mat?.id])

  const [calc, calculating] = useAction(async () => {
    setCalcError('')
    // نفس قيود الباك-إند (أبعاد > 0، العدد 1..10000) — بدل رسالة pydantic خام
    const badPiece = pieces.find(p => !String(p.label || '').trim() ||
      (isPlate ? !(Number(p.width) > 0 && Number(p.height) > 0) : !(Number(p.length) > 0)) ||
      !(Number.isInteger(Number(p.qty)) && Number(p.qty) >= 1 && Number(p.qty) <= 10000))
    if (badPiece) {
      setCalcError('تأكد من إدخال كود لكل قطعة وأبعاد أكبر من صفر وعدد صحيح بين 1 و 10000')
      return
    }
    try {
      if (isPlate) {
        const r = await api.inventory.cuttingPlan({
          material_id: mat.id,
          pieces: pieces.map(p => ({ label: p.label, width_cm: +p.width, height_cm: +p.height, qty: +p.qty })),
          kerf_mm: 0, allow_rotation: true, use_stock: true,
        })
        setResult(r)
      } else {
        const r = await api.inventory.barCuttingPlan({
          material_id: mat.id,
          pieces: pieces.map(p => ({ label: p.label, length_cm: +p.length, qty: +p.qty })),
          kerf_mm: 0, use_stock: true,
        })
        setResult(r)
      }
    } catch (e) {
      setCalcError(e?.message || 'لا توجد مقاسات ألواح/نسالة مسجّلة لهذه المادة')
    }
  })

  const [addStock, addingStock] = useAction(async () => {
    if (!(Number(newStock.length_cm) > 0) || (isPlate && !(Number(newStock.width_cm) > 0))) {
      toast('الأبعاد يجب أن تكون أكبر من صفر', 'error'); return
    }
    if (!(Number.isInteger(Number(newStock.qty || 1)) && Number(newStock.qty || 1) >= 1)) {
      toast('العدد يجب أن يكون رقماً صحيحاً 1 على الأقل', 'error'); return
    }
    if (isPlate) {
      await api.inventory.createSheetStock({ material_id: mat.id, ...newStock })
    } else {
      await api.inventory.createBarStock({ material_id: mat.id, length_cm: newStock.length_cm, qty: newStock.qty, source: newStock.source })
    }
    setNewStock({ length_cm: '', width_cm: '', qty: 1, source: 'remnant' })
    calc()
  })

  const items = isPlate ? (result?.sheets || []) : (result?.bars || [])
  const usedStockItemIds = items.map(it => it.stock_item_id).filter(Boolean)
  const hasResult = result && items.length > 0
  const hasShortage = result?.shortage?.length > 0

  const [confirm, confirming] = useAction(async () => {
    if (hasShortage && !window.confirm('المخزون المتاح لا يغطي كل القطع المطلوبة — هل تريد الصرف الجزئي للقطع المتاحة فقط؟')) return
    const wasteItems = []
    let consumedFromMainStock = 0
    if (isPlate) {
      items.forEach((sheet, si) => {
        if (sheet.source === 'bulk') {
          consumedFromMainStock += (sheet.sheet_width / 100) * (sheet.sheet_height / 100)
        }
        ;(sheet.waste_rects || []).forEach((r, ri) => {
          if (Math.min(r.width, r.height) < 15) return
          const action = wasteActions[`${si}-${ri}`] || 'remnant'
          wasteItems.push({ kind: 'sheet', length_cm: r.height, width_cm: r.width, qty: 1, action })
        })
      })
    } else {
      items.forEach((bar, bi) => {
        if (bar.source === 'bulk') {
          consumedFromMainStock += (bar.bar_length / 100) * (mat.weight_per_meter_kg || 0)
        }
        if (bar.waste_length < 15) return
        const action = wasteActions[`bar-${bi}`] || 'remnant'
        wasteItems.push({ kind: 'bar', length_cm: bar.waste_length, qty: 1, action })
      })
    }
    await api.inventory.commitCuttingPlan({
      material_id: mat.id,
      work_order_id: wo.id,
      reservation_id: res.id,
      used_stock_item_ids: usedStockItemIds,
      consumed_qty_from_main_stock: consumedFromMainStock,
      waste_items: wasteItems,
    })
    toast('✅ تم صرف المادة عبر مخطط التقطيع')
    onDone()
  })

  if (!res || !mat) return null

  return (
    <Modal open={!!res} onClose={onClose} title={`🪚 تقطيع وصرف: ${mat.name_ar}`} size="md">
      <div className="flex flex-col gap-3">
        <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          المطلوب صرفه: <b>{remaining.toFixed(2)} {mat.unit}</b> — مُعبّأ تلقائيًا كقطعة واحدة، عدّلها لو القطعة الحقيقية مختلفة.
        </div>

        {/* محرر القطع */}
        <div className="wo-table-wrap">
          <table className="erp-table" style={{ fontSize: 12 }}>
            <thead>
              <tr>
                <th>الكود</th>
                {isPlate ? <><th>العرض (سم)</th><th>الارتفاع (سم)</th></> : <th>الطول (سم)</th>}
                <th>العدد</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {pieces.map((p, i) => (
                <tr key={i}>
                  <td><input className="field-input" style={{ width: 50 }} value={p.label}
                    onChange={e => setPieces(ps => ps.map((x, j) => j === i ? { ...x, label: e.target.value } : x))} /></td>
                  {isPlate ? (
                    <>
                      <td><input className="field-input" type="number" style={{ width: 70 }} value={p.width}
                        onChange={e => setPieces(ps => ps.map((x, j) => j === i ? { ...x, width: e.target.value } : x))} /></td>
                      <td><input className="field-input" type="number" style={{ width: 70 }} value={p.height}
                        onChange={e => setPieces(ps => ps.map((x, j) => j === i ? { ...x, height: e.target.value } : x))} /></td>
                    </>
                  ) : (
                    <td><input className="field-input" type="number" style={{ width: 70 }} value={p.length}
                      onChange={e => setPieces(ps => ps.map((x, j) => j === i ? { ...x, length: e.target.value } : x))} /></td>
                  )}
                  <td><input className="field-input" type="number" style={{ width: 55 }} value={p.qty}
                    onChange={e => setPieces(ps => ps.map((x, j) => j === i ? { ...x, qty: e.target.value } : x))} /></td>
                  <td>
                    {pieces.length > 1 && (
                      <button className="btn btn-sm" onClick={() => setPieces(ps => ps.filter((_, j) => j !== i))}>حذف</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button className="btn btn-sm" style={{ marginTop: 6 }}
            onClick={() => setPieces(ps => [...ps, isPlate ? { label: String.fromCharCode(65 + ps.length), width: '', height: '', qty: 1 } : { label: String.fromCharCode(65 + ps.length), length: '', qty: 1 }])}>
            + إضافة قطعة
          </button>
        </div>

        <button className="btn btn-primary" onClick={calc} disabled={calculating}>
          {calculating ? '…' : '🔍 حساب أفضل تقطيع'}
        </button>

        {calcError && (
          <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: 10 }}>
            <div style={{ color: '#dc2626', fontSize: 12, marginBottom: 8 }}>⚠ {calcError}</div>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>+ إضافة مقاس مخزون لهذه المادة:</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <input className="field-input" type="number" placeholder="الطول (سم)" style={{ width: 90 }}
                value={newStock.length_cm} onChange={e => setNewStock(s => ({ ...s, length_cm: e.target.value }))} />
              {isPlate && (
                <input className="field-input" type="number" placeholder="العرض (سم)" style={{ width: 90 }}
                  value={newStock.width_cm} onChange={e => setNewStock(s => ({ ...s, width_cm: e.target.value }))} />
              )}
              <input className="field-input" type="number" placeholder="العدد" style={{ width: 60 }}
                value={newStock.qty} onChange={e => setNewStock(s => ({ ...s, qty: e.target.value }))} />
              <select className="field-select" value={newStock.source} onChange={e => setNewStock(s => ({ ...s, source: e.target.value }))}>
                <option value="remnant">نسالة</option>
                <option value="purchased">{isPlate ? 'لوح جديد' : 'سيخ جديد'}</option>
              </select>
              <button className="btn btn-sm btn-primary" onClick={addStock} disabled={addingStock || !newStock.length_cm}>إضافة</button>
            </div>
          </div>
        )}

        {hasResult && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {items.map((it, idx) => (
              <div key={idx} style={{ background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 8, padding: '8px 10px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, fontWeight: 600, color: '#15803d', marginBottom: 4 }}>
                  <span>{isPlate ? `لوح #${it.index}` : `سيخ #${it.index}`} — {it.source === 'remnant' ? 'نسالة' : (isPlate ? 'لوح جديد' : 'سيخ جديد')}</span>
                  <span>استفادة {it.efficiency_pct}%</span>
                </div>
                {isPlate ? (
                  <div style={{ fontSize: 11, color: '#475569' }}>
                    المقاس: {it.sheet_width}×{it.sheet_height} سم — القطع: {(it.placements || []).map(p => `${p.label}(${p.width}×${p.height})`).join('، ')}
                  </div>
                ) : (
                  <div style={{ fontSize: 11, color: '#475569' }}>
                    الطول: {it.bar_length} سم — القطع: {(it.cuts || []).map(c => `${c.label}(${c.length})`).join('، ')}
                  </div>
                )}
                {isPlate && (it.waste_rects || []).filter(r => Math.min(r.width, r.height) >= 15).map((r, ri) => (
                  <div key={ri} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11, marginTop: 4, padding: '4px 6px', background: '#fff', borderRadius: 4 }}>
                    <span>هالك {r.width}×{r.height} سم</span>
                    <select className="field-select" style={{ fontSize: 11 }} value={wasteActions[`${idx}-${ri}`] || 'remnant'}
                      onChange={e => setWasteActions(w => ({ ...w, [`${idx}-${ri}`]: e.target.value }))}>
                      <option value="remnant">حفظ كنسالة</option>
                      <option value="scrap">هالك نهائي</option>
                    </select>
                  </div>
                ))}
                {!isPlate && it.waste_length >= 15 && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11, marginTop: 4, padding: '4px 6px', background: '#fff', borderRadius: 4 }}>
                    <span>هالك {it.waste_length} سم</span>
                    <select className="field-select" style={{ fontSize: 11 }} value={wasteActions[`bar-${idx}`] || 'remnant'}
                      onChange={e => setWasteActions(w => ({ ...w, [`bar-${idx}`]: e.target.value }))}>
                      <option value="remnant">حفظ كنسالة</option>
                      <option value="scrap">هالك نهائي</option>
                    </select>
                  </div>
                )}
              </div>
            ))}
            {hasShortage && (
              <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: 10, fontSize: 12, color: '#dc2626' }}>
                ⚠ نواقص — لا يكفي المخزون المتاح لتغطية: {result.shortage.map(s => `${s.label}: ${s.qty}`).join('، ')}
              </div>
            )}
          </div>
        )}
      </div>
      <ModalFooter>
        <button className="btn" onClick={onClose}>إلغاء</button>
        <button className="btn btn-primary" onClick={confirm} disabled={!hasResult || confirming}>
          {confirming ? '…' : '✅ تأكيد الصرف'}
        </button>
      </ModalFooter>
    </Modal>
  )
}

function WorkOrderProfile({ wo, onBack, write, onRefresh }) {
  const toast = useToast()
  const [activeTab, setActiveTab] = useState('overview')
  const [form, setForm] = useState({})
  const [showEditModal, setShowEditModal] = useState(false)

  // عند فتح Edit modal، حمّل مواقع العميل الحالي
  useEffect(() => {
    if (showEditModal && form.customer_id) {
      api.get(`/customers/${form.customer_id}/locations`)
        .then(locs => setEditCustLocations(Array.isArray(locs) ? locs : []))
        .catch(bgErr('عناوين العميل', () => setEditCustLocations([])))
    }
  }, [showEditModal, form.customer_id])

  // Reload data for this specific WO
  const { data: costLines, reload: reloadCost } = useApi(
    () => api.get(`/accounting/work-orders/${wo.id}/cost-lines`).catch(bgErr('بنود تكلفة أمر الإنتاج', [])), [wo.id])
  const { data: reservations, reload: reloadRes } = useApi(
    () => api.production.reservations(wo.id).catch(bgErr('حجوزات المخزون لأمر الإنتاج', [])), [wo.id])
  const { data: woBomLines, reload: reloadWoBom } = useApi(
    () => api.get(`/accounting/work-orders/${wo.id}/bom-lines`).catch(bgErr('بنود BOM لأمر الإنتاج', null)), [wo.id])
  const { data: bomData } = useApi(
    () => wo.equipment_id ? api.get(`/equipment/${wo.equipment_id}/bom`).catch(bgErr('BOM المعدة', null)) : Promise.resolve(null),
    [wo.equipment_id])
  const { data: allEqs } = useApi(() => api.equipment.listAll())
  const { data: customers } = useApi(() => api.customers.list())
  const { data: allOrders } = useApi(() => api.get('/customers/orders/all').catch(bgErr('قائمة الطلبات', [])))

  const [editCustLocations, setEditCustLocations] = useState([])

  const onEditCustomerChange = async (custId) => {
    setForm(f => ({ ...f, customer_id: custId ? +custId : null, location_id: null }))
    setEditCustLocations([])
    if (custId) {
      try {
        const locs = await api.get(`/customers/${custId}/locations`)
        setEditCustLocations(Array.isArray(locs) ? locs : [])
      } catch (_) {}
    }
  }

  const [modalRes, setModalRes]       = useState(null)
  const [formRes, setFormRes]         = useState({ qty: '' })
  const { data: materials, loading: materialsLoading } = useApi(() => api.inventory.materials())
  const [showInvModal, setShowInvModal] = useState(false)
  const [invCustId, setInvCustId]       = useState('')
  // سعر البيع — كان بيتحط تلقائي = التكلفة المخططة (ربح صفر) من غير ما المستخدم يختار
  const [invPrice, setInvPrice]         = useState('')
  const [creatingInv, setCreatingInv]   = useState(false)
  const [shortages, setShortages] = useState(null)
  const [cutRes, setCutRes] = useState(null)

  const pct = Math.min(100, wo.cost_pct || 0)
  const planned = Number(wo.planned_cost || 0)
  const actual  = Number(wo.actual_cost  || 0)
  const variance = planned > 0 ? ((actual - planned) / planned * 100) : null

  /* ── Actions ── */
  const [changeStatus, changingStatus] = useAction(async (status) => {
    if (!status || status === wo.status) return
    if (status === 'cancelled' && !window.confirm(`إلغاء أمر الإنتاج ${wo.code}؟ سيتم إلغاء كل حجوزات المواد المفتوحة له.`)) return
    if (status === 'done' && !window.confirm(`تأكيد إنهاء أمر الإنتاج ${wo.code}؟`)) return
    await api.accounting.updateWOStatus(wo.id, status)
    toast('تم تغيير الحالة')
    if (status === 'in_progress') {
      const res = await api.post(`/production/auto-reserve/${wo.id}`).catch(() => {
        toast('تعذّر الحجز التلقائي للمواد من BOM', 'error')
        return null
      })
      if (res?.shortages?.length > 0) setShortages(res.shortages)
      else if (res?.reserved > 0) toast(`✅ تم حجز ${res.reserved} مادة تلقائياً من BOM`, 'info')
    }
    onRefresh()
  })

  const [doReserve, reserving] = useAction(async () => {
    if (!formRes.material_id || !formRes.qty || Number(formRes.qty) <= 0) { toast('اختر المادة وأدخل كمية أكبر من صفر', 'error'); return }
    await api.production.reserve({ work_order_id: wo.id, material_id: +formRes.material_id, qty: +formRes.qty })
    toast('تم حجز المادة'); setModalRes(null); setFormRes({ qty: '' }); reloadRes()
  })

  const [autoReserve, autoReserving] = useAction(async () => {
    const res = await api.post(`/production/auto-reserve/${wo.id}`)
    if (res?.shortages?.length > 0) { setShortages(res.shortages); toast(`⚠️ نقص في ${res.shortages.length} مادة`, 'error') }
    else toast(`✅ تم حجز ${res?.reserved || 0} مادة من BOM تلقائياً`)
    reloadRes()
  })

  const [doEditSave, editSaving] = useAction(async () => {
    const err = validateWoForm(form, allEqs, wo.equipment_id)
    if (err) { toast(err, 'error'); return }
    await api.accounting.updateWO(wo.id, normalizeWoDates(form))
    toast('تم التحديث'); setShowEditModal(false); onRefresh()
  })

  const [doDeleteWO, deletingWO] = useAction(async () => {
    if (!window.confirm(`تأكيد حذف أمر الإنتاج ${wo.code}؟ هذا الإجراء لا يمكن التراجع عنه.`)) return
    await api.accounting.deleteWO(wo.id)
    toast('تم حذف أمر الإنتاج')
    onRefresh()
    onBack()
  })

  const [doIssue, issuing] = useAction(async (resId) => {
    await api.production.issue({ reservation_id: resId })
    toast('✅ تم صرف المادة'); reloadRes(); reloadCost(); onRefresh()
  })

  const [doCancelRes] = useAction(async (resId) => {
    await api.production.cancelReservation(resId)
    toast('تم إلغاء الحجز'); reloadRes()
  })

  const [execResult, setExecResult] = useState(null)
  const [execBom, execBomRunning] = useAction(async () => {
    if (!window.confirm(`تنفيذ BOM كامل لأمر ${wo.code}؟ سيتم صرف كل المواد من المخزون وتسجيل تكلفتها.`)) return
    const res = await api.post(`/accounting/work-orders/${wo.id}/execute-bom`)
    setExecResult(res)
    reloadRes(); reloadCost(); reloadWoBom(); onRefresh()
    const shorts = res?.shortages?.length || 0
    if (shorts > 0) toast(`⚠️ تم التنفيذ مع نقص في ${shorts} مادة`, 'warn')
    else toast(`✅ تم تنفيذ BOM كامل — تكلفة ${Number(res?.total_cost || 0).toLocaleString()} ج.م`)
  })

  const doCreateInvoice = async () => {
    if (!invCustId) { toast('اختر العميل', 'error'); return }
    const salePrice = Number(invPrice === '' ? (wo.actual_cost || wo.planned_cost || 0) : invPrice)
    if (!Number.isFinite(salePrice) || salePrice <= 0) { toast('سعر البيع يجب أن يكون أكبر من صفر', 'error'); return }
    if (creatingInv) return
    setCreatingInv(true)
    try {
      await api.sales.createInvoice({
        code:           '',
        customer_id:    +invCustId,
        work_order_id:  wo.id,
        date:           today(),
        notes:          `مرجع: أمر إنتاج ${wo.code}`,
        tax_pct:        0,
        discount_pct:   0,
        lines: [{
          description: wo.equipment_name_ar || wo.equipment_name || wo.equipment || 'عمل إنتاجي',
          qty: 1,
          unit: 'وحدة',
          unit_price: salePrice,
          sort_order: 0,
        }],
      })
      toast('✅ تم إنشاء الفاتورة — يمكن فتحها من قسم الفواتير')
      setShowInvModal(false)
      setInvCustId('')
    } catch (e) {
      toast(e.message || 'فشل إنشاء الفاتورة', 'error')
    } finally {
      setCreatingInv(false)
    }
  }

  const tabs = [
    { key: 'overview',  label: 'نظرة عامة',      icon: '📋' },
    { key: 'materials', label: 'المواد المحجوزة',  icon: '📦' },
    { key: 'bom',       label: 'BOM المعدة',      icon: '🗂️' },
    { key: 'costs',     label: 'التكاليف',         icon: '💰' },
  ]

  const issuedCount   = (reservations || []).filter(r => r.status === 'fully_issued' || r.status === 'partially_issued').length
  const reservedCount = (reservations || []).length

  return (
    <div className="wo-profile">
      {/* ── Back + Header ── */}
      <div className="wo-profile-header">
        <button className="wo-back-btn" onClick={onBack}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <polyline points="9 18 15 12 9 6"/>
          </svg>
          العودة للقائمة
        </button>

        <div className="wo-header-main">
          <div>
            <div className="wo-code-badge">{wo.code}</div>
            <h1 className="wo-title">{wo.equipment_name_ar || wo.equipment_name || wo.equipment || 'أمر إنتاج'}</h1>
            <div className="wo-meta-row">
              <StatusPill value={wo.status} />
              {wo.start_date && <span className="wo-meta-chip">📅 {fmtDate(wo.start_date)}</span>}
              {wo.planned_end_date && <span className="wo-meta-chip">🏁 {fmtDate(wo.planned_end_date)}</span>}
            </div>
          </div>

          <div className="wo-header-actions">
            <button className="wo-btn-edit"
              style={{ background: '#475569', color: '#fff', border: 'none' }}
              onClick={() => api.accounting.printWO(wo.id)}
              title="طباعة أمر الإنتاج">
              🖨️ طباعة
            </button>
            {wo.equipment_id && (
              <button className="wo-btn-edit"
                style={{ background: '#064e3b', color: '#fff', border: 'none' }}
                onClick={() => api.equipment.printBOM(wo.equipment_id)}
                title="طباعة قائمة المواد BOM">
                📋 طباعة BOM
              </button>
            )}
            {/* زر احتساب BOM المتري — يظهر فقط لأوامر إنتاج المعدات المترية */}
            {wo.equipment_length_min && write && (
              <button className="wo-btn-edit"
                style={{ background: '#1d4ed8', color: '#fff', border: 'none', fontWeight: 700 }}
                onClick={async () => {
                  if (!wo.planned_length) {
                    toast('حدّد طول التصنيع أولاً من زر "تعديل"', 'error')
                    return
                  }
                  try {
                    const r = await api.post(`/accounting/work-orders/${wo.id}/apply-parametric-bom`)
                    toast(`✅ تم احتساب BOM للطول ${wo.planned_length}م — ${r.lines_added} بند، تكلفة مواد: ${r.total_material_cost?.toLocaleString()} ج.م`)
                    onRefresh(); reloadWoBom()
                  } catch(e) { toast(e.message || 'فشل الاحتساب', 'error') }
                }}
                title={`احتساب مواد البريمة عند طول ${wo.planned_length || '؟'} م`}>
                📐 احتساب BOM ({wo.planned_length ? `${wo.planned_length}م` : 'حدد الطول'})
              </button>
            )}
            {write && (
              <>
                <select
                  className="wo-status-select"
                  value={wo.status}
                  disabled={changingStatus}
                  onChange={e => changeStatus(e.target.value)}
                >
                  {STATUS_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <button className="wo-btn-edit" onClick={() => {
                    const cid = wo.customer_id || wo.customer_order?.customer_id || ''
                    setInvCustId(cid ? String(cid) : '')
                    setShowInvModal(true)
                  }}
                  style={{ background: 'var(--teal)', color: '#fff', border: 'none' }}
                  title="إنشاء فاتورة بيع مرتبطة بهذا الأمر">
                  📄 إنشاء فاتورة
                </button>
                {wo.status !== 'done' && wo.status !== 'cancelled' && (
                  <button className="wo-btn-edit" onClick={() => { setForm({...wo}); setShowEditModal(true) }}>
                    ✏️ تعديل
                  </button>
                )}
                {(wo.status === 'draft' || wo.status === 'cancelled') && (
                  <button className="wo-btn-edit" disabled={deletingWO} onClick={doDeleteWO}
                    style={{ background: 'var(--red)', color: '#fff', border: 'none' }}
                    title="حذف أمر الإنتاج نهائيًا">
                    🗑️ حذف
                  </button>
                )}
              </>
            )}
          </div>
        </div>

        {/* ── Progress Bar ── */}
        <div className="wo-progress-section">
          <div className="wo-progress-labels">
            <span>تقدم التكلفة</span>
            <span style={{ fontWeight: 700, color: pct > 90 ? '#EF4444' : pct > 70 ? '#F59E0B' : '#22C55E' }}>{pct}%</span>
          </div>
          <div className="wo-progress-track">
            <div className="wo-progress-fill" style={{
              width: `${pct}%`,
              background: pct > 90 ? 'linear-gradient(90deg,#EF4444,#DC2626)' :
                          pct > 70 ? 'linear-gradient(90deg,#F59E0B,#D97706)' :
                                     'linear-gradient(90deg,#22C55E,#16A34A)'
            }} />
          </div>
        </div>
      </div>

      {/* ── KPI Strip ── */}
      <div className="wo-kpi-strip">
        <div className="wo-kpi">
          <div className="wo-kpi-label">التكلفة المخططة</div>
          <div className="wo-kpi-value" style={{ color: '#0B1F3A' }}>{planned > 0 ? egp(planned) : '—'}</div>
        </div>
        <div className="wo-kpi">
          <div className="wo-kpi-label">التكلفة الفعلية</div>
          <div className="wo-kpi-value" style={{ color: actual > planned && planned > 0 ? '#EF4444' : '#0B1F3A' }}>{egp(actual)}</div>
        </div>
        <div className="wo-kpi">
          <div className="wo-kpi-label">الانحراف</div>
          <div className="wo-kpi-value" style={{ color: variance == null ? '#94A3B8' : variance > 0 ? '#EF4444' : '#22C55E' }}>
            {variance != null ? `${variance > 0 ? '+' : ''}${variance.toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="wo-kpi">
          <div className="wo-kpi-label">المواد المحجوزة</div>
          <div className="wo-kpi-value" style={{ color: '#3B82F6' }}>{reservedCount}</div>
        </div>
        <div className="wo-kpi">
          <div className="wo-kpi-label">تم صرفها</div>
          <div className="wo-kpi-value" style={{ color: '#22C55E' }}>{issuedCount}</div>
        </div>
        <div className="wo-kpi">
          <div className="wo-kpi-label">بنود BOM</div>
          <div className="wo-kpi-value" style={{ color: '#FF8A00' }}>{woBomLines?.count || bomData?.lines?.length || '—'}</div>
        </div>
      </div>

      {/* ── Metric Workflow Guide ── */}
      {wo.equipment_length_min && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', background: '#eff6ff', borderRadius: 8, margin: '0 0 8px', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11, color: '#64748b', fontWeight: 600, marginLeft: 4 }}>خطوات المعدة المترية:</span>
          {[
            { label: 'حدد الطول', done: !!wo.planned_length },
            { label: 'احتساب BOM', done: !!(woBomLines?.lines?.length > 0) },
            { label: 'حجز المواد',  done: !!(reservations?.length > 0) },
          ].map((step, i) => (
            <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, fontWeight: 600,
              color: step.done ? '#059669' : '#1d4ed8',
              background: step.done ? '#d1fae5' : '#dbeafe',
              border: `1px solid ${step.done ? '#6ee7b7' : '#93c5fd'}`,
              borderRadius: 20, padding: '2px 10px' }}>
              {step.done ? '✅' : '⬜'} {i + 1}. {step.label}
            </span>
          ))}
        </div>
      )}

      {/* ── Shortage Warning ── */}
      {shortages && (
        <div className="wo-shortage-alert">
          <div className="wo-shortage-title">⚠️ نقص في المواد التالية — يجب إصدار أمر شراء</div>
          <div className="wo-shortage-table-wrap">
            <table className="erp-table">
              <thead><tr><th>المادة</th><th>المتوفر</th><th>المطلوب</th><th>النقص</th></tr></thead>
              <tbody>
                {shortages.map((s, i) => (
                  <tr key={i}>
                    <td className="font-medium">{s.material_name}</td>
                    <td>{Number(s.available ?? 0).toFixed(1)}</td>
                    <td>{Number(s.required ?? 0).toFixed(1)}</td>
                    <td style={{ color: '#EF4444', fontWeight: 700 }}>{Number(s.shortage ?? 0).toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="wo-shortage-close" onClick={() => setShortages(null)}>حسناً — متابعة يدوياً</button>
        </div>
      )}

      {/* ── Tabs ── */}
      <div className="wo-tabs-bar">
        {tabs.map(t => (
          <button key={t.key} className={`wo-tab${activeTab === t.key ? ' active' : ''}`} onClick={() => setActiveTab(t.key)}>
            <span>{t.icon}</span> {t.label}
          </button>
        ))}
      </div>

      {/* ── Tab Content ── */}
      <div className="wo-tab-content">

        {/* OVERVIEW */}
        {activeTab === 'overview' && (
          <div className="wo-overview-grid">
            <div className="wo-info-card">
              <div className="wo-info-title">تفاصيل الأمر</div>
              <div className="wo-info-rows">
                <div className="wo-info-row"><span>الكود</span><span className="font-mono font-bold">{wo.code}</span></div>
                <div className="wo-info-row"><span>الحالة</span><StatusPill value={wo.status} /></div>
                <div className="wo-info-row"><span>المعدة</span><span>{wo.equipment_name_ar || wo.equipment || '—'}</span></div>
                <div className="wo-info-row"><span>العميل</span><span>{wo.customer_name || '—'}</span></div>
                <div className="wo-info-row"><span>الموقع</span><span>{wo.location_name || '—'}</span></div>
                <div className="wo-info-row"><span>طلب العميل المرتبط</span><span>{wo.customer_order_code || '—'}</span></div>
                <div className="wo-info-row"><span>المعدة الخارجية (الأصل المُسلَّم)</span><span>{wo.linked_asset_name || '—'}</span></div>
                <div className="wo-info-row"><span>تاريخ البدء</span><span>{fmtDate(wo.start_date) || '—'}</span></div>
                {wo.description && <div className="wo-info-row" style={{ flexDirection: 'column', gap: 4 }}>
                  <span>الوصف</span><span style={{ color: '#475569', fontSize: 13 }}>{wo.description}</span>
                </div>}
              </div>
            </div>

            <div className="wo-info-card">
              <div className="wo-info-title">ملخص التكاليف</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
                {[
                  { label: 'تكلفة مخططة', value: egp(planned), color: '#0B1F3A' },
                  { label: 'تكلفة فعلية', value: egp(actual), color: actual > planned && planned > 0 ? '#EF4444' : '#22C55E' },
                  { label: 'المواد المصروفة', value: egp((costLines || []).filter(l => l.category === 'material').reduce((a, l) => a + Number(l.amount || 0), 0)), color: '#3B82F6' },
                  { label: 'العمالة', value: egp((costLines || []).filter(l => l.category === 'labor').reduce((a, l) => a + Number(l.amount || 0), 0)), color: '#8B5CF6' },
                ].map((item, i) => (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ color: '#64748B', fontSize: 13 }}>{item.label}</span>
                    <span style={{ fontWeight: 700, color: item.color, fontSize: 15 }}>{item.value}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="wo-info-card" style={{ gridColumn: '1 / -1' }}>
              <div className="wo-info-title">إجراءات سريعة</div>
              <div className="wo-quick-actions">
                {write && (
                  <>
                    <button
                      className="wo-action-btn"
                      style={{ background: '#0f4c2a', color: '#fff', border: 'none', fontWeight: 700, fontSize: 14 }}
                      onClick={execBom}
                      disabled={execBomRunning}
                    >
                      <span>🚀</span>
                      {execBomRunning ? 'جاري التنفيذ…' : 'تنفيذ BOM كامل'}
                    </button>
                    <button className="wo-action-btn blue" onClick={() => setModalRes('reserve')}>
                      <span>📦</span> حجز مادة يدوياً
                    </button>
                    <button className="wo-action-btn orange" onClick={autoReserve} disabled={autoReserving}>
                      <span>🤖</span> {autoReserving ? 'جاري الحجز…' : 'حجز تلقائي فقط'}
                    </button>
                    <button className="wo-action-btn green" onClick={() => setActiveTab('materials')}>
                      <span>📋</span> عرض المواد المحجوزة
                    </button>
                    <button className="wo-action-btn purple" onClick={() => setActiveTab('costs')}>
                      <span>💰</span> عرض التكاليف
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        )}

        {/* MATERIALS / RESERVATIONS */}
        {activeTab === 'materials' && (
          <div className="wo-section-card">
            <div className="wo-section-header">
              <div className="wo-section-title">المواد المحجوزة لهذا الأمر</div>
              {write && (
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="wo-btn-sm orange" onClick={autoReserve} disabled={autoReserving}>
                    🤖 {autoReserving ? '…' : 'حجز تلقائي من BOM'}
                  </button>
                  <button className="wo-btn-sm blue" onClick={() => setModalRes('reserve')}>
                    + حجز مادة
                  </button>
                </div>
              )}
            </div>

            {!reservations?.length ? (
              <div className="wo-empty-state">
                <div style={{ fontSize: 40, marginBottom: 8 }}>📦</div>
                <div style={{ fontWeight: 600, color: '#475569' }}>لا توجد مواد محجوزة</div>
                <div style={{ color: '#94A3B8', fontSize: 13, marginTop: 4 }}>اضغط "حجز تلقائي من BOM" لحجز كل مواد الأمر دفعةً واحدة</div>
                {write && (
                  <button className="wo-btn-sm orange" style={{ marginTop: 16 }} onClick={autoReserve} disabled={autoReserving}>
                    🤖 {autoReserving ? '…' : 'حجز تلقائي من BOM'}
                  </button>
                )}
              </div>
            ) : (
              <div className="wo-table-wrap">
                <table className="erp-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>المادة</th>
                      <th>المطلوب</th>
                      <th>المحجوز</th>
                      <th>المصروف</th>
                      <th>الحالة</th>
                      {write && <th>إجراء</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {reservations.map((r, i) => {
                      const isPartial = r.status === 'partial' || r.status === 'partially_issued'
                      const isIssued  = r.status === 'fully_issued'
                      return (
                        <tr key={r.id}>
                          <td style={{ color: '#94A3B8', fontSize: 12 }}>{i + 1}</td>
                          <td>
                            <div style={{ fontWeight: 600 }}>{r.material_name}</div>
                            {r.material_code && <div style={{ fontSize: 11, color: '#94A3B8', fontFamily: 'monospace' }}>{r.material_code}</div>}
                          </td>
                          <td>{Number(r.required_qty || r.reserved_qty || 0).toFixed(2)}</td>
                          <td style={{ fontWeight: 600 }}>{Number(r.reserved_qty || 0).toFixed(2)}</td>
                          <td style={{ color: '#22C55E', fontWeight: 600 }}>{Number(r.issued_qty || 0).toFixed(2)}</td>
                          <td>
                            <span style={{
                              padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600,
                              background: isIssued ? '#22C55E18' : isPartial ? '#F59E0B18' : '#3B82F618',
                              color: isIssued ? '#16A34A' : isPartial ? '#D97706' : '#2563EB',
                            }}>
                              {isIssued ? '✓ تم الصرف' : isPartial ? '⚡ جزئي' : '⏳ محجوز'}
                            </span>
                          </td>
                          {write && (
                            <td>
                              {!isIssued && (
                                <button
                                  className="wo-btn-sm green"
                                  style={{ fontSize: 12, padding: '4px 10px', opacity: materialsLoading ? 0.5 : 1 }}
                                  disabled={materialsLoading || issuing}
                                  title={materialsLoading ? 'جارٍ تحميل بيانات المواد...' : undefined}
                                  onClick={() => {
                                    if (!materials) { toast('جارٍ تحميل بيانات المواد، حاول مرة أخرى', 'error'); return }
                                    const m = materials.find(x => x.id === r.material_id)
                                    const needsCutting = m && (m.is_plate || m.material_kind === 'plate' || m.material_kind === 'bar')
                                    if (needsCutting) setCutRes(r)
                                    else if (window.confirm(`صرف المتبقي من "${r.material_name}" من المخزون لأمر الإنتاج؟`)) doIssue(r.id)
                                  }}
                                >
                                  {materialsLoading ? '⏳ ...' : '📤 صرف'}
                                </button>
                              )}
                              {Number(r.issued_qty || 0) === 0 && (
                                <button
                                  className="wo-btn-sm"
                                  style={{ fontSize: 12, padding: '4px 10px', marginRight: 6, background: '#FEE2E2', color: '#B91C1C' }}
                                  onClick={() => { if (window.confirm(`إلغاء حجز "${r.material_name}"؟`)) doCancelRes(r.id) }}
                                  title="إلغاء الحجز"
                                >
                                  ✕ إلغاء الحجز
                                </button>
                              )}
                            </td>
                          )}
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* BOM */}
        {activeTab === 'bom' && (() => {
          const hasWoBom = woBomLines?.lines?.length > 0
          const displayLines = hasWoBom
            ? woBomLines.lines.map(l => ({ ...l, qty: l.required_qty }))
            : (bomData?.lines || [])
          const displayTotal = hasWoBom ? woBomLines.total_cost : bomData?.total_cost
          return (
            <div className="wo-section-card">
              <div className="wo-section-header">
                <div className="wo-section-title">
                  قائمة المواد (BOM) للمعدة
                  {hasWoBom && wo.planned_length && (
                    <span style={{ fontSize: 12, color: '#1d4ed8', background: '#eff6ff', borderRadius: 6, padding: '2px 8px', marginRight: 8, fontWeight: 600 }}>
                      📐 محتسب على {wo.planned_length} م
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  {displayTotal > 0 && (
                    <span style={{ fontWeight: 700, color: '#FF8A00', fontSize: 15 }}>
                      إجمالي BOM: {egp(displayTotal)}
                    </span>
                  )}
                  {wo.equipment_id && (
                    <button className="btn btn-sm text-xs"
                      style={{ background: '#064e3b', color: '#fff', border: 'none' }}
                      onClick={() => hasWoBom
                        ? api.accounting.printWoBOM(wo.id)
                        : api.equipment.printBOM(wo.equipment_id)}
                      title={hasWoBom ? `طباعة BOM المحتسب على ${wo.planned_length}م` : 'طباعة BOM المعدة'}>
                      🖨️ {hasWoBom ? `طباعة BOM (${wo.planned_length}م)` : 'طباعة BOM'}
                    </button>
                  )}
                </div>
              </div>
              {!displayLines.length ? (
                <div className="wo-empty-state">
                  <div style={{ fontSize: 36 }}>🗂️</div>
                  <div style={{ fontWeight: 600, color: '#475569', marginTop: 8 }}>
                    {wo.equipment_id ? 'لا توجد بنود BOM — اضغط "احتساب BOM" لتوليدها' : 'لم يتم تحديد معدة لهذا الأمر'}
                  </div>
                </div>
              ) : (
                <div className="wo-table-wrap">
                  <table className="erp-table">
                    <thead>
                      <tr><th>#</th><th>الكود</th><th>المادة</th><th>الكمية</th><th>الوحدة</th><th>التكلفة التقديرية</th><th>إجراء</th></tr>
                    </thead>
                    <tbody>
                      {displayLines.map((l, i) => {
                        const m = (materials || []).find(x => x.id === l.material_id)
                        const needsCutting = m && (m.is_plate || m.material_kind === 'plate' || m.material_kind === 'bar')
                        return (
                        <tr key={l.id || i}>
                          <td style={{ color: '#94A3B8', fontSize: 12 }}>{i + 1}</td>
                          <td><span style={{ fontFamily: 'monospace', fontSize: 12, color: '#3B82F6' }}>{l.material_code}</span></td>
                          <td style={{ fontWeight: 600 }}>{l.material_name}</td>
                          <td>{Number(l.qty || l.required_qty || 0).toFixed(3)}</td>
                          <td style={{ color: '#64748B' }}>{l.unit || '—'}</td>
                          <td style={{ color: '#FF8A00', fontWeight: 600 }}>{l.unit_cost ? egp((l.qty || l.required_qty || 0) * l.unit_cost) : (l.total_cost ? egp(l.total_cost) : '—')}</td>
                          <td>
                            {needsCutting && (
                              <button className="btn btn-sm text-xs"
                                onClick={() => {
                                  const r = (reservations || []).find(x => x.material_id === l.material_id)
                                  if (!r) { toast('احجز هذه المادة أولاً من تاب "المواد المحجوزة" (حجز تلقائي من BOM)', 'error'); setActiveTab('materials'); return }
                                  setActiveTab('materials')
                                  setCutRes(r)
                                }}>
                                ✂️ تخطيط التقطيع
                              </button>
                            )}
                          </td>
                        </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )
        })()}

        {/* COSTS */}
        {activeTab === 'costs' && (
          <div className="wo-section-card">
            <div className="wo-section-header">
              <div className="wo-section-title">بنود التكاليف الفعلية</div>
              <span style={{ fontWeight: 700, color: '#0B1F3A', fontSize: 15 }}>
                الإجمالي: {egp(actual)}
              </span>
            </div>
            {!costLines?.length ? (
              <div className="wo-empty-state">
                <div style={{ fontSize: 36 }}>💰</div>
                <div style={{ fontWeight: 600, color: '#475569', marginTop: 8 }}>لا توجد بنود تكلفة مسجلة بعد</div>
              </div>
            ) : (
              <div className="wo-table-wrap">
                <table className="erp-table">
                  <thead>
                    <tr><th>#</th><th>البيان</th><th>الفئة</th><th>التاريخ</th><th>المبلغ</th></tr>
                  </thead>
                  <tbody>
                    {costLines.map((l, i) => {
                      const catMap = { material: { label: 'مواد', color: '#3B82F6' }, labor: { label: 'عمالة', color: '#8B5CF6' }, overhead: { label: 'عمومية', color: '#F59E0B' } }
                      const cat = catMap[l.category] || { label: l.category, color: '#94A3B8' }
                      return (
                        <tr key={l.id || i}>
                          <td style={{ color: '#94A3B8', fontSize: 12 }}>{i + 1}</td>
                          <td style={{ fontWeight: 500 }}>{l.description || l.note || '—'}</td>
                          <td>
                            <span style={{ padding: '2px 8px', borderRadius: 10, fontSize: 11, fontWeight: 600, background: cat.color + '18', color: cat.color }}>
                              {cat.label}
                            </span>
                          </td>
                          <td style={{ color: '#64748B', fontSize: 13 }}>{fmtDate(l.date) || '—'}</td>
                          <td style={{ fontWeight: 700, color: '#0B1F3A' }}>{egp(l.amount)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Reserve Material Modal ── */}
      <Modal open={modalRes === 'reserve'} onClose={() => setModalRes(null)} title="حجز مادة يدوياً" size="sm">
        <div className="flex flex-col gap-3">
          <Select
            label="المادة *"
            value={formRes.material_id || ''}
            onChange={e => setFormRes(f => ({ ...f, material_id: e.target.value }))}
          >
            <option value="">— اختر المادة —</option>
            {(materials || []).map(m => (
              <option key={m.id} value={m.id}>
                {m.name_ar} (متوفر: {Number(m.stock_qty || 0).toFixed(1)} {m.unit})
              </option>
            ))}
          </Select>
          <Input
            label="الكمية *"
            type="number" step="0.1" min="0.1"
            value={formRes.qty}
            onChange={e => setFormRes(f => ({ ...f, qty: e.target.value }))}
            placeholder="0.00"
          />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModalRes(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={doReserve} disabled={reserving}>
            {reserving ? '…' : 'حجز'}
          </button>
        </ModalFooter>
      </Modal>

      {/* ── Create Invoice Modal ── */}
      <Modal open={showInvModal} onClose={() => { setShowInvModal(false); setInvCustId(''); setInvPrice('') }} title="إنشاء فاتورة بيع من أمر الإنتاج" size="sm">
        <div className="flex flex-col gap-3">
          <div style={{ padding: '8px 12px', background: 'var(--surface)', borderRadius: 8, fontSize: 13 }}>
            <b>أمر الإنتاج:</b> {wo.code} — {wo.equipment_name_ar || wo.equipment_name || ''}
          </div>
          <Select
            label="العميل *"
            value={invCustId}
            onChange={e => setInvCustId(e.target.value)}
          >
            <option value="">— اختر العميل —</option>
            {(customers || []).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
          <Input label="سعر البيع (ج.م)" type="number" min="0" step="0.01" value={invPrice}
            placeholder={String(wo.actual_cost || wo.planned_cost || '')}
            onChange={e => setInvPrice(e.target.value)} />
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            تكلفة المعدة الفعلية: {egp(wo.actual_cost || wo.planned_cost)} — سعر البيع الافتراضي = التكلفة (بدون ربح) لو سبت الخانة فاضية. المعدة هتتخصم من مخزون تام الصنع.
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => { setShowInvModal(false); setInvCustId('') }}>إلغاء</button>
          <button className="btn btn-primary" onClick={doCreateInvoice} disabled={creatingInv || !invCustId}>
            {creatingInv ? '…' : 'إنشاء الفاتورة'}
          </button>
        </ModalFooter>
      </Modal>

      {/* ── Execute BOM Results Modal ── */}
      <Modal open={!!execResult} onClose={() => setExecResult(null)} title="نتائج تنفيذ BOM" size="md">
        {execResult && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* إجمالي */}
            <div style={{ background: '#0f4c2a18', border: '1px solid #0f4c2a40', borderRadius: 8, padding: '10px 14px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontWeight: 700, fontSize: 15 }}>إجمالي التكلفة المصروفة</span>
              <span style={{ fontWeight: 800, fontSize: 18, color: '#0f4c2a' }}>{egp(execResult.total_cost)}</span>
            </div>

            {/* مواد عادية */}
            {execResult.issued_bulk?.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 13, color: '#475569', marginBottom: 4 }}>✅ مواد صُرفت مباشرة ({execResult.issued_bulk.length})</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {execResult.issued_bulk.map((m, i) => (
                    <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 8px', background: '#f0fdf4', borderRadius: 4 }}>
                      <span>{m.material_name}</span>
                      <span style={{ color: '#059669', fontWeight: 600 }}>{Number(m.issued || 0).toFixed(2)} {m.unit} — {egp(m.cost)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* ألواح */}
            {execResult.cut_sheet?.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 13, color: '#475569', marginBottom: 4 }}>🗂️ ألواح صُرفت ({execResult.cut_sheet.length})</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {execResult.cut_sheet.map((m, i) => (
                    <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 8px', background: '#eff6ff', borderRadius: 4 }}>
                      <span>{m.material_name}</span>
                      <span style={{ color: '#2563eb', fontWeight: 600 }}>{Number(m.issued_m2 || 0).toFixed(3)} م² — {egp(m.cost)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* أسياخ */}
            {execResult.cut_bar?.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 13, color: '#475569', marginBottom: 4 }}>🔩 أسياخ/مواسير صُرفت ({execResult.cut_bar.length})</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {execResult.cut_bar.map((m, i) => (
                    <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 8px', background: '#faf5ff', borderRadius: 4 }}>
                      <span>{m.material_name}</span>
                      <span style={{ color: '#7c3aed', fontWeight: 600 }}>{Number(m.issued_kg || 0).toFixed(2)} كج — {egp(m.cost)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* نسالة مستخدمة */}
            {execResult.remnants_used?.length > 0 && (
              <div style={{ fontSize: 12, color: '#64748b', padding: '6px 10px', background: '#f8fafc', borderRadius: 6 }}>
                ♻️ استُخدمت {execResult.remnants_used.length} قطعة نسالة من المخزون
                {execResult.new_remnants?.length > 0 && ` — تم إنشاء ${execResult.new_remnants.length} نسالة جديدة`}
              </div>
            )}

            {/* نقص */}
            {execResult.shortages?.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 13, color: '#dc2626', marginBottom: 4 }}>⚠️ نقص في المخزون ({execResult.shortages.length} مادة)</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {execResult.shortages.map((s, i) => (
                    <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 8px', background: '#fef2f2', borderRadius: 4, border: '1px solid #fecaca' }}>
                      <span style={{ color: '#dc2626' }}>{s.material_name}</span>
                      <span style={{ color: '#dc2626', fontWeight: 600 }}>
                        نقص {Number(s.shortage || s.shortage_m2 || s.shortage_kg || 0).toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
        <ModalFooter>
          <button className="btn btn-primary" onClick={() => setExecResult(null)}>حسناً</button>
        </ModalFooter>
      </Modal>

      {/* ── Edit WO Modal ── */}
      <Modal open={showEditModal} onClose={() => setShowEditModal(false)} title="تعديل أمر الإنتاج">
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="الكود (اختياري — يُولَّد تلقائياً)" value={form.code || ''} onChange={e => setForm(f => ({...f, code: e.target.value}))} placeholder="WO-0001" />
            <Select label="الحالة" value={form.status || 'draft'} onChange={e => setForm(f => ({...f, status: e.target.value}))} options={STATUS_OPTS} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="التكلفة المخططة" type="number" min="0" value={form.planned_cost || ''} onChange={e => setForm(f => ({...f, planned_cost: +e.target.value}))} />
            <Input label="تاريخ البدء" type="date" value={form.start_date || today()} onChange={e => setForm(f => ({...f, start_date: e.target.value}))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="تاريخ الانتهاء المخطط" type="date" value={form.planned_end_date || ''} onChange={e => setForm(f => ({...f, planned_end_date: e.target.value}))} />
            <Select label="الأولوية" value={form.priority || 'normal'} onChange={e => setForm(f => ({...f, priority: e.target.value}))}>
              <option value="low">منخفضة</option>
              <option value="normal">عادية</option>
              <option value="high">عالية</option>
              <option value="urgent">عاجلة</option>
            </Select>
          </FormGrid>
          <Textarea label="الوصف" value={form.description || ''} onChange={e => setForm(f => ({...f, description: e.target.value}))} />
          {(() => {
            const selEq = (allEqs || []).find(e => e.id === Number(form.equipment_id || wo.equipment_id))
            if (!selEq?.length_min) return null
            const unitLabel = selEq.calc_unit === 'meter' ? 'متر' : selEq.calc_unit === 'piece' ? 'قطعة' : selEq.calc_unit || 'متر'
            return (
              <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 8, padding: '10px 14px' }}>
                <label className="field-label" style={{ color: '#1d4ed8' }}>
                  📏 طول التصنيع ({unitLabel})
                  <span style={{ fontSize: 11, color: '#64748b', marginRight: 6 }}>
                    النطاق: {selEq.length_min} — {selEq.length_max} م
                  </span>
                </label>
                <input className="field-input" type="number" step="0.1"
                  min={selEq.length_min} max={selEq.length_max}
                  value={form.planned_length || ''}
                  onChange={e => setForm(f => ({ ...f, planned_length: e.target.value ? +e.target.value : null }))}
                  placeholder={`مثال: ${selEq.length_min}`} />
                <MetricBomPreview equipmentId={selEq.id} length={form.planned_length} />
              </div>
            )
          })()}
          <FormGrid cols={2}>
            <div>
              <label className="field-label">العميل (اختياري)</label>
              <select className="field-select w-full" value={form.customer_id || ''}
                onChange={e => onEditCustomerChange(e.target.value)}>
                <option value="">— بدون عميل —</option>
                {(Array.isArray(customers) ? customers : []).map(c => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="field-label">الموقع (اختياري)</label>
              <select className="field-select w-full" value={form.location_id || ''}
                onChange={e => setForm(f => ({ ...f, location_id: e.target.value ? +e.target.value : null }))}
                disabled={!form.customer_id}>
                <option value="">— بدون موقع —</option>
                {editCustLocations.map(l => (
                  <option key={l.id} value={l.id}>{l.name}</option>
                ))}
              </select>
            </div>
          </FormGrid>
          <div>
            <label className="field-label">طلب العميل المرتبط (اختياري)</label>
            <select className="field-select w-full" value={form.customer_order_id || ''}
              onChange={e => setForm(f => ({ ...f, customer_order_id: e.target.value ? +e.target.value : null }))}>
              <option value="">— بدون ربط بطلب —</option>
              {(Array.isArray(allOrders) ? allOrders : []).map(o => (
                <option key={o.id} value={o.id}>
                  {o.code} — {o.customer_name || `عميل #${o.customer_id}`}
                </option>
              ))}
            </select>
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setShowEditModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={doEditSave} disabled={editSaving}>
            {editSaving ? '…' : 'حفظ'}
          </button>
        </ModalFooter>
      </Modal>

      <CutIssueModal
        res={cutRes}
        mat={cutRes ? (materials || []).find(m => m.id === cutRes.material_id) : null}
        wo={wo}
        onClose={() => setCutRes(null)}
        onDone={() => { setCutRes(null); reloadRes(); reloadCost(); onRefresh() }}
      />
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════
   WORK ORDERS — LIST VIEW
══════════════════════════════════════════════════════════════ */
export default function WorkOrders({ onNavigate }) {
  const { canWrite } = useAuth()
  const toast = useToast()
  const write = canWrite('production')

  const { data: wos, loading, reload } = useApi(() => api.accounting.workOrders())
  const { data: allEqs } = useApi(() => api.equipment.listAll())
  const { data: allOrders } = useApi(() => api.get('/customers/orders/all').catch(bgErr('قائمة الطلبات', [])))
  const { data: allCustomers } = useApi(() => api.customers.list().catch(bgErr('قائمة العملاء', [])))
  const [custLocations, setCustLocations] = useState([])

  const [q, setQ]         = useState('')
  const [modal, setModal] = useState(null)
  const [form, setForm]   = useState({})
  const [bomLoading, setBomLoading] = useState(false)
  const [selectedWo, setSelectedWo] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [statusFilter, setStatusFilter] = useState('all')
  const [customerFilter, setCustomerFilter] = useState('all')
  const [equipmentFilter, setEquipmentFilter] = useState('all')

  const filtered = useMemo(() => {
    if (!wos) return []
    const lq = q.toLowerCase()
    return wos.filter(w => {
      const matchQ = !lq ||
        w.code?.toLowerCase().includes(lq) ||
        w.equipment?.toLowerCase().includes(lq) ||
        w.equipment_name_ar?.toLowerCase().includes(lq) ||
        w.customer_name?.toLowerCase().includes(lq)
      const matchStatus = statusFilter === 'all' || w.status === statusFilter
      const matchCustomer = customerFilter === 'all' || String(w.customer_id) === customerFilter
      const matchEquipment = equipmentFilter === 'all' || String(w.equipment_id) === equipmentFilter
      return matchQ && matchStatus && matchCustomer && matchEquipment
    })
  }, [wos, q, statusFilter, customerFilter, equipmentFilter])

  // قوائم الفلاتر تُبنى فقط من القيم الموجودة فعلياً في أوامر الإنتاج (لا تعرض عملاء/معدات بلا أوامر)
  const customerFilterOptions = useMemo(() => {
    const seen = new Map()
    ;(wos || []).forEach(w => { if (w.customer_id && !seen.has(w.customer_id)) seen.set(w.customer_id, w.customer_name) })
    return [...seen.entries()].sort((a, b) => (a[1] || '').localeCompare(b[1] || ''))
  }, [wos])
  const equipmentFilterOptions = useMemo(() => {
    const seen = new Map()
    ;(wos || []).forEach(w => { if (w.equipment_id && !seen.has(w.equipment_id)) seen.set(w.equipment_id, w.equipment_name_ar || w.equipment_name || w.equipment) })
    return [...seen.entries()].sort((a, b) => (a[1] || '').localeCompare(b[1] || ''))
  }, [wos])

  const openAdd = async () => {
    setForm({ status: 'draft', start_date: today(), planned_cost: 0, quantity: 1, quantity_unit: 'قطعة' })
    setCustLocations([])
    setModal({ mode: 'add' })
    try { const r = await api.accounting.nextWoCode(); setForm(f => ({ ...f, code: r.code })) } catch (_) {}
  }

  // يُعيد حساب planned_cost من BOM البارامتري كلما تغيّرت المعدة أو الكمية
  const fetchParametricBOM = async (eqId, qty) => {
    if (!eqId) return
    setBomLoading(true)
    try {
      const quantity = qty || 1
      const bom = await api.get(`/equipment/${eqId}/bom/parametric?quantity=${quantity}`).catch(bgErr('تكلفة BOM البارامترية', null))
      if (bom?.total_cost != null) {
        setForm(f => ({ ...f, planned_cost: bom.total_cost }))
        const fixedNote = bom.fixed_cost > 0 ? ` (مواد ${egp(bom.material_cost)} + تكاليف ثابتة ${egp(bom.fixed_cost)})` : ''
        toast(`✅ التكلفة المخططة (×${quantity}): ${egp(bom.total_cost)}${fixedNote}`, 'info')
      }
    } finally { setBomLoading(false) }
  }

  const onEquipmentChange = async (eqId) => {
    setForm(f => ({ ...f, equipment_id: +eqId, planned_cost: 0 }))
    if (!eqId) return
    setForm(f => { fetchParametricBOM(eqId, f.quantity || 1); return f })
  }

  const onQuantityChange = (val) => {
    setForm(f => {
      const qty = Number(val) || 1
      if (f.equipment_id) fetchParametricBOM(f.equipment_id, qty)
      return { ...f, quantity: qty }
    })
  }

  const onCustomerChange = async (custId) => {
    setForm(f => ({ ...f, customer_id: custId ? +custId : null, location_id: null }))
    setCustLocations([])
    if (custId) {
      try {
        const locs = await api.get(`/customers/${custId}/locations`)
        setCustLocations(Array.isArray(locs) ? locs : [])
      } catch (_) {}
    }
  }

  // فتح التعديل من الكارت: الموقع المحفوظ كان بيظهر "بدون موقع" لأن مواقع العميل ما كانتش بتتحمّل
  const openEdit = async (wo) => {
    setForm({ ...wo })
    setCustLocations([])
    setModal({ mode: 'edit', item: wo })
    if (wo.customer_id) {
      try {
        const locs = await api.get(`/customers/${wo.customer_id}/locations`)
        setCustLocations(Array.isArray(locs) ? locs : [])
      } catch (_) {}
    }
  }

  const [save, saving] = useAction(async () => {
    if (modal?.mode !== 'edit' && !form.equipment_id) { toast('اختر المعدة', 'error'); return }
    const err = validateWoForm(form, allEqs)
    if (err) { toast(err, 'error'); return }
    const payload = normalizeWoDates({
      ...form,
      quantity:          Number(form.quantity || 1),
      quantity_unit:     form.quantity_unit || 'قطعة',
      customer_order_id: form.customer_order_id ? Number(form.customer_order_id) : null,
      customer_id:       form.customer_id ? Number(form.customer_id) : null,
      location_id:       form.location_id ? Number(form.location_id) : null,
      planned_length:    form.planned_length ? Number(form.planned_length) : null,
    })
    if (modal?.mode === 'edit') await api.accounting.updateWO(form.id, payload)
    else await api.accounting.createWO(payload)
    toast('تم الحفظ'); setModal(null); reload()
  })

  const eqOptions = useMemo(() => (allEqs || []).map(eq => ({
    ...eq, label: '\u00A0'.repeat(eq.level * 4) + `L${eq.level} ${eq.code} — ${eq.name_ar}`
  })), [allEqs])

  // If a WO is selected, show its profile
  if (selectedWo) {
    const liveWo = (wos || []).find(w => w.id === selectedWo.id) || selectedWo
    return (
      <WorkOrderProfile
        wo={liveWo}
        write={write}
        onBack={() => setSelectedWo(null)}
        onRefresh={() => { reload(); setRefreshKey(k => k + 1) }}
      />
    )
  }

  if (loading) return <PageSpinner />

  const counts = {
    all: wos?.length || 0,
    draft: (wos || []).filter(w => w.status === 'draft').length,
    in_progress: (wos || []).filter(w => w.status === 'in_progress').length,
    done: (wos || []).filter(w => w.status === 'done').length,
  }

  return (
    <div className="flex flex-col gap-4">

      {/* ── Stat Cards ── */}
      <div className="wo-stat-row">
        {[
          { label: 'إجمالي الأوامر', value: counts.all, color: '#0B1F3A', bg: '#F8FAFC' },
          { label: 'مسودة', value: counts.draft, color: '#94A3B8', bg: '#F8FAFC' },
          { label: 'جارية', value: counts.in_progress, color: '#3B82F6', bg: '#EFF6FF' },
          { label: 'منتهية', value: counts.done, color: '#22C55E', bg: '#F0FDF4' },
        ].map((s, i) => (
          <div key={i} className="wo-stat-card" style={{ background: s.bg }}
            onClick={() => setStatusFilter(i === 0 ? 'all' : ['all','draft','in_progress','done'][i])}>
            <div style={{ fontSize: 28, fontWeight: 800, color: s.color }}>{s.value}</div>
            <div style={{ fontSize: 13, color: '#64748B', marginTop: 2 }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* ── Toolbar ── */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div className="flex-1 min-w-48">
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالكود أو المعدة…" />
        </div>
        <div className="flex gap-2 flex-wrap">
          {['all','draft','in_progress','done','cancelled'].map(s => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              style={{
                padding: '4px 12px', borderRadius: 20, fontSize: 12, fontWeight: 600,
                cursor: 'pointer', border: 'none', transition: 'all .15s',
                background: statusFilter === s ? '#0B1F3A' : '#F1F5F9',
                color: statusFilter === s ? '#fff' : '#475569',
              }}
            >
              {s === 'all' ? 'الكل' : STATUS_OPTS.find(o => o.value === s)?.label || s}
            </button>
          ))}
        </div>
        <select className="erp-input text-xs" style={{ maxWidth: 160 }} value={customerFilter} onChange={e => setCustomerFilter(e.target.value)}>
          <option value="all">كل العملاء</option>
          {customerFilterOptions.map(([id, name]) => <option key={id} value={String(id)}>{name || `#${id}`}</option>)}
        </select>
        <select className="erp-input text-xs" style={{ maxWidth: 180 }} value={equipmentFilter} onChange={e => setEquipmentFilter(e.target.value)}>
          <option value="all">كل المعدات</option>
          {equipmentFilterOptions.map(([id, name]) => <option key={id} value={String(id)}>{name || `#${id}`}</option>)}
        </select>
        <button className="btn btn-sm text-xs" title="تصدير Excel"
          onClick={() => api.export.workOrders()}>📥 Excel</button>
        {write && (
          <button className="btn btn-primary btn-sm" onClick={openAdd}>
            + أمر إنتاج جديد
          </button>
        )}
      </div>

      {/* ── Cards Grid ── */}
      {!filtered.length ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px 20px' }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>🏭</div>
          <div style={{ fontWeight: 700, fontSize: 18, color: '#475569' }}>لا توجد أوامر إنتاج</div>
          <div style={{ color: '#94A3B8', marginTop: 8, fontSize: 14 }}>أنشئ أمر إنتاج جديد للبدء</div>
          {write && <button className="btn btn-primary" style={{ marginTop: 20 }} onClick={openAdd}>+ أمر إنتاج جديد</button>}
        </div>
      ) : (
        <div className="wo-cards-grid">
          {filtered.map(wo => {
            const pct     = Math.min(100, wo.cost_pct || 0)
            const planned = Number(wo.planned_cost || 0)
            const actual  = Number(wo.actual_cost  || 0)
            const variance = planned > 0 ? ((actual - planned) / planned * 100) : null
            const isOver  = variance != null && variance > 15
            const barColor = pct > 90 ? '#EF4444' : pct > 70 ? '#F59E0B' : '#22C55E'

            return (
              <div
                key={wo.id}
                className="wo-card"
                onClick={() => setSelectedWo(wo)}
                style={{ cursor: 'pointer' }}
              >
                <div className="wo-card-top">
                  <span className="font-mono text-xs" style={{ color: '#64748B', background: '#F1F5F9', padding: '2px 8px', borderRadius: 6 }}>{wo.code}</span>
                  <StatusPill value={wo.status} />
                </div>

                <div className="wo-card-title">
                  {wo.equipment_name_ar || wo.equipment_name || wo.equipment || '—'}
                  {wo.planned_length && (
                    <span style={{ fontSize: 11, fontWeight: 700, background: '#eff6ff', color: '#1d4ed8', border: '1px solid #bfdbfe', borderRadius: 6, padding: '1px 7px', marginRight: 6 }}>
                      📐 {wo.planned_length}م
                    </span>
                  )}
                </div>

                {(wo.customer_name || wo.location_name) && (
                  <div style={{ fontSize: 12, color: '#475569', marginBottom: 4, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {wo.customer_name && <span>👤 {wo.customer_name}</span>}
                    {wo.location_name && <span>📍 {wo.location_name}</span>}
                  </div>
                )}

                <div className="wo-card-progress">
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, fontSize: 12 }}>
                    <span style={{ color: '#94A3B8' }}>التقدم</span>
                    <span style={{ fontWeight: 700, color: barColor }}>{pct}%</span>
                  </div>
                  <div style={{ height: 6, background: '#F1F5F9', borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${pct}%`, background: barColor, borderRadius: 3, transition: 'width .5s' }} />
                  </div>
                </div>

                <div className="wo-card-costs">
                  <div>
                    <div style={{ fontSize: 11, color: '#94A3B8' }}>مخططة</div>
                    <div style={{ fontWeight: 700, color: '#0B1F3A', fontSize: 14 }}>{planned > 0 ? egp(planned) : '—'}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: '#94A3B8' }}>فعلية</div>
                    <div style={{ fontWeight: 700, color: isOver ? '#EF4444' : '#0B1F3A', fontSize: 14 }}>{egp(actual)}</div>
                  </div>
                  {variance != null && (
                    <div>
                      <div style={{ fontSize: 11, color: '#94A3B8' }}>انحراف</div>
                      <div style={{ fontWeight: 700, color: variance > 0 ? '#EF4444' : '#22C55E', fontSize: 14 }}>
                        {variance > 0 ? '+' : ''}{variance.toFixed(1)}%
                      </div>
                    </div>
                  )}
                </div>

                <div style={{ fontSize: 12, color: '#94A3B8', marginTop: 8, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  {wo.start_date && <span>📅 {fmtDate(wo.start_date)}</span>}
                  {wo.planned_end_date && <span>🏁 {fmtDate(wo.planned_end_date)}</span>}
                  {wo.priority && wo.priority !== 'normal' && (
                    <span style={{ color: { high: '#F59E0B', urgent: '#EF4444', low: '#94A3B8' }[wo.priority] || '#94A3B8', fontWeight: 600 }}>
                      {{ high: '🔶 عالية', urgent: '🔴 عاجلة', low: '⬇️ منخفضة' }[wo.priority] || wo.priority}
                    </span>
                  )}
                </div>

                <div className="wo-card-footer">
                  <span style={{ fontSize: 12, color: '#64748B' }}>اضغط لعرض التفاصيل والإنتاج →</span>
                  {write && wo.status !== 'done' && wo.status !== 'cancelled' && (
                    <button
                      className="wo-btn-sm"
                      style={{ fontSize: 11, padding: '3px 8px' }}
                      title="تعديل"
                      onClick={e => { e.stopPropagation(); openEdit(wo) }}
                    >
                      ✏️
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* ── Add/Edit Modal ── */}
      <Modal open={!!modal} onClose={() => setModal(null)}
             title={modal?.mode === 'edit' ? 'تعديل أمر إنتاج' : 'أمر إنتاج جديد'}>
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="الكود (اختياري — يُولَّد تلقائياً)" value={form.code || ''}
              onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="WO-0001" />
            <Select label="الحالة" value={form.status || 'draft'}
              onChange={e => setForm(f => ({ ...f, status: e.target.value }))} options={STATUS_OPTS} />
          </FormGrid>
          <div>
            <label className="field-label">المعدة</label>
            <select className="field-select w-full" value={form.equipment_id || ''}
              onChange={e => onEquipmentChange(e.target.value)}>
              <option value="">— اختر معدة —</option>
              {eqOptions.map(eq => <option key={eq.id} value={eq.id}>{eq.label}</option>)}
            </select>
          </div>
          <FormGrid cols={2}>
            <div>
              <label className="field-label">
                الكمية المنتَجة
                {bomLoading && <span className="text-[10px] text-erp-muted mr-1 animate-pulse"> ⏳ BOM…</span>}
              </label>
              <input className="field-input" type="number" step="0.001" min="0.001"
                value={form.quantity ?? 1}
                onChange={e => onQuantityChange(e.target.value)}
                placeholder="1" />
            </div>
            <Select label="وحدة الكمية" value={form.quantity_unit || 'قطعة'}
              onChange={e => setForm(f => ({ ...f, quantity_unit: e.target.value }))}>
              <option value="قطعة">قطعة</option>
              <option value="متر">متر</option>
              <option value="طن">طن</option>
              <option value="كجم">كجم</option>
              <option value="لتر">لتر</option>
            </Select>
          </FormGrid>
          {/* حقل الطول — يظهر فقط للمعدات المترية */}
          {form.equipment_id && (() => {
            const selEq = eqOptions.find(e => e.id === Number(form.equipment_id))
            if (!selEq?.length_min) return null
            const unitLabel = selEq.calc_unit === 'meter' ? 'متر' : selEq.calc_unit === 'piece' ? 'قطعة' : selEq.calc_unit || 'متر'
            return (
              <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 8, padding: '10px 14px' }}>
                <label className="field-label" style={{ color: '#1d4ed8' }}>
                  📏 طول التصنيع ({unitLabel})
                  <span style={{ fontSize: 11, color: '#64748b', marginRight: 6 }}>
                    النطاق المسموح: {selEq.length_min} — {selEq.length_max} م
                  </span>
                </label>
                <input className="field-input" type="number" step="0.1"
                  min={selEq.length_min} max={selEq.length_max}
                  value={form.planned_length || ''}
                  onChange={e => setForm(f => ({ ...f, planned_length: e.target.value ? +e.target.value : null }))}
                  placeholder={`مثال: ${selEq.length_min}`} />
                <MetricBomPreview equipmentId={selEq.id} length={form.planned_length} />
              </div>
            )
          })()}
          <FormGrid cols={2}>
            <div>
              <label className="field-label">العميل (اختياري)</label>
              <select className="field-select w-full" value={form.customer_id || ''}
                onChange={e => onCustomerChange(e.target.value)}>
                <option value="">— بدون عميل —</option>
                {(Array.isArray(allCustomers) ? allCustomers : []).map(c => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="field-label">الموقع (اختياري)</label>
              <select className="field-select w-full" value={form.location_id || ''}
                onChange={e => setForm(f => ({ ...f, location_id: e.target.value ? +e.target.value : null }))}
                disabled={!form.customer_id}>
                <option value="">— بدون موقع —</option>
                {custLocations.map(l => (
                  <option key={l.id} value={l.id}>{l.name}</option>
                ))}
              </select>
            </div>
          </FormGrid>
          <div>
            <label className="field-label">طلب العميل المرتبط (اختياري)</label>
            <select className="field-select w-full" value={form.customer_order_id || ''}
              onChange={e => {
                const ordId = e.target.value ? +e.target.value : null
                const ord = ordId ? (allOrders || []).find(o => o.id === ordId) : null
                setForm(f => ({
                  ...f,
                  customer_order_id: ordId,
                  customer_id: ord?.customer_id ?? f.customer_id,
                  location_id: ord?.location_id ?? f.location_id,
                }))
                if (ord?.customer_id) onCustomerChange(ord.customer_id)
              }}>
              <option value="">— بدون ربط بطلب —</option>
              {(Array.isArray(allOrders) ? allOrders : []).map(o => (
                <option key={o.id} value={o.id}>
                  {o.code} — {o.customer_name || `عميل #${o.customer_id}`}
                </option>
              ))}
            </select>
          </div>
          <FormGrid cols={2}>
            <div>
              <label className="field-label">
                التكلفة المخططة
                <span className="text-[10px] text-erp-muted mr-1">(تُحسب تلقائياً من BOM × الكمية)</span>
              </label>
              <input className="field-input font-semibold" type="number" step="0.01" min="0"
                style={{ color: form.planned_cost > 0 ? '#FF8A00' : undefined }}
                value={form.planned_cost || ''}
                onChange={e => setForm(f => ({ ...f, planned_cost: +e.target.value }))}
                placeholder="يُحسب تلقائياً" />
            </div>
            <Input label="تاريخ البدء" type="date" value={form.start_date || today()}
              onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="تاريخ الانتهاء المخطط" type="date" value={form.planned_end_date || ''}
              onChange={e => setForm(f => ({ ...f, planned_end_date: e.target.value }))} />
            <Select label="الأولوية" value={form.priority || 'normal'}
              onChange={e => setForm(f => ({ ...f, priority: e.target.value }))}>
              <option value="low">منخفضة</option>
              <option value="normal">عادية</option>
              <option value="high">عالية</option>
              <option value="urgent">عاجلة</option>
            </Select>
          </FormGrid>
          <Textarea label="الوصف" value={form.description || ''}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? '…' : 'حفظ'}
          </button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
