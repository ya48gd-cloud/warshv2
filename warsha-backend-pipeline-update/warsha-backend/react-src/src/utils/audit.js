/**
 * مساعدات سجل التدقيق (Audit Log) — دوال نقية بدون React.
 *
 * الخادم هو المرجع لأسماء الإجراءات والكيانات (GET /audit/meta و row.action_label /
 * row.entity_label). الثوابت الثابتة هنا للـ fallback فقط عند غياب البيانات.
 *
 * شكل changes من الخادم: { "<field>": { old?, new?, old_unknown? }, "_truncated"?: int }
 *   create  => new فقط | delete => old فقط | update => old و new (null قيمة حقيقية)
 */

// قيمة الإخفاء والنص البديل للقيم الكبيرة — حرفياً كما يرسلها الخادم
export const MASK = '***'
export const LARGE_PREFIX = '[نص/بيانات كبيرة:'

// الإجراءات (تطابق الخادم). المفتاح غير المعروف يُعرض بشارة رمادية بنصه الخام.
export const ACTION_META = {
  create:            { label: 'إضافة',               badge: 'badge-green' },
  update:            { label: 'تعديل',               badge: 'badge-blue'  },
  delete:            { label: 'حذف',                 badge: 'badge-red'   },
  login:             { label: 'تسجيل دخول',          badge: 'badge-teal'  },
  login_failed:      { label: 'فشل تسجيل الدخول',    badge: 'badge-red'   },
  logout:            { label: 'تسجيل خروج',          badge: 'badge-gray'  },
  permission_change: { label: 'تغيير صلاحيات',       badge: 'badge-amber' },
  password_change:   { label: 'تغيير كلمة المرور',   badge: 'badge-amber' },
  purge:             { label: 'تنظيف السجل',         badge: 'badge-gray'  },
}

// fallback محدود لأسماء الكيانات (المصدر الأساسي meta.entity_types)
export const ENTITY_LABELS = {
  customers: 'عميل', users: 'مستخدم', invoices: 'فاتورة', materials: 'خامة',
  suppliers: 'مورد', workers: 'عامل', work_orders: 'أمر شغل', equipment: 'معدة',
}

// أسماء الحقول الشائعة بالعربي — أي حقل غير موجود يظهر باسمه الخام
const FIELD_LABELS = {
  id: 'المعرّف', name: 'الاسم', code: 'الكود', username: 'اسم المستخدم',
  full_name: 'الاسم الكامل', role: 'الدور', is_active: 'نشط', status: 'الحالة',
  phone: 'الهاتف', email: 'البريد الإلكتروني', address: 'العنوان', notes: 'ملاحظات',
  credit_limit: 'حد الائتمان', payment_terms: 'شروط الدفع', balance: 'الرصيد',
  total: 'الإجمالي', subtotal: 'الإجمالي الفرعي', amount: 'المبلغ', paid: 'المدفوع',
  paid_amount: 'المبلغ المدفوع', discount: 'الخصم', vat_amount: 'ضريبة القيمة المضافة',
  qty: 'الكمية', quantity: 'الكمية', stock_qty: 'رصيد المخزون', unit: 'الوحدة',
  unit_price: 'سعر الوحدة', unit_cost: 'تكلفة الوحدة', price: 'السعر', cost: 'التكلفة',
  date: 'التاريخ', due_date: 'تاريخ الاستحقاق', description: 'الوصف',
  customer_id: 'العميل', supplier_id: 'المورد', material_id: 'الخامة',
  warehouse_id: 'المخزن', invoice_id: 'الفاتورة', tenant_id: 'الورشة',
  password_hash: 'كلمة المرور', tax_id: 'الرقم الضريبي', category: 'الفئة',
  reference: 'المرجع', method: 'طريقة الدفع', type: 'النوع',
}

export function fieldLabel(name) {
  return FIELD_LABELS[name] || String(name)
}

// خط دفاع ثانٍ: اسم الحقل حساس => نعرض القيمة مخفية حتى لو وصلت من الخادم
const SENSITIVE_RE = /(password|passwd|pwd|secret|token|api_key|hash|credential|authorization|private_key)/i
export function isSensitiveField(name) {
  return SENSITIVE_RE.test(String(name || ''))
}

export const LOGIN_REASONS = {
  bad_password:    'كلمة مرور خاطئة',
  inactive:        'حساب معطل',
  tenant_disabled: 'ورشة معطّلة',
}
export function reasonLabel(r) {
  return LOGIN_REASONS[r] || (r ? String(r) : '—')
}

export function actionInfo(action, serverLabel) {
  const m = ACTION_META[action]
  return {
    label: serverLabel || m?.label || String(action ?? '—'),
    badge: m?.badge || 'badge-gray',
  }
}

export function entityLabelOf(row) {
  if (!row) return '—'
  return row.entity_label || ENTITY_LABELS[row.entity_type] || row.entity_type || '—'
}

/** user_name === null => عملية آلية. login_failed => اسم المستخدم المُجرَّب. */
export function userLabel(row) {
  if (!row) return '—'
  if (row.user_name === null || row.user_name === undefined || row.user_name === '') {
    return 'النظام (عملية آلية)'
  }
  return String(row.user_name)
}
export function isSystemRow(row) {
  return !!row && (row.user_name === null || row.user_name === undefined || row.user_name === '')
}

