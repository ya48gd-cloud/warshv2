/**
 * سجل التدقيق — من أضاف/عدّل/حذف إيه ومتى (للمدير فقط، قراءة فقط).
 * المصدر: GET /audit/meta (مرة واحدة) + GET /audit/logs (الجدول) + GET /audit/logs/{id} (التفاصيل).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import api from '../api/client'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import { PageSpinner } from '../components/ui/Spinner'
import AuditDetailModal from '../components/audit/AuditDetailModal'
import {
  ACTION_META, ENTITY_LABELS, actionInfo, buildAuditQuery, changedFieldsSummary,
  entityLabelOf, fmtAuditDateTime, normalizeListResponse, retentionLabel,
  userLabel, isSystemRow, validateAuditFilters,
} from '../utils/audit'

const EMPTY_FILTERS = { entity_type: '', entity_id: '', user_id: '', action: '', date_from: '', date_to: '', q: '' }
const LIMITS = [25, 50, 100]

const fromParams = (pp) => ({
  ...EMPTY_FILTERS,
  entity_type: pp?.entity_type ? String(pp.entity_type) : '',
  entity_id: pp?.entity_type && pp?.entity_id != null ? String(pp.entity_id) : '',
})

function ErrorBox({ error, onRetry }) {
  let icon = '⚠️', text = error?.message || 'تعذّر تحميل البيانات'
  if (error?.status === 403) { icon = '🔒'; text = 'ليس لديك صلاحية لعرض سجل التدقيق (للمدير فقط)' }
  else if (error?.network) { icon = '📡'; text = 'تعذّر الاتصال بالخادم — تحقق من الشبكة' }
  return (
    <div className="card" style={{ textAlign: 'center', padding: '40px 24px' }} role="alert">
      <div style={{ fontSize: 36, marginBottom: 8 }}>{icon}</div>
      <div className="text-sm" style={{ marginBottom: 12 }}>{text}</div>
      {onRetry && error?.status !== 403 && <button className="btn btn-sm" onClick={onRetry}>إعادة المحاولة</button>}
    </div>
  )
}

export default function AuditLog({ pageParams }) {
  const { user } = useAuth()
  const toast = useToast()
  const isAdmin = user?.role === 'admin'

  const [meta, setMeta] = useState(null)
  const [draft, setDraft] = useState(() => fromParams(pageParams))
  const [applied, setApplied] = useState(() => fromParams(pageParams))
  const [limit, setLimit] = useState(50)
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tick, setTick] = useState(0)
  const [selected, setSelected] = useState(null)
  const aliveRef = useRef(true)
  const seqRef = useRef(0)

  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false } }, [])

  // بيانات الفلاتر من الخادم — الفشل غير قاتل (نرجع للقوائم الثابتة)
  useEffect(() => {
    if (!isAdmin) return
    api.audit.meta()
      .then(m => { if (aliveRef.current) setMeta(m) })
      .catch(() => { /* fallback ثابت */ })
  }, [isAdmin])

  // الانتقال لنفس الصفحة بمعاملات جديدة (deep link من سجل كيان)
  const firstParamsRef = useRef(true)
  useEffect(() => {
    if (firstParamsRef.current) { firstParamsRef.current = false; return }   // الحالة الأولية جاهزة من useState
    if (!pageParams) return
    const f = fromParams(pageParams)
    setDraft(f); setApplied(f); setOffset(0)
  }, [pageParams])

  useEffect(() => {
    if (!isAdmin) return
    const seq = ++seqRef.current
    setLoading(true)
    api.audit.list(buildAuditQuery(applied, { limit, offset }))
      .then(r => {
        if (!aliveRef.current || seq !== seqRef.current) return
        const nd = normalizeListResponse(r, limit, offset)
        // الصفحة الحالية فاضية بعد ما التنظيف (retention) شال صفوف — نرجع لآخر صفحة
        if (nd.items.length === 0 && nd.total > 0 && offset >= nd.total) {
          setOffset(Math.floor((nd.total - 1) / limit) * limit)
          return
        }
        setData(nd); setError(null)
      })
      .catch(e => {
        if (!aliveRef.current || seq !== seqRef.current) return
        setError(e); setData(null)
      })
      .finally(() => {
        if (aliveRef.current && seq === seqRef.current) setLoading(false)
      })
  }, [isAdmin, applied, limit, offset, tick])

  const actionOptions = useMemo(() => (
    meta?.actions?.length
      ? meta.actions
      : Object.entries(ACTION_META).map(([value, m]) => ({ value, label: m.label }))
  ), [meta])
  const entityOptions = useMemo(() => (
    meta?.entity_types?.length
      ? meta.entity_types
      : Object.entries(ENTITY_LABELS).map(([value, label]) => ({ value, label }))
  ), [meta])
  const userOptions = meta?.users || []

  const setField = (k, v) => setDraft(d => (
    k === 'entity_type' ? { ...d, entity_type: v, entity_id: '' } : { ...d, [k]: v }
  ))

  const applyFilters = (e) => {
    e?.preventDefault()
    const msg = validateAuditFilters(draft)
    if (msg) { toast(msg, 'error'); return }
    setApplied({ ...draft }); setOffset(0)
  }
  const resetFilters = () => { setDraft(EMPTY_FILTERS); setApplied(EMPTY_FILTERS); setOffset(0) }

  if (!isAdmin) return <ErrorBox error={{ status: 403 }} />

  const items = data?.items || []
  const total = data?.total || 0
  const page = Math.floor(offset / limit) + 1
  const pages = Math.max(1, Math.ceil(total / limit))
  const retention = retentionLabel(meta?.retention)
  const hasFilters = Object.values(applied).some(v => String(v ?? '').trim() !== '')

  return (
    <div className="flex flex-col gap-3">
      <form className="card" onSubmit={applyFilters} style={{ padding: 12 }}>
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
          <div className="flex flex-col gap-1">
            <label className="field-label" htmlFor="aud-entity">الكيان</label>
            <select id="aud-entity" className="field-select" value={draft.entity_type}
              onChange={e => setField('entity_type', e.target.value)}>
              <option value="">الكل</option>
              {entityOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="field-label" htmlFor="aud-entity-id">رقم الكيان</label>
            <input id="aud-entity-id" className="field-input" type="number" min="1" step="1" inputMode="numeric"
              value={draft.entity_id} disabled={!draft.entity_type}
              placeholder={draft.entity_type ? 'مثال: 12' : 'اختر الكيان أولاً'}
              onChange={e => setField('entity_id', e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="field-label" htmlFor="aud-user">المستخدم</label>
            <select id="aud-user" className="field-select" value={draft.user_id}
              onChange={e => setField('user_id', e.target.value)}>
              <option value="">الكل</option>
              {userOptions.map(u => (
                <option key={u.id} value={u.id}>{u.full_name ? `${u.full_name} (${u.username})` : u.username}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="field-label" htmlFor="aud-action">الإجراء</label>
            <select id="aud-action" className="field-select" value={draft.action}
              onChange={e => setField('action', e.target.value)}>
              <option value="">الكل</option>
              {actionOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="field-label" htmlFor="aud-from">من تاريخ</label>
            <input id="aud-from" className="field-input" type="date" value={draft.date_from}
              max={draft.date_to || undefined} onChange={e => setField('date_from', e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="field-label" htmlFor="aud-to">إلى تاريخ</label>
            <input id="aud-to" className="field-input" type="date" value={draft.date_to}
              min={draft.date_from || undefined} onChange={e => setField('date_to', e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="field-label" htmlFor="aud-q">بحث (مرجع / مستخدم)</label>
            <input id="aud-q" className="field-input" type="search" value={draft.q} placeholder="ابحث…"
              onChange={e => setField('q', e.target.value)} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2" style={{ marginTop: 12 }}>
          <button type="submit" className="btn btn-primary btn-sm">تطبيق الفلاتر</button>
          <button type="button" className="btn btn-sm" onClick={resetFilters}>إعادة ضبط</button>
          <button type="button" className="btn btn-sm" title="تحديث" onClick={() => setTick(t => t + 1)}>↻ تحديث</button>
          <span className="text-xs text-erp-muted" style={{ marginRight: 'auto' }}>
            {retention}{retention && ' — '}السجل للقراءة فقط ولا يمكن تعديله أو حذفه
          </span>
        </div>
      </form>

      {loading && !data && !error && <PageSpinner />}
      {error && <ErrorBox error={error} onRetry={() => setTick(t => t + 1)} />}

      {data && (
        <div className="card p-0 overflow-hidden" style={{ opacity: loading ? 0.6 : 1 }}>
          <div className="overflow-x-auto">
            <table className="erp-table text-sm">
              <thead>
                <tr>
                  <th>التاريخ والوقت</th><th>المستخدم</th><th>الإجراء</th><th>الكيان</th>
                  <th>الحقول المتغيّرة</th><th>IP</th><th></th>
                </tr>
              </thead>
              <tbody>
                {items.map(r => {
                  const act = actionInfo(r.action, r.action_label)
                  const fields = changedFieldsSummary(r.changed_fields)
                  const sys = isSystemRow(r)
                  return (
                    <tr key={r.id} tabIndex={0} style={{ cursor: 'pointer' }}
                      onClick={() => setSelected(r)}
                      onKeyDown={e => { if (e.key === 'Enter') setSelected(r) }}>
                      <td className="text-xs" style={{ whiteSpace: 'nowrap' }}>{fmtAuditDateTime(r.created_at)}</td>
                      <td className="text-xs" style={sys ? { color: 'var(--muted)', fontStyle: 'italic' } : undefined}>
                        {userLabel(r)}
                      </td>
                      <td><span className={`badge ${act.badge}`}>{act.label}</span></td>
                      <td className="text-xs">
                        <span>{entityLabelOf(r)}</span>
                        {(r.entity_ref || r.entity_id != null) && (
                          <span className="text-erp-muted"> · {r.entity_ref || `#${r.entity_id}`}</span>
                        )}
                      </td>
                      <td className="text-xs text-erp-muted">{fields || '—'}</td>
                      <td className="text-xs font-mono" dir="ltr" style={{ textAlign: 'right' }}>{r.ip || '—'}</td>
                      <td>
                        <button type="button" className="btn btn-sm text-xs" title="التفاصيل"
                          onClick={e => { e.stopPropagation(); setSelected(r) }}>👁</button>
                      </td>
                    </tr>
                  )
                })}
                {items.length === 0 && (
                  <tr><td colSpan={7} className="text-center text-erp-muted" style={{ padding: '28px 8px' }}>
                    {hasFilters ? 'لا توجد نتائج مطابقة للفلاتر' : 'لا توجد عمليات مسجّلة بعد — سيظهر هنا كل ما يضيفه أو يعدّله أو يحذفه المستخدمون'}
                    {hasFilters && (
                      <div style={{ marginTop: 8 }}>
                        <button type="button" className="btn btn-sm" onClick={resetFilters}>مسح الفلاتر</button>
                      </div>
                    )}
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t border-erp-border" style={{ padding: '8px 12px' }}>
            <span className="text-xs text-erp-muted">الإجمالي: {total}</span>
            <div className="flex items-center gap-1" style={{ marginRight: 'auto' }}>
              <label className="text-xs text-erp-muted" htmlFor="aud-limit">لكل صفحة</label>
              <select id="aud-limit" className="field-select" style={{ width: 'auto', padding: '2px 6px' }}
                value={limit} onChange={e => { setLimit(Number(e.target.value)); setOffset(0) }}>
                {LIMITS.map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            <button className="btn btn-sm text-xs" disabled={offset <= 0 || loading}
              onClick={() => setOffset(Math.max(0, offset - limit))}>السابق</button>
            <span className="text-xs">صفحة {page} من {pages}</span>
            <button className="btn btn-sm text-xs" disabled={offset + limit >= total || loading}
              onClick={() => setOffset(offset + limit)}>التالي</button>
          </div>
        </div>
      )}

      {selected && (
        <AuditDetailModal id={selected.id} row={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}
