/**
 * طابور الفاتورة الإلكترونية — كل المستندات التي أُرسلت (أو تنتظر الإرسال) لمصلحة الضرائب.
 * GET /einvoice/submissions (admin/accountant) + POST /einvoice/queue/run (admin فقط، ورشتك فقط).
 * التكامل غير مُجرَّب على بيئة مصلحة الضرائب الفعلية.
 */
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import api from '../api/client'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import { egp } from '../utils/fmt'
import SearchInput from '../components/ui/SearchInput'
import ActionMenu from '../components/ui/ActionMenu'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'
import EtaBadge from '../components/einvoice/EtaBadge'
import useEta from '../components/einvoice/useEta'
import { etaErrorsToList, etaKindOf, etaKindLabel, fmtEtaDateTime, ETA_IN_PROGRESS, ETA_ENV_LABEL } from '../utils/einvoice'

const POLL_MS = 15000
const STATUS_FILTERS = [
  ['', 'كل الحالات'], ['queued', 'في الانتظار'], ['submitting', 'قيد الإرسال'], ['submitted', 'قيد المعالجة'],
  ['valid', 'مقبولة'], ['invalid', 'غير صالحة'], ['rejected', 'مرفوضة'], ['failed', 'فشل الإرسال'], ['cancelled', 'ملغاة'],
]

function Kpi({ label, value, color }) {
  return (
    <div className="card text-center py-3">
      <div className="text-xs text-erp-muted mb-1">{label}</div>
      <div className="text-lg font-bold" style={{ color }}>{value}</div>
    </div>
  )
}

