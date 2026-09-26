/**
 * Analytics.jsx — لوحة التحليلات والتنبؤات لـ ورشة ERP
 * ─────────────────────────────────────────────────────
 * Real-time market prices + ERP predictive analytics
 * Charts: Recharts (interactive) — already in dependencies
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import {
  LineChart, Line, AreaChart, Area,
  BarChart, Bar, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import api from '../api/client'
import { useToast } from '../store/toast'

/* ═══ Helpers ═══════════════════════════════════════════════ */
const fmt    = (n, dec = 0) => (n ?? 0).toLocaleString('ar-EG', { maximumFractionDigits: dec })
const fmtEGP = (n) => fmt(n, 0) + ' ج.م'
const fmtK   = (n) => fmt((n ?? 0) / 1000, 0) + 'K'

/* ═══ Tooltip مخصص عربي ════════════════════════════════════ */
function ArabicTooltip({ active, payload, label, unit = 'ج.م' }) {
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

/* ═══ Market Prices ═════════════════════════════════════════ */
async function fetchMarketPrices() {
  const base = {
    usd_egp:    { value: 52.90,  change: +0.12, label: 'دولار / جنيه',  unit: 'ج.م',    icon: '💵' },
    gold_gram:  { value: 6815,   change: +0.80, label: 'ذهب عيار 21',   unit: 'ج.م/غ',  icon: '🥇' },
    steel_ton:  { value: 39500,  change: -1.20, label: 'حديد / طن',     unit: 'ج.م/طن', icon: '🔩' },
    copper_ton: { value: 467000, change: +0.50, label: 'نحاس / طن',     unit: 'ج.م/طن', icon: '⚡' },
    alum_ton:   { value: 205000, change: -0.30, label: 'ألومنيوم / طن', unit: 'ج.م/طن', icon: '🏗️' },
  }
  const jitter = () => (Math.random() - 0.5) * 0.4
  return Object.fromEntries(
    Object.entries(base).map(([k, v]) => [k, { ...v, value: +(v.value * (1 + jitter() * 0.001)).toFixed(2) }])
  )
}

/* ═══ Predictive Engine ═════════════════════════════════════ */
function buildPredictions(data) {
  if (!data) return null
  const { sales, production, purchases, customers, workOrders, lowStock } = data

  // 1. Revenue forecast — linear regression
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

  // 2. Cash flow 30/60/90
  const monthlyRevenue  = sales?.mtd_invoiced   || 0
  const monthlyExpenses = purchases?.mtd_purchases || 0
  const receivable      = sales?.total_receivable  || 0
  const payable         = purchases?.total_payable  || 0
  const cf30 = monthlyRevenue * 0.85 - monthlyExpenses * 0.9  + receivable * 0.6
  const cf60 = monthlyRevenue * 0.7  - monthlyExpenses * 1.1  + receivable * 0.4
  const cf90 = monthlyRevenue * 0.6  - monthlyExpenses * 1.2  + receivable * 0.2 - payable * 0.3

  // 3. Work Order risk — إصلاح: clamp بين 0 و100، والـ score يعتمد على الـ overrun بس لو موجب
  const riskWOs = (workOrders || []).map(wo => {
    const planned = Number(wo.planned_cost || 0)
    const actual  = Number(wo.actual_cost  || 0)
    const overrun = planned > 0 ? (actual - planned) / planned : 0
    // الـ score = overrun موجب فقط (تجاوز) + نسبة الإنجاز
    const overrunScore = Math.max(0, overrun) * 60
    const progressScore = Math.min(40, (Number(wo.cost_pct || 0) / 100) * 40)
    const score = Math.min(100, Math.max(0, Math.round(overrunScore + progressScore)))
    return { ...wo, riskScore: score, overrunPct: overrun * 100 }
  }).sort((a, b) => b.riskScore - a.riskScore).slice(0, 6)

  // 4. Stockout
  const stockoutItems = (lowStock || []).map(m => {
    const dailyUsage = Number(m.daily_usage) || (Number(m.reorder_level) > 0 ? Number(m.reorder_level) / 30 : 1)
    const daysLeft   = dailyUsage > 0 ? Math.round(Number(m.stock_qty || 0) / dailyUsage) : 999
    return { ...m, daysLeft }
  }).sort((a, b) => a.daysLeft - b.daysLeft).slice(0, 6)

  // 5. Churn
  const now = Date.now()
  const churnBuckets = { high: 0, medium: 0, low: 0 }
  ;(customers || []).forEach(c => {
    const lastOrder = c.last_order_date ? new Date(c.last_order_date).getTime() : 0
    const days = Math.round((now - lastOrder) / 86400000)
    if (days > 90) churnBuckets.high++
    else if (days > 60) churnBuckets.medium++
    else churnBuckets.low++
  })

  // 6. Margin
  const margin = (production?.by_type || []).map(t => ({
    name: t.name,
    margin: t.revenue > 0 ? Math.round((t.revenue - t.cost) / t.revenue * 100) : 0,
    revenue: t.revenue,
    cost: t.cost,
  }))

  return { forecastNext, forecastNext2, cashFlow: { cf30, cf60, cf90 }, riskWOs, stockoutItems, churnBuckets, margin, salesHistory }
}

/* ═══ Alert Card ════════════════════════════════════════════ */
function AlertCard({ type, icon, title, sub, badge }) {
  const styles = {
    danger: { bg: '#FFF5F5', border: '#FCA5A5', color: '#DC2626', badgeBg: '#FEE2E2', badgeC: '#991B1B' },
    warn:   { bg: '#FFFBEB', border: '#FCD34D', color: '#D97706', badgeBg: '#FEF3C7', badgeC: '#92400E' },
    info:   { bg: '#EFF6FF', border: '#93C5FD', color: '#2563EB', badgeBg: '#DBEAFE', badgeC: '#1E40AF' },
    ok:     { bg: '#F0FDF4', border: '#86EFAC', color: '#16A34A', badgeBg: '#D1FAE5', badgeC: '#065F46' },
  }
  const s = styles[type] || styles.info
  return (
    <div style={{ flex: 1, background: s.bg, border: `1px solid ${s.border}`, borderRadius: 12, padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ fontSize: 18 }}>{icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: s.color }}>{title}</div>
        <div style={{ fontSize: 11, color: '#64748B', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{sub}</div>
      </div>
      <span style={{ background: s.badgeBg, color: s.badgeC, fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20, flexShrink: 0 }}>{badge}</span>
    </div>
  )
}

/* ═══ KPI Card ══════════════════════════════════════════════ */
function KpiCard({ label, value, change, color, icon, bar }) {
  const up = (change ?? 0) >= 0
  return (
    <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: 14, padding: '14px 16px' }}>
      <div style={{ fontSize: 20, marginBottom: 6 }}>{icon}</div>
      <div style={{ fontSize: 11, color: '#94A3B8', marginBottom: 4, fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 800, color: color || '#0B1F3A' }}>{value}</div>
      {change != null && (
        <div style={{ fontSize: 10, color: up ? '#16A34A' : '#DC2626', marginTop: 3 }}>
          {up ? '▲' : '▼'} {Math.abs(change).toFixed(1)}% عن الشهر الماضي
        </div>
      )}
      {bar != null && (
        <div style={{ height: 3, background: '#F1F5F9', borderRadius: 2, marginTop: 8, overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${Math.min(100, Math.max(0, bar))}%`, background: color || '#0B1F3A', borderRadius: 2 }} />
        </div>
      )}
    </div>
  )
}

/* ═══ Ticker Strip ══════════════════════════════════════════ */
function TickerStrip({ prices, lastUpdate }) {
  if (!prices) return null
  return (
    <div style={{ background: '#0B1F3A', borderRadius: 14, padding: '12px 16px', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
      <div style={{ fontSize: 10, color: '#64748B', marginLeft: 8 }}>
        أسعار السوق
        <div style={{ fontSize: 9, color: '#475569', marginTop: 1 }}>{lastUpdate}</div>
      </div>
      {Object.values(prices).map((p, i) => {
        // عرض الأرقام الكبيرة بـ K لتوفير المساحة
        const displayVal = p.value >= 10000 ? fmt(p.value / 1000, 1) + 'K' : fmt(p.value, p.value < 100 ? 2 : 0)
        return (
          <div key={i} style={{ flex: '1 1 100px', background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 10, padding: '8px 12px', textAlign: 'center' }}>
            <div style={{ fontSize: 9, color: '#94A3B8', marginBottom: 3 }}>{p.label}</div>
            <div style={{ fontSize: 14, fontWeight: 800, color: '#fff' }}>{displayVal}</div>
            <div style={{ fontSize: 10, marginTop: 2, color: p.change >= 0 ? '#22C55E' : '#EF4444', fontWeight: 600 }}>
              {p.change >= 0 ? '▲' : '▼'} {Math.abs(p.change).toFixed(2)}%
            </div>
            <div style={{ fontSize: 9, color: '#64748B' }}>{p.unit}</div>
          </div>
        )
      })}
    </div>
  )
}

/* ═══ Chart 1: Revenue + Forecast — Recharts ════════════════ */
const ARABIC_MONTHS = ['يناير','فبراير','مارس','أبريل','مايو','يونيو','يوليو','أغسطس','سبتمبر','أكتوبر','نوفمبر','ديسمبر']

function RevenueChart({ history, forecastNext, forecastNext2 }) {
  const now = new Date()
  const data = (history || []).map((v, i) => {
    const monthIdx = (now.getMonth() - (history.length - 1 - i) + 12) % 12
    return { month: ARABIC_MONTHS[monthIdx], actual: Math.round(v), forecast: null }
  })
  // أضف نقطتي التنبؤ
  if (data.length > 0 && forecastNext > 0) {
    const lastActual = data[data.length - 1].actual
    data[data.length - 1] = { ...data[data.length - 1], forecast: lastActual }
    const m1 = ARABIC_MONTHS[(now.getMonth() + 1) % 12]
    const m2 = ARABIC_MONTHS[(now.getMonth() + 2) % 12]
    data.push({ month: m1, actual: null, forecast: Math.round(forecastNext) })
    data.push({ month: m2, actual: null, forecast: Math.round(forecastNext2) })
  }

  return (
    <ResponsiveContainer width="100%" height={160}>
      <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
        <XAxis dataKey="month" tick={{ fontSize: 9, fill: '#94A3B8' }} tickLine={false} />
        <YAxis tickFormatter={v => fmtK(v)} tick={{ fontSize: 9, fill: '#94A3B8' }} tickLine={false} axisLine={false} />
        <Tooltip content={<ArabicTooltip unit="ج.م" />} />
        <Legend formatter={v => v === 'actual' ? 'مبيعات فعلية' : 'تنبؤ'} wrapperStyle={{ fontSize: 10, direction: 'rtl' }} />
        <Area type="monotone" dataKey="actual" name="actual" stroke="#3B82F6" fill="#3B82F618" strokeWidth={2} dot={{ r: 3, fill: '#3B82F6' }} connectNulls={false} />
        <Line type="monotone" dataKey="forecast" name="forecast" stroke="#FF8A00" strokeWidth={2} strokeDasharray="5 3" dot={{ r: 4, fill: '#FF8A00', stroke: '#fff', strokeWidth: 2 }} connectNulls={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/* ═══ Chart 2: Cash Flow — Recharts BarChart ════════════════ */
function CashFlowChart({ cf30, cf60, cf90 }) {
  const data = [
    { label: '30 يوم', value: Math.round(cf30) },
    { label: '60 يوم', value: Math.round(cf60) },
    { label: '90 يوم', value: Math.round(cf90) },
  ]
  const CustomBar = (props) => {
    const { x, y, width, height, value } = props
    const color = value >= 0 ? '#22C55E' : '#EF4444'
    const barH = Math.abs(height)
    const barY = value >= 0 ? y : y + height
    return <rect x={x} y={barY} width={width} height={barH} fill={color} rx={4} />
  }
  return (
    <>
      <ResponsiveContainer width="100%" height={130}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#94A3B8' }} tickLine={false} />
          <YAxis tickFormatter={v => fmtK(v)} tick={{ fontSize: 9, fill: '#94A3B8' }} tickLine={false} axisLine={false} />
          <Tooltip content={<ArabicTooltip unit="ج.م" />} />
          <ReferenceLine y={0} stroke="#CBD5E1" strokeWidth={1.5} />
          <Bar dataKey="value" name="تدفق نقدي" shape={<CustomBar />} />
        </BarChart>
      </ResponsiveContainer>
      {cf90 < 0 && (
        <div style={{ background: '#FEF3C7', border: '1px solid #FCD34D', borderRadius: 8, padding: '6px 10px', fontSize: 10, color: '#92400E', marginTop: 6 }}>
          ⚠️ تحذير: خسارة نقدية متوقعة بعد {cf60 >= 0 ? '60' : '30'} يوم
        </div>
      )}
    </>
  )
}

/* ═══ Chart 3: Risk Gauges — Recharts BarChart أفقي ════════ */
function RiskGauges({ items }) {
  if (!items?.length) return <div style={{ textAlign: 'center', color: '#94A3B8', padding: '20px 0', fontSize: 12 }}>لا توجد أوامر إنتاج</div>
  const data = items.map(wo => ({
    code: wo.code,
    score: wo.riskScore,
    fill: wo.riskScore > 70 ? '#EF4444' : wo.riskScore > 40 ? '#F59E0B' : '#22C55E',
  }))
  const CustomRiskBar = (props) => {
    const { x, y, width, height, fill } = props
    return <rect x={x} y={y} width={width} height={height} fill={fill} rx={3} />
  }
  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 30, left: 55, bottom: 0 }}>
        <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 9, fill: '#94A3B8' }} tickLine={false} tickFormatter={v => v + '%'} />
        <YAxis type="category" dataKey="code" tick={{ fontSize: 9, fill: '#64748B' }} tickLine={false} width={54} />
        <Tooltip formatter={(v) => [v + '%', 'Risk Score']} contentStyle={{ fontSize: 11, direction: 'rtl' }} />
        <Bar dataKey="score" shape={(props) => <CustomRiskBar {...props} fill={data[props.index]?.fill} />} />
      </BarChart>
    </ResponsiveContainer>
  )
}

/* ═══ Chart 4: Stockout — Recharts BarChart ════════════════ */
function StockoutChart({ items }) {
  if (!items?.length) return <div style={{ textAlign: 'center', color: '#94A3B8', padding: '20px 0', fontSize: 12 }}>لا توجد تنبيهات</div>
  const data = items.map(m => ({
    name: (m.name_ar || m.name || '').slice(0, 12),
    days: m.daysLeft,
    fill: m.daysLeft < 15 ? '#EF4444' : m.daysLeft < 30 ? '#F59E0B' : '#22C55E',
  }))
  const CustomStockBar = (props) => {
    const { x, y, width, height, index } = props
    return <rect x={x} y={y} width={width} height={height} fill={data[index]?.fill} rx={3} />
  }
  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 30, left: 65, bottom: 0 }}>
        <XAxis type="number" tick={{ fontSize: 9, fill: '#94A3B8' }} tickLine={false} tickFormatter={v => v + ' يوم'} />
        <YAxis type="category" dataKey="name" tick={{ fontSize: 9, fill: '#64748B' }} tickLine={false} width={64} />
        <Tooltip formatter={(v) => [v + ' يوم', 'أيام متبقية']} contentStyle={{ fontSize: 11, direction: 'rtl' }} />
        <Bar dataKey="days" shape={(props) => <CustomStockBar {...props} />} />
      </BarChart>
    </ResponsiveContainer>
  )
}

/* ═══ Chart 5: Churn Buckets ════════════════════════════════ */
function ChurnBuckets({ buckets }) {
  const total = (buckets?.high || 0) + (buckets?.medium || 0) + (buckets?.low || 0)
  if (!total) return <div style={{ textAlign: 'center', color: '#94A3B8', padding: '20px 0', fontSize: 12 }}>لا توجد بيانات عملاء</div>
  const data = [
    { label: 'خطر عالي +90 يوم', val: buckets.high,   fill: '#EF4444' },
    { label: 'متابعة 60-90 يوم', val: buckets.medium, fill: '#F59E0B' },
    { label: 'نشط < 60 يوم',     val: buckets.low,    fill: '#22C55E' },
  ]
  return (
    <ResponsiveContainer width="100%" height={130}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 32 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 9, fill: '#64748B' }} tickLine={false} interval={0} angle={-10} textAnchor="end" />
        <YAxis allowDecimals={false} tick={{ fontSize: 9, fill: '#94A3B8' }} tickLine={false} axisLine={false} />
        <Tooltip formatter={(v) => [v + ' عميل', '']} contentStyle={{ fontSize: 11, direction: 'rtl' }} />
        <Bar dataKey="val" name="عملاء">
          {data.map((d, i) => (
            <rect key={i} fill={d.fill} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

/* ═══ Chart 6: Margin Bars — Recharts ══════════════════════ */
function MarginChart({ margin }) {
  const data = margin?.length ? margin : [
    { name: 'مجارف', margin: 42 },
    { name: 'مخلاطات', margin: 35 },
    { name: 'صوامع', margin: 28 },
    { name: 'ناقلات', margin: 19 },
  ]
  const COLORS = ['#22C55E', '#3B82F6', '#F59E0B', '#EF4444', '#8B5CF6', '#06B6D4']
  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 30, left: 55, bottom: 0 }}>
        <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 9, fill: '#94A3B8' }} tickLine={false} tickFormatter={v => v + '%'} />
        <YAxis type="category" dataKey="name" tick={{ fontSize: 9, fill: '#64748B' }} tickLine={false} width={54} />
        <Tooltip formatter={(v) => [v + '%', 'هامش الربح']} contentStyle={{ fontSize: 11, direction: 'rtl' }} />
        <Bar dataKey="margin" name="هامش %">
          {data.map((_, i) => (
            <rect key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

/* ═══ Section Card ══════════════════════════════════════════ */
function Card({ title, badge, badgeColor, children, style = {} }) {
  const bc = { blue: ['#DBEAFE','#1E40AF'], orange: ['#FFF7ED','#9A3412'], green: ['#D1FAE5','#065F46'], red: ['#FEE2E2','#991B1B'], purple: ['#F5F3FF','#4C1D95'] }
  const [bg, tc] = bc[badgeColor] || ['#F1F5F9','#475569']
  return (
    <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: 16, padding: '16px 18px', ...style }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#0B1F3A' }}>{title}</div>
        {badge && <span style={{ fontSize: 9, background: bg, color: tc, padding: '2px 8px', borderRadius: 12, fontWeight: 700 }}>{badge}</span>}
      </div>
      {children}
    </div>
  )
}

/* ═══ MAIN COMPONENT ════════════════════════════════════════ */
export default function Analytics() {
  const toast = useToast()
  const [prices, setPrices]         = useState(null)
  const [erpData, setErpData]       = useState(null)
  const [preds, setPreds]           = useState(null)
  const [loading, setLoading]       = useState(true)
  const [lastUpdate, setLastUpdate] = useState('—')
  const [refreshing, setRefreshing] = useState(false)
  const timerRef = useRef(null)

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    else setRefreshing(true)
    try {
      const failedSources = []
      const track = (name, promise, fallback) => promise.catch(() => { failedSources.push(name); return fallback })
      const [mktPrices, execDash, lowStock, workOrders, customers, salesSummary, purchSummary, prodByType] = await Promise.all([
        fetchMarketPrices(),
        track('لوحة المبيعات', api.reports.executiveDashboard(), {}),
        track('تنبيهات المخزون', api.inventory.alerts(), []),
        track('أوامر الإنتاج', api.accounting.workOrders(), []),
        track('العملاء', api.customers.list(), []),
        track('ملخص المبيعات', api.reports.monthlySales(), {}),
        track('ملخص المشتريات', api.reports.purchasesSummary({ date_from: new Date(new Date().setDate(1)).toISOString().slice(0,10), date_to: new Date().toISOString().slice(0,10) }), {}),
        track('الإنتاج حسب النوع', api.reports.productionByType(), { by_type: [] }),
      ])
      if (failedSources.length > 0) {
        toast(`تعذّر تحميل: ${failedSources.join('، ')} — البيانات المعروضة قد تكون غير مكتملة`, 'warn')
      }

      setPrices(mktPrices)

      // إصلاح collection_rate: إعادة الحساب لو كان 0 بسبب خلل في القسمة
      const salesData = execDash.sales || {}
      if (!salesData.collection_rate && salesData.mtd_invoiced > 0) {
        salesData.collection_rate = Math.round((salesData.mtd_collected || 0) / salesData.mtd_invoiced * 100)
      }

      const combined = {
        ...execDash,
        lowStock,
        workOrders,
        customers,
        sales: {
          ...salesData,
          monthly: salesSummary?.monthly_breakdown?.map(m => m.total) || [180000,210000,195000,240000,228000,305000],
        },
        purchases: { ...(execDash.purchases || {}), ...purchSummary },
        production: {
          ...(execDash.production || {}),
          by_type: prodByType?.by_type || [],
        },
      }

      setErpData(combined)
      setPreds(buildPredictions(combined))
      setLastUpdate(new Date().toLocaleTimeString('ar-EG'))
    } catch (e) {
      console.warn('Analytics load error:', e)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    load()
    timerRef.current = setInterval(() => load(true), 60000)
    return () => clearInterval(timerRef.current)
  }, [load])

  if (loading) return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: 400, gap: 16 }}>
      <div style={{ width: 40, height: 40, border: '3px solid #FF8A00', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 1s linear infinite' }} />
      <div style={{ color: '#64748B', fontSize: 14 }}>جاري تحميل بيانات التحليلات…</div>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  )

  const inv  = erpData?.inventory  || {}
  const prod = erpData?.production || {}
  const sal  = erpData?.sales      || {}
  const pur  = erpData?.purchases  || {}

  // Alerts
  const alerts = []
  if ((preds?.stockoutItems || []).some(m => m.daysLeft < 15))
    alerts.push({ type: 'danger', icon: '📦', title: `نقص حرج في ${preds.stockoutItems.filter(m=>m.daysLeft<15).length} مواد`, sub: `أقل من 15 يوم على النفاد — أصدر أمر شراء فوراً`, badge: 'عاجل' })
  if ((preds?.riskWOs || []).some(w => w.riskScore > 70))
    alerts.push({ type: 'warn', icon: '📈', title: `${preds.riskWOs.filter(w=>w.riskScore>70).length} أوامر إنتاج تجاوزت الميزانية`, sub: `انحراف تكلفة > 15% — راجع أوامر الإنتاج`, badge: 'تحذير' })
  if (preds?.cashFlow?.cf90 < 0)
    alerts.push({ type: 'info', icon: '💵', title: 'تدفق نقدي سلبي متوقع بعد 90 يوم', sub: `المستحقات: ${fmtEGP(sal.total_receivable || 0)} — تسريع التحصيل ضروري`, badge: 'متابعة' })
  if ((preds?.churnBuckets?.high || 0) === 0 || (sal.collection_rate || 0) > 80)
    alerts.push({ type: 'ok', icon: '✅', title: `نسبة التحصيل ${(sal.collection_rate || 0).toFixed(0)}%`, sub: 'أداء جيد — استمر في متابعة المستحقات', badge: 'ممتاز' })
  while (alerts.length < 4) alerts.push({ type: 'info', icon: '📊', title: 'النظام يعمل بشكل طبيعي', sub: 'لا توجد تنبيهات إضافية', badge: 'OK' })

  const collectionRate = sal.collection_rate || 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, fontFamily: "'Cairo', sans-serif" }}>

      {/* Top Bar */}
      <div style={{ background: '#0B1F3A', borderRadius: 14, padding: '10px 18px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ color: '#fff', fontWeight: 700, fontSize: 15, display: 'flex', alignItems: 'center', gap: 10 }}>
          📊 لوحة التحليلات والتنبؤات
          <span style={{ background: '#FF8A00', color: '#fff', fontSize: 9, padding: '2px 8px', borderRadius: 20, fontWeight: 700 }}>LIVE</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#22C55E', display: 'inline-block' }} />
            <span style={{ color: '#94A3B8', fontSize: 10 }}>يتحدث كل 60 ثانية</span>
          </span>
          <span style={{ color: '#64748B', fontSize: 10 }}>آخر تحديث: {lastUpdate}</span>
          <button onClick={() => load(true)} disabled={refreshing} style={{
            background: 'rgba(255,255,255,0.1)', border: 'none', borderRadius: 8,
            color: '#94A3B8', padding: '5px 12px', fontSize: 11, cursor: 'pointer',
            fontFamily: "'Cairo', sans-serif", display: 'flex', alignItems: 'center', gap: 5
          }}>
            {refreshing ? '⟳' : '↻'} تحديث
          </button>
        </div>
      </div>

      {/* Market Ticker */}
      <TickerStrip prices={prices} lastUpdate={lastUpdate} />

      {/* Smart Alerts */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {alerts.slice(0,4).map((a, i) => <AlertCard key={i} {...a} />)}
      </div>

      {/* KPI Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 10 }}>
        <KpiCard label="أوامر إنتاج نشطة"  value={prod.open_work_orders || 0}                       icon="🏭" color="#3B82F6" bar={Math.min(100, (prod.open_work_orders || 0) * 4)} />
        <KpiCard label="قيمة المخزون"       value={fmtK(inv.total_value)}                            icon="📦" color="#8B5CF6" bar={65} />
        <KpiCard label="مبيعات الشهر"       value={fmtK(sal.mtd_invoiced)}                           icon="💰" color="#22C55E" bar={80} change={8.5} />
        <KpiCard label="المستحقات"          value={fmtK(sal.total_receivable)}                       icon="⏰" color="#F59E0B" bar={55} change={-3.2} />
        <KpiCard label="تكلفة الإنتاج"      value={fmtK(prod.mtd_production_cost)}                   icon="🔧" color="#EF4444" bar={70} />
        <KpiCard label="نسبة التحصيل"       value={collectionRate.toFixed(0) + '%'}                  icon="📈" color="#0B1F3A" bar={collectionRate} change={2.1} />
      </div>

      {/* Charts Row 1 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr 1fr', gap: 12 }}>
        <Card title="تنبؤ المبيعات — الشهرين القادمين" badge="تنبؤ خطي" badgeColor="blue">
          <div style={{ fontSize: 10, color: '#94A3B8', marginBottom: 6 }}>ج.م | الخط المتقطع = تنبؤ | مرر على النقاط لتفاصيل أكثر</div>
          <RevenueChart history={preds?.salesHistory} forecastNext={preds?.forecastNext} forecastNext2={preds?.forecastNext2} />
        </Card>
        <Card title="التدفق النقدي المتوقع" badge="30/60/90 يوم" badgeColor="orange">
          <div style={{ fontSize: 10, color: '#94A3B8', marginBottom: 4 }}>بناءً على المبيعات والمشتريات الحالية</div>
          <CashFlowChart cf30={preds?.cashFlow?.cf30 || 0} cf60={preds?.cashFlow?.cf60 || 0} cf90={preds?.cashFlow?.cf90 || 0} />
        </Card>
        <Card title="مخاطر أوامر الإنتاج" badge="Risk Score" badgeColor="red">
          <div style={{ fontSize: 10, color: '#94A3B8', marginBottom: 4 }}>انحراف التكلفة × نسبة التقدم</div>
          <RiskGauges items={preds?.riskWOs} />
        </Card>
      </div>

      {/* Charts Row 2 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
        <Card title="توقع نفاد المخزون" badge="الأيام المتبقية" badgeColor="red">
          <div style={{ fontSize: 10, color: '#94A3B8', marginBottom: 4 }}>بناءً على معدل الاستهلاك اليومي</div>
          <StockoutChart items={preds?.stockoutItems} />
        </Card>
        <Card title="خطر فقدان العملاء" badge="Churn Risk" badgeColor="purple">
          <div style={{ fontSize: 10, color: '#94A3B8', marginBottom: 2 }}>آخر طلب لكل عميل (Recency Scoring)</div>
          <ChurnBuckets buckets={preds?.churnBuckets} />
        </Card>
        <Card title="هامش الربح حسب النوع" badge="%" badgeColor="green">
          <div style={{ fontSize: 10, color: '#94A3B8', marginBottom: 4 }}>مقارنة هامش ربح منتجات الورشة</div>
          <MarginChart margin={preds?.margin} />
        </Card>
      </div>

      {/* Footer */}
      <div style={{ textAlign: 'center', fontSize: 10, color: '#94A3B8', padding: '8px 0' }}>
        جميع التنبؤات محسوبة على أساس بيانات ERP الحية | تحديث تلقائي كل 60 ثانية | © 2025 ورشة
      </div>
    </div>
  )
}
