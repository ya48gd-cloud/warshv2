/**
 * تفاصيل طلب موافقة + نموذج القرار (موافقة/رفض مع سبب).
 * الخادم هو المرجع في الصلاحيات؛ الواجهة تعتمد على can_decide لإظهار الأزرار فقط.
 */
import { useEffect, useRef, useState } from 'react'
import Modal, { ModalFooter } from '../../components/ui/Modal'
import { Textarea } from '../../components/ui/Field'
import { PageSpinner } from '../../components/ui/Spinner'
import { StatusBadge } from '../../components/ui/Badge'
import { useToast } from '../../store/toast'
import { useAuth } from '../../store/auth'
import api from '../../api/client'
import {
  APPROVAL_STATUS, APPROVAL_ACTION_NAV, actionIcon, actionLabel, emitApprovalsChanged,
  fmtDateTime, formatSnapshotValue, roleLabel, snapshotLabel,
} from '../../utils/approvals'

function KV({ title, data }) {
  const entries = Object.entries(data || {}).filter(([, v]) => v !== undefined)
  if (!entries.length) return null
  return (
    <div>
      <div className="text-xs font-semibold text-erp-muted" style={{ marginBottom: 4 }}>{title}</div>
      <div className="card p-0 overflow-hidden">
        <table className="erp-table text-sm">
          <tbody>
            {entries.map(([k, v]) => {
              const text = formatSnapshotValue(k, v)
              const isObj = v != null && typeof v === 'object'
              return (
                <tr key={k}>
                  <td className="text-xs text-erp-muted" style={{ width: '38%' }}>{snapshotLabel(k)}</td>
                  <td>{isObj
                    ? <pre dir="ltr" className="text-xs" style={{ margin: 0, whiteSpace: 'pre-wrap', textAlign: 'left' }}>{text}</pre>
                    : text}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function ApprovalDetailModal({ id, row, onClose, onDone, onNavigate, onCancelRequest }) {
  const toast = useToast()
  const { user } = useAuth()
  const [detail, setDetail] = useState(null)
  const [loadErr, setLoadErr] = useState(null)
  const [reason, setReason] = useState('')
  const [reasonErr, setReasonErr] = useState('')
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const aliveRef = useRef(true)

  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false } }, [])

  const load = async () => {
    setLoadErr(null)
    try {
      const d = await api.approvals.get(id)
      if (aliveRef.current) setDetail(d)
    } catch (e) {
      if (aliveRef.current) setLoadErr(e)
    }
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setDetail(null); setReason(''); setReasonErr(''); load() }, [id])

  const req = detail || row
  if (!req) return null

  const nav = APPROVAL_ACTION_NAV[req.action_key]
  const canDecide = !!req.can_decide && req.status === 'pending'
  const canCancel = (req.is_mine || user?.role === 'admin') && (req.status === 'pending' || req.status === 'approved')
  const snap = detail?.snapshot || null

  const decide = async (kind) => {
    if (busyRef.current) return
    const text = reason.trim()
    if (kind === 'reject' && text.length < 3) { setReasonErr('اكتب سبب الرفض (٣ أحرف على الأقل)'); return }
    setReasonErr('')
    busyRef.current = true; setBusy(true)
    try {
      if (kind === 'approve') {
        await api.approvals.approve(req.id, text)
        toast(`تمت الموافقة — يمكن لـ ${req.requested_by_name || 'مقدّم الطلب'} إعادة تنفيذ الإجراء الآن`)
      } else {
        await api.approvals.reject(req.id, text)
        toast('تم رفض الطلب')
      }
      emitApprovalsChanged()
      onDone?.()
      onClose?.()
    } catch (e) {
      toast(e.message || 'تعذّر تنفيذ القرار', 'error')
      if (e.network) return                      // نسيب المودال مفتوح عشان السبب ما يضيعش
      if (e.status === 404) { onDone?.(); onClose?.(); return }
      // 403/409/400: حالة الطلب اتغيّرت — حدّث القائمة والتفاصيل
      emitApprovalsChanged(); onDone?.(); load()
    } finally {
      busyRef.current = false
      if (aliveRef.current) setBusy(false)
    }
  }

  let note = null
  if (req.status === 'pending' && !canDecide) {
    note = req.is_mine
      ? 'لا يمكنك البت في طلب قدّمته بنفسك — بانتظار مستخدم آخر.'
      : `هذا الطلب يحتاج موافقة دور: ${req.required_role_label || roleLabel(req.required_role)}`
  }

  return (
    <Modal open onClose={onClose} size="lg" title={`${actionIcon(req.action_key)} طلب موافقة #${req.id}`}>
      <div className="flex flex-col gap-4 text-sm">
        {loadErr && !detail && (
          <div className="text-xs" style={{ color: 'var(--red)' }}>
            تعذّر تحميل كل التفاصيل: {loadErr.message}
            <button className="btn btn-sm text-xs" style={{ marginRight: 8 }} onClick={load}>إعادة المحاولة</button>
          </div>
        )}
        {!detail && !loadErr && <PageSpinner />}

        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
          <div><div className="text-xs text-erp-muted">الإجراء</div>
            <b>{actionLabel(req.action_key, req.action_label)}</b></div>
          <div><div className="text-xs text-erp-muted">المرجع</div>
            <span>{req.entity_label || (req.entity_id != null ? `#${req.entity_id}` : '—')}</span>
            {nav && (
              <button className="btn btn-sm text-xs" style={{ marginRight: 8 }}
                onClick={() => { onClose?.(); onNavigate?.(nav.page, nav.pageParams || null) }}>فتح</button>
            )}</div>
          <div><div className="text-xs text-erp-muted">الحالة</div>
            <StatusBadge map={APPROVAL_STATUS} value={req.status} /></div>
          <div><div className="text-xs text-erp-muted">الدور المطلوب</div>
            <b>{req.required_role_label || roleLabel(req.required_role)}</b></div>
          <div><div className="text-xs text-erp-muted">مقدّم الطلب</div>
            {req.requested_by_name || '—'} <span className="text-xs text-erp-muted">({fmtDateTime(req.requested_at)})</span></div>
          {req.decided_at && (
            <div><div className="text-xs text-erp-muted">القرار</div>
              {req.decided_by_name || '—'} <span className="text-xs text-erp-muted">({fmtDateTime(req.decided_at)})</span></div>
          )}
          {req.status === 'approved' && req.expires_at && (
            <div><div className="text-xs text-erp-muted">صالحة حتى</div>{fmtDateTime(req.expires_at)}</div>
          )}
          {req.consumed_at && (
            <div><div className="text-xs text-erp-muted">وقت التنفيذ</div>{fmtDateTime(req.consumed_at)}</div>
          )}
        </div>

        {req.summary && <div className="text-xs text-erp-muted">{req.summary}</div>}
        {req.decision_reason && (
          <div className="text-xs"><span className="text-erp-muted">سبب القرار: </span>{req.decision_reason}</div>
        )}
        {req.status === 'approved' && (
          <div className="text-xs" style={{ color: 'var(--teal)' }}>
            تمت الموافقة — أعد تنفيذ نفس الإجراء من شاشته مرة واحدة فقط.
          </div>
        )}

        {snap && (
          <>
            <KV title="المعايير" data={snap.metrics} />
            <KV title="بيانات الإجراء" data={snap.params} />
            <KV title="القاعدة المطبّقة" data={snap.rule} />
          </>
        )}

        {canDecide && (
          <Textarea label="السبب (اختياري عند الموافقة — مطلوب عند الرفض)" value={reason} error={reasonErr}
            onChange={e => { setReason(e.target.value); if (reasonErr) setReasonErr('') }} />
        )}
        {note && <div className="text-xs text-erp-muted" role="note">{note}</div>}
      </div>

      <ModalFooter>
        <button type="button" className="btn" onClick={onClose}>إغلاق</button>
        {canCancel && (
          <button type="button" className="btn" disabled={busy} onClick={() => onCancelRequest?.(req)}>إلغاء الطلب</button>
        )}
        {canDecide && (
          <>
            <button type="button" className="btn btn-danger" disabled={busy} onClick={() => decide('reject')}>❌ رفض</button>
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => decide('approve')}>
              {busy ? '…' : '✅ موافقة'}
            </button>
          </>
        )}
      </ModalFooter>
    </Modal>
  )
}
