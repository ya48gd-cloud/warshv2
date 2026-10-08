/**
 * معاينة مستند ETA (JSON) قبل الإرسال — قراءة فقط: لا شبكة ولا توقيع ولا تغيير حالة.
 * GET /einvoice/documents/{kind}/{id}/preview -> {document, errors[], warnings[], can_submit}
 */
import { useEffect, useState, useCallback } from 'react'
import Modal, { ModalFooter } from '../ui/Modal'
import { PageSpinner } from '../ui/Spinner'
import { useToast } from '../../store/toast'
import api from '../../api/client'
import { etaKindLabel } from '../../utils/einvoice'

export default function EtaPreviewModal({ open, kind, doc, onClose, onSend, canSend, isAdmin, onGoSettings }) {
  const toast = useToast()
  const [state, setState] = useState({ loading: true, data: null, error: null })

  const load = useCallback(async () => {
    if (!doc?.id) return
    setState({ loading: true, data: null, error: null })
    try {
      const data = await api.einvoice.preview(kind, doc.id)
      setState({ loading: false, data, error: null })
    } catch (e) {
      setState({ loading: false, data: null, error: e?.message || 'تعذّر تحميل المعاينة' })
    }
  }, [kind, doc?.id])

  useEffect(() => { if (open) load() }, [open, load])

  const { loading, data, error } = state
  const errors = Array.isArray(data?.errors) ? data.errors : []
  const warnings = Array.isArray(data?.warnings) ? data.warnings : []
  const json = data?.document ? JSON.stringify(data.document, null, 2) : ''

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(json)
      toast('تم نسخ JSON')
    } catch {
      toast('تعذّر النسخ تلقائياً — حدّد النص وانسخه يدوياً', 'warn')
    }
  }

  return (
    <Modal open={open} onClose={onClose} size="xl"
           title={`معاينة المستند الإلكتروني — ${etaKindLabel(kind)} ${doc?.code || ''}`}>
      {loading ? (
        <PageSpinner />
      ) : error ? (
        <div className="flex items-center justify-between gap-3 text-sm px-3 py-2.5 rounded-erp"
             style={{ background: 'var(--red-lt)', color: 'var(--red)' }}>
          <span>{error}</span>
          <button className="btn btn-sm" onClick={load}>إعادة المحاولة</button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {errors.length > 0 ? (
            <div className="px-3 py-2.5 rounded-erp text-sm" style={{ background: 'var(--red-lt)', color: 'var(--red)' }}>
              <div className="font-semibold mb-1">لا يمكن الإرسال — بيانات ناقصة أو غير صالحة:</div>
              <ul className="list-disc pr-5 leading-7">
                {errors.map((m, i) => <li key={i}>{String(m)}</li>)}
              </ul>
              {isAdmin && onGoSettings && (
                <button className="btn btn-sm mt-2" onClick={onGoSettings}>الذهاب لإعدادات الفاتورة الإلكترونية</button>
              )}
            </div>
          ) : (
            <div className="px-3 py-2 rounded-erp text-sm" style={{ background: 'var(--green-lt)', color: 'var(--green)' }}>
              المستند مكتمل ويمكن إرساله.
            </div>
          )}
          {warnings.length > 0 && (
            <div className="px-3 py-2.5 rounded-erp text-sm" style={{ background: 'var(--amber-lt)', color: 'var(--amber)' }}>
              <div className="font-semibold mb-1">تنبيهات:</div>
              <ul className="list-disc pr-5 leading-7">
                {warnings.map((m, i) => <li key={i}>{String(m)}</li>)}
              </ul>
            </div>
          )}
          <div className="text-xs text-erp-muted">
            هذه معاينة فقط (لا إرسال ولا توقيع). المستند النهائي الذي يُرسل يُبنى ويُحفظ عند الإرسال.
          </div>
          {json ? (
            <pre dir="ltr" className="text-xs"
                 style={{ textAlign: 'left', background: 'var(--bg)', border: '1px solid var(--erp-border)',
                          borderRadius: 8, padding: 12, maxHeight: '50vh', overflow: 'auto',
                          fontFamily: 'ui-monospace, Consolas, monospace', whiteSpace: 'pre', margin: 0 }}>
              {json}
            </pre>
          ) : (
            <div className="text-center text-erp-muted text-sm py-6">لا توجد بيانات للمعاينة</div>
          )}
        </div>
      )}
      <ModalFooter>
        {json && <button className="btn" onClick={copy}>📋 نسخ JSON</button>}
        <button className="btn" onClick={onClose}>إغلاق</button>
        {canSend && !loading && !error && (
          <button className="btn btn-primary" disabled={!data?.can_submit} onClick={onSend}
                  style={{ opacity: data?.can_submit ? 1 : 0.5, cursor: data?.can_submit ? 'pointer' : 'not-allowed' }}
                  title={data?.can_submit ? '' : 'أصلح البيانات الناقصة أولاً'}>
            إرسال للضرائب
          </button>
        )}
      </ModalFooter>
    </Modal>
  )
}
