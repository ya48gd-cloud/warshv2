/**
 * بروفايل الورشة — Workshop Profile + Reports
 */
import { useState, useEffect } from 'react'
import { useToast } from '../store/toast'

const STORAGE_KEY = 'workshop_profile'

const DEFAULT = {
  name:        'ورشة معدات الأعلاف',
  description: 'تصنيع وتوريد معدات الأعلاف والتصنيع الغذائي',
  phone:       '',
  mobile:      '',
  email:       '',
  tax_id:      '',
  commercial_reg: '',
  address:     'القاهرة، مصر',
  report_footer: 'شكراً لتعاملكم معنا',
  logo_url:    null,
}

function load() {
  try { return { ...DEFAULT, ...JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') } }
  catch { return DEFAULT }
}

export default function WorkshopProfile() {
  const toast = useToast()
  const [form, setForm] = useState(load())
  const [logoPreview, setLogoPreview] = useState(form.logo_url || null)
  const [saving, setSaving] = useState(false)

  const f = (k, v) => setForm(p => ({ ...p, [k]: v }))

  const handleLogo = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = ev => {
      const url = ev.target.result
      setLogoPreview(url)
      f('logo_url', url)
    }
    reader.readAsDataURL(file)
  }

  const save = () => {
    setSaving(true)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(form))
    setTimeout(() => { setSaving(false); toast('تم حفظ البيانات') }, 300)
  }

  return (
    <div className="grid gap-4" style={{ gridTemplateColumns: '1fr 1fr' }}>
      {/* Logo panel */}
      <div className="card flex flex-col gap-4">
        <div className="text-sm font-semibold">لوجو التقارير</div>
        <div className="flex flex-col items-center justify-center rounded-erp py-8"
             style={{ background: 'var(--bg)', border: '1px solid var(--border)', minHeight: 180 }}>
          {logoPreview
            ? <img src={logoPreview} alt="logo" style={{ maxHeight: 140, maxWidth: '100%', objectFit: 'contain' }} />
            : <div className="text-center text-erp-muted">
                <div className="text-4xl mb-2">🖼</div>
                <div className="text-xs">لا يوجد لوجو</div>
              </div>
          }
        </div>
        <div className="flex flex-col gap-2">
          <label className="field-label">رفع لوجو جديد</label>
          <input type="file" accept="image/*" className="field-input text-sm py-1.5"
            onChange={handleLogo} />
          {logoPreview && (
            <button className="btn btn-sm btn-danger text-xs self-start"
              onClick={() => { setLogoPreview(null); f('logo_url', null) }}>🗑 حذف اللوجو</button>
          )}
        </div>
        <div className="px-3 py-2.5 rounded-erp text-xs text-erp-muted"
             style={{ background: 'var(--amber-lt)', color: 'var(--amber)' }}>
          سيظهر اللوجو في رأس الفواتير وعروض الأسعار وكعلامة مائية خلف محتوى التقرير.
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
        <div className="grid gap-3" style={{ gridTemplateColumns: '1fr 1fr' }}>
          <div>
            <label className="field-label">التليفون</label>
            <input className="field-input w-full" value={form.phone}
              onChange={e => f('phone', e.target.value)} placeholder="02xxxxxxxx" />
          </div>
          <div>
            <label className="field-label">الموبايل</label>
            <input className="field-input w-full" value={form.mobile}
              onChange={e => f('mobile', e.target.value)} placeholder="010xxxxxxxx" />
          </div>
        </div>
        <div className="grid gap-3" style={{ gridTemplateColumns: '1fr 1fr' }}>
          <div>
            <label className="field-label">البريد الإلكتروني</label>
            <input className="field-input w-full" type="email" value={form.email}
              onChange={e => f('email', e.target.value)} />
          </div>
          <div>
            <label className="field-label">البطاقة الضريبية</label>
            <input className="field-input w-full" value={form.tax_id}
              onChange={e => f('tax_id', e.target.value)} />
          </div>
        </div>
        <div>
          <label className="field-label">السجل التجاري</label>
          <input className="field-input w-full" value={form.commercial_reg}
            onChange={e => f('commercial_reg', e.target.value)} />
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
        <button className="btn btn-primary self-start" onClick={save} disabled={saving}>
          {saving ? '…' : '💾 حفظ البيانات'}
        </button>
      </div>
    </div>
  )
}

// Export helper so reports can read workshop data
export function getWorkshopProfile() { return load() }
