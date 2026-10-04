/**
 * المخزون — صفحة مدمجة (Materials + Movements) متمحورة حول المخزن.
 *
 * شريط المخازن فوق هو نقطة البداية: "كل المخازن" أو مخزن بعينه. المخزن
 * المختار بيتطبق على الجدول (الرصيد والقيمة في المخزن ده بس) وعلى أزرار
 * الوارد/الصرف/التحويل (بتفتح والمخزن متحدد). مع 8 مخازن، الجدول الموحّد
 * القديم كان بيحط كل المخازن في خانة واحدة كـ chips صغيرة ومفيش طريقة تشتغل
 * جوه مخزن واحد.
 */
import { useEffect, useMemo, useState } from 'react'
import api, { bgErr } from '../api/client'
import { useApi } from '../hooks/useApi'
import { egp } from '../utils/fmt'
import Materials from './Materials'
import Movements from './Movements'

const TABS = [
  { key: 'materials', label: '📦 المواد والأرصدة' },
  { key: 'movements', label: '🔄 حركات المخزون' },
]

// أيقونة ولون مميز لكل نوع مخزن — عشان العين تفرّق بينهم بسرعة
const WH_TYPE_STYLE = {
  raw:            { icon: '🧱', color: 'var(--teal)' },
  spare:          { icon: '🔩', color: 'var(--blue)' },
  imported:       { icon: '🚢', color: 'var(--blue)' },
  wip:            { icon: '⚙️', color: 'var(--amber)' },
  finished_goods: { icon: '✅', color: 'var(--green, #16a34a)' },
  remnant:        { icon: '✂️', color: '#9333ea' },
  scrap:          { icon: '🗑', color: 'var(--red)' },
  returns:        { icon: '↩️', color: 'var(--erp-muted)' },
}
const whStyle = (t) => WH_TYPE_STYLE[t] || { icon: '🏬', color: 'var(--teal)' }

const LS_KEY = 'erp_inventory_wh'
const readLS = () => { try { return localStorage.getItem(LS_KEY) || '' } catch { return '' } }
const writeLS = (v) => { try { localStorage.setItem(LS_KEY, v) } catch { /* ignore */ } }

function WarehouseCard({ active, onClick, icon, color, title, count, value, muted }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
      className="text-right rounded-lg px-3 py-2 flex flex-col gap-0.5 transition-colors"
      style={{
        minWidth: 0,
        background: active ? color : 'var(--surface, #fff)',
        color: active ? '#fff' : 'var(--erp-text)',
        border: `1px solid ${active ? color : 'var(--erp-border)'}`,
        opacity: muted && !active ? 0.65 : 1,
      }}>
      <span className="text-sm font-semibold truncate">{icon} {title}</span>
      <span className="text-[11px]" style={{ opacity: 0.85 }}>
        {count} صنف · <span className="font-mono">{egp(value)}</span>
      </span>
    </button>
  )
}

export default function Inventory() {
  const [activeTab, setActiveTab] = useState('materials')
  const [whId, setWhIdState] = useState(readLS)
  const setWhId = (v) => { setWhIdState(v); writeLS(v) }
  // طلب فتح فورم حركة جاهز من شاشة الأرصدة: { type: 'in'|'out', materialId, warehouseId }
  const [movementPreset, setMovementPreset] = useState(null)

  const { data: summary, reload: reloadSummary } = useApi(
    () => api.warehouses.stockSummary().catch(bgErr('ملخص المخازن', [])))

  const warehouses = useMemo(
    () => (Array.isArray(summary) ? summary : []).filter(w => w.is_active !== false),
    [summary])

  // لو المخزن المحفوظ اتعطّل/اتمسح نرجع لـ"كل المخازن"
  useEffect(() => {
    if (whId && summary && !warehouses.some(w => String(w.id) === String(whId))) setWhId('')
  }, [summary]) // eslint-disable-line react-hooks/exhaustive-deps

  const totals = useMemo(() => ({
    count: warehouses.reduce((s, w) => s + Number(w.items_count || 0), 0),
    value: warehouses.reduce((s, w) => s + Number(w.total_value || 0), 0),
  }), [warehouses])

  const openMovement = (preset) => {
    setMovementPreset({ ...preset, warehouseId: preset.warehouseId ?? whId })
    setActiveTab('movements')
  }

  return (
    <div className="flex flex-col gap-4" style={{ minWidth: 0 }}>
      {/* شريط المخازن */}
      <div role="tablist" aria-label="اختيار المخزن"
        style={{ display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))' }}>
        <WarehouseCard active={!whId} onClick={() => setWhId('')}
          icon="🏭" color="var(--erp-text, #1f2937)" title="كل المخازن"
          count={totals.count} value={totals.value} />
        {warehouses.map(w => {
          const st = whStyle(w.warehouse_type)
          return (
            <WarehouseCard key={w.id} active={String(whId) === String(w.id)}
              onClick={() => setWhId(String(w.id))}
              icon={st.icon} color={st.color} title={w.name_ar}
              count={Number(w.items_count || 0)} value={Number(w.total_value || 0)}
              muted={!Number(w.items_count)} />
          )
        })}
      </div>

      <div className="flex gap-1 border-b border-erp-border pb-0">
        {TABS.map(tab => (
          <button
            key={tab.key}
            className="px-4 py-2 text-sm font-medium transition-colors"
            style={activeTab === tab.key
              ? { color: 'var(--teal)', borderBottom: '2px solid var(--teal)' }
              : { color: 'var(--erp-muted)', borderBottom: '2px solid transparent' }}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'materials' && (
        <Materials warehouseId={whId} warehouseSummary={warehouses}
          onStockChanged={reloadSummary} onMovement={openMovement} />
      )}
      {activeTab === 'movements' && (
        <Movements warehouseId={whId} preset={movementPreset}
          onPresetConsumed={() => setMovementPreset(null)} onStockChanged={reloadSummary} />
      )}
    </div>
  )
}
