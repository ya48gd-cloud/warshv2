import CatalogPicker from '../components/ui/CatalogPicker'
/**
 * Materials — المواد والخامات
 * تبويبات: الألواح | الحديد/السيخ | عام | مستهلكات
 */
import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp } from '../utils/fmt'
import SearchInput from '../components/ui/SearchInput'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, FormGrid } from '../components/ui/Field'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'
import StockTransferModal from '../components/ui/StockTransferModal'

const barWord = (name) => (name || '').trim().split(/\s+/)[0] || 'سيخ'

const UNITS = ['كجم', 'طن', 'متر', 'م²', 'م³', 'عدد', 'لتر', 'مجموعة']
const MATERIAL_KINDS = [
  { value: 'general',    label: 'عام' },
  { value: 'plate',      label: 'ألواح' },
  { value: 'bar',        label: 'حديد / سيخ' },
  { value: 'consumable', label: 'مستهلكات' },
]
const TABS = [
  { key: 'all',        label: 'الكل' },
  { key: 'plate',      label: '🔩 الألواح' },
  { key: 'bar',        label: '📏 الحديد' },
  { key: 'general',    label: '📦 عام' },
  { key: 'consumable', label: '🧴 مستهلكات' },
]

// أصناف النظام اللي بتتولّد من الإنتاج (تحت التشغيل / تام / هالك / نسالة) —
// مكانها الطبيعي مخزنها، مش وسط قايمة الخامات
const isProductionItem = (m) => /^(WIP|FG|SCRAP|REMNANT)-/.test(m?.code || '')

