/**
 * مولّد مخطط التقطيع (Cutting Plan / Nesting)
 * يحسب أقل عدد ممكن من الألواح (تقطيع 2D) أو القضبان (تقطيع 1D)
 * لتغطية مجموعة من القطع المطلوبة، مع رسم توضيحي لكل لوح/قضيب.
 */
import { useState, useMemo, useEffect } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useToast } from '../store/toast'
import api from '../api/client'
import { Input, Select } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'

const COLORS = [
  '#2dd4bf', '#60a5fa', '#f59e0b', '#a78bfa', '#f87171',
  '#34d399', '#fb923c', '#818cf8', '#facc15', '#4ade80',
]

let nextId = 1

const isPosNum = (v) => v !== '' && v != null && Number.isFinite(Number(v)) && Number(v) > 0
const isValidQty = (v) => Number.isInteger(Number(v)) && Number(v) >= 1 && Number(v) <= 10000
const STOCK_SOURCE_AR = { remnant: 'نسالة', bulk: 'مخزون عام' }

function groupLinesByMaterial(lines) {
  const groups = {}
  for (const l of lines) {
    const key = l.material_id
    if (!groups[key]) groups[key] = { material: l.material, lines: [] }
    groups[key].lines.push(l)
  }
  return Object.values(groups)
}

