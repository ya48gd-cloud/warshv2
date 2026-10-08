/**
 * رمز QR للمستند المقبول (valid). الباك إند يرجّع qr_text / share_url فقط عندما تكون الحالة valid
 * (GET /einvoice/documents/{kind}/{id}). الرمز يُولَّد محلياً في المتصفح (utils/qr.js) بدون أي CDN.
 * لا يُعرض رمز لمستند غير مقبول.
 */
import { useMemo } from 'react'
import Modal, { ModalFooter } from '../ui/Modal'
import { PageSpinner } from '../ui/Spinner'
import { useToast } from '../../store/toast'
import { useEtaState } from './EtaStateModal'
import { qrMatrix, qrToSvgPath } from '../../utils/qr'
import { etaKindLabel } from '../../utils/einvoice'

const ltr = { direction: 'ltr', unicodeBidi: 'isolate', wordBreak: 'break-all' }

function QrSvg({ text, size = 240 }) {
  const built = useMemo(() => {
    try {
      const { path, size: n } = qrToSvgPath(qrMatrix(text), 4)
      return { path, n }
    } catch (e) {
      return { error: e?.message || 'تعذّر توليد الرمز' }
    }
  }, [text])
  if (built.error) {
    return <div className="text-sm text-center px-3 py-2 rounded-erp" style={{ background: 'var(--amber-lt)', color: 'var(--amber)' }}>{built.error} — استخدم الرابط أدناه.</div>
  }
  return (
    <svg id="eta-qr-svg" role="img" aria-label="رمز QR للفاتورة الإلكترونية" viewBox={`0 0 ${built.n} ${built.n}`}
         width={size} height={size} shapeRendering="crispEdges" style={{ background: '#fff', maxWidth: '100%', height: 'auto' }}>
      <rect width={built.n} height={built.n} fill="#fff" />
      <path d={built.path} fill="#000" />
    </svg>
  )
}

export default function EtaQrModal({ open, kind, doc, onClose }) {
  const toast = useToast()
  const { loading, data, error, reload } = useEtaState(open, kind, doc?.id)
  const valid = data?.status === 'valid' && !!data?.qr_text

  const copy = async (text) => {
    try { await navigator.clipboard.writeText(text); toast('تم النسخ') }
    catch { toast('تعذّر النسخ تلقائياً — حدّد النص وانسخه يدوياً', 'warn') }
  }

  const printQr = () => {
    const svg = document.getElementById('eta-qr-svg')
    if (!svg) return
    const w = window.open('', '_blank', 'width=420,height=560')
    if (!w) { toast('المتصفح منع النافذة المنبثقة — اسمح بها ثم أعد المحاولة', 'warn'); return }
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
    w.document.write(
      `<!doctype html><html dir="rtl"><head><meta charset="utf-8"><title>QR</title>` +
      `<style>body{font-family:sans-serif;text-align:center;padding:24px}svg{width:260px;height:260px}` +
      `p{font-size:12px;direction:ltr;word-break:break-all;color:#333}</style></head><body>` +
      `<h3>${esc(etaKindLabel(kind))} ${esc(doc?.code || '')}</h3>${svg.outerHTML}` +
      `<p>${esc(data?.eta_uuid || '')}</p></body></html>`
    )
    w.document.close()
    w.focus()
    setTimeout(() => { try { w.print() } catch { /* ignore */ } }, 250)
  }

  return (
    <Modal open={open} onClose={onClose} size="sm" title={`رمز QR — ${etaKindLabel(kind)} ${doc?.code || ''}`}>
      {loading && !data ? (
        <PageSpinner />
      ) : error && !data ? (
        <div className="flex items-center justify-between gap-3 text-sm px-3 py-2.5 rounded-erp"
             style={{ background: 'var(--red-lt)', color: 'var(--red)' }}>
          <span>{error}</span>
          <button className="btn btn-sm" onClick={reload}>إعادة المحاولة</button>
        </div>
      ) : !valid ? (
        <div className="text-center text-erp-muted text-sm py-8">
          <div className="text-4xl mb-2">🔲</div>
          يظهر الرمز بعد قبول المستند من مصلحة الضرائب.
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3">
          <QrSvg text={data.qr_text} />
          {data.eta_uuid && (
            <div className="text-xs text-center">
              <div className="text-erp-muted mb-0.5">UUID</div>
              <div style={ltr}>{data.eta_uuid}</div>
            </div>
          )}
          <div className="text-xs text-erp-muted text-center">
            الرمز مُولَّد محلياً في المتصفح ولم يُجرَّب بماسح حقيقي — إن لم يُقرأ فاستخدم رابط المشاركة.
          </div>
          {data.share_url && (
            <div className="w-full">
              <div className="text-xs text-erp-muted mb-1">رابط المشاركة</div>
              <input readOnly dir="ltr" className="field-input text-xs" value={data.share_url}
                     onFocus={(e) => e.target.select()} />
            </div>
          )}
        </div>
      )}
      <ModalFooter>
        {valid && data?.share_url && <button className="btn" onClick={() => copy(data.share_url)}>نسخ الرابط</button>}
        {valid && <button className="btn" onClick={printQr}>🖨️ طباعة</button>}
        <button className="btn" onClick={onClose}>إغلاق</button>
      </ModalFooter>
    </Modal>
  )
}