export default function Materials({ warehouseId = '', warehouseSummary = [], onStockChanged, onMovement } = {}) {
  const { canWrite } = useAuth()
  const toast = useToast()
  const write = canWrite('inventory')

  const { data: materials, loading, reload } = useApi(() => api.inventory.materials())
  const { data: categories, reload: reloadCats } = useApi(() => api.inventory.categories().catch(bgErr('فئات الأصناف', [])))
  const { data: suppliers } = useApi(() => api.purchases.suppliers().catch(bgErr('قائمة الموردين', [])))
  // توزيع رصيد كل مادة على المخازن (StockQuant) + المخازن نفسها لتحويل الرصيد بينها
  const { data: whDist, reload: reloadDist } = useApi(() => api.inventory.warehouseDistribution().catch(bgErr('توزيع المخازن', {})))
  const { data: warehouses } = useApi(() => api.warehouses.list().catch(bgErr('المخازن', [])))
  const [transferMat, setTransferMat] = useState(null) // null = مقفول، '' = بدون مادة محددة
  const [showProd, setShowProd] = useState(false)   // "كل المخازن": إظهار أصناف الإنتاج
  const [showZero, setShowZero] = useState(false)   // مخزن محدد: إظهار الأصناف اللي رصيدها صفر فيه
  const selWh = useMemo(
    () => (warehouseSummary || []).find(w => String(w.id) === String(warehouseId)) || null,
    [warehouseSummary, warehouseId])
  const whQtyOf = (m) => (whDist?.[String(m.id)] || [])
    .filter(d => String(d.warehouse_id) === String(warehouseId))
    .reduce((s, d) => s + Number(d.qty || 0), 0)
  const afterStockChange = () => { reload(); reloadDist(); onStockChanged?.() }

  // القايمة الأساسية حسب المخزن المختار — كل الفلاتر التانية بتشتغل فوقها
  const baseList = useMemo(() => {
    if (!materials) return []
    if (warehouseId) {
      return materials.filter(m => Math.abs(whQtyOf(m)) > 1e-9 || (showZero && !isProductionItem(m)))
    }
    return showProd ? materials : materials.filter(m => !isProductionItem(m))
  }, [materials, whDist, warehouseId, showProd, showZero]) // eslint-disable-line react-hooks/exhaustive-deps
  const prodCount = useMemo(() => (materials || []).filter(isProductionItem).length, [materials])

  const [q, setQ]               = useState('')
  const [filterLow, setFilterLow] = useState(false)
  const [filterCat, setFilterCat] = useState('')
  const [activeTab, setActiveTab] = useState('all')
  const [modal, setModal]         = useState(null)
  const [catModal, setCatModal]   = useState(false)
  const [showCatalog, setShowCatalog] = useState(false)
  const [newCatName, setNewCatName] = useState('')
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [form, setForm]           = useState({})
  const [formErrors, setFormErrors] = useState({})
  const [previewLen, setPreviewLen] = useState('')
  const [previewWid, setPreviewWid] = useState('')

  const filtered = useMemo(() => {
    if (!materials) return []
    const lq = q.toLowerCase()
    let list = baseList
    if (activeTab !== 'all') {
      list = list.filter(m => {
        const kind = m.material_kind || (m.is_plate ? 'plate' : 'general')
        return kind === activeTab
      })
    }
    if (filterLow) list = list.filter(m => Number(m.stock_qty) <= Number(m.reorder_level))
    if (filterCat) list = list.filter(m => Number(m.category_id) === Number(filterCat))
    if (lq) list = list.filter(m =>
      m.name_ar?.toLowerCase().includes(lq) ||
      m.name_en?.toLowerCase().includes(lq) ||
      m.code?.toLowerCase().includes(lq)
    )
    return list
  }, [materials, baseList, q, filterLow, filterCat, activeTab])

  const lowCount = useMemo(
    () => baseList.filter(m => Number(m.stock_qty) <= Number(m.reorder_level)).length,
    [baseList]
  )

  const formDensity = useMemo(() => {
    if (form.material_kind !== 'plate') return null
    const { plate_length_cm: l, plate_width_cm: w, plate_weight_kg: wt } = form
    if (!l || !w || !wt || Number(wt) <= 0) return form.density_kg_m2 ? Number(form.density_kg_m2) : null
    const area = (Number(l) / 100) * (Number(w) / 100)
    return area > 0 ? (Number(wt) / area) : null
  }, [form.material_kind, form.plate_length_cm, form.plate_width_cm, form.plate_weight_kg, form.density_kg_m2])

  const formWPM = useMemo(() => {
    if (form.material_kind !== 'bar') return null
    const { bar_length_cm: l, bar_weight_kg: wt } = form
    if (!l || !wt || Number(l) <= 0) return form.weight_per_meter_kg ? Number(form.weight_per_meter_kg) : null
    return Number(wt) / (Number(l) / 100)
  }, [form.material_kind, form.bar_length_cm, form.bar_weight_kg, form.weight_per_meter_kg])

  const getCatName = (m) => {
    const cat = categories?.find(c => c.id === m.category_id)
    return cat?.name_ar || m.category?.name_ar || '—'
  }

  const openAdd = async () => {
    setForm({ unit: 'كجم', reorder_level: 0, material_kind: 'general', is_plate: false })
    setFormErrors({}); setPreviewLen(''); setPreviewWid('')
    setModal({ mode: 'add' })
    try {
      const res = await api.inventory.nextCode()
      setForm(prev => ({ ...prev, code: res.code }))
    } catch (_) {}
  }
  const openEdit = (item) => {
    setForm({ ...item })
    setFormErrors({}); setPreviewLen(''); setPreviewWid('')
    setModal({ mode: 'edit', item })
  }
  const f = (field, val) => setForm(prev => ({ ...prev, [field]: val }))

  const [save, saving] = useAction(async () => {
    const errs = {}
    if (!form.name_ar?.trim()) errs.name_ar     = 'الاسم بالعربي مطلوب'
    if (!form.category_id)     errs.category_id  = 'اختر الفئة'
    if (!form.unit_cost || Number(form.unit_cost) <= 0) errs.unit_cost = 'سعر الوحدة مطلوب'
    // نفس قواعد الباك-إند (MaterialCreate validators) عشان المستخدم يشوف الخطأ قبل الإرسال
    if (form.name_ar && form.name_ar.trim().length > 200) errs.name_ar = 'اسم المادة طويل جداً (الحد الأقصى 200 حرف)'
    if (form.code && String(form.code).length > 50) { toast('كود المادة طويل جداً (الحد الأقصى 50 حرف)', 'error'); return }
    if (form.reorder_level !== '' && form.reorder_level != null && Number(form.reorder_level) < 0) {
      toast('حد إعادة الطلب لا يمكن أن يكون سالباً', 'error'); return
    }
    const dimFields = ['thickness_mm', 'plate_length_cm', 'plate_width_cm', 'plate_weight_kg', 'bar_length_cm', 'bar_weight_kg']
    if (dimFields.some(k => form[k] !== '' && form[k] != null && Number(form[k]) < 0)) {
      toast('الأبعاد والأوزان لا يمكن أن تكون سالبة', 'error'); return
    }
    if (Object.keys(errs).length) { setFormErrors(errs); toast('تحقق من الحقول المطلوبة', 'error'); return }
    setFormErrors({})
    const payload = {
      code: form.code, name_ar: form.name_ar, name_en: form.name_en || form.name_ar,
      unit: form.unit || 'كجم', unit_cost: Number(form.unit_cost || 0),
      reorder_level: Number(form.reorder_level || 0), category_id: Number(form.category_id),
      supplier_id: form.supplier_id ? Number(form.supplier_id) : null,
      material_kind: form.material_kind || (form.is_plate ? 'plate' : 'general'),
      is_plate: form.material_kind === 'plate' || !!form.is_plate,
      thickness_mm:    form.material_kind === 'plate' && form.thickness_mm    ? Number(form.thickness_mm)    : null,
      plate_length_cm: form.material_kind === 'plate' && form.plate_length_cm ? Number(form.plate_length_cm) : null,
      plate_width_cm:  form.material_kind === 'plate' && form.plate_width_cm  ? Number(form.plate_width_cm)  : null,
      plate_weight_kg: form.material_kind === 'plate' && form.plate_weight_kg ? Number(form.plate_weight_kg) : null,
      bar_length_cm:   form.material_kind === 'bar'   && form.bar_length_cm   ? Number(form.bar_length_cm)   : null,
      bar_weight_kg:   form.material_kind === 'bar'   && form.bar_weight_kg   ? Number(form.bar_weight_kg)   : null,
      // محطة 9: سياسات الصنف (الحقل الوحيد اللي له أثر فعلي حاليًا — راجع الفورم)
      valuation_method: form.valuation_method || null,
    }
    if (modal?.mode === 'edit') await api.inventory.updateMat(form.id, payload)
    else await api.inventory.createMat(payload)
    toast(modal?.mode === 'edit' ? 'تم التحديث' : 'تمت الإضافة')
    setModal(null); reload()
  })

  const [doDelete] = useAction(async () => {
    await api.inventory.deleteMat(deleteTarget.id)
    toast('تم الحذف'); setDeleteTarget(null); reload(); reloadCats()
  })

  // حذف فئة — كان بيتنفّذ فورًا من غير أي تأكيد
  const [catDeleteTarget, setCatDeleteTarget] = useState(null)
  const [deletingCat, setDeletingCat] = useState(false)
  const confirmDeleteCat = async (c) => {
    if (!c || deletingCat) return
    setDeletingCat(true)
    try {
      await api.inventory.deleteCat(c.id)
      toast('تم حذف الصنف'); await reloadCats()
    } catch (err) {
      toast(err?.message || 'تعذّر حذف الصنف', 'error')
    } finally {
      setDeletingCat(false)
    }
  }

  const [addCategory, addingCat] = useAction(async () => {
    const name = newCatName.trim()
    if (!name) { toast('اكتب اسم الصنف', 'error'); return }
    const created = await api.inventory.createCat({ name_ar: name, name_en: name })
    toast('تمت إضافة الصنف')
    setNewCatName(''); setCatModal(false)
    await reloadCats()
    // اختر الصنف الجديد تلقائياً في الفورم لو المودال مفتوح
    if (created?.id && modal) f('category_id', created.id)
  })

  if (loading) return <PageSpinner />

  return (
    <div className="flex flex-col gap-4">
      {/* سياق المخزن المختار: ملخصه + عمليات عليه مباشرة */}
      {selWh && (
        <div className="card flex items-center gap-3 py-3 flex-wrap" style={{ borderInlineStart: '4px solid var(--teal)' }}>
          <div className="flex-1 min-w-48">
            <div className="font-semibold">{selWh.name_ar}</div>
            <div className="text-xs text-erp-muted">
              {Number(selWh.items_count || 0)} صنف فيه رصيد · قيمة المخزون <b className="font-mono">{egp(selWh.total_value)}</b>
            </div>
          </div>
          {write && onMovement && <button className="btn btn-sm" onClick={() => onMovement({ type: 'in' })}>📥 وارد لهذا المخزن</button>}
          {write && onMovement && <button className="btn btn-sm" onClick={() => onMovement({ type: 'out' })}>📤 صرف من هذا المخزن</button>}
          {write && <button className="btn btn-sm" onClick={() => setTransferMat('')}>⇄ تحويل من هذا المخزن</button>}
          <label className="text-xs flex items-center gap-1 cursor-pointer">
            <input type="checkbox" checked={showZero} onChange={e => setShowZero(e.target.checked)} />
            إظهار الأصناف غير الموجودة فيه
          </label>
        </div>
      )}

      {/* Toolbar */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div className="flex-1 min-w-48">
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالكود أو الاسم…" />
        </div>
        <button className="btn btn-sm"
          style={filterLow ? { background: 'var(--red-lt)', color: 'var(--red)', borderColor: 'var(--red-md)' } : {}}
          onClick={() => setFilterLow(f => !f)}>⚠ منخفض ({lowCount})</button>
        <select className="field-select" style={{ maxWidth: 170, height: 32, padding: '0 8px', fontSize: 13 }}
          value={filterCat} onChange={e => setFilterCat(e.target.value)}>
          <option value="">كل الأصناف</option>
          {(categories || []).map(c => (
            <option key={c.id} value={c.id}>{c.name_ar}{c.material_count != null ? ` (${c.material_count})` : ''}</option>
          ))}
        </select>
        <div className="text-xs text-erp-muted">{filtered.length} مادة</div>
        {!warehouseId && prodCount > 0 && (
          <label className="text-xs flex items-center gap-1 cursor-pointer" title="أصناف بيولّدها النظام من أوامر الإنتاج: تحت التشغيل، تام الصنع، هالك، نسالة">
            <input type="checkbox" checked={showProd} onChange={e => setShowProd(e.target.checked)} />
            أصناف الإنتاج ({prodCount})
          </label>
        )}
        <button className="btn btn-sm text-xs" title="تصدير Excel"
          onClick={() => api.export.materials()}>📥 Excel</button>
        {write && <button className="btn btn-sm" onClick={() => setCatModal(true)}>+ صنف</button>}
        {write && <button className="btn btn-sm" style={{background:'var(--teal)',color:'#fff',borderColor:'var(--teal)'}} onClick={() => setShowCatalog(true)}>📦 من الكتالوج</button>}
        {write && !selWh && <button className="btn btn-sm" onClick={() => setTransferMat('')}>⇄ تحويل بين المخازن</button>}
        {write && <button className="btn btn-primary btn-sm" onClick={openAdd}>+ مادة جديدة</button>}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 flex-wrap">
        {TABS.map(tab => {
          const count = tab.key === 'all'
            ? baseList.length
            : baseList.filter(m => (m.material_kind || (m.is_plate ? 'plate' : 'general')) === tab.key).length
          return (
            <button key={tab.key} className="btn btn-sm"
              style={activeTab === tab.key ? { background: 'var(--blue)', color: '#fff', borderColor: 'var(--blue)' } : {}}
              onClick={() => setActiveTab(tab.key)}>
              {tab.label} <span className="mr-1 text-[10px] opacity-75">({count})</span>
            </button>
          )
        })}
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr>
                <th>الكود</th><th>الاسم</th><th>الفئة</th><th>النوع</th>
                <th>{selWh ? `الرصيد في ${selWh.name_ar}` : 'مستوى المخزون'}</th>
                <th>{selWh ? 'محجوز (كل المخازن)' : 'محجوز'}</th><th>{selWh ? 'متاح (كل المخازن)' : 'متاح'}</th>
                <th>سعر الوحدة</th><th>{selWh ? 'القيمة في المخزن' : 'القيمة الإجمالية'}</th>
                <th>مواصفات المادة</th>
                {write && <th style={{ width: 120 }}>إجراءات</th>}
              </tr>
            </thead>
            <tbody>
              {!filtered.length && (
                <tr><td colSpan={write ? 11 : 10} className="text-center text-erp-muted py-10">
                  {q || filterLow || filterCat || activeTab !== 'all' ? 'لا توجد نتائج'
                    : selWh ? `مفيش أصناف فيها رصيد في ${selWh.name_ar}` : 'لا توجد مواد — أضف أول مادة'}
                </td></tr>
              )}
              {filtered.map(m => {
                const totalStock = Number(m.stock_qty || 0)
                // في مخزن محدد: الرقم الأساسي والقيمة = رصيد المخزن ده؛ حد الطلب بيفضل على الإجمالي
                const stock    = warehouseId ? whQtyOf(m) : totalStock
                const reorder  = Number(m.reorder_level || 0)
                const low      = reorder > 0 && totalStock <= reorder
                const empty    = warehouseId ? stock <= 0 : totalStock <= 0
                const pct      = reorder > 0 ? Math.min(100, (stock / reorder) * 100) : 100
                const barColor = empty ? '#ef4444' : low ? '#f59e0b' : '#22c55e'
                const totalVal = stock * Number(m.unit_cost || 0)
                const kind     = m.material_kind || (m.is_plate ? 'plate' : 'general')
                return (
                  <tr key={m.id} style={{ background: empty ? 'var(--red-lt)' : low ? 'var(--amber-lt)' : undefined }}>
                    <td>
                      <span className="font-mono text-xs bg-erp px-1.5 py-0.5 rounded">{m.code}</span>
                    </td>
                    <td>
                      <div className="font-medium">{m.name_ar}</div>
                      {m.name_en && m.name_en !== m.name_ar && <div className="text-[11px] text-erp-muted">{m.name_en}</div>}
                      <div className="text-[10px] text-erp-muted">{getCatName(m)} · {m.unit}</div>
                    </td>
                    <td className="text-erp-muted text-xs">{getCatName(m)}</td>
                    <td className="text-xs">
                      <span className={`badge text-[10px] ${
                        kind==='plate' ? 'badge-blue' : kind==='bar' ? 'badge-amber' :
                        kind==='consumable' ? 'badge-red' : 'badge-green'}`}>
                        {MATERIAL_KINDS.find(k=>k.value===kind)?.label||'عام'}
                      </span>
                    </td>
                    <td style={{ minWidth: 160 }}>
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-semibold text-xs" style={{ color: barColor }}>
                          {stock.toFixed(2)} {m.unit}
                        </span>
                        {empty
                          ? <span className="badge badge-red text-[9px]">نفذ</span>
                          : low
                          ? <span className="badge badge-amber text-[9px]">منخفض</span>
                          : <span className="text-[9px] text-erp-muted">حد: {reorder.toFixed(1)}</span>
                        }
                      </div>
                      {/* عرض عدد القطع للألواح والأسياخ — وزن القطعة مباشرة، أو محسوب من الكثافة×الأبعاد كحل بديل */}
                      {kind === 'plate' && stock > 0 && (() => {
                        const pw = Number(m.plate_weight_kg) ||
                          (m.density_kg_m2 && m.plate_length_cm && m.plate_width_cm
                            ? (Number(m.plate_length_cm) / 100) * (Number(m.plate_width_cm) / 100) * Number(m.density_kg_m2)
                            : 0)
                        if (!pw) return null
                        const fullSheets = Math.floor(stock / pw)
                        const remnantKg = +(stock % pw).toFixed(2)
                        return (
                          <div style={{ fontSize: 10, color: 'var(--blue)', marginBottom: 2 }}>
                            {fullSheets > 0 && <span>{fullSheets} لوح كامل</span>}
                            {remnantKg > 0.01 && <span style={{ color: '#9333ea', marginRight: 4 }}>{remnantKg > 0 && fullSheets > 0 ? '+ ' : ''}{remnantKg} كجم نساله</span>}
                          </div>
                        )
                      })()}
                      {kind === 'bar' && stock > 0 && (() => {
                        const bw = Number(m.bar_weight_kg) ||
                          (m.weight_per_meter_kg && m.bar_length_cm
                            ? (Number(m.bar_length_cm) / 100) * Number(m.weight_per_meter_kg)
                            : 0)
                        if (!bw) return null
                        const fullBars = Math.floor(stock / bw)
                        const remnantKg = +(stock % bw).toFixed(2)
                        return (
                          <div style={{ fontSize: 10, color: 'var(--amber)', marginBottom: 2 }}>
                            {fullBars > 0 && <span>{fullBars} {barWord(m.name_ar)} كامل</span>}
                            {remnantKg > 0.01 && <span style={{ color: '#9333ea', marginRight: 4 }}>{fullBars > 0 ? '+ ' : ''}{remnantKg} كجم نساله</span>}
                          </div>
                        )
                      })()}
                      <div style={{ height: 5, background: 'var(--erp-border)', borderRadius: 3, overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${pct}%`, background: barColor, borderRadius: 3, transition: 'width .3s' }} />
                      </div>
                      {warehouseId && Math.abs(totalStock - stock) > 1e-9 && (
                        <div className="text-[10px] text-erp-muted mt-1">إجمالي كل المخازن: <b className="font-mono">{totalStock.toFixed(2)}</b> {m.unit}</div>
                      )}
                      {/* توزيع الرصيد على المخازن */}
                      {!warehouseId && (whDist?.[String(m.id)] || []).filter(d => Number(d.qty)).length > 1 && (
                        <div className="flex flex-wrap gap-1 mt-1" title="الرصيد في كل مخزن">
                          {whDist[String(m.id)].filter(d => Number(d.qty)).map(d => (
                            <span key={d.warehouse_id ?? 'none'} className="text-[10px] px-1.5 py-0.5 rounded"
                              style={{ background: 'var(--bg)', border: '1px solid var(--erp-border)', color: d.qty < 0 ? 'var(--red)' : 'var(--erp-text)' }}>
                              {(d.warehouse_name || '').replace(/^مخزن\s+/, '')}: <b className="font-mono">{Number(d.qty).toFixed(2)}</b>
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="text-sm" style={{ color: 'var(--amber)' }}>
                      {Number(m.reserved_qty || 0) > 0 ? `${Number(m.reserved_qty).toFixed(2)} ${m.unit}` : '—'}
                    </td>
                    <td className="text-sm font-medium" style={{ color: Number(m.available_qty ?? stock) <= 0 ? 'var(--red)' : 'var(--teal)' }}>
                      {Number(m.available_qty ?? stock).toFixed(2)} {m.unit}
                    </td>
                    <td className="text-sm">{egp(m.unit_cost)}</td>
                    <td className="text-sm font-medium" style={{ color: totalVal > 0 ? 'var(--teal)' : 'var(--erp-muted)' }}>
                      {totalVal > 0 ? egp(totalVal) : '—'}
                    </td>
                    <td className="text-xs">
                      {kind === 'plate' && (
                        <div className="flex flex-col gap-0.5">
                          {m.thickness_mm && <span className="badge badge-blue text-[10px]">سماكة: {m.thickness_mm}mm</span>}
                          {m.plate_length_cm && m.plate_width_cm && (
                            <span style={{ color: 'var(--blue)' }}>{m.plate_length_cm}×{m.plate_width_cm} سم</span>
                          )}
                          {m.plate_weight_kg && <span className="text-erp-muted">وزن اللوح: {m.plate_weight_kg} كجم</span>}
                          {m.density_kg_m2 && (
                            <span className="font-semibold" style={{ color: 'var(--teal)' }}>
                              كثافة: {Number(m.density_kg_m2).toFixed(2)} كجم/م²
                            </span>
                          )}
                        </div>
                      )}
                      {kind === 'bar' && (
                        <div className="flex flex-col gap-0.5">
                          {m.bar_length_cm   && <span style={{ color: 'var(--amber)' }}>طول ال{barWord(m.name_ar)}: {m.bar_length_cm} سم</span>}
                          {m.bar_weight_kg   && <span className="text-erp-muted">وزن ال{barWord(m.name_ar)}: {m.bar_weight_kg} كجم</span>}
                          {m.weight_per_meter_kg && (
                            <span className="font-semibold" style={{ color: 'var(--teal)' }}>
                              {Number(m.weight_per_meter_kg).toFixed(4)} كجم/م
                            </span>
                          )}
                        </div>
                      )}
                      {(kind !== 'plate' && kind !== 'bar') && <span className="text-erp-muted">—</span>}
                    </td>
                    {write && (
                      <td><div className="flex gap-1">
                        <button className="btn btn-sm text-xs" onClick={() => openEdit(m)}>✏️</button>
                        {onMovement && <button className="btn btn-sm text-xs" title={selWh ? `وارد إلى ${selWh.name_ar}` : 'وارد'} onClick={() => onMovement({ type: 'in', materialId: String(m.id) })}>📥</button>}
                        {onMovement && <button className="btn btn-sm text-xs" title={selWh ? `صرف من ${selWh.name_ar}` : 'صرف'} onClick={() => onMovement({ type: 'out', materialId: String(m.id) })}>📤</button>}
                        <button className="btn btn-sm text-xs" title="تحويل بين المخازن" onClick={() => setTransferMat(String(m.id))}>⇄</button>
                        <button className="btn btn-sm btn-danger text-xs" onClick={() => setDeleteTarget(m)}>🗑</button>
                      </div></td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {showCatalog && <CatalogPicker onImport={() => reload()} onClose={() => setShowCatalog(false)} />}
      <StockTransferModal
        open={transferMat !== null}
        onClose={() => setTransferMat(null)}
        materials={materials}
        warehouses={warehouses}
        initialMaterialId={transferMat}
        initialFromWarehouseId={warehouseId}
        onDone={afterStockChange}
      />
      {/* Modal */}
      <Modal open={!!modal} onClose={() => setModal(null)}
        title={modal?.mode === 'edit' ? 'تعديل مادة' : 'مادة جديدة'}>
        <div className="flex flex-col gap-3">
          {/* ── المعلومات الأساسية ── */}
          <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider pb-1 border-b border-erp-border">
            المعلومات الأساسية
          </div>
          <FormGrid cols={2}>
            <div>
              {modal?.mode === 'edit'
                ? <Input label="الكود" value={form.code || ''} readOnly
                    style={{ background: 'var(--bg-alt)', cursor: 'not-allowed', opacity: 0.7 }} />
                : <>
                    <Input label="الكود (يُولَّد تلقائياً — قابل للتعديل)" value={form.code || ''}
                      onChange={e => f('code', e.target.value)}
                      placeholder="جارٍ التوليد…" />
                  </>
              }
            </div>
            <div>
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  <Select label="الفئة *" value={form.category_id || ''} onChange={e => { f('category_id', +e.target.value); setFormErrors(x => ({ ...x, category_id: '' })) }}
                    style={formErrors.category_id ? { borderColor: 'var(--red)' } : {}}>
                    <option value="">— اختر فئة —</option>
                    {(categories || []).map(c => <option key={c.id} value={c.id}>{c.name_ar}</option>)}
                  </Select>
                </div>
                <button type="button" className="btn btn-sm" title="إضافة صنف جديد"
                  onClick={() => setCatModal(true)}>+</button>
              </div>
              {formErrors.category_id && <div className="text-[11px] mt-1" style={{ color: 'var(--red)' }}>⚠ {formErrors.category_id}</div>}
            </div>
          </FormGrid>
          <FormGrid cols={2}>
            <div>
              <Input label="الاسم بالعربي *" value={form.name_ar || ''} onChange={e => { f('name_ar', e.target.value); setFormErrors(x => ({ ...x, name_ar: '' })) }}
                style={formErrors.name_ar ? { borderColor: 'var(--red)' } : {}} />
              {formErrors.name_ar && <div className="text-[11px] mt-1" style={{ color: 'var(--red)' }}>⚠ {formErrors.name_ar}</div>}
            </div>
            <Input label="الاسم بالإنجليزي" value={form.name_en || ''} onChange={e => f('name_en', e.target.value)} />
          </FormGrid>
          {/* ── التسعير والكميات ── */}
          <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider pb-1 border-b border-erp-border mt-1">
            التسعير والكميات
          </div>
          <FormGrid cols={3}>
            <Select label="وحدة القياس" value={form.unit || 'كجم'} onChange={e => f('unit', e.target.value)} options={UNITS} />
            <div>
              <Input label="حد إعادة الطلب" type="number" step="0.001" min="0" value={form.reorder_level ?? 0} onChange={e => f('reorder_level', e.target.value)} />
              <div className="text-[10px] text-erp-muted mt-0.5">تنبيه عند الوصول لهذا الرقم</div>
            </div>
            <div>
              <Input label="سعر الوحدة *" type="number" step="0.01" min="0"
                value={form.unit_cost || ''}
                onChange={e => { f('unit_cost', e.target.value); setFormErrors(x => ({ ...x, unit_cost: '' })) }}
                style={formErrors.unit_cost ? { borderColor: 'var(--red)' } : {}}
                placeholder="0.00"
              />
              {formErrors.unit_cost && <div className="text-[11px] mt-1" style={{ color: 'var(--red)' }}>⚠ {formErrors.unit_cost}</div>}
              <div className="text-[10px] text-erp-muted mt-0.5">بالجنيه لكل {form.unit || 'وحدة'}</div>
            </div>
          </FormGrid>
          <Select
            label="المورد المفضل"
            value={form.supplier_id || ''}
            onChange={e => f('supplier_id', e.target.value)}
          >
            <option value="">— بدون مورد مفضل —</option>
            {(suppliers || []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </Select>

          {/* ── نوع الخامة ── */}
          <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider pb-1 border-b border-erp-border mt-1">
            نوع الخامة
          </div>
          <Select label="نوع الخامة" value={form.material_kind || (form.is_plate ? 'plate' : 'general')}
            onChange={e => setForm(prev => ({
              ...prev, material_kind: e.target.value, is_plate: e.target.value === 'plate',
              unit: e.target.value === 'consumable' ? (prev.unit || 'عدد') : (prev.unit || 'كجم'),
            }))}>
            {MATERIAL_KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
          </Select>

          {/* ── محطة 9: سياسات الصنف ──
              ملحوظة: بعد المراجعة، الحقل الوحيد اللي فعلاً بيأثر على أي
              سلوك في النظام هو valuation_method (مُثبت إنه بيغيّر السعر
              فعليًا). شلنا stock_item_type وremoval_strategy وtracking من
              الفورم لأن مفيش أي منطق في النظام حاليًا بيقرأهم أو يتأثر
              بيهم — لسه قيمتهم الافتراضية بتتحفظ في الداتابيز عادي، وهيرجعوا
              للفورم لما يتفعّلوا فعليًا في محطات لاحقة (تتبع الدُفعات الفعلي،
              توجيه المخازن، الجرد). */}
          <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider pb-1 border-b border-erp-border mt-1">
            سياسات الصنف
          </div>
          <Select label="طريقة تقييم التكلفة" value={form.valuation_method || 'moving_average'}
            onChange={e => f('valuation_method', e.target.value)}>
            <option value="moving_average">متوسط مرجّح (موصى به)</option>
            <option value="last_price">آخر سعر استلام (سعر اليوم)</option>
            <option value="standard">تكلفة ثابتة</option>
            <option value="fifo">FIFO</option>
          </Select>
          {form.valuation_method === 'moving_average' && (
            <div className="text-xs text-erp-muted -mt-2">
              💡 السعر هيتحدّث تلقائيًا كمتوسط مرجّح مع كل استلام جديد (بدل ما يستبدل السعر القديم مباشرة).
            </div>
          )}
          {form.valuation_method === 'last_price' && (
            <div className="text-xs text-erp-muted -mt-2">
              💡 السعر هيتحدّث تلقائيًا لآخر سعر استلام مباشرة (من غير أي حساب متوسط) — مناسب لو سعر المورد بيتغيّر باستمرار وعايز تشوف سعر اليوم بالظبط.
            </div>
          )}


          {/* ════ قسم الألواح ════ */}
          {(form.material_kind === 'plate' || form.is_plate) && (
            <div className="border rounded-erp p-3" style={{ borderColor: 'var(--blue)', background: '#F0F7FF' }}>
              <div className="font-semibold text-sm mb-3" style={{ color: 'var(--blue)' }}>🔩 مواصفات اللوح الكامل</div>
              <FormGrid cols={2}>
                <Input label="سماكة اللوح (mm)" type="number" step="0.1" value={form.thickness_mm || ''} onChange={e => f('thickness_mm', e.target.value)} placeholder="6" />
                <Input label="وزن اللوح الكامل (كجم)" type="number" step="0.001" value={form.plate_weight_kg || ''} onChange={e => f('plate_weight_kg', e.target.value)} placeholder="50" />
                <Input label="طول اللوح الكامل (سم)" type="number" step="0.1" value={form.plate_length_cm || ''} onChange={e => f('plate_length_cm', e.target.value)} placeholder="600" />
                <Input label="عرض اللوح الكامل (سم)" type="number" step="0.1" value={form.plate_width_cm || ''} onChange={e => f('plate_width_cm', e.target.value)} placeholder="150" />
              </FormGrid>
              <div className="mt-2 flex items-center gap-2 px-3 py-2 rounded" style={{ background: '#fff' }}>
                <span className="text-xs text-erp-muted">الكثافة المحسوبة:</span>
                <span className="font-bold text-sm" style={{ color: formDensity ? 'var(--teal)' : 'var(--muted)' }}>
                  {formDensity ? `${Number(formDensity).toFixed(4)} كجم/م²` : '— أدخل المقاس والوزن'}
                </span>
              </div>
              {/* معاينة قطعة */}
              <div className="mt-3 pt-3 border-t" style={{ borderColor: '#CBD5E1' }}>
                <div className="text-xs font-semibold mb-2 text-erp-muted">معاينة وزن قطعة (اختياري)</div>
                <FormGrid cols={2}>
                  <Input label="طول القطعة (سم)" type="number" step="0.1" value={previewLen} onChange={e => setPreviewLen(e.target.value)} placeholder="40" />
                  <Input label="عرض القطعة (سم)" type="number" step="0.1" value={previewWid} onChange={e => setPreviewWid(e.target.value)} placeholder="30" />
                </FormGrid>
                {previewLen && previewWid && formDensity && (
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <div className="flex justify-between px-3 py-2 rounded font-semibold text-sm" style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
                      <span>الوزن</span>
                      <span>{((Number(previewLen)/100)*(Number(previewWid)/100)*Number(formDensity)).toFixed(3)} كجم</span>
                    </div>
                    {form.unit_cost && (
                      <div className="flex justify-between px-3 py-2 rounded text-sm" style={{ background: '#fff', color: 'var(--blue)' }}>
                        <span>السعر</span>
                        <span className="font-semibold">
                          {((Number(previewLen)/100)*(Number(previewWid)/100)*Number(formDensity)*Number(form.unit_cost)).toFixed(2)} ج
                        </span>
                      </div>
                    )}
                  </div>
                )}
              </div>
              <p className="text-[11px] text-erp-muted mt-2">تُستخدم هذه البيانات تلقائياً في حركات المخزون والـ BOM.</p>
            </div>
          )}

          {/* ════ قسم الحديد ════ */}
          {form.material_kind === 'bar' && (
            <div className="border rounded-erp p-3" style={{ borderColor: 'var(--amber)', background: '#FFFBF0' }}>
              <div className="font-semibold text-sm mb-3" style={{ color: 'var(--amber)' }}>📏 مواصفات الحديد / السيخ</div>
              <FormGrid cols={2}>
                <Input label="طول السيخ الكامل (سم)" type="number" step="0.1" value={form.bar_length_cm || ''} onChange={e => f('bar_length_cm', e.target.value)} placeholder="600" />
                <Input label="وزن السيخ الكامل (كجم)" type="number" step="0.001" value={form.bar_weight_kg || ''} onChange={e => f('bar_weight_kg', e.target.value)} placeholder="60" />
              </FormGrid>
              <div className="mt-2 flex items-center gap-2 px-3 py-2 rounded" style={{ background: '#fff' }}>
                <span className="text-xs text-erp-muted">وزن المتر المحسوب:</span>
                <span className="font-bold text-sm" style={{ color: formWPM ? 'var(--teal)' : 'var(--muted)' }}>
                  {formWPM ? `${Number(formWPM).toFixed(4)} كجم/م` : '— أدخل الطول والوزن'}
                </span>
              </div>
              {/* معاينة طول */}
              <div className="mt-3 pt-3 border-t" style={{ borderColor: '#F0D58C' }}>
                <div className="text-xs font-semibold mb-2 text-erp-muted">معاينة وزن طول محدد (اختياري)</div>
                <Input label="الطول (سم)" type="number" step="0.1" value={previewLen} onChange={e => setPreviewLen(e.target.value)} placeholder="50" />
                {previewLen && formWPM && (
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <div className="flex justify-between px-3 py-2 rounded font-semibold text-sm" style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
                      <span>الوزن</span><span>{((Number(previewLen)/100)*Number(formWPM)).toFixed(3)} كجم</span>
                    </div>
                    {form.unit_cost && (
                      <div className="flex justify-between px-3 py-2 rounded text-sm" style={{ background: '#fff', color: 'var(--blue)' }}>
                        <span>السعر</span>
                        <span className="font-semibold">{((Number(previewLen)/100)*Number(formWPM)*Number(form.unit_cost)).toFixed(2)} ج</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
              <p className="text-[11px] text-erp-muted mt-2">مثال: سيخ 600 سم وزنه 60 كجم = 10 كجم/م ← سحب 50 سم = 5 كجم.</p>
            </div>
          )}

          {form.material_kind === 'consumable' && (
            <div className="text-xs text-erp-muted px-3 py-2 rounded-erp bg-erp">
              المستهلكات تدخل في التكلفة فقط ولا تُضاف إلى وزن المعدة في الـ BOM.
            </div>
          )}
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>

      {/* مودال إدارة الأصناف */}
      {catModal && (
        <Modal open onClose={() => { setCatModal(false); setNewCatName('') }} title="إدارة الأصناف">
          <div className="flex flex-col gap-4">
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <Input label="اسم الصنف الجديد" value={newCatName}
                  onChange={e => setNewCatName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') addCategory() }}
                  placeholder="مثال: تروس، رمان بلي…" />
              </div>
              <button className="btn btn-primary" disabled={addingCat} onClick={addCategory}>
                {addingCat ? '...' : 'إضافة'}
              </button>
            </div>
            <div className="border-t pt-3" style={{ borderColor: 'var(--border)' }}>
              <div className="text-xs text-erp-muted mb-2">الأصناف الموجودة ({(categories || []).length})</div>
              <div className="flex flex-col gap-1 max-h-72 overflow-y-auto">
                {(categories || []).map(c => (
                  <div key={c.id} className="flex items-center justify-between py-1.5 px-2 rounded"
                    style={{ background: 'var(--surface-2)' }}>
                    <span className="text-sm">{c.name_ar}
                      <span className="text-[11px] text-erp-muted mr-2">({c.material_count ?? 0} مادة)</span>
                    </span>
                    <button className="btn btn-sm" title="حذف الصنف"
                      style={{ color: 'var(--red)' }} disabled={deletingCat}
                      onClick={() => setCatDeleteTarget(c)}>🗑</button>
                  </div>
                ))}
                {!(categories || []).length && (
                  <div className="text-sm text-erp-muted text-center py-4">لا توجد أصناف بعد</div>
                )}
              </div>
            </div>
          </div>
        </Modal>
      )}

      <ConfirmDialog
        open={!!catDeleteTarget}
        onClose={() => setCatDeleteTarget(null)}
        onConfirm={() => confirmDeleteCat(catDeleteTarget)}
        title="حذف فئة"
        message={`حذف الفئة "${catDeleteTarget?.name_ar || ''}"؟`}
        confirmLabel="حذف"
        danger
      />

      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={doDelete}
        confirmLabel="حذف"
        title="حذف مادة"
        message={`حذف "${deleteTarget?.name_ar}"؟ سيُحذف كل تاريخ حركاته.`}
        danger
      />
    </div>
  )
}
