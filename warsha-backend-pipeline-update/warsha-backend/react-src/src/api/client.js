/**
 * API Client — matches backend routes exactly
 */

const BASES = ['/api/v1', 'http://localhost:8000/api/v1']

export function getToken() {
  return sessionStorage.getItem('erp_token')
}

// إصلاح شكوى متكررة: رسائل validation الخام من FastAPI/Pydantic بتيجي
// بالإنجليزي وسط تطبيق عربي بالكامل. نترجم أشهر الصيغ بدل ما نسيبها زي
// ما هي أو نستبدلها برسالة عامة تخفي السبب الحقيقي.
const VALIDATION_MSG_MAP = [
  [/field required/i, 'الحقل مطلوب'],
  [/input should be a valid integer/i, 'لازم يكون رقم صحيح'],
  [/input should be a valid number/i, 'لازم يكون رقم'],
  [/input should be a valid string/i, 'لازم يكون نص'],
  [/input should be a valid email address|value is not a valid email address/i, 'بريد إلكتروني غير صحيح'],
  [/input should be greater than (.+)/i, 'لازم يكون أكبر من $1'],
  [/input should be greater than or equal to (.+)/i, 'لازم يكون أكبر من أو يساوي $1'],
  [/input should be less than (.+)/i, 'لازم يكون أقل من $1'],
  [/input should be less than or equal to (.+)/i, 'لازم يكون أقل من أو يساوي $1'],
  [/string should have at least (\d+) character/i, 'لازم يكون $1 حرف على الأقل'],
  [/string should have at most (\d+) character/i, 'أقصى طول $1 حرف'],
  [/input should be a valid date/i, 'تاريخ غير صحيح'],
]

function translateValidationMsg(rawMsg) {
  for (const [pattern, replacement] of VALIDATION_MSG_MAP) {
    if (pattern.test(rawMsg)) return rawMsg.replace(pattern, replacement)
  }
  return rawMsg // ما لقيناش ترجمة معروفة — نسيبها زي ما هي بدل ما نخفي السبب
}

function formatValidationErrors(detailArray) {
  return detailArray.map(e => {
    const fieldPath = Array.isArray(e.loc) ? e.loc.filter(p => p !== 'body').join(' → ') : ''
    const msg = translateValidationMsg(e.msg || JSON.stringify(e))
    return fieldPath ? `${fieldPath}: ${msg}` : msg
  }).join('، ')
}

