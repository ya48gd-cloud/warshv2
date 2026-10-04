import { useState } from 'react'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import Spinner from '../components/ui/Spinner'

export default function Signup({ onBack }) {
  const { signup } = useAuth()
  const toast = useToast()
  const [form, setForm] = useState({
    workshop_name:    '',
    workshop_slug:    '',
    admin_username:   '',
    admin_password:   '',
    admin_full_name:  '',
  })
  const [loading, setLoading] = useState(false)

  const slugify = (s) => s.toLowerCase().trim()
    .replace(/[^a-z0-9\-_]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')

  const submit = async (e) => {
    e.preventDefault()
    if (!form.workshop_name?.trim())  return toast('اسم الورشة مطلوب', 'error')
    if (!form.workshop_slug?.trim())  return toast('مُعرّف الورشة مطلوب', 'error')
    // اسم عربي بالكامل بيطلع slug فاضي بعد التنظيف — الباك هيرفضه، فنوضح السبب هنا
    const cleanSlug = slugify(form.workshop_slug)
    if (!cleanSlug) return toast('مُعرّف الورشة لازم يحتوي حروف إنجليزية أو أرقام', 'error')
    if (!form.admin_username?.trim()) return toast('اسم مستخدم المدير مطلوب', 'error')
    if (!form.admin_password || form.admin_password.length < 6)
      return toast('كلمة المرور قصيرة جداً (6 أحرف على الأقل)', 'error')
    if (loading) return

    setLoading(true)
    try {
      await signup({
        ...form,
        workshop_name: form.workshop_name.trim(),
        workshop_slug: cleanSlug,
        admin_username: form.admin_username.trim(),
        admin_full_name: form.admin_full_name?.trim() || null,
      })
      toast('تم إنشاء الورشة بنجاح — مرحباً')
    } catch (err) {
      toast(err.message || 'فشل إنشاء الحساب', 'error')
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

      <div className="relative w-full max-w-md">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-bold text-erp-text">إنشاء ورشة جديدة</h1>
          <p className="text-erp-muted text-sm mt-1">سجّل ورشتك على المنصة في أقل من دقيقة</p>
        </div>

        <div className="card shadow-modal">
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div className="pb-2 mb-2 border-b" style={{ borderColor: 'var(--border)' }}>
              <div className="text-sm font-semibold mb-1">بيانات الورشة</div>
            </div>

            <div>
              <label className="field-label">اسم الورشة *</label>
              <input className="field-input" type="text" placeholder="ورشة معدات الأعلاف"
                value={form.workshop_name}
                onChange={e => setForm(f => ({
                  ...f, workshop_name: e.target.value,
                  workshop_slug: f.workshop_slug || slugify(e.target.value)
                }))} autoFocus />
            </div>
            <div>
              <label className="field-label">مُعرّف الورشة (slug) *</label>
              <input className="field-input" type="text" placeholder="my-workshop"
                value={form.workshop_slug}
                onChange={e => setForm(f => ({ ...f, workshop_slug: slugify(e.target.value) }))} />
              <div className="text-[11px] text-erp-muted mt-1">
                مُعرّف فريد — حروف إنجليزية وأرقام وشرطات فقط. هيُستخدم لاحقاً في رابط ورشتك.
              </div>
            </div>

            <div className="pt-3 mt-2 pb-2 border-b border-t" style={{ borderColor: 'var(--border)' }}>
              <div className="text-sm font-semibold mb-1">حساب المدير</div>
            </div>

            <div>
              <label className="field-label">الاسم الكامل</label>
              <input className="field-input" type="text" placeholder="أحمد محمد"
                value={form.admin_full_name}
                onChange={e => setForm(f => ({ ...f, admin_full_name: e.target.value }))} />
            </div>
            <div>
              <label className="field-label">اسم المستخدم *</label>
              <input className="field-input" type="text" placeholder="admin"
                value={form.admin_username}
                onChange={e => setForm(f => ({ ...f, admin_username: e.target.value }))} />
            </div>
            <div>
              <label className="field-label">كلمة المرور * (6 أحرف على الأقل)</label>
              <input className="field-input" type="password" placeholder="••••••"
                value={form.admin_password}
                onChange={e => setForm(f => ({ ...f, admin_password: e.target.value }))} />
            </div>

            <button type="submit" disabled={loading}
              className="btn btn-primary w-full justify-center py-2.5 mt-2">
              {loading ? <Spinner size={16} className="mx-auto" /> : '🚀 إنشاء الورشة والدخول'}
            </button>

            <button type="button" onClick={onBack}
              className="text-xs text-erp-muted hover:underline self-center mt-1">
              ← العودة إلى تسجيل الدخول
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
