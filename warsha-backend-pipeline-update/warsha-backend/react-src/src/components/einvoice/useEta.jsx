/**
 * useEta — منطق الفاتورة الإلكترونية المشترك لصفحات الفواتير والإشعارات الدائنة وطابور الإرسال.
 *
 *   const eta = useEta({ kind: 'invoice', reload, onNavigate })
 *   eta.enabled                    -> هل التكامل مفعّل (من GET /einvoice/status)
 *   eta.menuItems(doc, {kind})     -> عناصر لـ ActionMenu
 *   eta.openState(doc, kind)       -> يفتح نافذة الحالة/الأخطاء
 *   eta.element                    -> JSX للمودالات (ضعه مرة واحدة في الصفحة)
 *
 * الصلاحيات: المعاينة والإرسال وإعادة الإرسال والتحديث = admin/accountant (canWrite('sales'))،
 * الحالة والـ QR لكل الأدوار، الإلغاء لدى الضرائب = admin. الإخفاء هنا تجميلي؛ الباك إند هو الحارس.
 */
import { useState, useRef, useCallback } from 'react'
import { useAuth } from '../../store/auth'
import { useToast } from '../../store/toast'
import api from '../../api/client'
import ConfirmDialog from '../ui/ConfirmDialog'
import useEtaEnabled from '../../hooks/useEtaEnabled'
import EtaPreviewModal from './EtaPreviewModal'
import EtaStateModal from './EtaStateModal'
import EtaQrModal from './EtaQrModal'
import EtaCancelModal from './EtaCancelModal'
import {
  etaStatusOf, canSendEta, canResendEta, canRefreshEta, isEtaEligibleDoc, etaKindOf, etaPathKind,
  etaKindLabel, ETA_ENV_LABEL,
} from '../../utils/einvoice'

