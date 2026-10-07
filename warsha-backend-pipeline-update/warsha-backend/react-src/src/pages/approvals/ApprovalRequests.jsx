/**
 * قائمة طلبات الموافقة — scope='inbox' (طلبات أقدر أبت فيها) أو 'mine' (طلباتي).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import api from '../../api/client'
import { useToast } from '../../store/toast'
import { PageSpinner } from '../../components/ui/Spinner'
import { StatusBadge } from '../../components/ui/Badge'
import SearchInput from '../../components/ui/SearchInput'
import ConfirmDialog from '../../components/ui/ConfirmDialog'
import ApprovalDetailModal from './ApprovalDetailModal'
import {
  APPROVAL_STATUS, APPROVAL_STATUS_FILTERS, APPROVAL_ACTION_NAV, actionIcon, actionLabel,
  emitApprovalsChanged, fmtDateTime, requestValueLabel, roleLabel,
} from '../../utils/approvals'

const POLL_MS = 60_000

export function ErrorBox({ error, onRetry }) {
  let icon = '⚠️', text = error?.message || 'تعذّر تحميل البيانات'
  if (error?.status === 403) { icon = '🔒'; text = 'ليس لديك صلاحية لعرض هذه الصفحة' }
  else if (error?.network) { icon = '📡'; text = 'تعذّر الاتصال بالخادم — تحقق من الشبكة' }
  else if (error?.status === 404) { icon = '🧩'; text = 'ميزة الموافقات غير مفعّلة على هذا الخادم' }
  return (
    <div className="card" style={{ textAlign: 'center', padding: '40px 24px' }} role="alert">
      <div style={{ fontSize: 36, marginBottom: 8 }}>{icon}</div>
      <div className="text-sm" style={{ marginBottom: 12 }}>{text}</div>
      {onRetry && error?.status !== 403 && (
        <button className="btn btn-sm" onClick={onRetry}>إعادة المحاولة</button>
      )}
    </div>
  )
}

export default function ApprovalRequests({ scope, highlightId, onNavigate }) {
  const toast = useToast()
  const [status, setStatus] = useState(scope === 'inbox' ? 'pending' : 'all')
  const [q, setQ] = useState('')
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState(null)
  const [confirmCancel, setConfirmCancel] = useState(null)
  const [cancelling, setCancelling] = useState(false)
  const aliveRef = useRef(true)
  const seqRef = useRef(0)
  const hlRef = useRef(null)

  const load = useCallback(async (silent = false) => {
    const seq = ++seqRef.current
    if (!silent) setLoading(true)
    try {
      const r = await api.approvals.requests({ scope, status, limit: 200 })
      if (!aliveRef.current || seq !== seqRef.current) return
      setRows(r); setError(null)
    } catch (e) {
      if (!aliveRef.current || seq !== seqRef.current) return
      if (!silent) { setError(e); setRows(null) }   // التحديث الصامت يحتفظ بآخر بيانات
    } finally {
      if (aliveRef.current && seq === seqRef.current && !silent) setLoading(false)
    }
  }, [scope, status])

  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false } }, [])
  useEffect(() => { load(false) }, [load])
  useEffect(() => {
    const t = setInterval(() => { if (!document.hidden) load(true) }, POLL_MS)
    const h = () => load(true)
    window.addEventListener('erp:approvals-changed', h)
    return () => { clearInterval(t); window.removeEventListener('erp:approvals-changed', h) }
  }, [load])

  useEffect(() => {
    if (highlightId != null && rows && hlRef.current) {
      hlRef.current.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
    }
  }, [highlightId, rows])

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase()
    if (!term) return rows || []
    return (rows || []).filter(r =>
      [r.entity_label, r.requested_by_name, actionLabel(r.action_key, r.action_label), r.summary]
        .filter(Boolean).join(' ').toLowerCase().includes(term))
  }, [rows, q])

  const doCancel = async () => {
    const req = confirmCancel
    if (!req || cancelling) return
    setCancelling(true)
    try {
      await api.approvals.cancel(req.id)
      toast('تم إلغاء الطلب')
    } catch (e) {
      toast(e.message || 'تعذّر إلغاء الطلب', 'error')
    } finally {
      setCancelling(false); setConfirmCancel(null)
      emitApprovalsChanged(); load(true)
    }
  }

  const selectedRow = (rows || []).find(r => r.id === selectedId) || null

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div style={{ minWidth: 220, flex: '1 1 220px', maxWidth: 320 }}>
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالمرجع أو مقدّم الطلب…" />
        </div>
        <div className="flex flex-wrap gap-1">
          {APPROVAL_STATUS_FILTERS.map(f => (
            <button key={f.key} type="button"
              className={`btn btn-sm text-xs${status === f.key ? ' btn-primary' : ''}`}
              onClick={() => setStatus(f.key)}>{f.label}</button>
          ))}
        </div>
        <button className="btn btn-sm text-xs" onClick={() => load(false)} title="تحديث">↻</button>
      </div>

      {loading && !rows && !error && <PageSpinner />}
      {error && !rows && <ErrorBox error={error} onRetry={() => load(false)} />}

      {rows && (
        <div className="card p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="erp-table text-sm">
              <thead>
                <tr>
                  <th>#</th><th>الإجراء</th><th>المرجع</th><th>المبلغ/القيمة</th>
                  <th>{scope === 'inbox' ? 'مقدّم الطلب' : 'الدور المطلوب'}</th>
                  <th>التاريخ</th><th>الحالة</th><th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(r => {
                  const nav = APPROVAL_ACTION_NAV[r.action_key]
                  const hl = highlightId != null && Number(highlightId) === r.id
                  return (
                    <tr key={r.id} ref={hl ? hlRef : undefined} className={hl ? 'row-highlight' : undefined}>
                      <td className="font-mono text-xs">{r.id}</td>
                      <td>{actionIcon(r.action_key)} {actionLabel(r.action_key, r.action_label)}</td>
                      <td>
                        <span className="text-xs">{r.entity_label || (r.entity_id != null ? `#${r.entity_id}` : '—')}</span>
                        {nav && (
                          <button className="btn btn-sm text-xs" style={{ marginRight: 6 }}
                            onClick={() => onNavigate?.(nav.page, nav.pageParams || null)}>فتح</button>
                        )}
                      </td>
                      <td className="font-mono text-xs">{requestValueLabel(r)}</td>
                      <td className="text-xs">
                        {scope === 'inbox'
                          ? (r.requested_by_name || '—')
                          : (r.required_role_label || roleLabel(r.required_role))}
                      </td>
                      <td className="text-xs">{fmtDateTime(r.requested_at)}</td>
                      <td>
                        <StatusBadge map={APPROVAL_STATUS} value={r.status} />
                        {r.status === 'approved' && r.expires_at && (
                          <div className="text-xs text-erp-muted">صالحة حتى {fmtDateTime(r.expires_at)}</div>
                        )}
                        {r.status === 'rejected' && r.decision_reason && (
                          <div className="text-xs text-erp-muted">{r.decision_reason}</div>
                        )}
                      </td>
                      <td>
                        <div className="flex flex-wrap gap-1">
                          <button className="btn btn-sm text-xs" title="التفاصيل" onClick={() => setSelectedId(r.id)}>👁</button>
                          {scope === 'inbox' && r.can_decide && r.status === 'pending' && (
                            <button className="btn btn-sm btn-primary text-xs" onClick={() => setSelectedId(r.id)}>✅ / ❌ قرار</button>
                          )}
                          {scope === 'mine' && r.status === 'approved' && nav && (
                            <button className="btn btn-sm btn-primary text-xs"
                              title="تمت الموافقة — أعد تنفيذ الإجراء مرة واحدة"
                              onClick={() => onNavigate?.(nav.page, nav.pageParams || null)}>🔗 أعد التنفيذ</button>
                          )}
                          {r.is_mine && (r.status === 'pending' || r.status === 'approved') && (
                            <button className="btn btn-sm text-xs" onClick={() => setConfirmCancel(r)}>إلغاء الطلب</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
                {filtered.length === 0 && (
                  <tr><td colSpan={8} className="text-center text-erp-muted" style={{ padding: '28px 8px' }}>
                    {q.trim()
                      ? 'لا توجد نتائج مطابقة للبحث'
                      : scope === 'inbox'
                        ? (status === 'pending' ? 'لا توجد طلبات موافقة معلّقة 🎉' : 'لا توجد طلبات بهذه الحالة')
                        : 'لم تقدّم أي طلبات موافقة'}
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {selectedId != null && (
        <ApprovalDetailModal
          id={selectedId}
          row={selectedRow}
          onClose={() => setSelectedId(null)}
          onDone={() => load(true)}
          onNavigate={onNavigate}
          onCancelRequest={(req) => { setSelectedId(null); setConfirmCancel(req) }}
        />
      )}

      <ConfirmDialog
        open={!!confirmCancel}
        title="إلغاء طلب الموافقة"
        message={confirmCancel ? `هل تريد إلغاء الطلب رقم ${confirmCancel.id}؟ لن يمكنك استخدام هذه الموافقة بعد الإلغاء.` : ''}
        confirmLabel="إلغاء الطلب"
        danger
        onConfirm={doCancel}
        onCancel={() => setConfirmCancel(null)}
      />
    </div>
  )
}
