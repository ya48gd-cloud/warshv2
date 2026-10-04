/**
 * بروفايل الورشة — Workshop Profile
 * مصدر وحيد للحقيقة: قاعدة البيانات (يُستخدم في طباعة الفواتير، عروض الأسعار، فواتير الشراء، والتقارير).
 */
import { useState, useEffect } from 'react'
import { useToast } from '../store/toast'
import { useAuth } from '../store/auth'
import api, { bgErr } from '../api/client'
import { PageSpinner } from '../components/ui/Spinner'
import ConfirmDialog from '../components/ui/ConfirmDialog'

const DEFAULT_FORM = {
  name:           'ورشة معدات الأعلاف',
  description:    '',
  phone:          '',
  mobile:         '',
  email:          '',
  tax_id:         '',
  commercial_reg: '',
  address:        '',
  report_footer:  'شكراً لتعاملكم معنا',
  currency:       'ج.م',
}

export default function WorkshopProfile() {
  const toast = useToast()
  const { user: me } = useAuth()
  const [form, setForm]                 = useState(DEFAULT_FORM)
  const [hasLogo, setHasLogo]           = useState(false)
  const [logoData, setLogoData]         = useState(null)   // base64 data:URI من الداتابيز
  const [logoBlobUrl, setLogoBlobUrl]   = useState(null)   // object URL مؤقت للعرض (fallback)
  const [logoVersion, setLogoVersion]   = useState(Date.now())  // لكسر الكاش بعد الرفع
  const [loading, setLoading]           = useState(true)
  const [saving, setSaving]             = useState(false)
  const [uploading, setUploading]       = useState(false)
  const [deletingLogo, setDeletingLogo] = useState(false)
  const [confirmDeleteLogo, setConfirmDeleteLogo] = useState(false)
  // إصلاح: لو فشل تحميل البيانات كان الفورم بيفضل بالقيم الافتراضية
  // ("ورشة معدات الأعلاف"...) وزرار الحفظ شغال — ضغطة حفظ واحدة كانت
  // بتمسح بيانات الورشة الحقيقية. دلوقتي بنمنع الحفظ لحد ما التحميل ينجح.
  const [loadError, setLoadError]       = useState(null)
  const [reloadKey, setReloadKey]       = useState(0)

  // تنظيف الـ object URL المؤقت عند تغييره أو مغادرة الصفحة (منع تسريب ذاكرة)
  useEffect(() => () => { if (logoBlobUrl) URL.revokeObjectURL(logoBlobUrl) }, [logoBlobUrl])

  // تحميل بيانات الورشة من الباك
  useEffect(() => {
    let alive = true
    setLoading(true)
    setLoadError(null)
    api.workshop.get()
      .then(data => {
        if (!alive) return
        _cache = data
        setForm({
          name:           data.name           || '',
          description:    data.description    || '',
          phone:          data.phone          || '',
          mobile:         data.mobile         || '',
          email:          data.email          || '',
          tax_id:         data.tax_id         || '',
          commercial_reg: data.commercial_reg || '',
          address:        data.address        || '',
          report_footer:  data.report_footer  || '',
          currency:       data.currency       || 'ج.م',
        })
        setHasLogo(!!data.has_logo)
        if (data.logo_data) {
          setLogoData(data.logo_data)
        } else if (data.has_logo) {
          // لوجو قديم بدون logo_data — نجلبه بالتوكن ونحوّله لـ object URL
          const token = sessionStorage.getItem('erp_token')
          fetch(api.workshop.logoUrl(), {
            headers: token ? { Authorization: `Bearer ${token}` } : {}
          })
            .then(r => r.ok ? r.blob() : null)
            .then(blob => {
              if (blob && alive) setLogoBlobUrl(URL.createObjectURL(blob))
            })
            .catch(() => {})
        }
      })
      .catch((err) => {
        if (!alive) return
        const m = err?.message || 'تعذّر تحميل بيانات الورشة'
        setLoadError(m)
        toast(`تعذّر تحميل بيانات الورشة: ${m}`, 'error')
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [reloadKey])

  const f = (k, v) => setForm(p => ({ ...p, [k]: v }))

  const save = async () => {
    if (saving) return
    if (loadError) { toast('لا يمكن الحفظ قبل تحميل بيانات الورشة الحالية بنجاح', 'error'); return }
    const name = (form.name || '').trim()
    if (!name) { toast('اسم الورشة مطلوب', 'error'); return }
    const email = (form.email || '').trim()
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      toast('البريد الإلكتروني غير صحيح', 'error'); return
    }
    setSaving(true)
    try {
      const payload = { ...form, name, email, currency: (form.currency || '').trim() || 'ج.م' }
      const updated = await api.workshop.update(payload)
      // تحديث الكاش المشترك (الطباعة/التقارير) والعملة العامة فوراً بدل ما
      // تفضل القيم القديمة لحد إعادة تحميل الصفحة
      if (updated && typeof updated === 'object') {
        _cache = updated
        if (updated.currency) window.__ERP_CURRENCY__ = updated.currency
      }
      toast('تم حفظ البيانات')
    } catch (err) {
      toast(err?.message ? `فشل الحفظ: ${err.message}` : 'فشل الحفظ', 'error')
    } finally {
      setSaving(false)
    }
  }

  const ALLOWED_LOGO_TYPES = ['image/png', 'image/jpeg', 'image/jpg', 'image/webp', 'image/svg+xml']

  const handleLogo = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (file.type && !ALLOWED_LOGO_TYPES.includes(file.type)) {
      toast('نوع الملف غير مدعوم — استخدم PNG أو JPG أو WEBP أو SVG', 'error'); e.target.value = ''; return
    }
    if (file.size > 5 * 1024 * 1024) { toast('حجم اللوجو أكبر من 5 ميجابايت', 'error'); e.target.value = ''; return }
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const token = sessionStorage.getItem('erp_token')
      const res = await fetch('/api/v1/workshop/profile/logo', {
        method: 'POST',
        headers: token ? { 'Authorization': `Bearer ${token}` } : {},
        body: fd,
      })
      if (!res.ok) {
        let detail = ''
        try { const j = await res.json(); detail = typeof j?.detail === 'string' ? j.detail : '' } catch { /* ignore */ }
        if (res.status === 401) window.dispatchEvent(new CustomEvent('erp:session-expired'))
        throw new Error(detail || 'فشل رفع اللوجو')
      }
      const data = await res.json()
      setHasLogo(true)
      setLogoData(data.logo_data || null)
      setLogoVersion(Date.now())
      // تحديث الكاش المشترك فوراً
      _cache = data
      toast('تم رفع اللوجو')
    } catch (err) {
      toast(err?.message || 'فشل رفع اللوجو', 'error')
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const deleteLogo = async () => {
    setDeletingLogo(true)
    try {
      await api.workshop.deleteLogo()
      setHasLogo(false)
      setLogoData(null)
      if (logoBlobUrl) { URL.revokeObjectURL(logoBlobUrl); setLogoBlobUrl(null) }
      if (_cache) _cache = { ..._cache, logo_data: null, has_logo: false }
      toast('تم حذف اللوجو')
    } catch (err) {
      toast(err?.message ? `فشل الحذف: ${err.message}` : 'فشل الحذف', 'error')
    } finally {
      setDeletingLogo(false)
    }
  }

  // بيانات الورشة (الاسم، الشعار، البيانات الضريبية...) تخص كل الورشة —
  // إصلاح: كانت هذه الصفحة بلا أي تحقق من الدور رغم أن الـ backend نفسه
  // (workshop.py) يسمح لأي مستخدم مسجّل (حتى "مشاهد" read-only) بتعديلها
  // أو حذف اللوجو. نمنع ذلك في الواجهة على الأقل بنفس نمط صفحة المستخدمين.
  if (me?.role !== 'admin') {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-erp-muted">
        <span className="text-5xl">🔒</span>
        <p className="font-medium">هذه الصفحة للمديرين فقط</p>
      </div>
    )
  }

  if (loading) return <PageSpinner />

  // الأولوية: data:URI من DB → blob URL مجلوب بتوكن → بدون صورة
  const logoSrc = logoData || logoBlobUrl || null

  return (
    <div className="grid gap-4 grid-cols-1 md:grid-cols-2">
      {loadError && (
        <div className="card md:col-span-2 flex items-center justify-between gap-3 text-sm"
             style={{ background: 'var(--red-lt)', color: 'var(--red)' }}>
          <span>تعذّر تحميل بيانات الورشة ({loadError}) — الحفظ معطّل لحماية البيانات الحالية.</span>
          <button className="btn btn-sm" onClick={() => setReloadKey(k => k + 1)}>إعادة المحاولة</button>
        </div>
      )}
      {/* Logo panel */}
      <div className="card flex flex-col gap-4">
        <div className="text-sm font-semibold">لوجو التقارير</div>
        <div className="flex flex-col items-center justify-center rounded-erp py-8"
             style={{ background: 'var(--bg)', border: '1px solid var(--border)', minHeight: 180 }}>
          {logoSrc
            ? <img src={logoSrc} alt="logo"
                   style={{ maxHeight: 140, maxWidth: '100%', objectFit: 'contain' }} />
            : <div className="text-center text-erp-muted">
                <div className="text-4xl mb-2">🖼</div>
                <div className="text-xs">لا يوجد لوجو</div>
              </div>
          }
        </div>
        <div className="flex flex-col gap-2">
          <label className="field-label">رفع لوجو جديد</label>
          <input type="file" accept="image/png,image/jpeg,image/jpg,image/webp,image/svg+xml"
                 className="field-input text-sm py-1.5"
                 onChange={handleLogo} disabled={uploading} />
          {uploading && <div className="text-xs text-erp-muted">جاري الرفع…</div>}
          {hasLogo && !uploading && (
            <button className="btn btn-sm btn-danger text-xs self-start"
                    onClick={() => setConfirmDeleteLogo(true)} disabled={deletingLogo}>
              {deletingLogo ? '…' : '🗑 حذف اللوجو'}
            </button>
          )}
        </div>
        <div className="px-3 py-2.5 rounded-erp text-xs"
             style={{ background: 'var(--amber-lt)', color: 'var(--amber)' }}>
          يظهر اللوجو في رأس عروض الأسعار وفواتير المبيعات وفواتير الشراء.
          الصيغ المدعومة: PNG, JPG, WEBP, SVG (حتى 5MB).
        </div>
      </div>

      {/* Data panel */}
      <div className="card flex flex-col gap-3">
        <div className="text-sm font-semibold">بيانات الورشة</div>

        <div>
          <label className="field-label">اسم الورشة *</label>
          <input className="field-input w-full" value={form.name}
                 onChange={e => f('name', e.target.value)} />
        </div>
        <div>
          <label className="field-label">وصف مختصر</label>
          <input className="field-input w-full" value={form.description}
                 onChange={e => f('description', e.target.value)} />
        </div>
        <div className="grid gap-3 grid-cols-1 sm:grid-cols-2">
          <div>
            <label className="field-label">التليفون (أرضي)</label>
            <input className="field-input w-full" value={form.phone}
                   onChange={e => f('phone', e.target.value)} />
          </div>
          <div>
            <label className="field-label">الموبايل</label>
            <input className="field-input w-full" value={form.mobile}
                   onChange={e => f('mobile', e.target.value)} placeholder="010xxxxxxxx" />
          </div>
        </div>
        <div className="grid gap-3 grid-cols-1 sm:grid-cols-2">
          <div>
            <label className="field-label">البريد الإلكتروني</label>
            <input className="field-input w-full" type="email" value={form.email}
                   onChange={e => f('email', e.target.value)} />
          </div>
          <div>
            <label className="field-label">العملة</label>
            <input className="field-input w-full" value={form.currency}
                   onChange={e => f('currency', e.target.value)} placeholder="ج.م" />
          </div>
        </div>
        <div className="grid gap-3 grid-cols-1 sm:grid-cols-2">
          <div>
            <label className="field-label">البطاقة الضريبية</label>
            <input className="field-input w-full" value={form.tax_id}
                   onChange={e => f('tax_id', e.target.value)} />
          </div>
          <div>
            <label className="field-label">السجل التجاري</label>
            <input className="field-input w-full" value={form.commercial_reg}
                   onChange={e => f('commercial_reg', e.target.value)} />
          </div>
        </div>
        <div>
          <label className="field-label">العنوان</label>
          <textarea className="field-input w-full" rows={2} value={form.address}
                    onChange={e => f('address', e.target.value)} />
        </div>
        <div>
          <label className="field-label">ملاحظة أسفل التقارير</label>
          <textarea className="field-input w-full" rows={2} value={form.report_footer}
                    onChange={e => f('report_footer', e.target.value)} />
        </div>
        <button className="btn btn-primary self-start" onClick={save} disabled={saving || !!loadError}>
          {saving ? '…' : '💾 حفظ البيانات'}
        </button>
      </div>

      <ConfirmDialog
        open={confirmDeleteLogo}
        message="هل تريد حذف لوجو الورشة نهائياً؟"
        onConfirm={() => { deleteLogo(); setConfirmDeleteLogo(false) }}
        onCancel={() => setConfirmDeleteLogo(false)}
      />
    </div>
  )
}

// ── Export helper for legacy reports — قراءة من الباك (لا cache) ──
let _cache = null
let _cachePromise = null
export async function fetchWorkshopProfile() {
  if (_cache) return _cache
  if (_cachePromise) return _cachePromise
  _cachePromise = api.workshop.get()
    .then(data => {
      _cache = data
      return data
    })
    .catch(bgErr('بيانات الورشة', { ...DEFAULT_FORM, logo_url: null }))
    .finally(() => { _cachePromise = null })
  return _cachePromise
}

// نسخة sync للتوافق العكسي مع Reports.jsx القديم (تستخدم آخر بيانات محمَّلة)
export function getWorkshopProfile() {
  if (_cache) {
    return {
      name:           _cache.name,
      description:    _cache.description || '',
      phone:          _cache.mobile || _cache.phone || '',
      email:          _cache.email || '',
      tax_id:         _cache.tax_id || '',
      commercial_reg: _cache.commercial_reg || '',
      address:        _cache.address || '',
      report_footer:  _cache.report_footer || '',
      logo_url:       _cache.logo_url ? `${_cache.logo_url}?t=${Date.now()}` : null,
    }
  }
  // الاستدعاء الأول: أطلق التحميل في الخلفية وأرجع defaults
  fetchWorkshopProfile()
  return { ...DEFAULT_FORM, logo_url: null }
}
