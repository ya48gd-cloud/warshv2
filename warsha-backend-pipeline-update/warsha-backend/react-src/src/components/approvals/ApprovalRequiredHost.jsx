/**
 * نافذة عامة "هذا الإجراء يحتاج موافقة" — تُعرض عند أي رد 428 approval_required
 * من أي شاشة (client.js يبعت الحدث erp:approval-required). تتركّب مرة واحدة في App.
 */
import { useEffect, useRef, useState } from 'react'
import Modal, { ModalFooter } from '../ui/Modal'
import { useAuth } from '../../store/auth'
import { actionIcon, actionLabel, emitApprovalsChanged, roleLabel } from '../../utils/approvals'

export default function ApprovalRequiredHost({ onNavigate }) {
  const { user } = useAuth()
  const [info, setInfo] = useState(null)      // تفاصيل الرد الأخير
  const [repeat, setRepeat] = useState(false) // نفس الطلب ظهر قبل كده في الجلسة (ما زال معلّق)
  const seenRef = useRef(new Set())

  useEffect(() => {
    const h = (e) => {
      const d = e.detail || {}
      // يخلي الـ toast العام في الصفحة (نفس النص) ما يتكرر فوق النافذة
      window.__ERP_APPROVAL_SUPPRESS__ = { msg: d.message || '', ts: Date.now() }
      const id = d.request_id
      const seen = id != null && seenRef.current.has(id)
      if (id != null) seenRef.current.add(id)
      setRepeat(seen)
      setInfo(d)
      emitApprovalsChanged()
    }
    window.addEventListener('erp:approval-required', h)
    return () => window.removeEventListener('erp:approval-required', h)
  }, [])

  // تسجيل الخروج/تغيير المستخدم: نقفل النافذة وننسى الطلبات اللي اتشافت
  useEffect(() => {
    if (!user) { setInfo(null); seenRef.current = new Set() }
  }, [user])

  if (!info || !user) return null

  const close = () => setInfo(null)
  const reqRole = info.required_role
  const roleName = info.required_role_label || roleLabel(reqRole)
  // مقدّم الطلب بدوره ينفع يوافق (مدير دايمًا، محاسب لو الدور المطلوب محاسب) لكن ممنوع يوافق على طلبه
  const selfCouldApprove = user.role === 'admin' || (reqRole === 'accountant' && user.role === 'accountant')
  const prev = info.previous_rejection

  return (
    <Modal open onClose={close} size="sm" title="🛡️ هذا الإجراء يحتاج موافقة">
      <div className="flex flex-col gap-3 text-sm" role="alert">
        <div>{info.message || 'هذا الإجراء يتطلب موافقة قبل التنفيذ.'}</div>

        <div className="card" style={{ padding: '10px 12px', background: 'var(--bg)' }}>
          <div className="flex flex-col gap-1 text-xs">
            <div><span className="text-erp-muted">الإجراء: </span>
              <b>{actionIcon(info.action_key)} {actionLabel(info.action_key, info.action_label)}</b></div>
            {info.summary && <div><span className="text-erp-muted">التفاصيل: </span>{info.summary}</div>}
            <div><span className="text-erp-muted">الدور المطلوب للموافقة: </span><b>{roleName}</b></div>
            {info.request_id != null && (
              <div><span className="text-erp-muted">رقم الطلب: </span><b className="font-mono">#{info.request_id}</b></div>
            )}
          </div>
        </div>

        {prev && (
          <div className="text-xs" style={{ color: 'var(--red)' }}>
            تم رفض الطلب السابق رقم {prev.request_id}{prev.reason ? `: ${prev.reason}` : ''}.
            {info.request_id != null ? ` تم إنشاء طلب جديد رقم ${info.request_id}.` : ''}
          </div>
        )}

        <div className="text-xs text-erp-muted">
          {repeat
            ? `طلبك رقم ${info.request_id} ما زال معلّقًا بانتظار الموافقة.`
            : `تم إرسال طلب موافقة${info.request_id != null ? ` رقم ${info.request_id}` : ''}. بعد الموافقة أعد تنفيذ نفس الإجراء مرة واحدة.`}
        </div>

        {selfCouldApprove && (
          <div className="text-xs" style={{ color: 'var(--amber)' }}>
            لا يمكنك الموافقة على طلبك بنفسك — اطلب من مستخدم آخر
            {reqRole === 'accountant' ? ' بدور محاسب أو مدير النظام' : ` بدور ${roleName}`}.
          </div>
        )}
      </div>
      <ModalFooter>
        <button type="button" className="btn" onClick={close}>حسنًا</button>
        {info.request_id != null && (
          <button type="button" className="btn btn-primary"
            onClick={() => { close(); onNavigate?.('approvals', { tab: 'mine', highlight: info.request_id }) }}>
            عرض الطلب
          </button>
        )}
      </ModalFooter>
    </Modal>
  )
}