export default function useEta({ kind: defaultKind = 'invoice', reload, onNavigate } = {}) {
  const { user, canWrite } = useAuth()
  const toast = useToast()
  const st = useEtaEnabled()
  const write = canWrite('sales')
  const isAdmin = user?.role === 'admin'

  // modal: { type: 'preview'|'state'|'qr'|'send'|'cancel', doc, kind, mode?: 'send'|'resend' }
  const [modal, setModal] = useState(null)
  const [busyKey, setBusyKey] = useState(null)
  const busyRef = useRef(false)

  const close = useCallback(() => setModal(null), [])
  const refresh = useCallback(() => { try { reload?.() } catch { /* ignore */ } }, [reload])

  // معالجة نتيجة submit / resubmit / refresh-status
  const handleResult = useCallback((res, doc, k, verb) => {
    if (res?.idempotent) { toast('المستند مُرسل بالفعل'); return }
    const s = res?.eta_status || res?.status
    if (s === 'valid') toast('تم قبول المستند من مصلحة الضرائب')
    else if (s === 'submitted' || s === 'submitting') toast('تم الإرسال — المستند قيد المعالجة لدى الضرائب')
    else if (s === 'queued' || s === 'failed') {
      toast('تعذّر الوصول لمصلحة الضرائب الآن — سيُعاد الإرسال تلقائيًا', 'warn')
    } else if (s === 'invalid' || s === 'rejected') {
      toast('رفضت مصلحة الضرائب المستند — راجع الأخطاء', 'error')
      setModal({ type: 'state', doc, kind: k })
    } else toast(verb === 'refresh' ? 'تم تحديث الحالة' : 'تم تسجيل الإرسال')
  }, [toast])

  const run = useCallback(async (verb, doc, k) => {
    if (busyRef.current) return          // حماية من الضغط المزدوج
    busyRef.current = true
    setBusyKey(`${k}:${doc.id}`)
    try {
      const fn = verb === 'resend' ? api.einvoice.resend : verb === 'refresh' ? api.einvoice.refreshStatus : api.einvoice.send
      const res = await fn(k, doc.id)
      handleResult(res, doc, k, verb)
    } catch (e) {
      if (e?.status === 422) {
        // فشل التحقق: لا صف إرسال اتعمل — نفتح المعاينة لعرض قائمة النواقص
        toast(e.message || 'بيانات المستند غير مكتملة', 'error')
        setModal({ type: 'preview', doc, kind: k })
      } else {
        toast(e?.message || 'تعذّر تنفيذ الإجراء', 'error')
      }
    } finally {
      busyRef.current = false
      setBusyKey(null)
      refresh()
    }
  }, [handleResult, refresh, toast])

  const doCancel = useCallback(async (reason) => {
    const m = modal
    if (!m || busyRef.current) return
    busyRef.current = true
    setBusyKey(`${m.kind}:${m.doc.id}`)
    try {
      const res = await api.einvoice.cancel(m.kind, m.doc.id, reason)
      toast(res?.message || 'تم إلغاء المستند لدى مصلحة الضرائب')
      setModal(null)
    } catch (e) {
      toast(e?.message || 'تعذّر الإلغاء', 'error')
    } finally {
      busyRef.current = false
      setBusyKey(null)
      refresh()
    }
  }, [modal, refresh, toast])

  const openState = useCallback((doc, k = defaultKind) => setModal({ type: 'state', doc, kind: etaPathKind(k) }), [defaultKind])

  const menuItems = useCallback((doc, opts = {}) => {
    if (!doc) return []
    const k = etaPathKind(opts.kind || defaultKind)
    const s = etaStatusOf(doc)
    const busy = busyKey === `${k}:${doc.id}`
    const eligible = isEtaEligibleDoc(doc, k)
    const items = []
    if (st.enabled && write) {
      items.push({ label: 'معاينة JSON الفاتورة الإلكترونية', icon: '🔍',
        onClick: () => setModal({ type: 'preview', doc, kind: k }) })
      if (eligible && canSendEta(doc)) {
        items.push({ label: 'إرسال للضرائب', icon: '🇪🇬', disabled: busy,
          onClick: () => setModal({ type: 'send', doc, kind: k, mode: 'send' }) })
      }
      if (eligible && canResendEta(doc)) {
        items.push({ label: 'إعادة الإرسال للضرائب', icon: '🔁', disabled: busy,
          onClick: () => setModal({ type: 'send', doc, kind: k, mode: 'resend' }) })
      }
    }
    if (write && canRefreshEta(doc)) {
      items.push({ label: 'تحديث حالة الضرائب', icon: '↻', disabled: busy, onClick: () => run('refresh', doc, k) })
    }
    // الحالة/الأخطاء/QR متاحة لكل الأدوار (GET require_any) طالما المستند اتبعت قبل كده
    if (s) items.push({ label: 'حالة الفاتورة الإلكترونية', icon: '📄', onClick: () => setModal({ type: 'state', doc, kind: k }) })
    if (s === 'valid') items.push({ label: 'رمز QR', icon: '🔲', onClick: () => setModal({ type: 'qr', doc, kind: k }) })
    if (isAdmin && s === 'valid') {
      items.push({ label: 'إلغاء لدى الضرائب', icon: '⛔', danger: true, disabled: busy,
        onClick: () => setModal({ type: 'cancel', doc, kind: k }) })
    }
    return items
  }, [st.enabled, write, isAdmin, busyKey, defaultKind, run])

  const m = modal
  const envNote = st.environment === 'prod'
    ? '⚠ البيئة الإنتاجية الفعلية — المستند المقبول يُعتد به ضريبياً ولا يمكن التراجع عنه.'
    : st.environment === 'preprod'
      ? 'البيئة التجريبية — لن يُعتد بالمستند ضريبياً.'
      : st.environment === 'custom' ? 'بيئة مخصصة.' : ''
  const mockNote = st.signerMode === 'mock'
    ? '\n⚠ التوقيع في وضع المحاكاة: لن تقبل مصلحة الضرائب هذا المستند.' : ''
  const sendMsg = m?.type === 'send'
    ? (m.mode === 'resend'
        ? `سيُعاد بناء ${etaKindLabel(m.kind)} ${m.doc.code || ''} من بياناته الحالية وإرساله من جديد لمصلحة الضرائب.`
        : `سيتم إرسال ${etaKindLabel(m.kind)} ${m.doc.code || ''} إلى مصلحة الضرائب المصرية، ولا يمكن تعديله أو حذفه بعد الإرسال.`)
      + (envNote ? `\n${envNote}` : '') + mockNote
    : ''

  const element = (
    <>
      <EtaPreviewModal
        open={m?.type === 'preview'} kind={m?.kind} doc={m?.doc} onClose={close}
        isAdmin={isAdmin}
        onGoSettings={onNavigate ? () => { close(); onNavigate('users', { tab: 'einvoice' }) } : null}
        canSend={write && !!m?.doc && isEtaEligibleDoc(m.doc, m.kind) && (canSendEta(m.doc) || canResendEta(m.doc))}
        onSend={() => setModal({ type: 'send', doc: m.doc, kind: m.kind, mode: canResendEta(m.doc) ? 'resend' : 'send' })}
      />
      <EtaStateModal
        open={m?.type === 'state'} kind={m?.kind} doc={m?.doc} onClose={close} write={write}
        onResend={() => setModal({ type: 'send', doc: m.doc, kind: m.kind, mode: 'resend' })}
        onRefresh={() => { const d = m.doc, k = m.kind; close(); run('refresh', d, k) }}
      />
      <EtaQrModal open={m?.type === 'qr'} kind={m?.kind} doc={m?.doc} onClose={close} />
      <EtaCancelModal open={m?.type === 'cancel'} kind={m?.kind} doc={m?.doc} busy={!!busyKey}
                      onClose={close} onConfirm={doCancel} />
      <ConfirmDialog
        open={m?.type === 'send'}
        title={m?.mode === 'resend' ? 'إعادة الإرسال للضرائب' : 'إرسال للضرائب'}
        message={sendMsg}
        confirmLabel={m?.mode === 'resend' ? 'إعادة الإرسال' : 'إرسال'}
        onCancel={close}
        onConfirm={() => { const d = m.doc, k = m.kind, v = m.mode; run(v, d, k) }}
      />
    </>
  )

  return {
    enabled: st.enabled, loadingStatus: st.loading, environment: st.environment,
    envLabel: ETA_ENV_LABEL[st.environment] || '', signerMode: st.signerMode,
    write, isAdmin, busyKey, menuItems, openState, element, kindOf: etaKindOf,
  }
}