export default function CuttingPlan({ equipmentId, plannedLength }) {
  const toast = useToast()
  const { data: materials, loading: loadingMats } = useApi(() => api.inventory.materials())
  const { data: equipmentList } = useApi(() => api.equipment.listAll())

  const plates = useMemo(
    () => (materials || []).filter(m => m.is_plate || m.material_kind === 'plate'),
    [materials]
  )
  const bars = useMemo(
    () => (materials || []).filter(m => m.material_kind === 'bar'),
    [materials]
  )

  // ── تقطيع الألواح (2D) ─────────────────────────────────────────────
  const [materialId, setMaterialId] = useState('')
  const [sheetW, setSheetW] = useState('')
  const [sheetH, setSheetH] = useState('')
  const [kerf, setKerf] = useState('0')
  const [allowRotation, setAllowRotation] = useState(true)
  const [pieces, setPieces] = useState([
    { id: nextId++, label: 'A', width: '', height: '', qty: 1 },
  ])
  const [result, setResult] = useState(null)

  const [useStock, setUseStock] = useState(false)
  const [stockItems, setStockItems] = useState([])
  const [loadingStock, setLoadingStock] = useState(false)
  const [stockBusy, setStockBusy] = useState(false)
  const [newStock, setNewStock] = useState({ length_cm: '', width_cm: '', qty: 1, source: 'purchased' })

  // ── تقطيع الحديد/القضبان (1D) ───────────────────────────────────────
  const [barMaterialId, setBarMaterialId] = useState('')
  const [barLength, setBarLength] = useState('')
  const [barKerf, setBarKerf] = useState('0')
  const [barPieces, setBarPieces] = useState([
    { id: nextId++, label: 'A', length: '', qty: 1 },
  ])
  const [barResult, setBarResult] = useState(null)

  const [useBarStock, setUseBarStock] = useState(false)
  const [barStockItems, setBarStockItems] = useState([])
  const [loadingBarStock, setLoadingBarStock] = useState(false)
  const [newBarStock, setNewBarStock] = useState({ length_cm: '', qty: 1, source: 'purchased' })

  // ── الهالك المتبقي بعد التقطيع: قرارات نسالة/هالك نهائي ───────────────
  const [wasteDecisions, setWasteDecisions] = useState({})
  const [committing, setCommitting] = useState(false)
  // منع تأكيد نفس المخطط مرتين (كان بيسجّل نسالة/هالك مكرر في المخزون)
  const [committedSheetResult, setCommittedSheetResult] = useState(null)
  const [committedBarResult, setCommittedBarResult] = useState(null)

  // ── تحميل القطع من BOM (مشترك) ───────────────────────────────────────
  const [bomEquipmentId, setBomEquipmentId] = useState(equipmentId ? String(equipmentId) : '')
  const [bomQty, setBomQty] = useState(1)
  const [bomLoading, setBomLoading] = useState(false)
  const [bomPlateGroups, setBomPlateGroups] = useState(null)
  const [bomBarGroups, setBomBarGroups] = useState(null)
  const [autoLoaded, setAutoLoaded] = useState(false)

  const onMaterialChange = (id) => {
    setMaterialId(id)
    const mat = plates.find(m => String(m.id) === String(id))
    if (mat) {
      if (mat.plate_length_cm) setSheetW(String(mat.plate_length_cm))
      if (mat.plate_width_cm) setSheetH(String(mat.plate_width_cm))
    }
  }

  const onBarMaterialChange = (id) => {
    setBarMaterialId(id)
    const mat = bars.find(m => String(m.id) === String(id))
    if (mat && mat.bar_length_cm) setBarLength(String(mat.bar_length_cm))
  }

  const loadStockItems = async (id) => {
    if (!id) { setStockItems([]); return }
    setLoadingStock(true)
    try {
      const items = await api.inventory.sheetStock(id)
      setStockItems(items || [])
    } catch (e) {
      toast(e?.message || 'تعذّر تحميل مخزون الألواح', 'error')
    } finally {
      setLoadingStock(false)
    }
  }

  useEffect(() => {
    loadStockItems(materialId)
  }, [materialId])

  const addStockItem = async () => {
    if (!materialId) {
      toast('اختر المادة أولاً', 'error')
      return
    }
    if (!newStock.length_cm || !newStock.width_cm) {
      toast('أدخل مقاس اللوح (الطول والعرض)', 'error')
      return
    }
    if (!isPosNum(newStock.length_cm) || !isPosNum(newStock.width_cm)) {
      toast('الأبعاد يجب أن تكون أكبر من صفر', 'error')
      return
    }
    if (!(Number.isInteger(Number(newStock.qty || 1)) && Number(newStock.qty || 1) >= 1)) {
      toast('العدد يجب أن يكون رقماً صحيحاً 1 على الأقل', 'error')
      return
    }
    if (stockBusy) return
    setStockBusy(true)
    try {
      await api.inventory.createSheetStock({
        material_id: Number(materialId),
        length_cm: Number(newStock.length_cm),
        width_cm: Number(newStock.width_cm),
        qty: Number(newStock.qty || 1),
        source: newStock.source,
      })
      toast('تمت إضافة المقاس', 'success')
      setNewStock({ length_cm: '', width_cm: '', qty: 1, source: 'purchased' })
      loadStockItems(materialId)
    } catch (e) {
      toast(e?.message || 'تعذّر إضافة المقاس', 'error')
    } finally {
      setStockBusy(false)
    }
  }

  const removeStockItem = async (id) => {
    if (!window.confirm('حذف هذا المقاس من مخزون الألواح؟')) return
    try {
      await api.inventory.deleteSheetStock(id)
      toast('تم الحذف', 'success')
    } catch (e) {
      toast(e?.message || 'تعذّر الحذف', 'error')
    }
    loadStockItems(materialId)
  }

  const loadBarStockItems = async (id) => {
    if (!id) { setBarStockItems([]); return }
    setLoadingBarStock(true)
    try {
      const items = await api.inventory.barStock(id)
      setBarStockItems(items || [])
    } catch (e) {
      toast(e?.message || 'تعذّر تحميل مخزون الأسياخ', 'error')
    } finally {
      setLoadingBarStock(false)
    }
  }

  useEffect(() => {
    loadBarStockItems(barMaterialId)
  }, [barMaterialId])

  const addBarStockItem = async () => {
    if (!barMaterialId) {
      toast('اختر المادة أولاً', 'error')
      return
    }
    if (!newBarStock.length_cm) {
      toast('أدخل طول السيخ', 'error')
      return
    }
    if (!isPosNum(newBarStock.length_cm)) {
      toast('الطول يجب أن يكون أكبر من صفر', 'error')
      return
    }
    if (!(Number.isInteger(Number(newBarStock.qty || 1)) && Number(newBarStock.qty || 1) >= 1)) {
      toast('العدد يجب أن يكون رقماً صحيحاً 1 على الأقل', 'error')
      return
    }
    if (stockBusy) return
    setStockBusy(true)
    try {
      await api.inventory.createBarStock({
        material_id: Number(barMaterialId),
        length_cm: Number(newBarStock.length_cm),
        qty: Number(newBarStock.qty || 1),
        source: newBarStock.source,
      })
      toast('تمت إضافة الطول', 'success')
      setNewBarStock({ length_cm: '', qty: 1, source: 'purchased' })
      loadBarStockItems(barMaterialId)
    } catch (e) {
      toast(e?.message || 'تعذّر إضافة الطول', 'error')
    } finally {
      setStockBusy(false)
    }
  }

  const removeBarStockItem = async (id) => {
    if (!window.confirm('حذف هذا الطول من مخزون الأسياخ؟')) return
    try {
      await api.inventory.deleteBarStock(id)
      toast('تم الحذف', 'success')
    } catch (e) {
      toast(e?.message || 'تعذّر الحذف', 'error')
    }
    loadBarStockItems(barMaterialId)
  }

  const applyBomPlateGroup = (group) => {
    const multiplier = Number(bomQty || 1)
    const L = Number(plannedLength || 1)
    onMaterialChange(String(group.material.id))
    setPieces(group.lines.map(l => {
      const isVariable = l.line_type === 'variable'
      // عند scale_dim_length: الطول (dim_length_cm) يتناسب مع L وعدد القطع ثابت (هيكل/غطاء).
      // غير ذلك (متغير عادي مثل الريش): الأبعاد ثابتة وعدد القطع يتناسب مع L.
      const lengthVal = isVariable && l.scale_dim_length ? Number(l.dim_length_cm) * L : Number(l.dim_length_cm)
      const countScale = isVariable && !l.scale_dim_length ? L : 1
      return {
        id: nextId++,
        label: l.notes || `${group.material.code}-${l.id}`,
        width: String(lengthVal),
        height: String(l.dim_width_cm),
        qty: String((Number(l.dim_count) || 1) * countScale * multiplier),
      }
    }))
    setBomPlateGroups(null)
    toast(`تم تحميل ${group.lines.length} قطعة لوح من BOM (${group.material.code})`, 'success')
  }

  const applyBomBarGroup = (group) => {
    const multiplier = Number(bomQty || 1)
    const L = Number(plannedLength || 1)
    onBarMaterialChange(String(group.material.id))
    setBarPieces(group.lines.map(l => {
      const isVariable = l.line_type === 'variable'
      return {
        id: nextId++,
        label: l.notes || `${group.material.code}-${l.id}`,
        length: isVariable ? String(l.bar_length_cm * L) : String(l.bar_length_cm),
        qty: String((Number(l.dim_count) || 1) * multiplier),
      }
    }))
    setBomBarGroups(null)
    toast(`تم تحميل ${group.lines.length} قطعة حديد من BOM (${group.material.code})`, 'success')
  }

  const loadFromBOM = async () => {
    if (!bomEquipmentId) {
      toast('اختر المعدة أولاً', 'error')
      return
    }
    setBomLoading(true)
    try {
      const lines = await api.equipment.bom(bomEquipmentId)
      const plateLines = (lines || []).filter(l =>
        l.dim_length_cm && l.dim_width_cm &&
        l.material && (l.material.is_plate || l.material.material_kind === 'plate')
      )
      const barLines = (lines || []).filter(l =>
        l.bar_length_cm &&
        l.material && l.material.material_kind === 'bar'
      )
      if (!plateLines.length && !barLines.length) {
        toast('لا توجد بنود ألواح أو حديد بأبعاد قص محددة في BOM هذه المعدة', 'error')
        return
      }

      const plateGroups = groupLinesByMaterial(plateLines)
      if (plateGroups.length === 1) applyBomPlateGroup(plateGroups[0])
      else if (plateGroups.length > 1) setBomPlateGroups(plateGroups)

      const barGroups = groupLinesByMaterial(barLines)
      if (barGroups.length === 1) applyBomBarGroup(barGroups[0])
      else if (barGroups.length > 1) setBomBarGroups(barGroups)
    } catch (e) {
      toast(e?.message || 'تعذّر تحميل BOM المعدة', 'error')
    } finally {
      setBomLoading(false)
    }
  }

  useEffect(() => {
    if (equipmentId && !autoLoaded) {
      setAutoLoaded(true)
      loadFromBOM()
    }
  }, [equipmentId, autoLoaded])

  const addPiece = () => setPieces(p => [...p, { id: nextId++, label: '', width: '', height: '', qty: 1 }])
  const removePiece = (id) => setPieces(p => p.filter(x => x.id !== id))
  const updatePiece = (id, field, val) => setPieces(p => p.map(x => x.id === id ? { ...x, [field]: val } : x))

  const addBarPiece = () => setBarPieces(p => [...p, { id: nextId++, label: '', length: '', qty: 1 }])
  const removeBarPiece = (id) => setBarPieces(p => p.filter(x => x.id !== id))
  const updateBarPiece = (id, field, val) => setBarPieces(p => p.map(x => x.id === id ? { ...x, [field]: val } : x))

  const [run, computing] = useAction(async () => {
    if (useStock) {
      if (!materialId) {
        toast('اختر المادة لاستخدام المخزون المتاح', 'error')
        return
      }
    } else if (!sheetW || !sheetH) {
      toast('حدد مقاس اللوح (طول وعرض)', 'error')
      return
    } else if (!isPosNum(sheetW) || !isPosNum(sheetH)) {
      toast('مقاس اللوح يجب أن يكون أكبر من صفر', 'error')
      return
    }
    if (kerf !== '' && !(Number(kerf) >= 0)) {
      toast('سُمك القطع (kerf) لا يمكن أن يكون سالباً', 'error')
      return
    }
    // صفوف فيها أبعاد بدون اسم كانت بتتشال بصمت من الحساب — ننبّه بدل ما نسقطها
    const partial = pieces.filter(p => (p.width || p.height) && !(p.label || '').trim())
    if (partial.length) {
      toast('أدخل الكود/الاسم لكل قطعة لها مقاسات', 'error')
      return
    }
    const validPieces = pieces.filter(p => (p.label || '').trim() && p.width && p.height && Number(p.qty) > 0)
    if (!validPieces.length) {
      toast('أضف قطعة واحدة على الأقل بمقاساتها', 'error')
      return
    }
    if (validPieces.some(p => !isPosNum(p.width) || !isPosNum(p.height))) {
      toast('أبعاد القطعة يجب أن تكون أكبر من صفر', 'error')
      return
    }
    if (validPieces.some(p => !isValidQty(p.qty))) {
      toast('عدد القطع يجب أن يكون رقماً صحيحاً بين 1 و 10000', 'error')
      return
    }
    const payload = {
      material_id: materialId ? Number(materialId) : null,
      sheet_width_cm: useStock ? null : Number(sheetW),
      sheet_height_cm: useStock ? null : Number(sheetH),
      allow_rotation: allowRotation,
      kerf_mm: Number(kerf || 0),
      use_stock: useStock,
      pieces: validPieces.map(p => ({
        label: p.label, width_cm: Number(p.width), height_cm: Number(p.height), qty: Number(p.qty),
      })),
    }
    const res = await api.inventory.cuttingPlan(payload)
    // نتيجة جديدة → قرارات الهالك القديمة (بنفس المفاتيح s1-0…) لا تنطبق عليها
    setWasteDecisions(d => Object.fromEntries(Object.entries(d).filter(([k]) => !k.startsWith('s'))))
    setResult(res ? { ...res, _materialId: materialId } : res)
  })

  const [runBar, computingBar] = useAction(async () => {
    if (useBarStock) {
      if (!barMaterialId) {
        toast('اختر المادة لاستخدام المخزون المتاح', 'error')
        return
      }
    } else if (!barLength) {
      toast('حدد طول القضيب', 'error')
      return
    } else if (!isPosNum(barLength)) {
      toast('طول القضيب يجب أن يكون أكبر من صفر', 'error')
      return
    }
    if (barKerf !== '' && !(Number(barKerf) >= 0)) {
      toast('سُمك القطع (kerf) لا يمكن أن يكون سالباً', 'error')
      return
    }
    const partial = barPieces.filter(p => p.length && !(p.label || '').trim())
    if (partial.length) {
      toast('أدخل الكود/الاسم لكل قطعة لها طول', 'error')
      return
    }
    const validPieces = barPieces.filter(p => (p.label || '').trim() && p.length && Number(p.qty) > 0)
    if (!validPieces.length) {
      toast('أضف قطعة واحدة على الأقل بمقاسها', 'error')
      return
    }
    if (validPieces.some(p => !isPosNum(p.length))) {
      toast('طول القطعة يجب أن يكون أكبر من صفر', 'error')
      return
    }
    if (validPieces.some(p => !isValidQty(p.qty))) {
      toast('عدد القطع يجب أن يكون رقماً صحيحاً بين 1 و 10000', 'error')
      return
    }
    const payload = {
      material_id: barMaterialId ? Number(barMaterialId) : null,
      bar_length_cm: useBarStock ? null : Number(barLength),
      kerf_mm: Number(barKerf || 0),
      use_stock: useBarStock,
      pieces: validPieces.map(p => ({
        label: p.label, length_cm: Number(p.length), qty: Number(p.qty),
      })),
    }
    const res = await api.inventory.barCuttingPlan(payload)
    setWasteDecisions(d => Object.fromEntries(Object.entries(d).filter(([k]) => !k.startsWith('b'))))
    setBarResult(res ? { ...res, _materialId: barMaterialId } : res)
  })

  // ── هالك الألواح: تصنيف النسالة القابلة للحفظ مقابل الهالك النهائي ──────
  const sheetMaterial = plates.find(m => String(m.id) === String(materialId))
  const minRemnantAreaM2 = sheetMaterial?.min_remnant_value != null ? Number(sheetMaterial.min_remnant_value) : 0.05

  const sheetWasteItems = useMemo(() => {
    if (!result) return []
    const items = []
    ;(result.sheets || []).forEach(sheet => {
      ;(sheet.waste_rects || []).forEach((r, i) => {
        const areaM2 = (r.width * r.height) / 10000
        if (areaM2 < 0.001) return
        items.push({
          key: `s${sheet.index}-${i}`,
          length_cm: r.height,
          width_cm: r.width,
          area_m2: areaM2,
          eligible: areaM2 >= minRemnantAreaM2,
        })
      })
    })
    return items
  }, [result, minRemnantAreaM2])

  // ── هالك القضبان: تصنيف النسالة القابلة للحفظ مقابل الهالك النهائي ──────
  const barMaterial = bars.find(m => String(m.id) === String(barMaterialId))
  const minRemnantLengthCm = barMaterial?.min_remnant_value != null ? Number(barMaterial.min_remnant_value) : 20

  const barWasteItems = useMemo(() => {
    if (!barResult) return []
    const items = []
    ;(barResult.bars || []).forEach(bar => {
      if (bar.waste_length > 0.01) {
        items.push({
          key: `b${bar.index}`,
          length_cm: bar.waste_length,
          eligible: bar.waste_length >= minRemnantLengthCm,
        })
      }
    })
    return items
  }, [barResult, minRemnantLengthCm])

  useEffect(() => {
    setWasteDecisions(prev => {
      const next = { ...prev }
      sheetWasteItems.forEach(it => {
        if (!(it.key in next)) next[it.key] = it.eligible ? 'remnant' : 'scrap'
      })
      return next
    })
  }, [sheetWasteItems])

  useEffect(() => {
    setWasteDecisions(prev => {
      const next = { ...prev }
      barWasteItems.forEach(it => {
        if (!(it.key in next)) next[it.key] = it.eligible ? 'remnant' : 'scrap'
      })
      return next
    })
  }, [barWasteItems])

  const commitSheetWaste = async () => {
    if (!materialId) {
      toast('اختر المادة أولاً', 'error')
      return
    }
    if (committing || committedSheetResult === result) return
    // المخطط اتحسب لمادة مختلفة عن المختارة حالياً → تسجيل النسالة على المادة الغلط
    if (result?._materialId && String(result._materialId) !== String(materialId)) {
      toast('تم تغيير المادة بعد حساب المخطط — أعد حساب المخطط أولاً', 'error')
      return
    }
    if (!window.confirm('تأكيد تسجيل الهالك/النسالة في المخزون؟ لا يمكن التراجع عن هذه العملية من هنا.')) return
    setCommitting(true)
    try {
      const waste_items = sheetWasteItems.map(it => ({
        kind: 'sheet',
        length_cm: it.length_cm,
        width_cm: it.width_cm,
        qty: 1,
        action: wasteDecisions[it.key] || (it.eligible ? 'remnant' : 'scrap'),
      }))
      const res = await api.inventory.commitCuttingPlan({ material_id: Number(materialId), waste_items })
      toast(`تم تحديث المخزون — ${res?.remnants_created?.length ?? 0} نسالة جديدة، ${res?.scrap_records?.length ?? 0} سجل هالك`, 'success')
      setCommittedSheetResult(result)
      loadStockItems(materialId)
    } catch (e) {
      toast(e?.message || 'تعذّر تحديث المخزون', 'error')
    } finally {
      setCommitting(false)
    }
  }

  const commitBarWaste = async () => {
    if (!barMaterialId) {
      toast('اختر المادة أولاً', 'error')
      return
    }
    if (committing || committedBarResult === barResult) return
    if (barResult?._materialId && String(barResult._materialId) !== String(barMaterialId)) {
      toast('تم تغيير المادة بعد حساب المخطط — أعد حساب المخطط أولاً', 'error')
      return
    }
    if (!window.confirm('تأكيد تسجيل الهالك/النسالة في المخزون؟ لا يمكن التراجع عن هذه العملية من هنا.')) return
    setCommitting(true)
    try {
      const waste_items = barWasteItems.map(it => ({
        kind: 'bar',
        length_cm: it.length_cm,
        qty: 1,
        action: wasteDecisions[it.key] || (it.eligible ? 'remnant' : 'scrap'),
      }))
      const res = await api.inventory.commitCuttingPlan({ material_id: Number(barMaterialId), waste_items })
      toast(`تم تحديث المخزون — ${res?.remnants_created?.length ?? 0} نسالة جديدة، ${res?.scrap_records?.length ?? 0} سجل هالك`, 'success')
      setCommittedBarResult(barResult)
      loadBarStockItems(barMaterialId)
    } catch (e) {
      toast(e?.message || 'تعذّر تحديث المخزون', 'error')
    } finally {
      setCommitting(false)
    }
  }

  if (loadingMats) return <PageSpinner />

  return (
    <div className="flex flex-col gap-4">
      <div className="card flex flex-col gap-3">
        <div className="font-bold text-base">🧩 تحميل القطع المطلوبة من BOM معدة</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
          {equipmentId ? (
            <div>
              <div className="text-xs text-erp-muted mb-1">المعدة</div>
              <div className="font-medium text-sm">
                {(() => {
                  const eq = (equipmentList || []).find(e => String(e.id) === String(equipmentId))
                  return eq ? `${eq.code} — ${eq.name_ar}` : `#${equipmentId}`
                })()}
              </div>
            </div>
          ) : (
            <Select
              label="المعدة"
              value={bomEquipmentId}
              onChange={e => setBomEquipmentId(e.target.value)}
            >
              <option value="">— اختر معدة —</option>
              {(equipmentList || []).map(eq => (
                <option key={eq.id} value={eq.id}>{eq.code} — {eq.name_ar}</option>
              ))}
            </Select>
          )}
          <Input label="عدد الوحدات" type="number" min="1" value={bomQty} onChange={e => setBomQty(e.target.value)} />
          <button className="btn btn-sm btn-primary" onClick={loadFromBOM} disabled={bomLoading}>
            {bomLoading ? 'جارٍ التحميل...' : '+ تحميل القطع من BOM (ألواح وحديد)'}
          </button>
        </div>
        {bomPlateGroups && (
          <div className="flex flex-col gap-1">
            <div className="text-sm text-erp-muted">يوجد أكثر من مادة لوح في BOM — اختر المادة المطلوب تحضير مخطط تقطيع ألواح لها:</div>
            <div className="flex flex-wrap gap-2">
              {bomPlateGroups.map(g => (
                <button key={g.material.id} className="btn btn-sm btn-secondary" onClick={() => applyBomPlateGroup(g)}>
                  {g.material.code} — {g.material.name_ar} ({g.lines.length} قطعة)
                </button>
              ))}
            </div>
          </div>
        )}
        {bomBarGroups && (
          <div className="flex flex-col gap-1">
            <div className="text-sm text-erp-muted">يوجد أكثر من مادة حديد في BOM — اختر المادة المطلوب تحضير مخطط تقطيع حديد لها:</div>
            <div className="flex flex-wrap gap-2">
              {bomBarGroups.map(g => (
                <button key={g.material.id} className="btn btn-sm btn-secondary" onClick={() => applyBomBarGroup(g)}>
                  {g.material.code} — {g.material.name_ar} ({g.lines.length} قطعة)
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── تقطيع الألواح (2D) ───────────────────────────────────────── */}
      <div className="card flex flex-col gap-3">
        <div className="font-bold text-base">🪵 تقطيع الألواح</div>

        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Select
            label="المادة (لوح) — اختياري"
            value={materialId}
            onChange={e => onMaterialChange(e.target.value)}
          >
            <option value="">— بدون / تحديد يدوي —</option>
            {plates.map(m => (
              <option key={m.id} value={m.id}>{m.code} — {m.name_ar}</option>
            ))}
          </Select>
          <Input label="عرض اللوح (سم)" type="number" min="0" value={sheetW} onChange={e => setSheetW(e.target.value)} disabled={useStock} />
          <Input label="ارتفاع اللوح (سم)" type="number" min="0" value={sheetH} onChange={e => setSheetH(e.target.value)} disabled={useStock} />
          <Input label="سُمك القص - kerf (مم)" type="number" min="0" value={kerf} onChange={e => setKerf(e.target.value)} />
          <label className="flex items-center gap-2 mt-6">
            <input type="checkbox" checked={allowRotation} onChange={e => setAllowRotation(e.target.checked)} />
            <span className="text-sm">السماح بتدوير القطع 90°</span>
          </label>
        </div>

        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={useStock}
            onChange={e => setUseStock(e.target.checked)}
            disabled={!materialId}
          />
          <span className="text-sm">استخدام المخزون المتاح (مقاسات متعددة + نسالة) — يتطلب تحديد المادة</span>
        </label>

        {materialId && (
          <div className="flex flex-col gap-2 border border-erp-border rounded-md p-3">
            <div className="font-bold text-sm">مخزون الألواح المتاحة لهذه المادة</div>
            {loadingStock ? (
              <div className="text-sm text-erp-muted">جارٍ التحميل...</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="erp-table">
                  <thead>
                    <tr>
                      <th>الطول (سم)</th><th>العرض (سم)</th><th>العدد</th><th>المصدر</th><th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {stockItems.map(item => (
                      <tr key={item.id}>
                        <td>{item.length_cm}</td>
                        <td>{item.width_cm}</td>
                        <td>{item.qty}</td>
                        <td>{STOCK_SOURCE_AR[item.source] || 'لوح جديد'}</td>
                        <td>
                          <button className="btn btn-sm btn-secondary" onClick={() => removeStockItem(item.id)}>حذف</button>
                        </td>
                      </tr>
                    ))}
                    {!stockItems.length && (
                      <tr><td colSpan={5} className="text-center text-erp-muted">لا توجد مقاسات مسجّلة</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 items-end">
              <Input label="الطول (سم)" type="number" value={newStock.length_cm} onChange={e => setNewStock(s => ({ ...s, length_cm: e.target.value }))} />
              <Input label="العرض (سم)" type="number" value={newStock.width_cm} onChange={e => setNewStock(s => ({ ...s, width_cm: e.target.value }))} />
              <Input label="العدد" type="number" min="1" value={newStock.qty} onChange={e => setNewStock(s => ({ ...s, qty: e.target.value }))} />
              <Select label="المصدر" value={newStock.source} onChange={e => setNewStock(s => ({ ...s, source: e.target.value }))}>
                <option value="purchased">لوح جديد</option>
                <option value="remnant">نسالة</option>
              </Select>
              <button className="btn btn-sm btn-primary" onClick={addStockItem} disabled={stockBusy}>{stockBusy ? '…' : '+ إضافة مقاس'}</button>
            </div>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <div className="font-bold text-sm">القطع المطلوبة</div>
          <div className="overflow-x-auto">
            <table className="erp-table">
              <thead>
                <tr>
                  <th>الكود/الاسم</th><th>العرض (سم)</th><th>الارتفاع (سم)</th><th>العدد</th><th></th>
                </tr>
              </thead>
              <tbody>
                {pieces.map(p => (
                  <tr key={p.id}>
                    <td><input className="field-input" value={p.label} onChange={e => updatePiece(p.id, 'label', e.target.value)} /></td>
                    <td><input className="field-input" type="number" value={p.width} onChange={e => updatePiece(p.id, 'width', e.target.value)} /></td>
                    <td><input className="field-input" type="number" value={p.height} onChange={e => updatePiece(p.id, 'height', e.target.value)} /></td>
                    <td><input className="field-input w-20" type="number" min="1" value={p.qty} onChange={e => updatePiece(p.id, 'qty', e.target.value)} /></td>
                    <td>
                      <button className="btn btn-sm btn-secondary" onClick={() => removePiece(p.id)} disabled={pieces.length === 1}>حذف</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <button className="btn btn-sm btn-secondary" onClick={addPiece}>+ إضافة قطعة</button>
          </div>
        </div>

        <div>
          <button className="btn btn-primary" onClick={run} disabled={computing}>
            {computing ? 'جارٍ الحساب...' : 'حساب مخطط تقطيع الألواح'}
          </button>
        </div>
      </div>

      {result && (
        <div className="flex flex-col gap-4">
          <div className="card flex items-center gap-6 flex-wrap">
            <div>
              <div className="text-xs text-erp-muted">عدد الألواح المطلوبة</div>
              <div className="text-2xl font-bold" style={{ color: 'var(--teal)' }}>{result.total_sheets}</div>
            </div>
            <div>
              <div className="text-xs text-erp-muted">إجمالي القطع</div>
              <div className="text-2xl font-bold">{result.total_pieces}</div>
            </div>
            {result.overall_efficiency_pct != null && (
              <div>
                <div className="text-xs text-erp-muted">نسبة الاستفادة الكلية</div>
                <div className="text-2xl font-bold">{result.overall_efficiency_pct}%</div>
              </div>
            )}
            {result.sheet_width != null && (
              <div>
                <div className="text-xs text-erp-muted">مقاس اللوح</div>
                <div className="text-lg font-bold">{result.sheet_width} × {result.sheet_height} سم</div>
              </div>
            )}
          </div>

          {result.shortage && result.shortage.length > 0 && (
            <div className="card" style={{ borderColor: '#f87171' }}>
              <div className="font-bold text-sm" style={{ color: '#dc2626' }}>⚠️ نواقص — لا يكفي المخزون المتاح لتغطية:</div>
              <ul className="text-sm mt-1">
                {result.shortage.map((s, i) => (
                  <li key={i}>{s.label}: {s.qty} قطعة</li>
                ))}
              </ul>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {(result.sheets || []).map(sheet => (
              <div key={sheet.index} className="card flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <div className="font-bold">
                    لوح #{sheet.index}
                    {sheet.source && (
                      <span className="text-xs font-normal text-erp-muted">
                        {' '}— {STOCK_SOURCE_AR[sheet.source] || 'لوح جديد'} ({sheet.sheet_width}×{sheet.sheet_height})
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-erp-muted">
                    استفادة {sheet.efficiency_pct}% — هالك {(sheet.waste_area / 10000).toFixed(3)} م²
                  </div>
                </div>
                <SheetSVG sheet={sheet} sheetW={sheet.sheet_width ?? result.sheet_width} sheetH={sheet.sheet_height ?? result.sheet_height} />
              </div>
            ))}
          </div>

          {materialId && sheetWasteItems.length > 0 && (
            <div className="card flex flex-col gap-2">
              <div className="font-bold text-sm">♻️ الهالك المتبقي — ماذا تفعل به؟</div>
              <div className="text-xs text-erp-muted">
                القطع الأصغر من {minRemnantAreaM2} م² تُصنَّف تلقائيًا كهالك نهائي. القطع الأكبر يمكن حفظها كنسالة لاستخدامها في عمليات تقطيع لاحقة.
              </div>
              <div className="overflow-x-auto">
                <table className="erp-table">
                  <thead>
                    <tr>
                      <th>المقاس (طول × عرض)</th><th>المساحة (م²)</th><th>القرار</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sheetWasteItems.map(it => (
                      <tr key={it.key}>
                        <td>{it.length_cm} × {it.width_cm} سم</td>
                        <td>{it.area_m2.toFixed(3)}</td>
                        <td>
                          {it.eligible ? (
                            <Select
                              value={wasteDecisions[it.key] || 'remnant'}
                              onChange={e => setWasteDecisions(d => ({ ...d, [it.key]: e.target.value }))}
                            >
                              <option value="remnant">حفظ كنسالة</option>
                              <option value="scrap">تسجيل كهالك نهائي</option>
                            </Select>
                          ) : (
                            <span className="text-xs text-erp-muted">هالك نهائي (أصغر من الحد الأدنى)</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div>
                <button className="btn btn-primary" onClick={commitSheetWaste} disabled={committing || committedSheetResult === result}>
                  {committing ? 'جارٍ التحديث...' : committedSheetResult === result ? '✓ تم تحديث المخزون لهذا المخطط' : 'تأكيد وتحديث المخزون'}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── تقطيع الحديد/القضبان (1D) ────────────────────────────────── */}
      <div className="card flex flex-col gap-3">
        <div className="font-bold text-base">🔩 تقطيع الحديد والقضبان</div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Select
            label="المادة (حديد/قضيب) — اختياري"
            value={barMaterialId}
            onChange={e => onBarMaterialChange(e.target.value)}
          >
            <option value="">— بدون / تحديد يدوي —</option>
            {bars.map(m => (
              <option key={m.id} value={m.id}>{m.code} — {m.name_ar}</option>
            ))}
          </Select>
          <Input label="طول القضيب (سم)" type="number" min="0" value={barLength} onChange={e => setBarLength(e.target.value)} disabled={useBarStock} />
          <Input label="سُمك القص - kerf (مم)" type="number" min="0" value={barKerf} onChange={e => setBarKerf(e.target.value)} />
        </div>

        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={useBarStock}
            onChange={e => setUseBarStock(e.target.checked)}
            disabled={!barMaterialId}
          />
          <span className="text-sm">استخدام المخزون المتاح (أطوال متعددة + نسالة) — يتطلب تحديد المادة</span>
        </label>

        {barMaterialId && (
          <div className="flex flex-col gap-2 border border-erp-border rounded-md p-3">
            <div className="font-bold text-sm">مخزون الأسياخ المتاحة لهذه المادة</div>
            {loadingBarStock ? (
              <div className="text-sm text-erp-muted">جارٍ التحميل...</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="erp-table">
                  <thead>
                    <tr>
                      <th>الطول (سم)</th><th>العدد</th><th>المصدر</th><th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {barStockItems.map(item => (
                      <tr key={item.id}>
                        <td>{item.length_cm}</td>
                        <td>{item.qty}</td>
                        <td>{STOCK_SOURCE_AR[item.source] || 'سيخ جديد'}</td>
                        <td>
                          <button className="btn btn-sm btn-secondary" onClick={() => removeBarStockItem(item.id)}>حذف</button>
                        </td>
                      </tr>
                    ))}
                    {!barStockItems.length && (
                      <tr><td colSpan={4} className="text-center text-erp-muted">لا توجد أطوال مسجّلة</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
              <Input label="الطول (سم)" type="number" value={newBarStock.length_cm} onChange={e => setNewBarStock(s => ({ ...s, length_cm: e.target.value }))} />
              <Input label="العدد" type="number" min="1" value={newBarStock.qty} onChange={e => setNewBarStock(s => ({ ...s, qty: e.target.value }))} />
              <Select label="المصدر" value={newBarStock.source} onChange={e => setNewBarStock(s => ({ ...s, source: e.target.value }))}>
                <option value="purchased">سيخ جديد</option>
                <option value="remnant">نسالة</option>
              </Select>
              <button className="btn btn-sm btn-primary" onClick={addBarStockItem} disabled={stockBusy}>{stockBusy ? '…' : '+ إضافة مقاس'}</button>
            </div>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <div className="font-bold text-sm">القطع المطلوبة</div>
          <div className="overflow-x-auto">
            <table className="erp-table">
              <thead>
                <tr>
                  <th>الكود/الاسم</th><th>الطول (سم)</th><th>العدد</th><th></th>
                </tr>
              </thead>
              <tbody>
                {barPieces.map(p => (
                  <tr key={p.id}>
                    <td><input className="field-input" value={p.label} onChange={e => updateBarPiece(p.id, 'label', e.target.value)} /></td>
                    <td><input className="field-input" type="number" value={p.length} onChange={e => updateBarPiece(p.id, 'length', e.target.value)} /></td>
                    <td><input className="field-input w-20" type="number" min="1" value={p.qty} onChange={e => updateBarPiece(p.id, 'qty', e.target.value)} /></td>
                    <td>
                      <button className="btn btn-sm btn-secondary" onClick={() => removeBarPiece(p.id)} disabled={barPieces.length === 1}>حذف</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <button className="btn btn-sm btn-secondary" onClick={addBarPiece}>+ إضافة قطعة</button>
          </div>
        </div>

        <div>
          <button className="btn btn-primary" onClick={runBar} disabled={computingBar}>
            {computingBar ? 'جارٍ الحساب...' : 'حساب مخطط تقطيع الحديد'}
          </button>
        </div>
      </div>

      {barResult && (
        <div className="flex flex-col gap-4">
          <div className="card flex items-center gap-6 flex-wrap">
            <div>
              <div className="text-xs text-erp-muted">عدد القضبان المطلوبة</div>
              <div className="text-2xl font-bold" style={{ color: 'var(--teal)' }}>{barResult.total_bars}</div>
            </div>
            <div>
              <div className="text-xs text-erp-muted">إجمالي القطع</div>
              <div className="text-2xl font-bold">{barResult.total_pieces}</div>
            </div>
            <div>
              <div className="text-xs text-erp-muted">نسبة الاستفادة الكلية</div>
              <div className="text-2xl font-bold">{barResult.overall_efficiency_pct}%</div>
            </div>
            {barResult.bar_length != null && (
              <div>
                <div className="text-xs text-erp-muted">طول القضيب</div>
                <div className="text-lg font-bold">{barResult.bar_length} سم</div>
              </div>
            )}
          </div>

          {barResult.shortage && barResult.shortage.length > 0 && (
            <div className="card" style={{ borderColor: '#f87171' }}>
              <div className="font-bold text-sm" style={{ color: '#dc2626' }}>⚠️ نواقص — لا يكفي المخزون المتاح لتغطية:</div>
              <ul className="text-sm mt-1">
                {barResult.shortage.map((s, i) => (
                  <li key={i}>{s.label}: {s.qty} قطعة</li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex flex-col gap-3">
            {(barResult.bars || []).map(bar => (
              <div key={bar.index} className="card flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <div className="font-bold">
                    قضيب #{bar.index}
                    {bar.source && (
                      <span className="text-xs font-normal text-erp-muted">
                        {' '}— {STOCK_SOURCE_AR[bar.source] || 'سيخ جديد'} ({bar.bar_length})
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-erp-muted">
                    استفادة {bar.efficiency_pct}% — هالك {bar.waste_length} سم
                  </div>
                </div>
                <BarSVG bar={bar} />
              </div>
            ))}
          </div>

          {barMaterialId && barWasteItems.length > 0 && (
            <div className="card flex flex-col gap-2">
              <div className="font-bold text-sm">♻️ الهالك المتبقي — ماذا تفعل به؟</div>
              <div className="text-xs text-erp-muted">
                القطع الأقصر من {minRemnantLengthCm} سم تُصنَّف تلقائيًا كهالك نهائي. القطع الأطول يمكن حفظها كنسالة لاستخدامها في عمليات تقطيع لاحقة.
              </div>
              <div className="overflow-x-auto">
                <table className="erp-table">
                  <thead>
                    <tr>
                      <th>الطول (سم)</th><th>القرار</th>
                    </tr>
                  </thead>
                  <tbody>
                    {barWasteItems.map(it => (
                      <tr key={it.key}>
                        <td>{it.length_cm} سم</td>
                        <td>
                          {it.eligible ? (
                            <Select
                              value={wasteDecisions[it.key] || 'remnant'}
                              onChange={e => setWasteDecisions(d => ({ ...d, [it.key]: e.target.value }))}
                            >
                              <option value="remnant">حفظ كنسالة</option>
                              <option value="scrap">تسجيل كهالك نهائي</option>
                            </Select>
                          ) : (
                            <span className="text-xs text-erp-muted">هالك نهائي (أصغر من الحد الأدنى)</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div>
                <button className="btn btn-primary" onClick={commitBarWaste} disabled={committing || committedBarResult === barResult}>
                  {committing ? 'جارٍ التحديث...' : committedBarResult === barResult ? '✓ تم تحديث المخزون لهذا المخطط' : 'تأكيد وتحديث المخزون'}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function BarSVG({ bar }) {
  const barLength = bar.bar_length
  let x = 0
  return (
    <svg viewBox={`0 0 ${barLength} 20`} style={{ width: '100%', height: '60px', background: '#f8fafc', border: '1px solid var(--erp-border)' }}>
      <rect x="0" y="0" width={barLength} height="20" fill="none" stroke="#94a3b8" strokeWidth="0.4" />
      {(bar.cuts || []).map((c, i) => {
        const cx = x + c.length / 2
        const el = (
          <g key={i}>
            <rect
              x={x} y="0" width={c.length} height="20"
              fill={COLORS[i % COLORS.length]} opacity="0.55"
              stroke="#334155" strokeWidth="0.3"
            />
            <text x={cx} y="8" textAnchor="middle" dominantBaseline="middle" fontSize={Math.min(c.length / 5, 3)} fill="#1e293b">
              {c.label}
            </text>
            <text x={cx} y="14" textAnchor="middle" dominantBaseline="middle" fontSize={Math.min(c.length / 6, 2.5)} fill="#475569">
              {c.length}
            </text>
          </g>
        )
        x += c.length
        return el
      })}
      {bar.waste_length > 0 && (
        <g>
          <rect
            x={x} y="0" width={bar.waste_length} height="20"
            fill="#f1f5f9" opacity="0.9"
            stroke="#cbd5e1" strokeWidth="0.3" strokeDasharray="1.5,1"
          />
          <text x={x + bar.waste_length / 2} y="11" textAnchor="middle" dominantBaseline="middle" fontSize={Math.min(bar.waste_length / 6, 3)} fill="#94a3b8">
            هالك {bar.waste_length}
          </text>
        </g>
      )}
    </svg>
  )
}

function SheetSVG({ sheet, sheetW, sheetH }) {
  return (
    <svg viewBox={`0 0 ${sheetW} ${sheetH}`} style={{ width: '100%', background: '#f8fafc', border: '1px solid var(--erp-border)' }}>
      <rect x="0" y="0" width={sheetW} height={sheetH} fill="none" stroke="#94a3b8" strokeWidth="0.4" />
      {(sheet.waste_rects || []).map((r, i) => {
        const cx = r.x + r.width / 2
        const cy = r.y + r.height / 2
        const minSide = Math.min(r.width, r.height)
        if (minSide >= 15) {
          return (
            <g key={`w${i}`}>
              <rect
                x={r.x} y={r.y} width={r.width} height={r.height}
                fill="#f1f5f9" opacity="0.9"
                stroke="#cbd5e1" strokeWidth="0.3" strokeDasharray="1.5,1"
              />
              <text
                x={cx} y={cy}
                textAnchor="middle" dominantBaseline="middle"
                fontSize={minSide / 5}
                fill="#94a3b8"
              >
                هالك
              </text>
              <text
                x={cx} y={cy + minSide / 4.5}
                textAnchor="middle" dominantBaseline="middle"
                fontSize={minSide / 7}
                fill="#94a3b8"
              >
                {r.width}×{r.height}
              </text>
            </g>
          )
        }
        // شريط هالك ضيق: سطر واحد بالمقاس فقط، بخط يتجه مع الضلع الطويل
        const fontSize = Math.max(minSide * 0.7, 1.5)
        const rotate = r.width < r.height
        return (
          <g key={`w${i}`}>
            <rect
              x={r.x} y={r.y} width={r.width} height={r.height}
              fill="#f1f5f9" opacity="0.9"
              stroke="#cbd5e1" strokeWidth="0.3" strokeDasharray="1.5,1"
            />
            <text
              x={cx} y={cy}
              textAnchor="middle" dominantBaseline="middle"
              fontSize={fontSize}
              fill="#94a3b8"
              transform={rotate ? `rotate(-90 ${cx} ${cy})` : undefined}
            >
              {r.width}×{r.height}
            </text>
          </g>
        )
      })}
      {(sheet.placements || []).map((p, i) => (
        <g key={i}>
          <rect
            x={p.x} y={p.y} width={p.width} height={p.height}
            fill={COLORS[i % COLORS.length]} opacity="0.55"
            stroke="#334155" strokeWidth="0.3"
          />
          <text
            x={p.x + p.width / 2} y={p.y + p.height / 2}
            textAnchor="middle" dominantBaseline="middle"
            fontSize={Math.min(p.width, p.height) / 5}
            fill="#1e293b"
          >
            {p.label}{p.rotated ? ' ↻' : ''}
          </text>
          <text
            x={p.x + p.width / 2} y={p.y + p.height / 2 + Math.min(p.width, p.height) / 4.5}
            textAnchor="middle" dominantBaseline="middle"
            fontSize={Math.min(p.width, p.height) / 7}
            fill="#475569"
          >
            {p.width}×{p.height}
          </text>
        </g>
      ))}
    </svg>
  )
}
