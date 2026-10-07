/**
 * عدّاد الموافقات المشترك (Topbar + Sidebar) — Poller واحد لكل التطبيق.
 * - فشل صامت (بدون toast/bgErr) ويحتفظ بآخر قيمة.
 * - 403/404 => available=false (الجرس يختفي).
 * - يتحدّث كل 60 ثانية (يتخطى لو التبويب مخفي) وعند 'erp:approvals-changed' وعند focus.
 */
import { useEffect, useSyncExternalStore } from 'react'
import api from '../api/client'

const INTERVAL_MS = 60_000
const INITIAL = { inbox: 0, mineReady: 0, minePending: 0, available: true }

let state = INITIAL
let subscribers = 0
let timer = null
let inflight = false
let generation = 0          // يُبطل ردود قديمة بعد إيقاف/إعادة تشغيل الـ poller
const listeners = new Set()

function setState(next) {
  if (next.inbox === state.inbox && next.mineReady === state.mineReady &&
      next.minePending === state.minePending && next.available === state.available) return
  state = next
  listeners.forEach(l => l())
}

async function fetchCount() {
  if (inflight) return
  inflight = true
  const gen = generation
  try {
    const r = await api.approvals.pendingCount()
    if (gen !== generation) return
    setState({
      inbox: Number(r?.inbox_pending) || 0,
      minePending: Number(r?.mine_pending) || 0,
      mineReady: Number(r?.mine_ready) || 0,
      available: true,
    })
  } catch (e) {
    if (gen !== generation) return
    if (e?.status === 403 || e?.status === 404) setState({ ...state, available: false })
    // أي فشل تاني (شبكة/500): صامت ونحتفظ بآخر قيمة
  } finally {
    inflight = false
  }
}

const onTick = () => { if (!document.hidden) fetchCount() }
const onChanged = () => fetchCount()
const onFocus = () => fetchCount()

function start() {
  generation++
  fetchCount()
  timer = setInterval(onTick, INTERVAL_MS)
  window.addEventListener('erp:approvals-changed', onChanged)
  window.addEventListener('focus', onFocus)
}

function stop() {
  generation++
  clearInterval(timer); timer = null
  window.removeEventListener('erp:approvals-changed', onChanged)
  window.removeEventListener('focus', onFocus)
  inflight = false
  state = INITIAL
  listeners.forEach(l => l())
}

function subscribe(cb) {
  listeners.add(cb)
  return () => listeners.delete(cb)
}
const getSnapshot = () => state

/**
 * useApprovalsBadge(enabled) → { pending, ready, available, refresh }
 * pending = طلبات أقدر أبت فيها (inbox_pending)، ready = طلباتي الموافق عليها وبانتظار إعادة التنفيذ.
 */
export function useApprovalsBadge(enabled = true) {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)

  useEffect(() => {
    if (!enabled) return undefined
    subscribers++
    if (subscribers === 1) start()
    return () => {
      subscribers--
      if (subscribers === 0) stop()
    }
  }, [enabled])

  return {
    pending: enabled ? snap.inbox : 0,
    ready: enabled ? snap.mineReady : 0,
    available: enabled ? snap.available : false,
    refresh: fetchCount,
  }
}
