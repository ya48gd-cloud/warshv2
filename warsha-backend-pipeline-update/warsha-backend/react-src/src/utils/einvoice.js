/**
 * الفاتورة الإلكترونية (ETA) — ثوابت ومساعدات الواجهة.
 * مفردات الحالة مطابقة لعقد الباك إند: NULL (لم تُرسل), queued, submitting, submitted,
 * valid, invalid, rejected, failed, cancelled.
 */

export const ETA_STATUS = {
  queued:     { label: 'في الانتظار',    badge: 'badge-blue'  },
  submitting: { label: 'قيد المعالجة',   badge: 'badge-amber' },
  submitted:  { label: 'قيد المعالجة',   badge: 'badge-amber' },
  valid:      { label: 'مقبولة',         badge: 'badge-green' },
  invalid:    { label: 'مرفوضة',         badge: 'badge-red'   },
  rejected:   { label: 'مرفوضة',         badge: 'badge-red'   },
  failed:     { label: 'فشل الإرسال',    badge: 'badge-red'   },
  cancelled:  { label: 'ملغاة',          badge: 'badge-gray'  },
}
const NOT_SENT = { label: 'لم تُرسل', badge: 'badge-gray' }

/** الحالة الفعلية للمستند (null = لم يُرسل أبداً). */
export function etaStatusOf(doc) {
  const s = doc?.eta_status
  return s == null || s === '' ? null : String(s)
}

/** وصف عرض الحالة؛ حالة غير معروفة تُعرض كما هي بشارة رمادية. */
export function etaMeta(status) {
  if (status == null || status === '') return NOT_SENT
  return ETA_STATUS[status] || { label: String(status), badge: 'badge-gray' }
}

// مقفول في الـ ERP (لا تعديل ولا حذف ولا إلغاء) — الحماية الحقيقية في الباك إند
export const ETA_LOCKED = ['queued', 'submitting', 'submitted', 'valid']
export const ETA_IN_PROGRESS = ['queued', 'submitting', 'submitted']

export const isEtaLocked   = (doc) => ETA_LOCKED.includes(etaStatusOf(doc))
export const isEtaPending  = (status) => ETA_IN_PROGRESS.includes(status)
// لا تعديل: المقفول + الملغي لدى الضرائب (الـ internalID محروق) — الحذف مسموح للملغي فقط
export const isEtaEditBlocked = (doc) => isEtaLocked(doc) || etaStatusOf(doc) === 'cancelled'
export const canSendEta    = (doc) => etaStatusOf(doc) === null
export const canResendEta  = (doc) => ['rejected', 'invalid', 'failed'].includes(etaStatusOf(doc))
export const canRefreshEta = (doc) => ETA_IN_PROGRESS.includes(etaStatusOf(doc))

/** 'credit_note' | 'credit-note' -> 'credit-note' (جزء المسار)؛ أي شيء آخر -> 'invoice'. */
export const etaPathKind = (kind) => (kind === 'credit_note' || kind === 'credit-note') ? 'credit-note' : 'invoice'
/** نوع داخلي للواجهة: 'invoice' | 'credit_note'. */
export const etaKindOf = (kind) => etaPathKind(kind) === 'credit-note' ? 'credit_note' : 'invoice'
export const etaKindLabel = (kind) => etaPathKind(kind) === 'credit-note' ? 'إشعار دائن' : 'فاتورة'

/**
 * هل يصلح المستند من ناحية حالته في الـ ERP للإرسال؟
 * الفاتورة: ليست مسودة/ملغاة. الإشعار الدائن: صادر أو مُطبَّق فقط.
 * (الباك إند يتحقق دائماً؛ هذا لإخفاء أزرار لا معنى لها فقط.) حالة غير معروفة -> true.
 */
export function isEtaEligibleDoc(doc, kind) {
  const st = doc?.status
  if (st == null || st === '') return true
  if (etaPathKind(kind) === 'credit-note') return st === 'issued' || st === 'applied'
  return st !== 'draft' && st !== 'cancelled'
}

/**
 * أخطاء ETA بأي شكل (مصفوفة كائنات / مصفوفة نصوص / نص JSON / نص عادي) -> [{code,message,target}]
 */
export function etaErrorsToList(raw) {
  if (raw == null || raw === '') return []
  let v = raw
  if (typeof v === 'string') {
    const s = v.trim()
    if (!s) return []
    try { v = JSON.parse(s) } catch { return [{ code: '', message: s, target: '' }] }
  }
  if (!Array.isArray(v)) v = [v]
  return v.map((e) => {
    if (e == null) return null
    if (typeof e === 'string') return { code: '', message: e, target: '' }
    if (typeof e === 'object') {
      return {
        code: e.code != null ? String(e.code) : '',
        message: String(e.message ?? e.msg ?? e.error ?? e.details ?? ''),
        target: e.target != null ? String(e.target) : '',
      }
    }
    return { code: '', message: String(e), target: '' }
  }).filter((e) => e && (e.message || e.code))
}

// الخادم يرجّع ISO مع Z؛ لو جاء بدونها نفترض UTC
export function fmtEtaDateTime(s) {
  if (!s) return '—'
  let str = String(s)
  if (!/([zZ]|[+-]\d\d:?\d\d)$/.test(str)) str = str.replace(' ', 'T') + 'Z'
  const d = new Date(str)
  if (isNaN(d.getTime())) return '—'
  try {
    return new Intl.DateTimeFormat('ar-EG-u-nu-latn', { dateStyle: 'medium', timeStyle: 'short' }).format(d)
  } catch {
    return d.toLocaleString()
  }
}

export const ETA_DISCLAIMER =
  'غير مُجرَّب على بيئة مصلحة الضرائب الفعلية — يحتاج شهادة توقيع وبيانات اعتماد وبيئة تجريبية'

export const ETA_ENV_LABEL = { preprod: 'بيئة تجريبية', prod: 'بيئة إنتاج', custom: 'بيئة مخصصة' }
