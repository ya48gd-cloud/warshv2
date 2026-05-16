import { useApi } from '../hooks/useApi'
import api from '../api/client'
import { egp, fmtDate, WO_STATUS } from '../utils/fmt'
import { StatusBadge } from '../components/ui/Badge'
import { PageSpinner } from '../components/ui/Spinner'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

const COLOR_MAP = {
  blue: 'var(--blue)', teal: 'var(--teal)', green: 'var(--green-md)',
  amber: 'var(--amber-md)', red: 'var(--red-md)',
}

function KpiCard({ k }) {
  const col = COLOR_MAP[k.color] || 'var(--teal)'
  const val = k.format === 'currency' ? egp(Number(k.value || 0)) : Number(k.value || 0).toLocaleString('ar-EG')
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
        const col = a.level === 'critical' ? 'var(--red)' : a.level === 'warning' ? 'var(--amber-md)' : 'var(--blue)'
        const bg  = a.level === 'critical' ? 'var(--red-lt)' : a.level === 'warning' ? 'var(--amber-lt)' : 'var(--blue-lt)'
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

export default function Dashboard({ onNavigate }) {
  const { data, loading } = useApi(() => api.dashboard.full().catch(async () => {
    const [kpis, lowStock, wos] = await Promise.all([
      api.dashboard.kpis().catch(() => null),
      api.dashboard.lowStock().catch(() => []),
      api.accounting.workOrders().catch(() => []),
    ])
    return { kpis: kpis ? {
      active_work_orders: { value: kpis.open_work_orders, label: 'أوامر نشطة', color: 'blue', icon: '📋' },
      low_stock_items:    { value: kpis.low_stock_count,  label: 'مواد ناقصة', color: 'red',  icon: '⚠️' },
    } : {}, alerts: [], work_orders: wos, low_stock: lowStock, mrp: [] }
  }))

  if (loading) return <PageSpinner />

  const kpis     = data?.kpis || {}
  const alerts   = data?.alerts || []
  const wos      = data?.work_orders || []
  const lowStock = data?.low_stock || []
  const mrp      = data?.mrp || []

  return (
    <div className="flex flex-col gap-4">
      <AlertBar alerts={alerts} />

      {/* KPI Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {KPI_ORDER.map(k => kpis[k] ? <KpiCard key={k} k={kpis[k]} /> : null)}
      </div>

      {/* Middle row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Work Orders */}
        <div className="card">
          <div className="text-sm font-semibold mb-3">📋 أوامر الإنتاج النشطة</div>
          {wos.length === 0
            ? <p className="text-erp-muted text-sm">لا توجد أوامر نشطة</p>
            : <div className="overflow-x-auto">
                <table className="erp-table">
                  <thead><tr>
                    <th>الكود</th><th>المعدة</th><th>الحالة</th><th>التقدم</th><th>التكلفة</th>
                  </tr></thead>
                  <tbody>
                    {wos.slice(0, 6).map(wo => {
                      const pct = wo.cost_pct > 1 ? Math.min(100, wo.cost_pct) : Math.min(100, (wo.cost_pct || 0) * 100)
                      return (
                        <tr key={wo.id} className="cursor-pointer" onClick={() => onNavigate?.('workorders')}>
                          <td className="font-medium font-mono text-xs">{wo.code}</td>
                          <td className="text-erp-muted text-xs">{wo.equipment_name_ar || wo.equipment_name || wo.equipment || '—'}</td>
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

        {/* Low Stock */}
        <div className="card">
          <div className="text-sm font-semibold mb-3">⚠️ مواد تحتاج طلبية</div>
          {lowStock.length === 0
            ? <p className="text-erp-muted text-sm" style={{ color: 'var(--green)' }}>✓ المخزون في المستوى الجيد</p>
            : <div className="overflow-x-auto">
                <table className="erp-table">
                  <thead><tr>
                    <th>المادة</th><th>المتوفر</th><th>الحد</th><th>النقص</th>
                  </tr></thead>
                  <tbody>
                    {lowStock.slice(0, 6).map(m => {
                      const p = m.pct || 0
                      const col = p < 25 ? 'var(--red)' : p < 50 ? 'var(--amber-md)' : 'var(--teal)'
                      return (
                        <tr key={m.id} className="cursor-pointer" onClick={() => onNavigate?.('materials')}>
                          <td>
                            <div className="font-medium text-xs">{m.name_ar || m.name_en || m.code}</div>
                            <div className="font-mono text-[10px] text-erp-muted">{m.code}</div>
                          </td>
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

      {/* Bottom row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* MRP */}
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

        {/* Quick Actions */}
        <div className="card">
          <div className="text-sm font-semibold mb-3">⚡ إجراءات سريعة</div>
          <div className="grid grid-cols-2 gap-2">
            {[
              { label: '📋 أوامر الإنتاج', page: 'workorders' },
              { label: '📋 عرض سعر جديد', page: 'quotations' },
              { label: '📦 المواد والمخزون', page: 'materials' },
              { label: '🏢 العملاء',        page: 'customers' },
              { label: '👷 العاملون',        page: 'workers' },
              { label: '🧾 الفواتير',        page: 'invoices' },
            ].map(q => (
              <button
                key={q.page}
                className="btn text-xs py-2.5 justify-center"
                onClick={() => onNavigate?.(q.page)}
              >{q.label}</button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
