/**
 * لوحة تتبع دورة حياة المعدة (Equipment Pipeline) — محطة 17
 * "المعدة رقم X من أمر البيع Y — دلوقتي فين؟"
 *
 * CustomerOrder → WorkOrder → WIP (محطة 14) → FG (محطة 15) → DeliveryOrder (محطة 16)
 */
import { useState } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { Input } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'
import { fmtDate } from '../utils/fmt'

const STATUS_STYLE = {
  'لم يبدأ التصنيع':               { badge: 'badge-gray',  dot: '#6b7280' },
  'تحت التصنيع':                   { badge: 'badge-amber', dot: 'var(--teal)' },
  'جاهزة في المخزون (FG)':         { badge: 'badge-blue',  dot: '#2563eb' },
  'تم التسليم جزئياً':             { badge: 'badge-amber', dot: '#b45309' },
  'تم التسليم بالكامل':            { badge: 'badge-green', dot: '#15803d' },
  'مكتملة (بدون رصيد FG مسجَّل)':  { badge: 'badge-gray',  dot: '#6b7280' },
}

function StatusBadge({ status }) {
  const s = STATUS_STYLE[status] || { badge: 'badge-gray', dot: '#6b7280' }
  return (
    <span className={`badge ${s.badge}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: s.dot, display: 'inline-block' }} />
      {status}
    </span>
  )
}

function PipelineRow({ row, expanded, onToggle }) {
  return (
    <>
      <tr className="cursor-pointer" onClick={onToggle}>
        <td className="font-mono text-xs">{row.work_order_code}</td>
        <td>{row.equipment_name || '—'}</td>
        <td>
          {row.is_stock_based
            ? <span className="badge badge-gray">مخزون (بدون عميل)</span>
            : <span>{row.customer_name || '—'} <span className="text-xs text-erp-muted">({row.customer_order_code})</span></span>}
        </td>
        <td><StatusBadge status={row.pipeline_status} /></td>
        <td className="text-center">{row.quantity}</td>
        <td className="text-center">{row.wip_qty}</td>
        <td className="text-center">{row.fg_qty}</td>
        <td className="text-center">{row.delivered_qty}</td>
        <td className="text-center">
          {row.progress_percent != null ? `${row.progress_percent}%` : '—'}
        </td>
        <td className="text-xs text-erp-muted">{fmtDate(row.end_date || row.start_date)}</td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={10} className="bg-erp-hover px-4 py-3">
            <div className="flex flex-col gap-2 text-sm">
              <div className="flex gap-6 flex-wrap">
                <div><span className="text-erp-muted">بداية التصنيع:</span> {fmtDate(row.start_date) || '—'}</div>
                <div><span className="text-erp-muted">نهاية التصنيع:</span> {fmtDate(row.end_date) || '—'}</div>
                <div><span className="text-erp-muted">حالة أمر الإنتاج الخام:</span> {row.status}</div>
              </div>
              {row.deliveries?.length > 0 ? (
                <div>
                  <div className="text-erp-muted mb-1">أوامر التسليم المرتبطة:</div>
                  <ul className="flex flex-col gap-1">
                    {row.deliveries.map((d, i) => (
                      <li key={i} className="font-mono text-xs">
                        {d.delivery_order_code} — {fmtDate(d.delivery_date) || 'بدون تاريخ'}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <div className="text-erp-muted text-xs">لا يوجد تسليم مسجَّل لهذا الأمر بعد.</div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function EquipmentPipeline() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const toast = useToast()

  const [q, setQ] = useState('')
  const [searchTerm, setSearchTerm] = useState('')
  const { data: rows, loading, reload } = useApi(() => api.production.pipeline(searchTerm), [searchTerm])
  const [expandedId, setExpandedId] = useState(null)
  const [backfillReport, setBackfillReport] = useState(null)

  const [runBackfill, backfilling] = useAction(async (dryRun) => {
    const report = await api.production.pipelineBackfill(dryRun)
    setBackfillReport(report)
    if (!dryRun) {
      toast(`تم: ${report.wip_created.length} WIP + ${report.fg_created.length} FG، وتخطّي ${report.skipped.length}`)
      reload()
    }
    return report
  })

  const onSearch = (e) => {
    e.preventDefault()
    setSearchTerm(q)
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-xl font-bold">🚚 تتبع دورة حياة المعدة</h1>
          <p className="text-sm text-erp-muted">أمر بيع → تصنيع (WIP) → منتج تام (FG) → تسليم للعميل</p>
        </div>
        {isAdmin && (
          <div className="flex gap-2">
            <button className="btn btn-sm" disabled={backfilling} onClick={() => runBackfill(true)}>
              {backfilling ? '…' : '🔍 مراجعة الأوامر القديمة'}
            </button>
            <button className="btn btn-sm btn-primary" disabled={backfilling} onClick={() => runBackfill(false)}>
              {backfilling ? '…' : '⚙️ تشغيل Backfill'}
            </button>
          </div>
        )}
      </div>

      {backfillReport && (
        <div className="card text-sm">
          <div className="flex gap-6 flex-wrap">
            <div>سيتم/تم تسجيل WIP: <b>{backfillReport.wip_created.length}</b></div>
            <div>سيتم/تم تسجيل FG: <b>{backfillReport.fg_created.length}</b></div>
            <div>تم تخطّيه (مسجَّل بالفعل): <b>{backfillReport.skipped.length}</b></div>
            {backfillReport.dry_run && <span className="badge badge-gray">مراجعة فقط — لم يُعدَّل شيء بعد</span>}
          </div>
        </div>
      )}

      <form onSubmit={onSearch} className="flex gap-2 items-end">
        <div className="flex-1">
          <Input placeholder="ابحث بكود أمر الإنتاج / اسم المعدة / اسم العميل / كود طلب البيع"
            value={q} onChange={e => setQ(e.target.value)} />
        </div>
        <button className="btn btn-primary" type="submit">🔎 بحث</button>
        {searchTerm && (
          <button type="button" className="btn" onClick={() => { setQ(''); setSearchTerm('') }}>مسح</button>
        )}
      </form>

      {loading ? <PageSpinner /> : (
        <div className="card p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="erp-table text-sm">
              <thead>
                <tr>
                  <th>أمر الإنتاج</th>
                  <th>المعدة</th>
                  <th>العميل / أمر البيع</th>
                  <th>الحالة</th>
                  <th className="text-center">الكمية</th>
                  <th className="text-center">WIP</th>
                  <th className="text-center">FG</th>
                  <th className="text-center">مُسلَّم</th>
                  <th className="text-center">الإنجاز</th>
                  <th>آخر تحديث</th>
                </tr>
              </thead>
              <tbody>
                {(rows || []).map(row => (
                  <PipelineRow
                    key={row.work_order_id}
                    row={row}
                    expanded={expandedId === row.work_order_id}
                    onToggle={() => setExpandedId(id => id === row.work_order_id ? null : row.work_order_id)}
                  />
                ))}
                {(!rows || rows.length === 0) && (
                  <tr><td colSpan={10} className="text-center text-erp-muted py-4">لا توجد أوامر إنتاج مطابقة</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