async function request(method, path, body = null) {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`

  for (const base of BASES) {
    try {
      const res = await fetch(`${base}${path}`, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
      })
      let data
      try { data = await res.json() }
      catch { if (res.ok) throw Object.assign(new Error('استجابة غير صالحة من الخادم'), { status: 502 }); data = {} }
      if (!res.ok) {
        // انتهت الجلسة — نُسجّل خروج تلقائي (ليس من /auth/me نفسه — ذلك يُعالَج في auth.jsx)
        if (res.status === 401 && !path.endsWith('/auth/me')) {
          sessionStorage.removeItem('erp_token')
          window.__ERP_FORCE_LOGOUT__ = true
          window.dispatchEvent(new CustomEvent('erp:session-expired'))
        }
        // استخراج رسالة الخطأ بشكل آمن
        const msg = typeof data.detail === 'string'
          ? data.detail
          : Array.isArray(data.detail)
            ? formatValidationErrors(data.detail)
            : data.message || 'خطأ في الخادم'
        throw Object.assign(new Error(msg), { status: res.status, data })
      }
      return data
    } catch (err) {
      if (err.status) throw err
      // network error — try next base
    }
  }
  throw new Error('تعذر الاتصال بالخادم')
}

// ── جلب استجابة خام بمصادقة (يحافظ على ترويسة Authorization) ───────────
async function authedFetch(path, opts = {}) {
  const token = getToken()
  const headers = { ...(opts.headers || {}) }
  if (token) headers['Authorization'] = `Bearer ${token}`
  let lastErr
  for (const base of BASES) {
    try {
      const res = await fetch(`${base}${path}`, { ...opts, headers })
      if (res.status === 401) {
        sessionStorage.removeItem('erp_token')
        window.__ERP_FORCE_LOGOUT__ = true
        window.dispatchEvent(new CustomEvent('erp:session-expired'))
        throw Object.assign(new Error('غير مسموح — يرجى تسجيل الدخول'), { status: 401 })
      }
      if (!res.ok) throw Object.assign(new Error('فشل الطلب من الخادم'), { status: res.status })
      return res
    } catch (err) {
      if (err.status) throw err
      lastErr = err // خطأ شبكة — جرّب العنوان التالي
    }
  }
  throw lastErr || new Error('تعذر الاتصال بالخادم')
}

// ── معالج موحّد لفشل تحميل بيانات خلفية (dropdowns، قوائم ثانوية...) ─────
// بدل ما نسيب .catch(() => []) صامت تمامًا، بنوري توست خفيف (warn) بيقول
// مين اللي فشل، وبنرجّع fallback عشان الواجهة متتكسرش. فيه "تهدئة" (cooldown)
// لكل تسمية (label) عشان لو نفس المصدر فشل كذا مرة ورا بعض (إعادة رندر،
// polling، إلخ) ميطلعش توست كل مرة — بيتقال مرة واحدة كل 15 ثانية بس.
const _bgErrCooldown = new Set()
export function bgErr(label, fallback) {
  return (err) => {
    if (!_bgErrCooldown.has(label)) {
      _bgErrCooldown.add(label)
      window.dispatchEvent(new CustomEvent('erp:toast', {
        detail: { msg: `تعذّر تحميل ${label}`, type: 'warn' },
      }))
      setTimeout(() => _bgErrCooldown.delete(label), 15000)
    }
    return typeof fallback === 'function' ? fallback() : fallback
  }
}

// ── تنزيل ملف بمصادقة (بديل window.open الذي لا يحمل التوكن) ────────────
export async function download(path, filename) {
  const res = await authedFetch(path)
  const blob = await res.blob()
  let name = filename
  const cd = res.headers.get('content-disposition')
  if (!name && cd) {
    const m = /filename\*?=(?:UTF-8'')?["']?([^"';]+)/i.exec(cd)
    if (m) name = decodeURIComponent(m[1])
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name || 'export'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// ── طباعة في نافذة منبثقة (popup) — تجلب HTML بمصادقة ثم تعرضه ─────────
export async function printPopup(path) {
  // نفتح النافذة فوراً داخل حدث الضغط (قبل await) حتى لا يحجبها مانع النوافذ المنبثقة
  const features = 'popup=yes,width=900,height=700,menubar=no,toolbar=no,location=no,status=no,scrollbars=yes,resizable=yes'
  const w = window.open('', '_blank', features)
  if (!w) {
    // هذا الملف خارج شجرة React فمفيش useToast() هنا — بنبعت حدث عام
    // والـ ToastProvider بيسمعه ويعرض نفس التوست الموحّد بدل alert()
    window.dispatchEvent(new CustomEvent('erp:toast', {
      detail: { msg: 'تعذّر فتح نافذة الطباعة — فعّل النوافذ المنبثقة (popups) لهذا الموقع', type: 'error' },
    }))
    return
  }
  w.document.write('<!doctype html><meta charset="utf-8"><title>جارٍ التحميل…</title><body style="font-family:sans-serif;direction:rtl;padding:24px">جارٍ تحضير الطباعة…</body>')
  try {
    const res = await authedFetch(path)
    const html = await res.text()
    w.document.open()
    w.document.write(html) // صفحات الطباعة تستدعي window.print تلقائياً
    w.document.close()
  } catch (err) {
    w.document.open()
    w.document.write('<!doctype html><meta charset="utf-8"><body style="font-family:sans-serif;direction:rtl;padding:24px;color:#b00">' + (err.message || 'فشل تحميل صفحة الطباعة') + '</body>')
    w.document.close()
  }
}

// ── معاينة ملف محمي في تبويب (يجلب بمصادقة ثم يفتح عبر blob URL) ───────
export async function openFile(path) {
  const w = window.open('', '_blank')
  try {
    const res = await authedFetch(path)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    if (w) w.location = url
    setTimeout(() => URL.revokeObjectURL(url), 60000)
  } catch (err) {
    if (w) { w.document.write('<body style="font-family:sans-serif;direction:rtl;padding:24px;color:#b00">' + (err.message || 'فشل فتح الملف') + '</body>'); w.document.close() }
  }
}

export const api = {
  get:    (path)        => request('GET', path),
  post:   (path, body)  => request('POST', path, body),
  put:    (path, body)  => request('PUT', path, body),
  patch:  (path, body)  => request('PATCH', path, body),
  delete: (path)        => request('DELETE', path),

  // ── Health (root level, NOT /api/v1) ──────────────────────────
  health: () => fetch('/health').then(r => r.json()),

  // ── Auth ──────────────────────────────────────────────────────
  login:          (u, p, tenantSlug) => request('POST', '/auth/login', { username: u, password: p, tenant_slug: tenantSlug || null }),
  // ملاحظة: /auth/signup أصبح مغلقاً في الباك (403 — "التسجيل متاح فقط عبر Super Admin").
  // إنشاء الورش الجديدة يتم فعلياً عبر /auth/signup-internal (نفس الشكل)، لذا نستخدمه هنا
  // حتى تعمل صفحة Signup.jsx لو أُعيد ربطها بالتوجيه مستقبلاً.
  signup:         (payload)          => request('POST', '/auth/signup-internal', payload),
  me:             ()     => request('GET',  '/auth/me'),
  changePassword: (o, n) => request('POST', '/auth/change-password', { old_password: o, new_password: n }),

  // ── Users (RBAC) ──────────────────────────────────────────────
  users: {
    list:          ()      => request('GET',    '/users/').then(r => Array.isArray(r) ? r : []),
    create:        (b)     => request('POST',   '/users/', b),
    update:        (id, b) => request('PUT',    `/users/${id}`, b),
    resetPassword: (id, p) => request('POST',   `/users/${id}/reset-password`, { new_password: p }),
    delete:        (id)    => request('DELETE', `/users/${id}`),
  },

  // ── Dashboard ─────────────────────────────────────────────────
  dashboard: {
    full:          () => request('GET', '/dashboard/full'),
    kpis:          () => request('GET', '/accounting/dashboard'),
    lowStock:      () => request('GET', '/inventory/alerts/low-stock'),
    alerts:        () => request('GET', '/dashboard/alerts'),
    cashflowChart: () => request('GET', '/dashboard/charts/cashflow'),
  },

  // ── Inventory ─────────────────────────────────────────────────
  inventory: {
    materials:  ()      => request('GET',    '/inventory/materials'),
    nextCode:   ()      => request('GET',    '/inventory/materials/next-code'),
    createMat:  (b)     => request('POST',   '/inventory/materials', b),
    updateMat:  (id, b) => request('PUT',    `/inventory/materials/${id}`, b),
    deleteMat:  (id)    => request('DELETE', `/inventory/materials/${id}`),
    categories: ()      => request('GET',    '/inventory/categories'),
    createCat:  (b)     => request('POST',   '/inventory/categories', b),
    deleteCat:  (id)    => request('DELETE', `/inventory/categories/${id}`),
    movements:     (id)    => request('GET',    `/inventory/movements/${id}`),
    createMov:     (b)     => request('POST',   '/inventory/movements', b),
    deleteMovement:(id)    => request('DELETE', `/inventory/movements/${id}`),
    alerts:        ()      => request('GET',    '/inventory/alerts/low-stock'),
    cuttingPlan:   (b)     => request('POST',   '/inventory/cutting-plan', b),
    barCuttingPlan: (b)    => request('POST',   '/inventory/bar-cutting-plan', b),
    sheetStock:    (materialId) => request('GET', `/inventory/sheet-stock${materialId ? `?material_id=${materialId}` : ''}`),
    createSheetStock: (b)  => request('POST',   '/inventory/sheet-stock', b),
    deleteSheetStock: (id) => request('DELETE', `/inventory/sheet-stock/${id}`),
    barStock:      (materialId) => request('GET', `/inventory/bar-stock${materialId ? `?material_id=${materialId}` : ''}`),
    createBarStock: (b)    => request('POST',   '/inventory/bar-stock', b),
    deleteBarStock: (id)   => request('DELETE', `/inventory/bar-stock/${id}`),
    commitCuttingPlan: (b) => request('POST',   '/inventory/cutting-plan/commit', b),
  },

  // ── Equipment & BOM ───────────────────────────────────────────
  equipment: {
    list:      ()      => request('GET',    '/equipment'),
    listAll:   ()      => request('GET',    '/equipment/list-all'),
    get:       (id)    => request('GET',    `/equipment/${id}`),
    create:    (b)     => request('POST',   '/equipment', b),
    update:    (id, b) => request('PUT',    `/equipment/${id}`, b),
    updateDescription: (id, description) => request('PATCH', `/equipment/${id}/description`, { description }),
    duplicate: (id, b) => request('POST',   `/equipment/${id}/duplicate`, b),
    delete:    (id)    => request('DELETE', `/equipment/${id}`),
    bom:       (id)    => request('GET',    `/equipment/${id}/bom`),
    createBOM: (b)     => request('POST',   '/equipment/bom', b),
    deleteBOM: (id)    => request('DELETE', `/equipment/bom/${id}`),
    bomParametricLength: (id, length, quantity = 1) =>
      request('GET', `/equipment/${id}/bom/parametric-length?length=${length}&quantity=${quantity}`),
    printBOM:  (id, length)    => printPopup(`/equipment/${id}/bom/print${length != null && length !== '' ? `?length=${length}` : ''}`),
    categories:     ()   => request('GET',    '/equipment/categories/list'),
    createCategory: (b)  => request('POST',   '/equipment/categories', b),
    deleteCategory: (id) => request('DELETE', `/equipment/categories/${id}`),
    // إضافة صغيرة: كانت مفقودة رغم وجود مسار الحذف بالباك-إند — راجع تقرير المراجعة
    deleteDimension: (id) => request('DELETE', `/equipment/dimension/${id}`),
  },

  // ── Accounting / Work Orders ───────────────────────────────────
  accounting: {
    workOrders:     ()      => request('GET',   '/accounting/work-orders'),
    nextWoCode:     ()      => request('GET',   '/accounting/work-orders/next-code'),
    createWO:       (b)     => request('POST',  '/accounting/work-orders', b),
    updateWO:       (id, b) => request('PUT',   `/accounting/work-orders/${id}`, b),
    updateWOStatus: (id, s) => request('PATCH', `/accounting/work-orders/${id}/status?status=${s}`, {}),
    deleteWO:       (id)    => request('DELETE', `/accounting/work-orders/${id}`),
    costLines:      (id)    => request('GET',   `/accounting/work-orders/${id}/cost-lines`),
    createCL:       (b)     => request('POST',  '/accounting/cost-lines', b),
    deleteCL:       (id)    => request('DELETE',`/accounting/cost-lines/${id}`),
    journals:       (id)    => request('GET',   `/accounting/work-orders/${id}/journal`),
    dashboard:      ()      => request('GET',   '/accounting/dashboard'),
    printWO:        (id)    => printPopup(`/accounting/work-orders/${id}/print`),
    printWoBOM:     (id)    => printPopup(`/accounting/work-orders/${id}/bom/print`),
    applyParametricBOM: (id) => request('POST', `/accounting/work-orders/${id}/apply-parametric-bom`, {}),
  },

  // ── Workers & Payroll ─────────────────────────────────────────
  treasury: {
    summary:        ()     => request('GET',    '/treasury/summary'),
    accounts:       ()     => request('GET',    '/treasury/accounts'),
    createAccount:  (b)    => request('POST',   '/treasury/accounts', b),
    updateAccount:  (id,b) => request('PUT',    `/treasury/accounts/${id}`, b),
    deleteAccount:  (id)   => request('DELETE', `/treasury/accounts/${id}`),
    categories:     ()     => request('GET',    '/treasury/categories'),
    createCategory: (b)    => request('POST',   '/treasury/categories', b),
    deleteCategory: (id)   => request('DELETE', `/treasury/categories/${id}`),
    transactions:   ()     => request('GET',    '/treasury/transactions'),
    addTxn:         (b)    => request('POST',   '/treasury/transactions', b),
    updateTxn:      (id,b) => request('PUT',    `/treasury/transactions/${id}`, b),
    deleteTxn:      (id)   => request('DELETE', `/treasury/transactions/${id}`),
    transfer:       (b)    => request('POST',   '/treasury/transfer', b),
    cheques:            (params) => request('GET', '/treasury/cheques' + (params ? `?${new URLSearchParams(params)}` : '')),
    updateChequeStatus: (id, status) => request('PATCH', `/treasury/cheques/${id}/status`, { cheque_status: status }),
  },

  // ── Fixed Assets ───────────────────────────────────────────────
  fixedAssets: {
    list:        (status) => request('GET',    '/fixed-assets' + (status ? `?status=${status}` : '')),
    get:         (id)     => request('GET',    `/fixed-assets/${id}`),
    create:      (b)      => request('POST',   '/fixed-assets', b),
    update:      (id, b)  => request('PATCH',  `/fixed-assets/${id}`, b),
    delete:      (id)     => request('DELETE', `/fixed-assets/${id}`),
    dispose:     (id)     => request('PATCH',  `/fixed-assets/${id}/dispose`),
    depreciate:  (id, b)  => request('POST',   `/fixed-assets/${id}/depreciate`, b),
  },

  workers: {
    list:          ()      => request('GET',    '/workers').then(r => Array.isArray(r) ? r : []),
    nextCode:      ()      => request('GET',    '/workers/next-code'),
    create:        (b)     => request('POST',   '/workers', b),
    update:        (id, b) => request('PUT',    `/workers/${id}`, b),
    delete:        (id)    => request('DELETE', `/workers/${id}`),
    payrollRuns:   ()      => request('GET',    '/workers/payroll'),
    createPayroll: (b)     => request('POST',   '/workers/payroll', b),
    payrollDetail: (id)    => request('GET',    `/workers/payroll/${id}`),
    deletePayroll: (id)    => request('DELETE', `/workers/payroll/${id}`),
    payPayroll:    (id)    => request('POST',   `/workers/payroll/${id}/pay`),
  },

  // ── Attendance ────────────────────────────────────────────────
  attendance: {
    list:    (d)     => request('GET',  `/attendance?date=${d}`),
    bulk:    (b)     => request('POST', '/attendance/bulk', b),
    summary: (d)     => request('GET',  `/attendance/summary?week=${d}`),
  },

  // ── Customers & Orders ────────────────────────────────────────
  customers: {
    list:        ()      => request('GET',    '/customers').then(r => Array.isArray(r) ? r : []),
    nextCode:    ()      => request('GET',    '/customers/next-code'),
    create:      (b)     => request('POST',   '/customers', b),
    update:      (id, b) => request('PUT',    `/customers/${id}`, b),
    delete:      (id)    => request('DELETE', `/customers/${id}`),
    orders:      (id)    => request('GET',    `/customers/${id}/orders`),
    ledger:      (id, p) => request('GET',    `/customers/${id}/ledger${p ? '?'+new URLSearchParams(p) : ''}`),
    locations:   (id)    => request('GET',    `/customers/${id}/locations`),
    addLocation: (id, b) => request('POST',   `/customers/${id}/locations`, b),
    updateLocation: (lid, b) => request('PUT', `/customers/locations/${lid}`, b),
    deleteLocation: (lid)    => request('DELETE', `/customers/locations/${lid}`),
    nextOrderCode:   ()           => request('GET',  '/customers/orders/next-code'),
    createOrder:     (b)          => request('POST', '/customers/orders', b),
    editOrder:       (id, b)      => request('PATCH', `/customers/orders/${id}/edit`, b),
    updateOrder:     (id, status) => request('PUT',  `/customers/orders/${id}?status=${status}`, {}),
    printOrderUrl:   (id)         => `/api/v1/customers/orders/${id}/print`,
    printStatement:  (id, mode = 'summary') => printPopup(`/customers/${id}/print-statement?mode=${mode}`),
  },

  // ── Sales ─────────────────────────────────────────────────────
  sales: {
    quotations:    ()      => request('GET',    '/sales/quotations'),
    createQuote:   (b)     => request('POST',   '/sales/quotations', b),
    updateQuote:   (id, b) => request('PUT',    `/sales/quotations/${id}`, b),
    deleteQuote:   (id)    => request('DELETE', `/sales/quotations/${id}`),
    toInvoice:     (id, code) => request('POST', `/sales/quotations/${id}/to-invoice?invoice_code=${encodeURIComponent(code)}`),
    toOrder:       (id, b)   => request('POST', `/sales/quotations/${id}/to-order`, b),
    // محطة 7: قالب خطة دفع على عرض السعر
    getPaymentTemplate:    (id)          => request('GET',  `/sales/quotations/${id}/payment-schedule-template`),
    createPaymentTemplate: (id, b, replace) =>
      request('POST', `/sales/quotations/${id}/payment-schedule-template${replace ? '?replace=true' : ''}`, b),
    toWorkOrder:   (id, b)   => request('POST', `/sales/orders/${id}/to-work-order`, b),
    advancePayment:(id, b)   => request('POST', `/sales/orders/${id}/advance-payment`, b),
    invoices:         ()      => request('GET',  '/sales/invoices'),
    nextInvoiceCode:  ()      => request('GET',  '/sales/invoices/next-code'),
    createInvoice:    (b)     => request('POST', '/sales/invoices', b),
    updateInvoice: (id, b) => request('PUT',  `/sales/invoices/${id}`, b),
    recordPayment: (id, b) => request('POST', `/sales/invoices/${id}/payment`, b),
    deleteInvoice: (id)    => request('DELETE', `/sales/invoices/${id}`),
    printInvoice:  (id)    => printPopup(`/sales/invoices/${id}/print`),
    // محطة 4: جدولة أقساط الفاتورة
    getPaymentSchedule:    (id)          => request('GET',  `/sales/invoices/${id}/payment-schedule`),
    patchInstallment:      (iid, insId, b) => request('PATCH', `/sales/invoices/${iid}/payment-schedule/${insId}`, b),
    payInstallment:        (iid, insId, b) => request('POST',  `/sales/invoices/${iid}/payment-schedule/${insId}/pay`, b),
    createPaymentSchedule: (id, b, replace) =>
      request('POST', `/sales/invoices/${id}/payment-schedule${replace ? '?replace=true' : ''}`, b),
  },

  // ── Purchases & Suppliers ─────────────────────────────────────
  purchases: {
    suppliers:          ()      => request('GET',    '/purchases/suppliers'),
    nextSupplierCode:   ()      => request('GET',    '/purchases/suppliers/next-code'),
    createSupplier:     (b)     => request('POST',   '/purchases/suppliers', b),
    updateSupplier: (id, b) => request('PUT',    `/purchases/suppliers/${id}`, b),
    deleteSupplier: (id)    => request('DELETE', `/purchases/suppliers/${id}`),
    supplierLedger: (id, p) => request('GET',    `/purchases/suppliers/${id}/ledger${p ? '?'+new URLSearchParams(p) : ''}`),
    invoices:       ()      => request('GET',    '/purchases/invoices'),
    getInvoice:     (id)    => request('GET',    `/purchases/invoices/${id}`),
    createInvoice:  (b)     => request('POST',   '/purchases/invoices', b),
    updateInvoice:  (id, b) => request('PUT',    `/purchases/invoices/${id}`, b),
    recordPayment:  (id, b) => request('POST',   `/purchases/invoices/${id}/payment`, b),
    recordSupplierPayment: (b) => request('POST', '/purchases/payments', b),
    deleteInvoice:  (id)    => request('DELETE', `/purchases/invoices/${id}`),
    printInvoice:   (id)    => printPopup(`/purchases/invoices/${id}/print`),
    uploadAttachment: async (id, file) => {
      const fd = new FormData(); fd.append('file', file)
      const token = getToken()
      const headers = {}
      if (token) headers['Authorization'] = `Bearer ${token}`
      let lastErrorMsg = null
      for (const base of BASES) {
        try {
          const res = await fetch(`${base}/purchases/invoices/${id}/attachment`, { method: 'POST', headers, body: fd })
          if (!res.ok) {
            try { lastErrorMsg = (await res.json())?.detail } catch (_) {}
            continue
          }
          return await res.json()
        } catch (_) { continue }
      }
      throw new Error(lastErrorMsg || 'فشل رفع الملف')
    },
  },

  // ── Field Service ─────────────────────────────────────────────
  fieldService: {
    orders:            ()       => request('GET',    '/field-service/orders'),
    getOrder:          (id)     => request('GET',    `/field-service/orders/${id}`),
    createOrder:       (b)      => request('POST',   '/field-service/orders', b),
    updateOrder:       (id, b)  => request('PUT',    `/field-service/orders/${id}`, b),
    deleteOrder:       (id)     => request('DELETE', `/field-service/orders/${id}`),
    updateOrderStatus: (id, b)  => request('PATCH',  `/field-service/orders/${id}/status`, b),
    assignWorkers:     (id, b)  => request('POST',   `/field-service/orders/${id}/assign`, b),
    // إضافة صغيرة: إلغاء تعيين فني — المسار موجود بالباك-إند لكن لم يكن مربوطاً بالواجهة
    removeAssignment:  (id, aid) => request('DELETE', `/field-service/orders/${id}/assign/${aid}`),
    recordVisit:       (id, b)  => request('POST',   `/field-service/orders/${id}/visit`, b),
    getVisits:         (id)     => request('GET',    `/field-service/orders/${id}/visits`),
    assets:            (cid)    => request('GET',    `/field-service/assets${cid ? `?customer_id=${cid}` : ''}`),
    getAsset:          (id)     => request('GET',    `/field-service/assets/${id}`),
    createAsset:       (b)      => request('POST',   '/field-service/assets', b),
    updateAsset:       (id, b)  => request('PUT',    `/field-service/assets/${id}`, b),
    deleteAsset:       (id)     => request('DELETE', `/field-service/assets/${id}`),
    addMovement:       (id, b)  => request('POST',   `/field-service/assets/${id}/movements`, b),
    dashboard:         ()       => request('GET',    '/field-service/dashboard'),
    calendar:          (m, y)   => request('GET',    `/field-service/calendar?month=${m}&year=${y}`),
    listLabor:         (oid)    => request('GET',    `/field-service/orders/${oid}/labor`),
    addLabor:          (oid, b) => request('POST',   `/field-service/orders/${oid}/labor`, b),
    deleteLabor:       (oid, id)=> request('DELETE', `/field-service/orders/${oid}/labor/${id}`),
    listTools:         (oid)    => request('GET',    `/field-service/orders/${oid}/tools`),
    withdrawTool:      (oid, b) => request('POST',   `/field-service/orders/${oid}/tools`, b),
    returnTool:        (oid, wid, b) => request('PATCH', `/field-service/orders/${oid}/tools/${wid}/return`, b),
    deleteTool:        (oid, wid)   => request('DELETE', `/field-service/orders/${oid}/tools/${wid}`),
  },

  // ── Reports ───────────────────────────────────────────────────
  reports: {
    inventoryValue:    ()       => request('GET', '/reports/inventory/value'),
    inventoryMovement: (p)      => request('GET', `/reports/inventory/movement?${new URLSearchParams(p)}`),
    inventorySlowMoving: (days) => request('GET', `/reports/inventory/slow-moving?days=${days||90}`),
    inventorySummary:  ()       => request('GET', '/reports/inventory/summary'),
    inventoryTx:       (p)      => request('GET', `/reports/inventory/transactions?${new URLSearchParams(p)}`),
    productionSummary: (p)      => request('GET', `/reports/production/summary?${new URLSearchParams(p)}`),
    scrapAnalysis:     (p)      => request('GET', `/reports/production/scrap-analysis?${new URLSearchParams(p)}`),
    salesSummary:      (p)      => request('GET', `/reports/sales/summary?${new URLSearchParams(p)}`),
    salesAging:        ()       => request('GET', '/reports/sales/aging'),
    installmentsUpcoming: (days=7) => request('GET', `/reports/sales/installments/upcoming?days=${days}`),
    installmentsOverdue:  ()       => request('GET', '/reports/sales/installments/overdue'),
    installmentsAll:      (filter='all') => request('GET', `/reports/sales/installments/all?filter=${filter}`),
    installmentsBadge:    ()       => request('GET', '/reports/sales/installments/badge'),
    salesByCustomer:   (p)      => request('GET', `/reports/sales/by-customer?${new URLSearchParams(p)}`),
    purchasesSummary:  (p)      => request('GET', `/reports/purchases/summary?${new URLSearchParams(p)}`),
    purchasesBySupplier: (p)    => request('GET', `/reports/purchases/by-supplier?${new URLSearchParams(p)}`),
    workersSummary:    (p)      => request('GET', `/reports/workers/summary?${new URLSearchParams(p)}`),
    executiveDashboard:()       => request('GET', '/reports/dashboard'),
    profitability:     (p)      => request('GET', `/reports/profitability?${new URLSearchParams(p)}`),
    cashflow:          (p)      => request('GET', `/reports/cashflow?${new URLSearchParams(p)}`),
    laborPerformance:  (p)      => request('GET', `/reports/labor-performance?${new URLSearchParams(p)}`),
    periodComparison:  (p)      => request('GET', `/reports/period-comparison?${new URLSearchParams(p)}`),
  },

  // ── Production ────────────────────────────────────────────────
  alerts:     ()      => request('GET',  '/production/alerts'),
  production: {
    reserve:      (b)    => request('POST', `/production/reserve/${b.work_order_id}`, b),
    issue:        (b)    => request('POST', `/production/issue-by-reservation?reservation_id=${b.reservation_id}`),
    scrapList:    ()     => request('GET',  '/production/scrap/report'),
    recordScrap:  (b)    => request('POST', '/production/scrap', b),
    scrapInventory: ()   => request('GET',  '/production/scrap/inventory'),
    scrapSales:     ()   => request('GET',  '/production/scrap-sales'),
    scrapSaleDetail:(id) => request('GET',  `/production/scrap-sales/${id}`),
    createScrapSale:(b)  => request('POST', '/production/scrap-sales', b),
    mrp:          ()     => request('GET',  '/production/mrp/suggestions'),
    runMrp:       ()     => request('POST', '/production/mrp/run', {}),
    reservations: (id)   => request('GET',  `/production/${id}/reservations`),
    cancelReservation: (id) => request('DELETE', `/production/reservations/${id}`),
    stockCounts:  ()     => request('GET',  '/production/stock-count'),
    startCount:   ()     => request('POST', '/production/stock-count', {}),
    scanLine:     (id,b) => request('POST', `/production/stock-count/${id}/scan`, b),
    closeCount:   (id)   => request('POST', `/production/stock-count/${id}/close`, {}),
    team: {
      list:   (woId)        => request('GET',    `/production/work-orders/${woId}/team`),
      add:    (woId, b)     => request('POST',   `/production/work-orders/${woId}/team`, b),
      hours:  (woId, mid, h)=> request('PATCH',  `/production/work-orders/${woId}/team/${mid}/hours?actual_hours=${h}`),
      remove: (woId, mid)   => request('DELETE', `/production/work-orders/${woId}/team/${mid}`),
    },
    // محطة 17: لوحة تتبع دورة حياة المعدة — "المعدة X من أمر البيع Y فين؟"
    pipeline:         (q)   => request('GET', `/production/pipeline${q ? `?q=${encodeURIComponent(q)}` : ''}`),
    pipelineDetail:   (id)  => request('GET', `/production/pipeline/${id}`),
    pipelineBackfill: (dryRun = true) => request('POST', `/production/pipeline/backfill?dry_run=${dryRun}`),
  },

  // ── Workshop Profile ──────────────────────────────────────────
  workshop: {
    get:        ()  => request('GET', '/workshop/profile'),
    update:     (b) => request('PUT', '/workshop/profile', b),
    deleteLogo: ()  => request('DELETE', '/workshop/profile/logo'),
    logoUrl:    (cacheBust) => `${BASES[0]}/workshop/profile/logo${cacheBust ? `?t=${cacheBust}` : ''}`,
  },

  // ── Delivery Orders — أوامر التوريد ──────────────────────────
  deliveryOrders: {
    list:         ()           => request('GET',    '/delivery-orders'),
    get:          (id)         => request('GET',    `/delivery-orders/${id}`),
    create:       (body)       => request('POST',   '/delivery-orders', body),
    setStatus:    (id, status) => request('PATCH',  `/delivery-orders/${id}/status`, { status }),
    delete:       (id)         => request('DELETE', `/delivery-orders/${id}`),
  },

  // ── External Projects — المشاريع الخارجية ────────────────────
  extProjects: {
    list:             ()           => request('GET',    '/ext-projects'),
    dashboard:        ()           => request('GET',    '/ext-projects/dashboard'),
    get:              (id)         => request('GET',    `/ext-projects/${id}`),
    create:           (b)          => request('POST',   '/ext-projects', b),
    update:           (id, b)      => request('PUT',    `/ext-projects/${id}`, b),
    updateStatus:     (id, status) => request('PATCH',  `/ext-projects/${id}/status?status=${status}`),
    delete:           (id)         => request('DELETE', `/ext-projects/${id}`),
    costSummary:      (id)         => request('GET',    `/ext-projects/${id}/cost-summary`),
    addMember:        (id, b)      => request('POST',   `/ext-projects/${id}/team`, b),
    addSiteAdvance:   (pid, mid, amount) => request('PATCH', `/ext-projects/${pid}/team/${mid}/advance?amount=${amount}`),
    removeMember:     (pid, mid)   => request('DELETE', `/ext-projects/${pid}/team/${mid}`),
    recordAttendance: (id, b)      => request('POST',   `/ext-projects/${id}/attendance`, b),
    getAttendance:    (id, date)   => request('GET',    `/ext-projects/${id}/attendance/${date}`),
    addTool:          (id, b)      => request('POST',   `/ext-projects/${id}/tools`, b),
    returnTool:       (pid, tid, b) => request('PATCH', `/ext-projects/${pid}/tools/${tid}/return?returned_qty=${b.returned_qty}&condition_in=${b.condition_in}`),
    deleteTool:       (pid, tid)   => request('DELETE', `/ext-projects/${pid}/tools/${tid}`),
    addMaterial:      (id, b)      => request('POST',   `/ext-projects/${id}/materials`, b),
    deleteMaterial:   (pid, mid)   => request('DELETE', `/ext-projects/${pid}/materials/${mid}`),
    addTrip:          (id, b)      => request('POST',   `/ext-projects/${id}/trips`, b),
    deleteTrip:       (pid, tid)   => request('DELETE', `/ext-projects/${pid}/trips/${tid}`),
    addExpense:       (id, b)      => request('POST',   `/ext-projects/${id}/expenses`, b),
    deleteExpense:    (pid, eid)   => request('DELETE', `/ext-projects/${pid}/expenses/${eid}`),
    addProgress:      (id, b)      => request('POST',   `/ext-projects/${id}/progress`, b),
    deleteProgress:   (pid, rid)   => request('DELETE', `/ext-projects/${pid}/progress/${rid}`),
    addPayment:       (id, b)      => request('POST',   `/ext-projects/${id}/payments`, b),
    receivePayment:   (pid, payid, b) => request('PATCH', `/ext-projects/${pid}/payments/${payid}/receive`, b),
    deletePayment:    (pid, payid) => request('DELETE', `/ext-projects/${pid}/payments/${payid}`),
    printReport:      (id)         => printPopup(`/ext-projects/${id}/print`),
  },
}

export default api

// Patch: add analytics + payment history endpoints
const _reportsOrig = api.reports
api.reports = {
  ..._reportsOrig,
  monthlySales:     () => request('GET', '/reports/analytics/monthly-sales'),
  productionByType: () => request('GET', '/reports/analytics/production-by-type'),
  paymentHistory:   (p) => request('GET', `/reports/payments/history?${new URLSearchParams(p)}`),
}

// ── New v9 API endpoints ─────────────────────────────────────
api.supplyContracts = {
  list:            (p)    => request('GET',   `/supply-contracts${p ? '?'+new URLSearchParams(p) : ''}`),
  get:             (id)   => request('GET',   `/supply-contracts/${id}`),
  nextCode:        ()     => request('GET',   '/supply-contracts/next-code'),
  create:          (b)    => request('POST',  '/supply-contracts', b),
  activate:        (id)   => request('PATCH', `/supply-contracts/${id}/activate`),
  cancel:          (id)   => request('PATCH', `/supply-contracts/${id}/cancel`),
  delete:          (id)   => request('DELETE',`/supply-contracts/${id}`),
  fromQuotation:   (qid)  => request('POST',  `/supply-contracts/from-quotation/${qid}`),
  deliver:              (id, b)    => request('POST',   `/supply-contracts/${id}/deliver`, b),
  getScheduleTemplate:  (id)       => request('GET',    `/supply-contracts/${id}/payment-schedule-template`),
  saveScheduleTemplate: (id, b, r) => request('POST',   `/supply-contracts/${id}/payment-schedule-template${r ? '?replace=true' : ''}`, b),
  deleteScheduleTemplate:(id)      => request('DELETE', `/supply-contracts/${id}/payment-schedule-template`),
}

api.purchaseOrders = {
  list:     (p)   => request('GET',    `/purchase-orders${p ? '?'+new URLSearchParams(p) : ''}`),
  get:      (id)  => request('GET',    `/purchase-orders/${id}`),
  nextCode: ()    => request('GET',    '/purchase-orders/next-code'),
  create:   (b)   => request('POST',   '/purchase-orders', b),
  update:   (id,b)=> request('PUT',    `/purchase-orders/${id}`, b),
  approve:  (id)  => request('PATCH',  `/purchase-orders/${id}/approve`),
  cancel:   (id)  => request('PATCH',  `/purchase-orders/${id}/cancel`),
  receive:  (id,b)=> request('POST',   `/purchase-orders/${id}/receive`, b),
  delete:   (id)  => request('DELETE', `/purchase-orders/${id}`),
  dashboard:()    => request('GET',    '/purchase-orders/dashboard/summary'),
}

api.accounts = {
  chart:          ()    => request('GET',  '/accounts/chart'),
  seed:           ()    => request('POST', '/accounts/chart/seed'),
  entries:        (p)   => request('GET',  `/accounts/entries${p ? '?'+new URLSearchParams(p) : ''}`),
  createEntry:    (b)   => request('POST', '/accounts/entries', b),
  postEntry:      (id)  => request('PATCH', `/accounts/entries/${id}/post`),
  trialBalance:   (p)   => request('GET',  `/accounts/trial-balance${p ? '?'+new URLSearchParams(p) : ''}`),
  balanceSheet:   (p)   => request('GET',  `/accounts/balance-sheet${p ? '?'+new URLSearchParams(p) : ''}`),
  incomeStatement:(p)   => request('GET',  `/accounts/income-statement${p ? '?'+new URLSearchParams(p) : ''}`),
  vatReturn:      (p)   => request('GET',  `/accounts/vat-return${p ? '?'+new URLSearchParams(p) : ''}`),
  allocate:       (pid,b)=> request('POST', `/accounts/payments/${pid}/allocate`, b),
  taxSummary:        (p) => request('GET',  `/accounts/tax-summary${p ? '?'+new URLSearchParams(p) : ''}`),
  recordTaxPayment:  (b) => request('POST', '/accounts/tax-payment', b),
  recordTaxProvision:(b) => request('POST', '/accounts/tax-provision', b),
}

api.stages = {
  list:     (woId) => request('GET',   `/production/stages/${woId}`),
  seed:     (woId) => request('POST',  `/production/stages/${woId}/seed`),
  create:   (woId,b)=> request('POST', `/production/stages/${woId}`, b),
  start:    (id)   => request('PATCH', `/production/stages/${id}/start`),
  complete: (id, hrs) => request('PATCH', `/production/stages/${id}/complete${hrs ? `?actual_hours=${hrs}` : ''}`),
  skip:     (id, reason) => request('PATCH', `/production/stages/${id}/skip${reason ? `?reason=${encodeURIComponent(reason)}` : ''}`),
  update:   (id,b) => request('PUT',   `/production/stages/${id}`, b),
  delete:   (id)   => request('DELETE',`/production/stages/${id}`),
  dashboard:()     => request('GET',   '/production/stages/dashboard/overview'),
}

api.qc = {
  list:       (p)   => request('GET',  `/production/v2/quality${p ? '?'+new URLSearchParams(p) : ''}`),
  create:     (b)   => request('POST', '/production/v2/quality', b),
  update:     (id,b)=> request('PUT',  `/production/v2/quality/${id}`, b),
  delete:     (id)  => request('DELETE',`/production/v2/quality/${id}`),
  dashboard:  ()    => request('GET',  '/production/v2/quality/dashboard'),
}

api.schedule = {
  list:       (p)   => request('GET',  `/production/v2/schedules${p ? '?'+new URLSearchParams(p) : ''}`),
  create:     (b)   => request('POST', '/production/v2/schedules', b),
  update:     (id,b)=> request('PATCH',`/production/v2/schedules/${id}`, b),
  start:      (id)  => request('PATCH',`/production/v2/schedules/${id}/start`),
  complete:   (id)  => request('PATCH',`/production/v2/schedules/${id}/complete`),
  gantt:      ()    => request('GET',  '/production/v2/schedules/gantt'),
}

api.batches = {
  list:         (p)     => request('GET',   `/inventory/batches${p ? '?'+new URLSearchParams(p) : ''}`),
  get:          (id)    => request('GET',   `/inventory/batches/${id}`),
  create:       (b)     => request('POST',  '/inventory/batches', b),
  issue:        (id, b) => request('POST',  `/inventory/batches/${id}/issue`, b),
  traceability: (matId) => request('GET',   `/inventory/batches/material/${matId}/traceability`),
  expiryAlerts: (days)  => request('GET',   `/inventory/batches/dashboard/expiry-alerts${days ? `?days_ahead=${days}` : ''}`),
}

// محطة 10: المخازن المنطقية
api.warehouses = {
  list:         (includeInactive) => request('GET',    `/warehouses${includeInactive ? '?include_inactive=true' : ''}`),
  create:       (b)                => request('POST',   '/warehouses', b),
  update:       (id, b)            => request('PUT',    `/warehouses/${id}`, b),
  remove:       (id)                => request('DELETE', `/warehouses/${id}`),
  toggleActive: (id)                => request('POST',   `/warehouses/${id}/toggle-active`),
}

api.creditNotes = {
  list:          (p)   => request('GET',   `/credit-notes${p ? '?'+new URLSearchParams(p) : ''}`),
  get:           (id)  => request('GET',   `/credit-notes/${id}`),
  create:        (b)   => request('POST',  '/credit-notes', b),
  update:        (id, b) => request('PUT',  `/credit-notes/${id}`, b),
  issue:         (id)  => request('PATCH', `/credit-notes/${id}/issue`),
  cancel:        (id)  => request('PATCH', `/credit-notes/${id}/cancel`),
  delete:        (id)  => request('DELETE',`/credit-notes/${id}`),
  fromInvoice:   (iid) => request('POST',  `/credit-notes/from-invoice/${iid}`),
}


api.workCompletionCerts = {
  list:         (p)   => request('GET',   `/work-completion-certs${p ? '?'+new URLSearchParams(p) : ''}`),
  get:          (id)  => request('GET',   `/work-completion-certs/${id}`),
  create:       (b)   => request('POST',  '/work-completion-certs', b),
  issue:        (id)  => request('PATCH', `/work-completion-certs/${id}/issue`),
  delete:       (id)  => request('DELETE',`/work-completion-certs/${id}`),
  fromContract: (cid) => request('POST',  `/work-completion-certs/from-contract/${cid}`),
}

api.warrantyCerts = {
  list:   (p)   => request('GET',   `/warranty-certs${p ? '?'+new URLSearchParams(p) : ''}`),
  get:    (id)  => request('GET',   `/warranty-certs/${id}`),
  create: (b)   => request('POST',  '/warranty-certs', b),
  issue:  (id)  => request('PATCH', `/warranty-certs/${id}/issue`),
  delete: (id)  => request('DELETE',`/warranty-certs/${id}`),
}

// Patch: add aging print + CSV export
const _reportsOrig2 = api.reports
api.reports = {
  ..._reportsOrig2,
  agingPrint: () => printPopup('/reports/sales/aging/print'),
}

api.export = {
  materials:  () => download('/reports/export/materials',   'materials.csv'),
  invoices:   () => download('/reports/export/invoices',    'invoices.csv'),
  customers:  () => download('/reports/export/customers',   'customers.csv'),
  workOrders: () => download('/reports/export/work-orders', 'work-orders.csv'),
  quotations: () => download('/reports/export/quotations',  'quotations.csv'),
  customerLedger: (id, p) => download(`/reports/export/customer-ledger/${id}${p ? '?'+new URLSearchParams(p) : ''}`),
  supplierLedger: (id, p) => download(`/reports/export/supplier-ledger/${id}${p ? '?'+new URLSearchParams(p) : ''}`),
  profitability: (p) => download(`/reports/export/profitability${p ? '?'+new URLSearchParams(p) : ''}`),
  cashflow: (p) => download(`/reports/export/cashflow${p ? '?'+new URLSearchParams(p) : ''}`),
  laborPerformance: (p) => download(`/reports/export/labor-performance${p ? '?'+new URLSearchParams(p) : ''}`),
  periodComparison: (p) => download(`/reports/export/period-comparison${p ? '?'+new URLSearchParams(p) : ''}`),
}

// رفع مرفق (إثبات دفع أو غيره) لأي كيان
api.uploadAttachment = async (entityType, entityId, file, notes = '') => {
  const fd = new FormData()
  fd.append('file', file)
  if (notes) fd.append('notes', notes)
  const token = getToken()
  const headers = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  let lastErrorMsg = null
  for (const base of BASES) {
    try {
      const res = await fetch(`${base}/purchases/attach?entity_type=${entityType}&entity_id=${entityId}`, {
        method: 'POST', headers, body: fd,
      })
      if (!res.ok) {
        try { lastErrorMsg = (await res.json())?.detail } catch (_) {}
        continue
      }
      return await res.json()
    } catch (_) { continue }
  }
  throw new Error(lastErrorMsg || 'فشل رفع الملف')
}

// جلب مرفقات كيان معين (مثل إثبات دفعة عميل/مورد)
api.listAttachments = (entityType, entityId) =>
  request('GET', `/purchases/attachments?entity_type=${entityType}&entity_id=${entityId}`)

// إتاحة الدوال المساعدة عبر كائن api أيضاً
api.print = printPopup
api.download = download
api.openFile = openFile
