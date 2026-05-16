import { createContext, useContext, useState, useCallback } from 'react'

const ToastCtx = createContext(null)

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const toast = useCallback((msg, type = 'success') => {
    const id = Date.now()
    setToasts(t => [...t, { id, msg, type }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3200)
  }, [])

  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div className="fixed bottom-6 left-6 z-[200] flex flex-col gap-2">
        {toasts.map(t => (
          <div
            key={t.id}
            className="toast px-4 py-2.5 rounded-erp text-sm font-medium shadow-float flex items-center gap-2"
            style={{
              background: t.type === 'error' ? '#A32D2D' : t.type === 'warn' ? '#854F0B' : '#1a1a18',
              color: '#fff',
            }}
          >
            <span>{t.type === 'error' ? '✗' : t.type === 'warn' ? '⚠' : '✓'}</span>
            {t.msg}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

export function useToast() {
  return useContext(ToastCtx)
}
