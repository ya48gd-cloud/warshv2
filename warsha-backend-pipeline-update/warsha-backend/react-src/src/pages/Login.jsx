import { useState } from 'react'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import Spinner from '../components/ui/Spinner'

export default function Login() {
  const { login } = useAuth()
  const toast = useToast()
  const [form, setForm] = useState({ username: '', password: '', tenant_slug: '' })
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    if (!form.username || !form.password) return toast('أدخل اسم المستخدم وكلمة المرور', 'error')
    setLoading(true)
    try {
      await login(form.username, form.password, form.tenant_slug)
    } catch (err) {
      toast(err.message || 'فشل تسجيل الدخول', 'error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-erp flex items-center justify-center p-4" dir="rtl">
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div style={{
          position: 'absolute', inset: 0,
          backgroundImage: 'radial-gradient(circle at 20% 50%, rgba(15,110,86,0.06) 0%, transparent 60%), radial-gradient(circle at 80% 20%, rgba(24,95,165,0.05) 0%, transparent 50%)',
        }} />
      </div>

      <div className="relative w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center mb-4">
            <img src="/logo.png" alt="Warsha ERP" style={{ width: 140, height: 140, objectFit: 'contain' }} />
          </div>
          <h1 className="text-2xl font-bold text-erp-text">ورشة ERP</h1>
          <p className="text-erp-muted text-sm mt-1">نظام إدارة الورشة المتكامل</p>
        </div>

        <div className="card shadow-modal">
          <h2 className="text-base font-semibold mb-5">تسجيل الدخول</h2>
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div>
              <label className="field-label">مُعرّف الورشة (اختياري)</label>
              <input className="field-input" type="text" placeholder="my-workshop"
                value={form.tenant_slug}
                onChange={e => setForm(f => ({ ...f, tenant_slug: e.target.value }))} />
              <div className="text-[11px] text-erp-muted mt-1">اتركه فاضي لو معندكش معرف ورشة</div>
            </div>
            <div>
              <label className="field-label">اسم المستخدم</label>
              <input className="field-input" type="text" placeholder="username"
                autoComplete="username" value={form.username}
                onChange={e => setForm(f => ({ ...f, username: e.target.value }))} autoFocus />
            </div>
            <div>
              <label className="field-label">كلمة المرور</label>
              <input className="field-input" type="password" placeholder="••••••"
                autoComplete="current-password" value={form.password}
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))} />
            </div>
            <button type="submit" disabled={loading}
              className="btn btn-primary w-full justify-center py-2.5 mt-1 text-sm">
              {loading ? <Spinner size={16} className="mx-auto" /> : 'دخول'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
