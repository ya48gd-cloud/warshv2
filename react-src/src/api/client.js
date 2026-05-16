/**
 * API Client — matches backend routes exactly
 */

const BASES = ['/api/v1', 'http://localhost:8000/api/v1']

export function getToken() {
  return localStorage.getItem('erp_token')
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
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw Object.assign(new Error(data.detail || 'خطأ في الخادم'), { status: res.status, data })
      return data
    } catch (err) {
      if (err.status) throw err
      // network error — try next base
    }
  }
  throw new Error('تعذر الاتصال بالخادم')
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
  login:          (u, p) => request('POST', '/auth/login', { username: u, password: p }),
  me:             ()     => request('GET',  '/auth/me'),
  changePassword: (o, n) => request('POST', '/auth/change-password', { old_password: o, new_password: n }),

  // ── Users (RBAC) ──────────────────────────────────────────────
  users: {
    list:          ()      => request('GET',    '/users/'),
    create:        (b)     => request('POST',   '/users/', b),
    update:        (id, b) => request('PUT',    `/users/${id}`, b),
    resetPassword: (id, p) => request('POST',   `/users/${id}/reset-password`, { new_password: p }),
    delete:        (id)    => request('DELETE', `/users/${id}`),
  },

  // ── Dashboard ─────────────────────────────────────────────────
  dashboard: {
    full:     () => request('GET', '/dashboard/full'),
    kpis:     () => request('GET', '/accounting/dashboard'),
    lowStock: () => request('GET', '/inventory/alerts/low-stock'),
  },

  // ── Inventory ─────────────────────────────────────────────────
  inventory: {
    materials:  ()      => request('GET',    '/inventory/materials'),
    createMat:  (b)     => request('POST',   '/inventory/materials', b),
    updateMat:  (id, b) => request('PUT',    `/inventory/materials/${id}`, b),
    deleteMat:  (id)    => request('DELETE', `/inventory/materials/${id}`),
    categories: ()      => request('GET',    '/inventory/categories'),
    movements:  (id)    => request('GET',    `/inventory/movements/${id}`),
    createMov:  (b)     => request('POST',   '/inventory/movements', b),
    alerts:     ()      => request('GET',    '/inventory/alerts/low-stock'),
  },

  // ── Equipment & BOM ───────────────────────────────────────────
  equipment: {
    list:      ()      => request('GET',    '/equipment'),
    get:       (id)    => request('GET',    `/equipment/${id}`),
    create:    (b)     => request('POST',   '/equipment', b),
    update:    (id, b) => request('PUT',    `/equipment/${id}`, b),
    delete:    (id)    => request('DELETE', `/equipment/${id}`),
    bom:       (id)    => request('GET',    `/equipment/${id}/bom`),
    createBOM: (b)     => request('POST',   '/equipment/bom', b),
    deleteBOM: (id)    => request('DELETE', `/equipment/bom/${id}`),
  },

  // ── Accounting / Work Orders ───────────────────────────────────
  accounting: {
    workOrders:     ()      => request('GET',   '/accounting/work-orders'),
    createWO:       (b)     => request('POST',  '/accounting/work-orders', b),
    updateWO:       (id, b) => request('PUT',   `/accounting/work-orders/${id}`, b),
    updateWOStatus: (id, s) => request('PATCH', `/accounting/work-orders/${id}/status?status=${s}`, {}),
    costLines:      (id)    => request('GET',   `/accounting/work-orders/${id}/cost-lines`),
    createCL:       (b)     => request('POST',  '/accounting/cost-lines', b),
    deleteCL:       (id)    => request('DELETE',`/accounting/cost-lines/${id}`),
    journals:       (id)    => request('GET',   `/accounting/work-orders/${id}/journal`),
    dashboard:      ()      => request('GET',   '/accounting/dashboard'),
  },

  // ── Workers & Payroll ─────────────────────────────────────────
  workers: {
    list:          ()      => request('GET',    '/workers/'),
    create:        (b)     => request('POST',   '/workers/', b),
    update:        (id, b) => request('PUT',    `/workers/${id}`, b),
    delete:        (id)    => request('DELETE', `/workers/${id}`),
    payrollRuns:   ()      => request('GET',    '/workers/payroll'),
    createPayroll: (b)     => request('POST',   '/workers/payroll', b),
    payrollDetail: (id)    => request('GET',    `/workers/payroll-runs/${id}`),
  },

  // ── Attendance ────────────────────────────────────────────────
  attendance: {
    list:    (d)     => request('GET',  `/attendance/?date=${d}`),
    bulk:    (b)     => request('POST', '/attendance/bulk', b),
    summary: (d)     => request('GET',  `/attendance/summary?week=${d}`),
  },

  // ── Customers & Orders ────────────────────────────────────────
  customers: {
    list:        ()      => request('GET',    '/customers/'),
    create:      (b)     => request('POST',   '/customers/', b),
    update:      (id, b) => request('PUT',    `/customers/${id}`, b),
    delete:      (id)    => request('DELETE', `/customers/${id}`),
    orders:      (id)    => request('GET',    `/customers/${id}/orders`),
    createOrder: (b)     => request('POST',   '/customers/orders', b),
  },

  // ── Sales ─────────────────────────────────────────────────────
  sales: {
    quotations:    ()      => request('GET',  '/sales/quotations'),
    createQuote:   (b)     => request('POST', '/sales/quotations', b),
    updateQuote:   (id, b) => request('PUT',  `/sales/quotations/${id}`, b),
    invoices:      ()      => request('GET',  '/sales/invoices'),
    createInvoice: (b)     => request('POST', '/sales/invoices', b),
    updateInvoice: (id, b) => request('PUT',  `/sales/invoices/${id}`, b),
    recordPayment: (id, b) => request('POST', `/sales/invoices/${id}/payment`, b),
  },

  // ── Production ────────────────────────────────────────────────
  production: {
    reserve:      (b)    => request('POST', '/production/reserve', b),
    issue:        (b)    => request('POST', '/production/issue', b),
    scrapList:    ()     => request('GET',  '/production/scrap'),
    recordScrap:  (b)    => request('POST', '/production/scrap', b),
    mrp:          ()     => request('GET',  '/production/mrp'),
    runMrp:       ()     => request('POST', '/production/mrp/run', {}),
    reservations: (id)   => request('GET',  `/production/reservations/${id}`),
    stockCounts:  ()     => request('GET',  '/production/stock-count'),
    startCount:   ()     => request('POST', '/production/stock-count/start', {}),
    scanLine:     (id,b) => request('POST', `/production/stock-count/${id}/scan`, b),
    closeCount:   (id)   => request('POST', `/production/stock-count/${id}/close`, {}),
  },
}

export default api
