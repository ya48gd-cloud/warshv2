/**
 * تفاصيل عملية في سجل التدقيق — يجلب GET /audit/logs/{id} في كل مرة فتح.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Modal, { ModalFooter } from '../ui/Modal'
import Spinner from '../ui/Spinner'
import api from '../../api/client'
import AuditDiff from './AuditDiff'
import {
  actionInfo, entityLabelOf, fmtAuditDateTime, formatValue, reasonLabel, userLabel,
} from '../../utils/audit'

const EXTRA_LABELS = {
  reason: 'سبب الفشل',
  username_attempted: 'اسم المستخدم المُجرَّب',
  deleted: 'عدد السجلات المحذوفة',
  cutoff: 'تاريخ القطع',
  retention_days: 'مدة الاحتفاظ (يوم)',
}

function extraValue(k, v) {
  if (k === 'reason') return reasonLabel(v)
  if (k === 'cutoff') return fmtAuditDateTime(v)
  return formatValue(v)
}

function Meta({ label, children, mono }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div className="text-xs text-erp-muted">{label}</div>
      <div className={`text-sm${mono ? ' font-mono' : ''}`} dir={mono ? 'ltr' : undefined}
        style={{ wordBreak: 'break-word', textAlign: mono ? 'right' : undefined }}>{children || '—'}</div>
    </div>
  )
}

export default function AuditDetailModal({ id, row, onClose }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const aliveRef = useRef(true)
  const seqRef = useRef(0)

  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false } }, [])

  const load = useCallback(async () => {
    const seq = ++seqRef.current
    setLoading(true); setError(null)
    try {
      const d = await api.audit.get(id)
      if (!aliveRef.current || seq !== seqRef.current) return
      setDetail(d)
    } catch (e) {
      if (!aliveRef.current || seq !== seqRef.current) return
      setError(e)
    } finally {
      if (aliveRef.current && seq === seqRef.current) setLoading(false)
    }
  }, [id])

  useEffect(() => { setDetail(null); load() }, [load])

  const r = detail || row || {}
  const act = actionInfo(r.action, r.action_label)
  const extraEntries = detail?.extra && typeof detail.extra === 'object'
    ? Object.entries(detail.extra).filter(([, v]) => v !== undefined) : []
  const notFound = error?.status === 404

  return (
    <Modal open onClose={onClose} title={`تفاصيل العملية #${id}`} size="lg">
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`badge ${act.badge}`}>{act.label}</span>
          <span className="text-sm">{entityLabelOf(r)}</span>
          {r.entity_ref && <span className="text-sm font-semibold">{r.entity_ref}</span>}
          {r.entity_id != null && <span className="text-xs font-mono text-erp-muted">#{r.entity_id}</span>}
        </div>

        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' }}>
          <Meta label="التاريخ والوقت">{fmtAuditDateTime(r.created_at)}</Meta>
          <Meta label={r.action === 'login_failed' ? 'اسم المستخدم المُجرَّب' : 'المستخدم'}>{userLabel(r)}</Meta>
          <Meta label="عنوان IP" mono>{r.ip}</Meta>
          <Meta label="المسار" mono>{r.path}</Meta>
          <Meta label="رقم الطلب" mono>{r.request_id}</Meta>
        </div>

        {r.summary && (
          <div className="text-sm" style={{ wordBreak: 'break-word' }}>{r.summary}</div>
        )}

        {loading && !detail && (
          <div className="flex items-center justify-center" style={{ padding: 24 }}><Spinner size={26} /></div>
        )}

        {error && !detail && (
          <div className="card" style={{ textAlign: 'center', padding: '24px 16px' }} role="alert">
            <div className="text-sm" style={{ marginBottom: 10 }}>
              {notFound ? 'العملية غير موجودة' : (error.message || 'تعذّر تحميل التفاصيل')}
            </div>
            {!notFound && <button className="btn btn-sm" onClick={load}>إعادة المحاولة</button>}
          </div>
        )}

        {detail && extraEntries.length > 0 && (
          <div>
            <div className="text-xs font-semibold text-erp-muted" style={{ marginBottom: 4 }}>معلومات إضافية</div>
            <div className="card p-0 overflow-hidden">
              <table className="erp-table text-sm">
                <tbody>
                  {extraEntries.map(([k, v]) => (
                    <tr key={k}>
                      <td className="text-xs text-erp-muted" style={{ width: '38%' }}>{EXTRA_LABELS[k] || k}</td>
                      <td dir="auto">{extraValue(k, v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {detail && detail.changes && (
          <div>
            <div className="text-xs font-semibold text-erp-muted" style={{ marginBottom: 4 }}>التغييرات (قبل / بعد)</div>
            <AuditDiff changes={detail.changes} />
          </div>
        )}
        {detail && !detail.changes && extraEntries.length === 0 && (
          <div className="text-xs text-erp-muted">لا توجد تفاصيل تغييرات لهذه العملية</div>
        )}
      </div>
      <ModalFooter>
        <button className="btn" onClick={onClose}>إغلاق</button>
      </ModalFooter>
    </Modal>
  )
}