export default function EInvoiceQueue({ onNavigate }) {
  const { user: me, canWrite } = useAuth()
  const toast = useToast()
  const isAdmin = me?.role === 'admin'
  const allowed = canWrite('sales')        // admin + accountant

  const [filters, setFilters] = useState({ status: '', kind: '', q: '' })
  const [qDebounced, setQDebounced] = useState('')
  const [state, setState] = useState({ data: null, loading: true, error: null, network: false, forbidden: false })
  const [confirmRun, setConfirmRun] = useState(false)
  const [running, setRunning] = useState(false)
  const alive = useRef(true)
  const runningRef = useRef(false)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => { const t = setTimeout(() => setQDebounced(filters.q.trim()), 400); return () => clearTimeout(t) }, [filters.q])

  const load = useCallback(async (silent = false) => {
    if (!allowed) return
    if (!silent) setState(s => ({ ...s, loading: !s.data, error: null }))
    try {
      // doc_type نفلتره محلياً (مفردات الفلتر في الخادم غير مؤكدة)؛ status و q في الخادم
      const data = await api.einvoice.submissions({ status: filters.status, q: qDebounced, limit: 200 })
      if (alive.current) setState({ data, loading: false, error: null, network: false, forbidden: false })
    } catch (e) {
      if (!alive.current) return
      setState(s => ({
        data: s.data, loading: false,
        error: e?.message || 'تعذّر تحميل الطابور',
        network: !!e?.network, forbidden: e?.status === 403,
      }))
    }
  }, [allowed, filters.status, qDebounced])

  useEffect(() => { load() }, [load])

  const eta = useEta({ kind: 'invoice', reload: () => load(true), onNavigate })

  const items = useMemo(() => {
    const all = Array.isArray(state.data?.items) ? state.data.items : []
    return filters.kind ? all.filter(it => etaKindOf(it.doc_type) === filters.kind) : all
  }, [state.data, filters.kind])

  const hasPending = (Array.isArray(state.data?.items) ? state.data.items : []).some(it => ETA_IN_PROGRESS.includes(it.status))

  // تحديث تلقائي كل 15 ثانية طالما فيه مستند قيد الإرسال والتبويب ظاهر
  useEffect(() => {
    if (!hasPending) return undefined
    const t = setInterval(() => { if (!document.hidden) load(true) }, POLL_MS)
    return () => clearInterval(t)
  }, [hasPending, load])

  const runQueue = async () => {
    if (runningRef.current) return
    runningRef.current = true
    setRunning(true)
    try {
      const r = await api.einvoice.runQueue()
      toast(`تم تشغيل الطابور — معالَج: ${r?.processed ?? 0} | مقبول: ${r?.valid ?? 0} | مرفوض: ${r?.rejected ?? 0} | فاشل: ${r?.failed ?? 0}`)
    } catch (e) {
      toast(e?.message || 'تعذّر تشغيل الطابور', 'error')
    } finally {
      runningRef.current = false
      if (alive.current) setRunning(false)
      load(true)
    }
  }

  if (!allowed) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-erp-muted">
        <span className="text-5xl">🔒</span>
        <p className="font-medium">ليس لديك صلاحية لعرض طابور الفاتورة الإلكترونية</p>
      </div>
    )
  }

  // التكامل غير مفعّل (المدير فقط يصل لهذا التبويب في هذه الحالة)
  if (!eta.loadingStatus && !eta.enabled) {
    return (
      <div className="card flex flex-col items-center gap-3 py-10 text-center">
        <span className="text-4xl">🇪🇬</span>
        <div className="font-semibold">الفاتورة الإلكترونية غير مفعّلة</div>
        <div className="text-sm text-erp-muted">فعّل التكامل وأدخل بيانات التسجيل الضريبي من الإعدادات لتبدأ الإرسال.</div>
        {isAdmin && onNavigate && (
          <button className="btn btn-primary" onClick={() => onNavigate('users', { tab: 'einvoice' })}>
            الذهاب لإعدادات الفاتورة الإلكترونية
          </button>
        )}
      </div>
    )
  }

  if (state.loading && !state.data) return <PageSpinner />

  const sm = state.data?.summary || {}
  const n = (k) => Number(sm[k] || 0)
  const failedCount = n('failed') + n('rejected') + n('invalid')

  return (
    <div className="flex flex-col gap-4">
      {eta.environment && (
        <div className="px-3 py-2 rounded-erp text-xs"
             style={eta.environment === 'prod'
               ? { background: 'var(--red-lt)', color: 'var(--red)' }
               : { background: 'var(--amber-lt)', color: 'var(--amber)' }}>
          {eta.environment === 'prod' ? 'البيئة الإنتاجية الفعلية' : `${ETA_ENV_LABEL[eta.environment] || eta.environment} — لن يُعتد بالمستندات ضريبياً`}
          {eta.signerMode === 'mock' && ' — التوقيع في وضع المحاكاة'}
        </div>
      )}

      {state.error && (
        <div className="flex items-center justify-between gap-3 text-sm px-3 py-2.5 rounded-erp"
             style={{ background: 'var(--red-lt)', color: 'var(--red)' }}>
          <span>
            {state.forbidden ? 'ليس لديك صلاحية لعرض هذه البيانات'
              : state.network ? `تعذر الاتصال بالخادم${state.data ? ' — المعروض آخر بيانات تم تحميلها' : ''}`
              : `${state.error}${state.data ? ' — المعروض آخر بيانات تم تحميلها' : ''}`}
          </span>
          {!state.forbidden && <button className="btn btn-sm" onClick={() => load()}>إعادة المحاولة</button>}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi label="في الانتظار" value={n('queued')} color="var(--blue)" />
        <Kpi label="قيد المعالجة" value={n('submitting') + n('submitted')} color="var(--amber)" />
        <Kpi label="مقبولة" value={n('valid')} color="var(--green)" />
        <Kpi label="مرفوضة / فاشلة" value={failedCount} color="var(--red)" />
      </div>

      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div style={{ flex: '1 1 200px' }}>
          <SearchInput value={filters.q} onChange={q => setFilters(f => ({ ...f, q }))} placeholder="بحث بالكود أو العميل…" />
        </div>
        <select className="field-select" style={{ width: 'auto' }} value={filters.kind} aria-label="نوع المستند"
                onChange={e => setFilters(f => ({ ...f, kind: e.target.value }))}>
          <option value="">الفواتير والإشعارات</option>
          <option value="invoice">الفواتير</option>
          <option value="credit_note">الإشعارات الدائنة</option>
        </select>
        <select className="field-select" style={{ width: 'auto' }} value={filters.status} aria-label="الحالة"
                onChange={e => setFilters(f => ({ ...f, status: e.target.value }))}>
          {STATUS_FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <button className="btn btn-sm" onClick={() => load()} disabled={state.loading}>↻ تحديث</button>
        {isAdmin && (
          <button className="btn btn-sm btn-primary" disabled={running} onClick={() => setConfirmRun(true)}
                  style={{ opacity: running ? 0.5 : 1 }}
                  title="يعالج المستندات المستحقة لورشتك الآن">
            {running ? '…' : '▶ تشغيل الطابور الآن'}
          </button>
        )}
      </div>

      <div className="card p-0" style={{ overflowX: 'auto', minHeight: 240 }}>
        <table className="erp-table" style={{ minWidth: 820 }}>
          <thead>
            <tr>
              <th>النوع</th><th>الكود</th><th>العميل</th><th>الإجمالي</th><th>حالة الضرائب</th>
              <th>المحاولات</th><th>آخر محاولة / التالية</th><th>الأخطاء</th><th style={{ width: 50 }}></th>
            </tr>
          </thead>
          <tbody>
            {!items.length && (
              <tr><td colSpan={9} className="text-center text-erp-muted py-10">
                {filters.status || filters.kind || qDebounced ? 'لا توجد نتائج مطابقة' : 'لا توجد مستندات مُرسلة بعد'}
              </td></tr>
            )}
            {items.map(it => {
              const k = etaKindOf(it.doc_type)
              const errs = etaErrorsToList(it.errors)
              const adapted = { id: it.doc_id, code: it.code, eta_status: it.status, eta_errors: it.errors, eta_submitted_at: it.submitted_at }
              return (
                <tr key={`${k}-${it.doc_id}`}>
                  <td className="text-xs">{etaKindLabel(k)}</td>
                  <td className="font-mono text-xs font-medium">
                    {k === 'invoice' && onNavigate
                      ? <button type="button" className="font-mono" style={{ color: 'var(--teal)', background: 'none', border: 'none', cursor: 'pointer' }}
                                onClick={() => onNavigate('invoices', { highlight: it.doc_id })}>{it.code}</button>
                      : it.code}
                  </td>
                  <td className="font-medium">{it.customer_name || '—'}</td>
                  <td className="font-semibold">{egp(it.total)}</td>
                  <td><EtaBadge doc={adapted} onClick={() => eta.openState(adapted, k)} /></td>
                  <td className="text-center">{it.attempts ?? 0}</td>
                  <td className="text-xs text-erp-muted">
                    <div>{fmtEtaDateTime(it.last_attempt_at)}</div>
                    {it.next_attempt_at && ['queued', 'failed'].includes(it.status) && (
                      <div style={{ color: 'var(--amber)' }}>التالية: {fmtEtaDateTime(it.next_attempt_at)}</div>
                    )}
                  </td>
                  <td className="text-xs" style={{ maxWidth: 220 }}>
                    {errs.length ? (
                      <button type="button" style={{ color: 'var(--red)', background: 'none', border: 'none', cursor: 'pointer', textAlign: 'right' }}
                              onClick={() => eta.openState(adapted, k)}
                              title={errs.map(e => e.message || e.code).join('\n')}>
                        {errs[0].message || errs[0].code}{errs.length > 1 ? ` (+${errs.length - 1})` : ''}
                      </button>
                    ) : '—'}
                  </td>
                  <td><ActionMenu items={eta.menuItems(adapted, { kind: k })} /></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {Number(state.data?.total || 0) > (state.data?.items?.length || 0) && (
        <div className="text-xs text-erp-muted text-center">
          يُعرض أحدث {state.data.items.length} من {state.data.total} — استخدم البحث أو الفلاتر لتضييق النتائج.
        </div>
      )}
      {hasPending && <div className="text-xs text-erp-muted text-center">يتم التحديث تلقائياً كل 15 ثانية أثناء وجود مستندات قيد المعالجة.</div>}

      <ConfirmDialog
        open={confirmRun} title="تشغيل الطابور الآن"
        message={`سيُعالَج حتى 50 مستنداً مستحقاً من ورشتك ويُرسَل لمصلحة الضرائب${eta.environment === 'prod' ? ' (البيئة الإنتاجية الفعلية)' : ''}. متابعة؟`}
        confirmLabel="تشغيل"
        onCancel={() => setConfirmRun(false)}
        onConfirm={runQueue}
      />
      {eta.element}
    </div>
  )
}
