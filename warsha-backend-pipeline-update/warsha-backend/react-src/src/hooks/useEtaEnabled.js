/**
 * useEtaEnabled — حالة تفعيل الفاتورة الإلكترونية (GET /einvoice/status، متاح لكل الأدوار).
 *
 * كاش على مستوى الموديول مرتبط بالتوكن (حتى لا تتسرب حالة ورشة لورشة أخرى بعد تسجيل
 * خروج/دخول بدون إعادة تحميل)، وبمدة صلاحية قصيرة. الفشل = "غير مفعّلة" بدون كاش دائم.
 * بعد حفظ الإعدادات استدعِ invalidateEtaStatus().
 *
 * لا نستخدم GET /settings للتحقق من التفعيل لأنه للمدير فقط (403 للمحاسب).
 */
import { useEffect, useState } from 'react'
import api, { getToken } from '../api/client'

const TTL_MS = 60 * 1000
let cache = null            // { data, at, token }
let inflight = null
const listeners = new Set()

const emit = (v) => listeners.forEach(fn => { try { fn(v) } catch { /* ignore */ } })
const validCache = () => cache && cache.token === getToken() ? cache : null

function loadStatus() {
  if (inflight) return inflight
  const token = getToken()
  inflight = api.einvoice.status()
    .then((data) => { cache = { data, at: Date.now(), token }; emit(data) })
    .catch(() => { emit({ enabled: false, _failed: true }) })   // لا كاش للفشل
    .finally(() => { inflight = null })
  return inflight
}

export function invalidateEtaStatus() {
  cache = null
  return loadStatus()
}

export default function useEtaEnabled() {
  const [st, setSt] = useState(() => validCache()?.data || null)

  useEffect(() => {
    let alive = true
    const fn = (v) => { if (alive) setSt(v) }
    listeners.add(fn)
    const c = validCache()
    if (c) setSt(c.data)
    if (!c || Date.now() - c.at > TTL_MS) loadStatus()
    return () => { alive = false; listeners.delete(fn) }
  }, [])

  return {
    loading: st == null,
    enabled: !!st?.enabled,
    environment: st?.environment || null,
    ready: !!st?.ready,
    signerMode: st?.signer_mode || null,
    failed: !!st?._failed,
  }
}
