/**
 * مساعدات دورة الموافقات (Approval Workflow) — دوال نقية بدون React.
 * مفاتيح الإجراءات هنا للأيقونة والتنقل فقط؛ قائمة الإجراءات وشروطها الفعلية
 * تأتي من GET /approvals/meta (الخادم هو المرجع).
 */
import { egp } from './fmt'

// مفتاح الإجراء (يطابق الخادم) -> أيقونة + الصفحة اللي ينفَّذ منها الإجراء
export const APPROVAL_ACTION_NAV = {
  po_approve:        { icon: '📝', label: 'اعتماد أمر شراء',         page: 'purchaseorders' },
  invoice_discount:  { icon: '🏷️', label: 'خصم على فاتورة',          page: 'invoices' },
  invoice_delete:    { icon: '🧾', label: 'حذف/إلغاء فاتورة صادرة',  page: 'invoices' },
  worker_advance:    { icon: '💸', label: 'سلفة عامل',               page: 'workers' },
  credit_note_issue: { icon: '📋', label: 'إصدار إشعار دائن',        page: 'invoices', pageParams: { tab: 'creditnotes' } },
  treasury_out:      { icon: '💰', label: 'صرف من الخزينة',          page: 'treasury' },
}

// نفس شكل WO_STATUS في fmt.js عشان يشتغل مع StatusBadge
export const APPROVAL_STATUS = {
  pending:   { label: 'معلّق',                         badge: 'badge-amber' },
  approved:  { label: 'موافق عليه - بانتظار التنفيذ',  badge: 'badge-green' },
  rejected:  { label: 'مرفوض',                         badge: 'badge-red'   },
  consumed:  { label: 'تم التنفيذ',                    badge: 'badge-gray'  },
  cancelled: { label: 'ملغي',                          badge: 'badge-gray'  },
  expired:   { label: 'منتهي الصلاحية',                badge: 'badge-gray'  },
}

// شرائح الفلتر في صندوق الوارد / طلباتي
export const APPROVAL_STATUS_FILTERS = [
  { key: 'all',       label: 'الكل' },
  { key: 'pending',   label: 'معلّق' },
  { key: 'approved',  label: 'موافق عليه' },
  { key: 'rejected',  label: 'مرفوض' },
  { key: 'consumed',  label: 'تم التنفيذ' },
  { key: 'cancelled', label: 'ملغي' },
  { key: 'expired',   label: 'منتهي' },
]

export const ROLE_LABELS = { admin: 'مدير النظام', accountant: 'محاسب', production: 'إنتاج', viewer: 'مشاهد' }
export const roleLabel = (r) => ROLE_LABELS[r] || r || '—'

export const SNAPSHOT_LABELS = {
  amount: 'المبلغ', percent: 'النسبة %', discount_pct: 'نسبة الخصم %', discount_amount: 'قيمة الخصم',
  code: 'الكود', supplier_name: 'المورد', customer_name: 'العميل', worker_name: 'العامل',
  total: 'الإجمالي', subtotal: 'الإجمالي قبل الخصم', notes: 'ملاحظات', summary: 'ملخص',
  condition_type: 'نوع الشرط', threshold: 'الحد', name: 'الاسم', id: 'المعرّف',
}
export const snapshotLabel = (k) => SNAPSHOT_LABELS[k] || k

const MONEY_KEY = /(amount|total|price|cost|subtotal|balance)/i

/** عرض قيمة داخل جدول الـ snapshot: مبالغ بصيغة العملة، كائنات كـ JSON، الباقي كنص */
export function formatSnapshotValue(key, value) {
  if (value == null || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  if (typeof value === 'boolean') return value ? 'نعم' : 'لا'
  if (typeof value === 'number' && /(pct|percent)/i.test(key)) return `${value}%`
  if (typeof value === 'number' && MONEY_KEY.test(key)) return egp(value)
  return String(value)
}

export function isApprovalRequiredError(err) {
  return !!err && (err.code === 'approval_required' ||
    (err.status === 428 && err.data?.code === 'approval_required'))
}

/** وصف الشرط بالعربي — "المبلغ أكبر من 50,000 ج" / "نسبة الخصم أكبر من 10%" / "دائمًا" */
export function describeCondition(rule) {
  if (!rule) return '—'
  const t = Number(rule.threshold)
  const hasT = rule.threshold != null && Number.isFinite(t)
  switch (rule.condition_type) {
    case 'always':     return 'دائمًا'
    case 'amount_gt':  return hasT ? `المبلغ أكبر من ${egp(t)}` : 'المبلغ (بدون حد)'
    case 'percent_gt': return hasT ? `نسبة الخصم أكبر من ${t}%` : 'نسبة الخصم (بدون حد)'
    default:           return rule.condition_type || '—'
  }
}

export function actionIcon(key) { return APPROVAL_ACTION_NAV[key]?.icon || '🛡️' }

/** اسم الإجراء: label من الخادم أولًا، ثم المحلي، ثم المفتاح الخام */
export function actionLabel(key, serverLabel) {
  return serverLabel || APPROVAL_ACTION_NAV[key]?.label || key || '—'
}

/** الخادم يرجّع UTC بدون Z — نضيفها عشان المتصفح ما يفسّرها كتوقيت محلي */
export function fmtDateTime(s) {
  if (!s) return '—'
  let str = String(s).replace(' ', 'T')
  if (/T\d{2}:\d{2}/.test(str) && !/(Z|[+-]\d{2}:?\d{2})$/.test(str)) str += 'Z'
  const d = new Date(str)
  if (isNaN(d.getTime())) return String(s)
  return d.toLocaleString('ar-EG', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    numberingSystem: 'latn',
  })
}

export function emitApprovalsChanged() {
  try { window.dispatchEvent(new CustomEvent('erp:approvals-changed')) } catch { /* بيئة بدون window */ }
}

/** قيمة/مبلغ الطلب للعرض في الجدول */
export function requestValueLabel(req) {
  if (!req) return '—'
  if (req.amount != null) return egp(req.amount)
  if (req.percent != null) return `${Number(req.percent)}%`
  return '—'
}
