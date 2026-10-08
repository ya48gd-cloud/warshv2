/**
 * حالة المستند لدى مصلحة الضرائب: الأخطاء، المعرّفات، المحاولات، السجل.
 * GET /einvoice/documents/{kind}/{id} (require_any — حتى المشاهد). لا يرجّع payload ولا أسرار.
 * useEtaState مُصدَّر لمودال QR أيضاً.
 */
import { useEffect, useState, useCallback } from 'react'
import Modal, { ModalFooter } from '../ui/Modal'
import { PageSpinner } from '../ui/Spinner'
import EtaBadge from './EtaBadge'
import api from '../../api/client'
import {
  etaErrorsToList, etaStatusOf, canResendEta, canRefreshEta, fmtEtaDateTime, etaKindLabel,
} from '../../utils/einvoice'

export function useEtaState(open, kind, id) {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  const load = useCallback(async () => {
    if (!id) return
    setState(s => ({ loading: true, data: s.data, error: null }))
    try {
      const data = await api.einvoice.state(kind, id)
      setState({ loading: false, data, error: null })
    } catch (e) {
      setState(s => ({ loading: false, data: s.data, error: e?.message || 'تعذّر تحميل حالة المستند' }))
    }
  }, [kind, id])
  useEffect(() => { if (open) { setState({ loading: true, data: null, error: null }); load() } }, [open, load])
  return { ...state, reload: load }
}

const ltr = { direction: 'ltr', unicodeBidi: 'isolate', display: 'inline-block', wordBreak: 'break-all' }

function Row({ label, children }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 border-b border-erp-border text-sm">
      <span className="text-erp-muted shrink-0">{label}</span>
      <span style={{ textAlign: 'left', minWidth: 0 }}>{children}</span>
    </div>
  )
}

export default function EtaStateModal({ open, kind, doc, onClose, write, onResend, onRefresh }) {
  const { loading, data, error, reload } = useEtaState(open, kind, doc?.id)

  const status = data ? (data.status ?? null) : etaStatusOf(doc)
  // أثناء/بعد فشل التحميل نعرض أخطاء الصف التي وصلتنا مع القائمة
  const errs = etaErrorsToList(data ? data.errors : doc?.eta_errors)
  const history = Array.isArray(data?.history) ? data.history.slice(-10).reverse() : []
  const asDoc = { eta_status: status }

  return (
    <Modal open={open} onClose={onClose} size="lg"
           title={`حالة الفاتورة الإلكترونية — ${etaKindLabel(kind)} ${doc?.code || ''}`}>
      {loading && !data ? (
        <PageSpinner />
      ) : (
        <div className="flex flex-col gap-4">
          {error && (
            <div className="flex items-center justify-between gap-3 text-sm px-3 py-2.5 rounded-erp"
                 style={{ background: 'var(--amber-lt)', color: 'var(--amber)' }}>
              <span>{data ? `تعذّر تحديث الحالة (${error}) — المعروض آخر بيانات محمّلة.` : `${error} — المعروض بيانات القائمة فقط.`}</span>
              <button className="btn btn-sm" onClick={reload}>إعادة المحاولة</button>
            </div>
          )}

          <div>
            <Row label="الحالة"><EtaBadge doc={asDoc} /></Row>
            {data && (
              <>
                <Row label="المعرّف (UUID)"><span style={ltr}>{data.eta_uuid || '—'}</span></Row>
                {data.eta_long_id && <Row label="المعرّف الطويل"><span style={ltr}>{data.eta_long_id}</span></Row>}
                <Row label="رقم الدفعة (Submission)"><span style={ltr}>{data.eta_submission_id || '—'}</span></Row>
                <Row label="تاريخ الإرسال">{fmtEtaDateTime(data.submitted_at)}</Row>
                {data.validated_at && <Row label="تاريخ القبول">{fmtEtaDateTime(data.validated_at)}</Row>}
                <Row label="عدد المحاولات">{data.attempts ?? 0}</Row>
                {data.next_attempt_at && status !== 'valid' && (
                  <Row label="المحاولة التالية (تلقائية)">{fmtEtaDateTime(data.next_attempt_at)}</Row>
                )}
              </>
            )}
          </div>

          <div>
            <div className="text-sm font-semibold mb-2">الأخطاء</div>
            {errs.length === 0 ? (
              <div className="text-sm text-erp-muted">لا توجد أخطاء مسجّلة.</div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table className="erp-table" style={{ minWidth: 420 }}>
                  <thead><tr><th>الكود</th><th>الرسالة</th><th>الحقل</th></tr></thead>
                  <tbody>
                    {errs.map((e, i) => (
                      <tr key={i}>
                        <td className="text-xs whitespace-nowrap"><span style={ltr}>{e.code || '—'}</span></td>
                        <td className="text-sm">{e.message || '—'}</td>
                        <td className="text-xs"><span style={ltr}>{e.target || '—'}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {status === 'failed' && (
              <div className="text-xs mt-2" style={{ color: 'var(--amber)' }}>
                فشل مؤقت في الإرسال — سيعيد النظام المحاولة تلقائياً، ويمكنك أيضاً إعادة الإرسال يدوياً.
              </div>
            )}
          </div>

          {history.length > 0 && (
            <div>
              <div className="text-sm font-semibold mb-2">آخر التغييرات</div>
              <div style={{ maxHeight: 160, overflowY: 'auto' }}>
                {history.map((h, i) => (
                  <div key={i} className="text-xs py-1 border-b border-erp-border flex gap-2 flex-wrap">
                    <span className="text-erp-muted">{fmtEtaDateTime(h.at)}</span>
                    <span>{h.from || '—'} ← {h.to || '—'}</span>
                    {h.note && <span className="text-erp-muted">{String(h.note)}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
      <ModalFooter>
        {write && canRefreshEta(asDoc) && onRefresh && (
          <button className="btn" onClick={onRefresh}>↻ تحديث الحالة من الضرائب</button>
        )}
        {write && canResendEta(asDoc) && onResend && (
          <button className="btn btn-primary" onClick={onResend}>إعادة الإرسال</button>
        )}
        <button className="btn" onClick={onClose}>إغلاق</button>
      </ModalFooter>
    </Modal>
  )
}
