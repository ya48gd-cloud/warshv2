import { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react'

const ToastCtx = createContext(null)

const MAX_VISIBLE_TOASTS = 4

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const counter = useRef(0)

  const toast = useCallback((msg, type = 'success') => {
    // حماية: رسالة undefined/كائن (مثلاً err.message فاضية) كانت بتكسر msg.length
    msg = msg == null ? '' : (typeof msg === 'string' ? msg : (msg.message || String(msg)))
    if (!msg) msg = type === 'error' ? 'حدث خطأ غير متوقع' : 'تم'
    // رسالة approval_required بتظهر في نافذة الموافقات العامة — نتجاهل نفس النص كـ toast لمدة 3 ثواني
    const sup = typeof window !== 'undefined' ? window.__ERP_APPROVAL_SUPPRESS__ : null
    if (type === 'error' && sup && sup.msg && sup.msg === msg && Date.now() - sup.ts < 3000) return
    const id = ++counter.current
    // إصلاح شكوى متكررة: مدة العرض كانت ثابتة (3.2 ثانية) لكل رسالة —
    // رسالة خطأ طويلة (زي "الكمية المتاحة غير كافية — الرصيد الكلي...")
    // كانت بتختفي قبل ما المستخدم يخلص قراءتها. المدة دلوقتي بتتحسب
    // على طول الرسالة (بحد أدنى وأقصى معقولين).
    const duration = Math.min(9000, Math.max(3200, msg.length * 90))
    setToasts(t => {
      const next = [...t, { id, msg, type }]
      // حد أقصى للتوستات الظاهرة مع بعض عشان مايغطوش الشاشة لو حصل أكتر
      // من خطأ ورا بعض بسرعة — الأقدم بيتشال أول
      return next.length > MAX_VISIBLE_TOASTS ? next.slice(next.length - MAX_VISIBLE_TOASTS) : next
    })
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), duration)
  }, [])

  const dismiss = useCallback((id) => {
    setToasts(t => t.filter(x => x.id !== id))
  }, [])

  // كود بره React (زي printPopup في client.js) بيقدر يستخدم نفس نظام
  // التوست الموحّد عن طريق الحدث ده، بدل ما يضطر لـ alert()
  useEffect(() => {
    const h = (e) => toast(e.detail?.msg || '', e.detail?.type || 'error')
    window.addEventListener('erp:toast', h)
    return () => window.removeEventListener('erp:toast', h)
  }, [toast])

  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div className="fixed bottom-6 left-6 z-[200] flex flex-col gap-2" role="status" aria-live="polite" dir="rtl">
        {toasts.map(t => (
          <div
            key={t.id}
            className="toast px-4 py-2.5 rounded-erp text-sm font-medium shadow-float flex items-center gap-2"
            style={{
              background: t.type === 'error' ? '#A32D2D' : t.type === 'warn' ? '#854F0B' : '#1a1a18',
              color: '#fff',
              maxWidth: 420,
            }}
          >
            <span>{t.type === 'error' ? '✗' : t.type === 'warn' ? '⚠' : '✓'}</span>
            <span style={{ flex: 1 }}>{t.msg}</span>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              aria-label="إغلاق"
              style={{
                background: 'none', border: 'none', color: 'inherit', opacity: 0.7,
                cursor: 'pointer', fontSize: 14, padding: '0 0 0 4px', lineHeight: 1,
              }}
            >✕</button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

export function useToast() {
  return useContext(ToastCtx)
}