// ── التواريخ ─────────────────────────────────────────────────────
/** دقائق شرق UTC (القاهرة شتاءً = +120) — يطابق tz_offset_minutes في الخادم */
export function tzOffsetMinutes(d = new Date()) {
  const v = -d.getTimezoneOffset()
  return Number.isFinite(v) ? v : 0
}

/** created_at يصل بصيغة ISO مع Z؛ نُضيف Z لو غابت. العرض بتوقيت المتصفح (نفس منطق الفلتر). */
export function parseAuditDate(s) {
  if (!s) return null
  let str = String(s).replace(' ', 'T')
  if (/T\d{2}:\d{2}/.test(str) && !/(Z|[+-]\d{2}:?\d{2})$/.test(str)) str += 'Z'
  const d = new Date(str)
  return isNaN(d.getTime()) ? null : d
}
export function fmtAuditDateTime(s) {
  if (!s) return '—'
  const d = parseAuditDate(s)
  if (!d) return String(s)
  return d.toLocaleString('ar-EG', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    numberingSystem: 'latn',
  })
}

// ── القيم ────────────────────────────────────────────────────────
export function isMasked(v) { return v === MASK }
export function isLargePlaceholder(v) {
  return typeof v === 'string' && v.startsWith(LARGE_PREFIX)
}

/** نص للعرض. null => '—' (قيمة null حقيقية). */
export function formatValue(v) {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? 'نعم' : 'لا'
  if (typeof v === 'number') return String(v)
  if (typeof v === 'string') return v === '' ? '(فارغ)' : v
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

/**
 * يحوّل changes إلى صفوف عرض.
 * @returns {{ rows: Array<{field,label,hasOld,hasNew,old,new,oldUnknown,sensitive}>, truncated: boolean }}
 */
export function normalizeChanges(changes) {
  const out = { rows: [], truncated: false }
  if (!changes || typeof changes !== 'object' || Array.isArray(changes)) return out
  for (const [field, v] of Object.entries(changes)) {
    if (field === '_truncated') { out.truncated = true; continue }
    if (field.startsWith('_')) continue
    if (!v || typeof v !== 'object' || Array.isArray(v)) continue
    const sensitive = isSensitiveField(field)
    const hasOld = 'old' in v
    const hasNew = 'new' in v
    out.rows.push({
      field,
      label: fieldLabel(field),
      hasOld, hasNew,
      old: sensitive && hasOld && v.old != null ? MASK : v.old,
      new: sensitive && hasNew && v.new != null ? MASK : v.new,
      oldUnknown: v.old_unknown === true,
      sensitive,
    })
  }
  return out
}

/** ملخّص الحقول المتغيّرة للجدول: أول 3 حقول + "+N". */
export function changedFieldsSummary(fields, max = 3) {
  const list = Array.isArray(fields) ? fields.filter(f => typeof f === 'string' && !f.startsWith('_')) : []
  if (!list.length) return ''
  const shown = list.slice(0, max).map(fieldLabel).join('، ')
  return list.length > max ? `${shown} +${list.length - max}` : shown
}

// ── الاستعلام ────────────────────────────────────────────────────
/**
 * يبني query string من الفلاتر — القيم الفارغة تُستبعد.
 * entity_id لا يُرسل إلا مع entity_type (ورقم صحيح موجب).
 */
export function buildAuditQuery(f = {}, { limit = 50, offset = 0, tz = tzOffsetMinutes() } = {}) {
  const p = new URLSearchParams()
  const put = (k, v) => { if (v !== undefined && v !== null && String(v).trim() !== '') p.set(k, String(v).trim()) }
  put('entity_type', f.entity_type)
  if (f.entity_type && /^\d+$/.test(String(f.entity_id ?? '').trim())) put('entity_id', f.entity_id)
  put('user_id', f.user_id)
  put('action', f.action)
  put('date_from', f.date_from)
  put('date_to', f.date_to)
  put('q', f.q)
  if (f.date_from || f.date_to) p.set('tz_offset_minutes', String(tz))
  p.set('limit', String(limit))
  p.set('offset', String(offset))
  return p.toString()
}

/** تحقق من الفلاتر قبل الإرسال — يرجع رسالة خطأ أو null. */
export function validateAuditFilters(f = {}) {
  if (f.date_from && f.date_to && f.date_from > f.date_to) {
    return 'تاريخ البداية يجب أن يكون قبل أو يساوي تاريخ النهاية'
  }
  const id = String(f.entity_id ?? '').trim()
  if (id && !/^\d+$/.test(id)) return 'رقم الكيان يجب أن يكون رقماً صحيحاً'
  return null
}

/** الاستجابة المتوقعة {items,total,limit,offset}؛ نتسامح مع مصفوفة عارية. */
export function normalizeListResponse(r, fallbackLimit = 50, fallbackOffset = 0) {
  if (Array.isArray(r)) return { items: r, total: r.length, limit: fallbackLimit, offset: fallbackOffset }
  return {
    items: Array.isArray(r?.items) ? r.items : [],
    total: Number.isFinite(r?.total) ? r.total : (Array.isArray(r?.items) ? r.items.length : 0),
    limit: r?.limit ?? fallbackLimit,
    offset: r?.offset ?? fallbackOffset,
  }
}

/** مدة الاحتفاظ للعرض فقط. */
export function retentionLabel(ret) {
  if (!ret || typeof ret.days !== 'number') return ''
  return ret.days === 0 ? 'مدة الاحتفاظ: غير محدودة' : `مدة الاحتفاظ: ${ret.days} يوم`
}
