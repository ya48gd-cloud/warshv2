/**
 * API النسخ الاحتياطي للسوبر أدمن — /api/v1/super-admin/backups*
 *
 * منفصل عن api/client.js عمداً: السوبر أدمن له توكن مختلف (sessionStorage 'sa_token')
 * وصفحته خارج مصادقة الورش، فاستخدام request()/download() الخاصة بالورش كان
 * هيبعت erp_token غلط ويطلّع حدث "انتهت الجلسة" الخاص بالورش.
 *
 * قواعد مهمة:
 *  - الجذر (root) اللي ردّ على أول طلب ناجح بيتحفظ ويُستخدم لباقي الطلبات.
 *  - طلبات POST/DELETE لا تُعاد على جذر تاني بعد فشل غامض (timeout أثناء إنشاء
 *    نسخة ممكن يكون لسه شغال على السيرفر — الإعادة كانت هتعمل نسخة مكررة أو 409).
 *  - أي 401 => حدث 'erp:sa-session-expired' (تسجيل خروج من لوحة السوبر أدمن).
 */
import { extractErrorMessage } from './client'

const ROOTS = import.meta.env.VITE_API_URL
  ? [import.meta.env.VITE_API_URL]
  : ['', 'http://localhost:8000']
const PREFIX = '/api/v1/super-admin/backups'

// نص التأكيد الافتراضي (المصدر الأساسي هو confirm_text من prepare)
export const RESTORE_CONFIRM_TEXT = 'RESTORE'

let _root = null // الجذر الذي ردّ فعلياً (لا نعيد المحاولة على غيره بعد ما يُعرف)

function networkError() {
  return Object.assign(new Error('تعذر الاتصال بالخادم — تحقق من الاتصال بالشبكة'), { network: true })
}

function getSaToken() {
  try { return sessionStorage.getItem('sa_token') || '' } catch { return '' }
}

async function parseError(res) {
  let data = null
  try { data = await res.json() } catch { /* ليس JSON */ }
  const msg = extractErrorMessage(data, res.status)
  return Object.assign(new Error(msg), { status: res.status, data })
}

/**
 * ينفّذ الطلب ويرجّع Response ناجح (res.ok) أو يرمي Error.
 * GET: يجرّب الجذور بالترتيب (نفس منطق SuperAdmin.jsx) إلى أن يعرف الجذر الصحيح.
 * غير GET: يستخدم الجذر المعروف فقط (بدون إعادة على جذر آخر).
 */
async function rawRequest(method, path, { json } = {}) {
  const headers = {}
  const token = getSaToken()
  if (token) headers.Authorization = `Bearer ${token}`
  let body
  if (json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(json)
  }

  const isRead = method === 'GET'
  let roots
  if (_root !== null) roots = [_root]
  else if (isRead) roots = ROOTS
  else {
    // لم يُحدَّد الجذر بعد (نادر: لا يوجد GET سابق) — نكتشفه بطلب قراءة آمن أولاً
    try { await rawRequest('GET', '/status') } catch (e) { if (e.network) throw e }
    roots = _root !== null ? [_root] : [ROOTS[0]]
  }

  let res = null
  for (let i = 0; i < roots.length; i++) {
    const root = roots[i]
    try {
      res = await fetch(root + PREFIX + path, { method, headers, body })
    } catch {
      res = null
      continue // خطأ شبكة — للقراءة فقط نجرّب الجذر التالي (roots فيه عنصر واحد لغير القراءة)
    }
    // سيرفر static بدون API بيرجّع 404 HTML — نجرّب العنوان التالي (للقراءة فقط)
    const ct = res.headers.get('content-type') || ''
    if (isRead && _root === null && res.status === 404 && !ct.includes('json') && i < roots.length - 1) {
      res = null
      continue
    }
    _root = root
    break
  }
  if (!res) throw networkError()

  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('erp:sa-session-expired'))
    throw await parseError(res)
  }
  if (!res.ok) throw await parseError(res)
  return res
}

async function jsonRequest(method, path, opts) {
  const res = await rawRequest(method, path, opts)
  if (res.status === 204) return null
  try { return await res.json() } catch {
    throw Object.assign(new Error('استجابة غير صالحة من الخادم'), { status: 502 })
  }
}

function asArray(r) {
  return Array.isArray(r) ? r : (r?.items || [])
}

export const saBackupApi = {
  status: () => jsonRequest('GET', '/status'),
  list: (limit = 200) => jsonRequest('GET', `/?limit=${encodeURIComponent(limit)}`).then(asArray),
  instructions: () => jsonRequest('GET', '/restore-instructions'),
  create: () => jsonRequest('POST', '/'),
  remove: (id) => jsonRequest('DELETE', `/${encodeURIComponent(id)}`),
  verify: (id) => jsonRequest('POST', `/${encodeURIComponent(id)}/verify`),
  prepareRestore: (id) => jsonRequest('POST', `/${encodeURIComponent(id)}/restore/prepare`),
  restore: (id, confirmToken, confirmText) =>
    jsonRequest('POST', `/${encodeURIComponent(id)}/restore`, {
      json: { confirm_token: confirmToken, confirm_text: confirmText },
    }),

  /**
   * تنزيل الملف بتوكن السوبر أدمن ثم حفظه باسم fileName.
   * لا نعتمد على Content-Disposition (ممكن لا يكون مكشوفاً عبر CORS على localhost:8000).
   */
  download: async (id, fileName) => {
    const res = await rawRequest('GET', `/${encodeURIComponent(id)}/download`)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fileName || `backup-${id}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    return { size: blob.size }
  },
}

export default saBackupApi
