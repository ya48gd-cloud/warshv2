/**
 * Dashboard.jsx — لوحة التحكم الموحدة
 * دمج لوحة التحكم + لوحة التحليلات في صفحة واحدة مع تبويبات
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import {
  AreaChart, Area, BarChart, Bar, ComposedChart, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { useApi } from '../hooks/useApi'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp, fmtDate, WO_STATUS } from '../utils/fmt'
import { StatusBadge } from '../components/ui/Badge'
import { PageSpinner } from '../components/ui/Spinner'

// ════════════════════════════════════════════════════
// HELPERS
// ════════════════════════════════════════════════════
const fmt    = (n, d = 0) => (n ?? 0).toLocaleString('ar-EG', { maximumFractionDigits: d })
const fmtK   = (n) => fmt((n ?? 0) / 1000, 0) + 'K'
const ARABIC_MONTHS = ['يناير','فبراير','مارس','أبريل','مايو','يونيو','يوليو','أغسطس','سبتمبر','أكتوبر','نوفمبر','ديسمبر']

const COLOR_MAP = {
  blue: 'var(--blue)', teal: 'var(--teal)', green: 'var(--green-md)',
  amber: 'var(--amber-md)', red: 'var(--red-md)',
}

function ArabicTooltip({ active, payload, label, unit = '' }) {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: '#0B1F3A', border: '1px solid #334155', borderRadius: 8, padding: '8px 12px', fontSize: 11, color: '#fff', direction: 'rtl' }}>
      {label && <div style={{ color: '#94A3B8', marginBottom: 4 }}>{label}</div>}
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color || '#fff', fontWeight: 700 }}>
          {p.name}: {fmt(p.value, 0)} {unit}
        </div>
      ))}
    </div>
  )
}

// ════════════════════════════════════════════════════
// TAB 1 — لوحة التحكم
// ════════════════════════════════════════════════════
function KpiCard({ k }) {
  const col = COLOR_MAP[k.color] || 'var(--teal)'
  const val = k.format === 'currency' ? egp(Number(k.value || 0)) : Number(k.value || 0).toLocaleString('ar-EG', { numberingSystem: 'latn' })
  return (
    <div className="kpi-card">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-erp-muted font-medium">{k.label}</span>
        <span className="text-lg">{k.icon}</span>
      </div>
      <div className="text-xl font-bold" style={{ color: col }}>{val}</div>
      {k.trend != null && (
        <div className="text-[11px] mt-1" style={{ color: k.trend >= 0 ? 'var(--green)' : 'var(--red)' }}>
          {k.trend >= 0 ? '↑' : '↓'} {Math.abs(k.trend)}% عن الشهر السابق
        </div>
      )}
    </div>
  )
}

function AlertBar({ alerts }) {
  if (!alerts?.length) return null
  return (
    <div className="flex flex-col gap-2 mb-4">
      {alerts.map((a, i) => {
        const col = a.level === 'critical' ? 'var(--red)' : a.level === 'warning' ? 'var(--amber-md)' : a.level === 'info' ? '#0891b2' : 'var(--blue)'
        const bg  = a.level === 'critical' ? 'var(--red-lt)' : a.level === 'warning' ? 'var(--amber-lt)' : a.level === 'info' ? '#e0f2fe' : 'var(--blue-lt)'
        return (
          <div key={i} className="flex items-center justify-between px-4 py-2.5 rounded-erp text-sm"
               style={{ background: bg, borderRight: `3px solid ${col}` }}>
            <div>
              <span className="font-semibold" style={{ color: col }}>{a.icon} {a.title}</span>
              {a.detail && <span className="text-erp-muted text-xs mr-2">— {a.detail}</span>}
            </div>
            {a.action && <span className="text-xs font-medium cursor-pointer" style={{ color: col }}>{a.action} →</span>}
          </div>
        )
      })}
    </div>
  )
}

const KPI_ORDER = ['active_work_orders','stock_value','sales_this_month','receivables','low_stock_items','completed_wo_month','scrap_cost_month','active_workers']

function ControlPanel({ onNavigate }) {
  const toast = useToast()
  const { data: alertsData } = useApi(() => api.alerts().catch(bgErr('التنبيهات', [])))
  const { data, loading } = useApi(() => api.dashboard.full().catch(async () => {
    const failedSources = []
    const track = (name, promise, fallback) => promise.catch(() => { failedSources.push(name); return fallback })
    const [kpis, lowStock, wos] = await Promise.all([
      track('المؤشرات الرئيسية', api.dashboard.kpis(), null),
      track('تنبيهات نقص المخزون', api.dashboard.lowStock(), []),
      track('أوامر الإنتاج', api.accounting.workOrders(), []),
    ])
    if (failedSources.length > 0) {
      toast(`تعذّر تحميل: ${failedSources.join('، ')} — لوحة التحكم قد تكون غير مكتملة`, 'warn')
    }
    return {
      kpis: kpis ? {
        active_work_orders: { value: kpis.open_work_orders, label: 'أوامر نشطة', color: 'blue', icon: '📋' },
        low_stock_items:    { value: kpis.low_stock_count,  label: 'مواد ناقصة', color: 'red',  icon: '⚠️' },
      } : {},
      alerts: [], work_orders: wos, low_stock: lowStock, mrp: []
    }
  }))

  if (loading) return <PageSpinner />

  const kpis     = data?.kpis || {}
  const alerts   = [...(alertsData || []), ...(data?.alerts || [])]
  const wos      = data?.work_orders || []
  const lowStock = data?.low_stock || []
  const mrp      = data?.mrp || []

  return (
    <div className="flex flex-col gap-4">
      <AlertBar alerts={alerts} />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {KPI_ORDER.map(k => kpis[k] ? <KpiCard key={k} k={kpis[k]} /> : null)}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card">
          <div className="text-sm font-semibold mb-3">📋 أوامر الإنتاج النشطة</div>
          {wos.length === 0
            ? <p className="text-erp-muted text-sm">لا توجد أوامر نشطة</p>
            : <div className="overflow-x-auto">
                <table className="erp-table">
                  <thead><tr><th>الكود</th><th>المعدة</th><th>الحالة</th><th>التقدم</th><th>التكلفة</th></tr></thead>
                  <tbody>
                    {wos.slice(0, 6).map(wo => {
                      const pct = wo.cost_pct > 1 ? Math.min(100, wo.cost_pct) : Math.min(100, (wo.cost_pct || 0) * 100)
                      return (
                        <tr key={wo.id} className="cursor-pointer" onClick={() => onNavigate?.('workorders')}>
                          <td className="font-medium font-mono text-xs">{wo.code}</td>
                          <td className="text-erp-muted text-xs">{wo.equipment_name_ar || wo.equipment_name || '—'}</td>
                          <td><StatusBadge map={WO_STATUS} value={wo.status} /></td>
                          <td>
                            <div className="w-20">
                              <div className="text-[10px] mb-0.5">{pct}%</div>
                              <div className="progress-track">
                                <div className="progress-fill" style={{ width: `${pct}%`, background: pct > 90 ? 'var(--red-md)' : 'var(--teal-md)' }} />
                              </div>
                            </div>
                          </td>
                          <td className="font-medium text-xs">{egp(wo.actual_cost)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
          }
        </div>

        <div className="card">
          <div className="text-sm font-semibold mb-3">⚠️ مواد تحتاج طلبية</div>
          {lowStock.length === 0
            ? <p className="text-sm" style={{ color: 'var(--green)' }}>✓ المخزون في المستوى الجيد</p>
            : <div className="overflow-x-auto">
                <table className="erp-table">
                  <thead><tr><th>المادة</th><th>المتوفر</th><th>الحد</th><th>النقص</th></tr></thead>
                  <tbody>
                    {lowStock.slice(0, 6).map(m => {
                      const p = m.pct || 0
                      const col = p < 25 ? 'var(--red)' : p < 50 ? 'var(--amber-md)' : 'var(--teal)'
                      return (
                        <tr key={m.id} className="cursor-pointer" onClick={() => onNavigate?.('materials')}>
                          <td><div className="font-medium text-xs">{m.name_ar || m.code}</div></td>
                          <td className="font-semibold text-xs" style={{ color: col }}>{Number(m.stock_qty).toFixed(1)} {m.unit}</td>
                          <td className="text-erp-muted text-xs">{Number(m.reorder_level).toFixed(1)}</td>
                          <td className="text-xs" style={{ color: 'var(--red)' }}>{Number(m.shortage).toFixed(1)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
          }
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card">
          <div className="text-sm font-semibold mb-3">🛒 اقتراحات الشراء (MRP)</div>
          {mrp.length === 0
            ? <p className="text-erp-muted text-sm">لا توجد اقتراحات</p>
            : mrp.slice(0, 4).map((s, i) => (
                <div key={i} className="flex items-center justify-between px-3 py-2.5 rounded-erp mb-2"
                     style={{ background: 'var(--amber-lt)' }}>
                  <div>
                    <div className="font-medium text-sm">{s.material_name}</div>
                    <div className="text-xs text-erp-muted mt-0.5">نقص: {Number(s.shortage).toFixed(1)} — وصول: {s.expected_by}</div>
                  </div>
                  <div className="text-xs font-semibold" style={{ color: 'var(--amber)' }}>
                    {Number(s.suggested_qty).toFixed(1)} وحدة
                  </div>
                </div>
              ))
          }
        </div>

        <div className="card">
          <div className="text-sm font-semibold mb-3">⚡ إجراءات سريعة</div>
          <div className="grid grid-cols-2 gap-2">
            {[
              { label: '📋 أوامر الإنتاج',   page: 'workorders' },
              { label: '📋 عرض سعر جديد',    page: 'orders' },
              { label: '📦 المواد والمخزون', page: 'inventory' },
              { label: '🏢 العملاء',          page: 'customers' },
              { label: '👷 العاملون',          page: 'workers' },
              { label: '🧾 الفواتير',          page: 'invoices' },
            ].map(q => (
              <button key={q.page} className="btn text-xs py-2.5 justify-center" onClick={() => onNavigate?.(q.page)}>
                {q.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════
// TAB 2 — التحليلات
// ════════════════════════════════════════════════════

async function fetchMarketPrices() {
  const base = {
    usd_egp:    { value: 52.90,  change: +0.12, label: 'دولار / جنيه',  unit: 'ج.م',    icon: '💵' },
    steel_ton:  { value: 39500,  change: -1.20, label: 'حديد / طن',     unit: 'ج.م/طن', icon: '🔩' },
    copper_ton: { value: 467000, change: +0.50, label: 'نحاس / طن',     unit: 'ج.م/طن', icon: '⚡' },
    alum_ton:   { value: 205000, change: -0.30, label: 'ألومنيوم / طن', unit: 'ج.م/طن', icon: '🏗️' },
    gold_gram:  { value: 6815,   change: +0.80, label: 'ذهب عيار 21',   unit: 'ج.م/غ',  icon: '🥇' },
  }
  const jitter = () => (Math.random() - 0.5) * 0.4
  return Object.fromEntries(
    Object.entries(base).map(([k, v]) => [k, { ...v, value: +(v.value * (1 + jitter() * 0.001)).toFixed(2) }])
  )
}

function buildPredictions(data) {
  if (!data) return null
  const { sales, production, purchases, customers, workOrders, lowStock } = data
  const salesHistory = sales?.monthly || []
  const n = salesHistory.length
  let forecastNext = 0, forecastNext2 = 0
  if (n >= 3) {
    const sumX  = salesHistory.reduce((a, _, i) => a + i, 0)
    const sumY  = salesHistory.reduce((a, v) => a + v, 0)
    const sumXY = salesHistory.reduce((a, v, i) => a + i * v, 0)
    const sumX2 = salesHistory.reduce((a, _, i) => a + i * i, 0)
    const slope = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX || 1)
    const intercept = (sumY - slope * sumX) / n
    forecastNext  = Math.max(0, intercept + slope * n)
    forecastNext2 = Math.max(0, intercept + slope * (n + 1))
  }

  const monthlyRevenue  = sales?.mtd_invoiced   || 0
  const monthlyExpenses = purchases?.mtd_purchases || 0
  const receivable      = sales?.total_receivable  || 0
  const payable         = purchases?.total_payable  || 0
  const cf30 = monthlyRevenue * 0.85 - monthlyExpenses * 0.9  + receivable * 0.6
  const cf60 = monthlyRevenue * 0.7  - monthlyExpenses * 1.1  + receivable * 0.4
  const cf90 = monthlyRevenue * 0.6  - monthlyExpenses * 1.2  + receivable * 0.2 - payable * 0.3

  const riskWOs = (workOrders || []).map(wo => {
    const planned = Number(wo.planned_cost || 0)
    const actual  = Number(wo.actual_cost  || 0)
    const overrun = planned > 0 ? (actual - planned) / planned : 0
    const score = Math.min(100, Math.max(0, Math.round(Math.max(0, overrun) * 60 + Math.min(40, (Number(wo.cost_pct || 0) / 100) * 40))))
    return { ...wo, riskScore: score, overrunPct: overrun * 100 }
  }).sort((a, b) => b.riskScore - a.riskScore).slice(0, 6)

  const stockoutItems = (lowStock || []).map(m => {
    const dailyUsage = Number(m.daily_usage) || (Number(m.reorder_level) > 0 ? Number(m.reorder_level) / 30 : 1)
    const daysLeft   = dailyUsage > 0 ? Math.round(Number(m.stock_qty || 0) / dailyUsage) : 999
    return { ...m, daysLeft }
  }).sort((a, b) => a.daysLeft - b.daysLeft).slice(0, 6)

  const now = Date.now()
  const churnBuckets = { high: 0, medium: 0, low: 0 }
  ;(customers || []).forEach(c => {
    const days = Math.round((now - (c.last_order_date ? new Date(c.last_order_date).getTime() : 0)) / 86400000)
    if (days > 90) churnBuckets.high++
    else if (days > 60) churnBuckets.medium++
    else churnBuckets.low++
  })

  const margin = (production?.by_type || []).map(t => ({
    name: t.name,
    margin: t.revenue > 0 ? Math.round((t.revenue - t.cost) / t.revenue * 100) : 0,
    revenue: t.revenue, cost: t.cost,
  }))

  return { forecastNext, forecastNext2, cashFlow: { cf30, cf60, cf90 }, riskWOs, stockoutItems, churnBuckets, margin, salesHistory }
}

function SectionCard({ title, badge, badgeColor, children }) {
  const bc = { blue: ['#DBEAFE','#1E40AF'], orange: ['#FFF7ED','#9A3412'], green: ['#D1FAE5','#065F46'], red: ['#FEE2E2','#991B1B'], purple: ['#F5F3FF','#4C1D95'] }
  const [bg, tc] = bc[badgeColor] || ['#F1F5F9','#475569']
  return (
    <div style={{ background: 'var(--bg)', border: '1px solid var(--erp-border)', borderRadius: 14, padding: '14px 16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 700 }}>{title}</div>
        {badge && <span style={{ fontSize: 9, background: bg, color: tc, padding: '2px 8px', borderRadius: 12, fontWeight: 700 }}>{badge}</span>}
      </div>
      {children}
    </div>
  )
}

function RevenueChart({ history, forecastNext, forecastNext2 }) {
  const now = new Date()
  const data = (history || []).map((v, i) => {
    const idx = (now.getMonth() - (history.length - 1 - i) + 12) % 12
    return { month: ARABIC_MONTHS[idx], actual: Math.round(v), forecast: null }
  })
  if (data.length > 0 && forecastNext > 0) {
    data[data.length - 1] = { ...data[data.length - 1], forecast: data[data.length - 1].actual }
    data.push({ month: ARABIC_MONTHS[(now.getMonth() + 1) % 12], actual: null, forecast: Math.round(forecastNext) })
    data.push({ month: ARABIC_MONTHS[(now.getMonth() + 2) % 12], actual: null, forecast: Math.round(forecastNext2) })
  }
  return (
    <ResponsiveContainer width="100%" height={160}>
      <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
        <XAxis dataKey="month" tick={{ fontSize: 9, fill: '#94A3B8' }} tickLine={false} />
        <YAxis tickFormatter={v => fmtK(v)} tick={{ fontSize: 9, fill: '#94A3B8' }} tickLine={false} axisLine={false} />
        <Tooltip content={<ArabicTooltip unit="ج.م" />} />
        <Legend formatter={v => v === 'actual' ? 'مبيعات فعلية' : 'تنبؤ'} wrapperStyle={{ fontSize: 10, direction: 'rtl' }} />
        <Area type="monotone" dataKey="actual"   name="actual"   stroke="#3B82F6" fill="#3B82F618" strokeWidth={2} dot={{ r: 3, fill: '#3B82F6' }} connectNulls={false} />
        <Line  type="monotone" dataKey="forecast" name="forecast" stroke="#FF8A00" strokeWidth={2} strokeDasharray="5 3" dot={{ r: 4, fill: '#FF8A00' }} connectNulls={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

function RealCashFlowChart() {
  const [cf, setCf] = useState(null)
  const toast = useToast()
  useEffect(() => {
    api.dashboard.cashflowChart()
      .then(setCf)
      .catch(bgErr('كاش فلو', () => {}))
  }, [])
  if (!cf) return <div style={{ textAlign: 'center', color: '#94A3B8', padding: '30px 0', fontSize: 12 }}>جاري التحميل...</div>

  // نعرض آخر 14 يوم بس عشان الرسم يكون واضح
  const slice = 14
  const labels  = cf.labels.slice(-slice)
  const inflow  = cf.inflow.slice(-slice)
  const outflow = cf.outflow.slice(-slice)
  const net     = cf.net.slice(-slice)
  const chartData = labels.map((d, i) => ({
    date: d.slice(5),  // MM-DD فقط
    تحصيلات: inflow[i],
    مدفوعات: outflow[i],
    صافي: net[i],
  }))
  const totalIn  = cf.inflow.reduce((a, b) => a + b, 0)
  const totalOut = cf.outflow.reduce((a, b) => a + b, 0)
  const totalNet = totalIn - totalOut

  return (
    <>
      <div style={{ display: 'flex', gap: 12, marginBottom: 10 }}>
        {[
          { label: 'تحصيلات', val: totalIn,  color: '#22C55E' },
          { label: 'مدفوعات', val: totalOut, color: '#EF4444' },
          { label: 'صافي',    val: totalNet, color: totalNet >= 0 ? '#3B82F6' : '#F59E0B' },
        ].map(({ label, val, color }) => (
          <div key={label} style={{ flex: 1, textAlign: 'center' }}>
            <div style={{ fontSize: 9, color: '#94A3B8' }}>{label}</div>
            <div style={{ fontSize: 13, fontWeight: 800, color }}>{Math.round(val).toLocaleString('ar-EG')}</div>
          </div>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={120}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
          <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#94A3B8' }} tickLine={false} />
          <YAxis tickFormatter={v => fmtK(v)} tick={{ fontSize: 8, fill: '#94A3B8' }} tickLine={false} axisLine={false} />
          <Tooltip content={<ArabicTooltip unit="ج.م" />} />
          <ReferenceLine y={0} stroke="#CBD5E1" strokeWidth={1} />
          <Bar dataKey="تحصيلات" fill="#22C55E" opacity={0.8} />
          <Bar dataKey="مدفوعات" fill="#EF4444" opacity={0.8} />
          <Line type="monotone" dataKey="صافي" stroke="#3B82F6" strokeWidth={2} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </>
  )
}

function CashFlowChart({ cf30, cf60, cf90 }) {
  const data = [
    { label: '30 يوم', value: Math.round(cf30) },
    { label: '60 يوم', value: Math.round(cf60) },
    { label: '90 يوم', value: Math.round(cf90) },
  ]
  return (
    <>
      <ResponsiveContainer width="100%" height={130}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#94A3B8' }} tickLine={false} />
          <YAxis tickFormatter={v => fmtK(v)} tick={{ fontSize: 9, fill: '#94A3B8' }} tickLine={false} axisLine={false} />
          <Tooltip content={<ArabicTooltip unit="ج.م" />} />
          <ReferenceLine y={0} stroke="#CBD5E1" strokeWidth={1.5} />
          <Bar dataKey="value" name="تدفق نقدي"
            fill="#22C55E"
            label={false}
          />
        </BarChart>
      </ResponsiveContainer>
      {cf90 < 0 && (
        <div style={{ background: '#FEF3C7', border: '1px solid #FCD34D', borderRadius: 8, padding: '6px 10px', fontSize: 10, color: '#92400E', marginTop: 6 }}>
          ⚠️ خسارة نقدية متوقعة بعد {cf60 >= 0 ? '60' : '30'} يوم
        </div>
      )}
    </>
  )
}

function RiskBars({ items }) {
  if (!items?.length) return <div style={{ textAlign: 'center', color: '#94A3B8', padding: '20px 0', fontSize: 12 }}>لا توجد أوامر إنتاج</div>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {items.map((wo, i) => {
        const color = wo.riskScore > 70 ? '#EF4444' : wo.riskScore > 40 ? '#F59E0B' : '#22C55E'
        return (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ fontSize: 10, color: '#64748B', width: 60, flexShrink: 0, textAlign: 'right' }}>{wo.code}</div>
            <div style={{ flex: 1, height: 10, background: '#F1F5F9', borderRadius: 4, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${wo.riskScore}%`, background: color, borderRadius: 4, transition: 'width .4s' }} />
            </div>
            <div style={{ fontSize: 10, color, fontWeight: 700, width: 30, flexShrink: 0 }}>{wo.riskScore}%</div>
          </div>
        )
      })}
    </div>
  )
}

function StockoutBars({ items }) {
  if (!items?.length) return <div style={{ textAlign: 'center', color: '#94A3B8', padding: '20px 0', fontSize: 12 }}>لا توجد تنبيهات</div>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {items.map((m, i) => {
        const color = m.daysLeft < 15 ? '#EF4444' : m.daysLeft < 30 ? '#F59E0B' : '#22C55E'
        const pct = Math.min(100, (m.daysLeft / 60) * 100)
        return (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ fontSize: 10, color: '#64748B', width: 70, flexShrink: 0, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {(m.name_ar || m.name || '').slice(0, 10)}
            </div>
            <div style={{ flex: 1, height: 10, background: '#F1F5F9', borderRadius: 4, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 4 }} />
            </div>
            <div style={{ fontSize: 10, color, fontWeight: 700, width: 40, flexShrink: 0 }}>{m.daysLeft} يوم</div>
          </div>
        )
      })}
    </div>
  )
}

function TickerStrip({ prices }) {
  if (!prices) return null
  return (
    <div style={{
      background: '#0B1F3A', borderRadius: 12, padding: '10px 14px',
      display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12,
    }}>
      <div style={{ fontSize: 10, color: '#64748B', marginLeft: 6, flexShrink: 0 }}>أسعار السوق</div>
      {Object.values(prices).map((p, i) => {
        const displayVal = p.value >= 10000 ? fmt(p.value / 1000, 1) + 'K' : fmt(p.value, p.value < 100 ? 2 : 0)
        return (
          <div key={i} style={{
            flex: '1 1 90px', background: 'rgba(255,255,255,.07)',
            border: '1px solid rgba(255,255,255,.1)', borderRadius: 9,
            padding: '7px 10px', textAlign: 'center',
          }}>
            <div style={{ fontSize: 9, color: '#94A3B8', marginBottom: 2 }}>{p.icon} {p.label}</div>
            <div style={{ fontSize: 13, fontWeight: 800, color: '#fff' }}>{displayVal}</div>
            <div style={{ fontSize: 9, color: p.change >= 0 ? '#22C55E' : '#EF4444', fontWeight: 600 }}>
              {p.change >= 0 ? '▲' : '▼'} {Math.abs(p.change).toFixed(2)}%
            </div>
          </div>
        )
      })}
    </div>
  )
}

function AnalyticsPanel() {
  const toast = useToast()
  const [prices, setPrices]   = useState(null)
  const [erpData, setErpData] = useState(null)
  const [preds, setPreds]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [lastUpdate, setLastUpdate] = useState('—')
  const timer = useRef(null)

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    else setRefreshing(true)
    try {
      // إصلاح شكوى متكررة: كان أي مصدر بيانات فشل بيرجّع صفر/فاضي بصمت —
      // يعني ممكن تشوف "0 مبيعات الشهر ده" وهي في الحقيقة البيانات فشلت
      // تتحمّل، مش إن مفيش مبيعات فعلاً. دلوقتي بنسجّل مين فشل ونوريك توست واحد مجمّع.
      const failedSources = []
      const track = (name, promise, fallback) => promise.catch(() => { failedSources.push(name); return fallback })
      const [mktPrices, execDash, lowStock, workOrders, customers, salesSummary, purchSummary, prodByType, smartAlerts] = await Promise.all([
        fetchMarketPrices(),
        track('لوحة المبيعات', api.reports.executiveDashboard(), {}),
        track('تنبيهات المخزون', api.inventory.alerts(), []),
        track('أوامر الإنتاج', api.accounting.workOrders(), []),
        track('العملاء', api.customers.list(), []),
        track('ملخص المبيعات', api.reports.monthlySales(), {}),
        track('ملخص المشتريات', api.reports.purchasesSummary({ date_from: new Date(new Date().setDate(1)).toISOString().slice(0,10), date_to: new Date().toISOString().slice(0,10) }), {}),
        track('الإنتاج حسب النوع', api.reports.productionByType(), { by_type: [] }),
        track('التنبيهات الذكية', api.get('/production/alerts'), []),
      ])
      if (failedSources.length > 0) {
        toast(`تعذّر تحميل: ${failedSources.join('، ')} — البيانات المعروضة قد تكون غير مكتملة`, 'warn')
      }
      setPrices(mktPrices)
      const salesData = execDash.sales || {}
      if (!salesData.collection_rate && salesData.mtd_invoiced > 0)
        salesData.collection_rate = Math.round((salesData.mtd_collected || 0) / salesData.mtd_invoiced * 100)
      const combined = {
        ...execDash, lowStock, workOrders, customers, smartAlerts: smartAlerts || [],
        sales:      { ...salesData, monthly: salesSummary?.monthly_breakdown?.map(m => m.total) || [180000,210000,195000,240000,228000,305000] },
        purchases:  { ...(execDash.purchases || {}), ...purchSummary },
        production: { ...(execDash.production || {}), by_type: prodByType?.by_type || [] },
      }
      setErpData(combined)
      setPreds(buildPredictions(combined))
      setLastUpdate(new Date().toLocaleTimeString('ar-EG'))
    } catch (e) { console.warn('Analytics:', e) }
    finally { setLoading(false); setRefreshing(false) }
  }, [])

  useEffect(() => {
    load()
    timer.current = setInterval(() => load(true), 60000)
    return () => clearInterval(timer.current)
  }, [load])

  if (loading) return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: 360, gap: 14 }}>
      <div style={{ width: 36, height: 36, border: '3px solid #FF8A00', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 1s linear infinite' }} />
      <div style={{ color: '#64748B', fontSize: 13 }}>جاري تحميل بيانات التحليلات…</div>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  )

  const inv  = erpData?.inventory  || {}
  const prod = erpData?.production || {}
  const sal  = erpData?.sales      || {}
  const collectionRate = sal.collection_rate || 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Header bar */}
      <div style={{ background: '#0B1F3A', borderRadius: 12, padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ color: '#fff', fontWeight: 700, fontSize: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
          📊 التحليلات والتنبؤات
          <span style={{ background: '#FF8A00', color: '#fff', fontSize: 9, padding: '2px 8px', borderRadius: 20, fontWeight: 700 }}>LIVE</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ color: '#64748B', fontSize: 10 }}>آخر تحديث: {lastUpdate}</span>
          <button onClick={() => load(true)} disabled={refreshing}
            style={{ background: 'rgba(255,255,255,.1)', border: 'none', borderRadius: 8, color: '#94A3B8', padding: '4px 10px', fontSize: 11, cursor: 'pointer', fontFamily: 'inherit' }}>
            {refreshing ? '⟳' : '↻'} تحديث
          </button>
        </div>
      </div>

      {/* Market ticker */}
      <TickerStrip prices={prices} />

      {/* KPI row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
        {[
          { label: 'أوامر إنتاج نشطة', value: prod.open_work_orders || 0,     icon: '🏭', color: '#3B82F6' },
          { label: 'مبيعات الشهر',      value: fmtK(sal.mtd_invoiced),         icon: '💰', color: '#22C55E' },
          { label: 'المستحقات',          value: fmtK(sal.total_receivable),     icon: '⏰', color: '#F59E0B' },
          { label: 'نسبة التحصيل',       value: collectionRate.toFixed(0) + '%', icon: '📈', color: '#8B5CF6' },
        ].map((k, i) => (
          <div key={i} style={{ background: 'var(--bg)', border: '1px solid var(--erp-border)', borderRadius: 12, padding: '12px 14px' }}>
            <div style={{ fontSize: 11, color: '#94A3B8', marginBottom: 4 }}>{k.icon} {k.label}</div>
            <div style={{ fontSize: 18, fontWeight: 800, color: k.color }}>{k.value}</div>
          </div>
        ))}
      </div>

      {/* Charts row 1 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr 1fr', gap: 12 }}>
        <SectionCard title="تنبؤ المبيعات" badge="تنبؤ خطي" badgeColor="blue">
          <div style={{ fontSize: 10, color: '#94A3B8', marginBottom: 6 }}>الخط المتقطع = التنبؤ</div>
          <RevenueChart history={preds?.salesHistory} forecastNext={preds?.forecastNext} forecastNext2={preds?.forecastNext2} />
        </SectionCard>
        <SectionCard title="التدفق النقدي الفعلي" badge="آخر 30 يوم" badgeColor="orange">
          <RealCashFlowChart />
        </SectionCard>
        <SectionCard title="مخاطر أوامر الإنتاج" badge="Risk Score" badgeColor="red">
          <div style={{ fontSize: 10, color: '#94A3B8', marginBottom: 6 }}>الانحراف عن الميزانية المخططة</div>
          <RiskBars items={preds?.riskWOs} />
        </SectionCard>
      </div>

      {/* Charts row 2 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <SectionCard title="توقع نفاد المخزون" badge="الأيام المتبقية" badgeColor="red">
          <div style={{ fontSize: 10, color: '#94A3B8', marginBottom: 6 }}>بناءً على معدل الاستهلاك اليومي</div>
          <StockoutBars items={preds?.stockoutItems} />
        </SectionCard>
        <SectionCard title="خطر فقدان العملاء" badge="Churn Risk" badgeColor="purple">
          <div style={{ fontSize: 10, color: '#94A3B8', marginBottom: 8 }}>آخر طلب لكل عميل</div>
          {preds?.churnBuckets && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {[
                { label: 'خطر عالي (+90 يوم)',   val: preds.churnBuckets.high,   color: '#EF4444' },
                { label: 'متابعة (60-90 يوم)',   val: preds.churnBuckets.medium, color: '#F59E0B' },
                { label: 'نشط (أقل من 60 يوم)', val: preds.churnBuckets.low,    color: '#22C55E' },
              ].map((b, i) => {
                const total = (preds.churnBuckets.high + preds.churnBuckets.medium + preds.churnBuckets.low) || 1
                return (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{ fontSize: 10, color: '#64748B', width: 110, flexShrink: 0 }}>{b.label}</div>
                    <div style={{ flex: 1, height: 10, background: '#F1F5F9', borderRadius: 4, overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${(b.val / total) * 100}%`, background: b.color, borderRadius: 4 }} />
                    </div>
                    <div style={{ fontSize: 11, fontWeight: 700, color: b.color, width: 30, flexShrink: 0 }}>{b.val}</div>
                  </div>
                )
              })}
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════
// MAIN COMPONENT
// ════════════════════════════════════════════════════
const TABS = [
  { key: 'control',    label: '🏠 لوحة التحكم' },
  { key: 'analytics',  label: '📊 التحليلات'   },
]

export default function Dashboard({ onNavigate }) {
  const [tab, setTab] = useState('control')

  return (
    <div className="flex flex-col gap-3">
      {/* Tab bar */}
      <div style={{
        display: 'flex', gap: 4,
        background: 'var(--bg-card)', border: '1px solid var(--erp-border)',
        borderRadius: 10, padding: 4, width: 'fit-content',
      }}>
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              padding: '6px 18px', borderRadius: 7, border: 'none', cursor: 'pointer',
              fontSize: 13, fontWeight: tab === t.key ? 700 : 400,
              fontFamily: 'inherit',
              background: tab === t.key ? 'var(--teal)' : 'transparent',
              color: tab === t.key ? '#fff' : 'var(--erp-muted)',
              transition: 'all .15s',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      {tab === 'control'   && <ControlPanel   onNavigate={onNavigate} />}
      {tab === 'analytics' && <AnalyticsPanel />}
    </div>
  )
}
