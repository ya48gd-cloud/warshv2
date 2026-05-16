import { useState } from 'react'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import Spinner from '../components/ui/Spinner'

export default function Login() {
  const { login } = useAuth()
  const toast = useToast()
  const [form, setForm] = useState({ username: '', password: '' })
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    if (!form.username || !form.password) return toast('أدخل اسم المستخدم وكلمة المرور', 'error')
    setLoading(true)
    try {
      await login(form.username, form.password)
    } catch (err) {
      toast(err.message || 'فشل تسجيل الدخول', 'error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-erp flex items-center justify-center p-4" dir="rtl">
      {/* Background texture */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div style={{
          position: 'absolute', inset: 0,
          backgroundImage: 'radial-gradient(circle at 20% 50%, rgba(15,110,86,0.06) 0%, transparent 60%), radial-gradient(circle at 80% 20%, rgba(24,95,165,0.05) 0%, transparent 50%)',
        }} />
        <div style={{
          position: 'absolute', inset: 0,
          backgroundImage: `repeating-linear-gradient(0deg, transparent, transparent 40px, rgba(0,0,0,.015) 40px, rgba(0,0,0,.015) 41px),
                            repeating-linear-gradient(90deg, transparent, transparent 40px, rgba(0,0,0,.015) 40px, rgba(0,0,0,.015) 41px)`,
        }} />
      </div>

      <div className="relative w-full max-w-sm">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-teal mb-4 shadow-float">
            <span className="text-white text-2xl font-bold">H</span>
          </div>
          <h1 className="text-2xl font-bold text-erp-text">Heavy ERP</h1>
          <p className="text-erp-muted text-sm mt-1">ورشة المعدات الثقيلة</p>
        </div>

        {/* Card */}
        <div className="card shadow-modal">
          <h2 className="text-base font-semibold mb-5">تسجيل الدخول</h2>
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div>
              <label className="field-label">اسم المستخدم</label>
              <input
                className="field-input"
                type="text"
                placeholder="username"
                autoComplete="username"
                value={form.username}
                onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
                autoFocus
              />
            </div>
            <div>
              <label className="field-label">كلمة المرور</label>
              <input
                className="field-input"
                type="password"
                placeholder="••••••"
                autoComplete="current-password"
                value={form.password}
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="btn btn-primary w-full justify-center py-2.5 mt-1 text-sm"
            >
              {loading ? <Spinner size={16} className="mx-auto" /> : 'دخول'}
            </button>
          </form>
        </div>

        {/* Demo credentials */}
        <div className="mt-4 card-sm text-xs text-erp-muted">
          <div className="font-medium mb-2">بيانات تجريبية:</div>
          <div className="grid grid-cols-2 gap-1">
            {[
              ['admin', 'admin123'],
              ['accountant', 'accountant123'],
              ['production', 'production123'],
              ['viewer', 'viewer123'],
            ].map(([u, p]) => (
              <button
                key={u}
                className="text-right text-[11px] px-2 py-1 rounded border border-erp-border hover:bg-erp transition-colors cursor-pointer"
                onClick={() => setForm({ username: u, password: p })}
              >
                <span className="font-mono">{u}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
