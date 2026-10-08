/**
 * إعدادات الفاتورة الإلكترونية (ETA) — للمدير فقط.
 *
 * الأسرار (client_id / client_secret / signer_token) write-only: لا تُعرض ولا تُملأ مسبقاً ولا تُرسل
 * إلا لو كتبها المدير. الحذف عبر clear_client_secret / clear_signer_token.
 * التكامل غير مُجرَّب على بيئة مصلحة الضرائب الفعلية ويحتاج شهادة توقيع وبيانات اعتماد وبيئة تجريبية.
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import api from '../api/client'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import { Input, Select } from '../components/ui/Field'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'
import { invalidateEtaStatus } from '../hooks/useEtaEnabled'
import { fmtEtaDateTime, ETA_DISCLAIMER } from '../utils/einvoice'

const PLAIN_FIELDS = [
  'enabled', 'environment', 'custom_id_base_url', 'custom_api_base_url',
  'tax_registration_number', 'activity_code', 'issuer_name', 'branch_id',
  'governate', 'region_city', 'street', 'building_number',
  'signer_mode', 'signer_url',
  'default_item_type', 'default_item_code', 'default_unit_type', 'vat_subtype', 'zero_vat_subtype',
]

const toForm = (s) => {
  const f = {}
  PLAIN_FIELDS.forEach(k => {
    const v = s?.[k]
    f[k] = k === 'enabled' ? !!v : (v == null ? '' : String(v))
  })
  return f
}

const isHttps = (u) => /^https:\/\/[^\s/]+/i.test(u)
const ltrInput = { dir: 'ltr', style: { textAlign: 'left' } }

function Section({ title, hint, children }) {
  return (
    <div className="card flex flex-col gap-3">
      <div>
        <div className="text-sm font-semibold">{title}</div>
        {hint && <div className="text-xs text-erp-muted mt-0.5">{hint}</div>}
      </div>
      {children}
    </div>
  )
}

const Grid = ({ children }) => <div className="grid gap-3 grid-cols-1 sm:grid-cols-2">{children}</div>

export default function EInvoiceSettings() {
  const { user: me } = useAuth()
  const toast = useToast()
  const isAdmin = me?.role === 'admin'

  const [server, setServer] = useState(null)        // آخر نسخة من الخادم (بدون أسرار)
  const [form, setForm] = useState(null)
  const [secrets, setSecrets] = useState({ client_id: '', client_secret: '', signer_token: '' })
  const [loadError, setLoadError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testRes, setTestRes] = useState(null)
  const [confirm, setConfirm] = useState(null)      // 'prod' | 'clear_client_secret' | 'clear_signer_token'
  const busy = useRef(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const apply = useCallback((s) => {
    setServer(s); setForm(toForm(s))
    setSecrets({ client_id: '', client_secret: '', signer_token: '' })
  }, [])

  const load = useCallback(async () => {
    setLoading(true); setLoadError(null)
    try {
      const s = await api.einvoice.settings()
      if (alive.current) apply(s)
    } catch (e) {
      if (alive.current) setLoadError(e?.message || 'تعذّر تحميل الإعدادات')
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [apply])

  useEffect(() => { if (isAdmin) load() }, [isAdmin, load])

  if (!isAdmin) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-erp-muted">
        <span className="text-5xl">🔒</span>
        <p className="font-medium">هذه الصفحة للمديرين فقط</p>
      </div>
    )
  }
  if (loading && !server) return <PageSpinner />
  if (loadError && !server) {
    return (
      <div className="flex items-center justify-between gap-3 text-sm px-3 py-2.5 rounded-erp"
           style={{ background: 'var(--red-lt)', color: 'var(--red)' }}>
        <span>تعذّر تحميل الإعدادات ({loadError}) — لن يتم الحفظ حتى ينجح التحميل.</span>
        <button className="btn btn-sm" onClick={load}>إعادة المحاولة</button>
      </div>
    )
  }
  if (!form || !server) return null

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))
  const setSecret = (k, v) => setSecrets(s => ({ ...s, [k]: v }))
  const base = toForm(server)
  const typedSecrets = Object.entries(secrets).filter(([, v]) => v.trim() !== '')
  const dirty = PLAIN_FIELDS.some(k => form[k] !== base[k]) || typedSecrets.length > 0
  const isCustom = form.environment === 'custom'
  const isProd = form.environment === 'prod'

  // ── تحقق عميل (الباك إند هو المرجع النهائي) ──
  const errors = {}
  const tax = String(form.tax_registration_number || '').trim()
  const act = String(form.activity_code || '').trim()
  if (tax && !/^\d{9}$/.test(tax)) errors.tax_registration_number = 'الرقم الضريبي يجب أن يكون 9 أرقام'
  if (act && !/^\d{4,6}$/.test(act)) errors.activity_code = 'كود النشاط يجب أن يكون من 4 إلى 6 أرقام'
  if (isCustom) {
    if (form.custom_id_base_url && !isHttps(form.custom_id_base_url.trim())) errors.custom_id_base_url = 'يجب أن يبدأ الرابط بـ https://'
    if (form.custom_api_base_url && !isHttps(form.custom_api_base_url.trim())) errors.custom_api_base_url = 'يجب أن يبدأ الرابط بـ https://'
  }
  if (form.signer_mode === 'external' && form.signer_url && !isHttps(String(form.signer_url).trim())) {
    errors.signer_url = 'يجب أن يبدأ الرابط بـ https://'
  }
  if (isProd && form.signer_mode !== 'external') {
    errors.signer_mode = 'البيئة الإنتاجية تتطلب توقيعاً خارجياً (لا يُسمح بالمحاكاة أو بدون توقيع)'
  }
  const hasErrors = Object.keys(errors).length > 0

  const buildPayload = () => {
    const p = {}
    PLAIN_FIELDS.forEach(k => {
      if (k.startsWith('custom_') && !isCustom) return       // يُعالَج بالأسفل
      if (form[k] !== base[k]) {
        const v = form[k]
        p[k] = typeof v === 'string' ? (v.trim() === '' ? null : v.trim()) : v
      }
    })
    // الخروج من البيئة المخصصة: امسح الروابط المخصصة المحفوظة
    if (!isCustom) {
      ['custom_id_base_url', 'custom_api_base_url'].forEach(k => { if (base[k]) p[k] = null })
    }
    typedSecrets.forEach(([k, v]) => { p[k] = v.trim() })
    return p
  }

  const doSave = async (extra = null) => {
    if (busy.current) return
    if (!extra && hasErrors) { toast('صحّح الحقول المعلّمة أولاً', 'error'); return }
    busy.current = true
    setSaving(true)
    try {
      const s = await api.einvoice.saveSettings(extra || buildPayload())
      if (alive.current) { apply(s); setTestRes(null) }
      invalidateEtaStatus()
      toast(extra ? 'تم حذف السر المحفوظ' : 'تم حفظ إعدادات الفاتورة الإلكترونية')
    } catch (e) {
      toast(e?.message || 'تعذّر حفظ الإعدادات', 'error')
    } finally {
      busy.current = false
      if (alive.current) setSaving(false)
    }
  }

  const onSaveClick = () => {
    if (hasErrors) { toast('صحّح الحقول المعلّمة أولاً', 'error'); return }
    if (isProd && server.environment !== 'prod') { setConfirm('prod'); return }
    doSave()
  }

  const testConn = async () => {
    if (testing) return
    setTesting(true); setTestRes(null)
    try {
      const r = await api.einvoice.testConnection()
      if (alive.current) setTestRes({ ok: !!r?.ok, message: r?.message || (r?.ok ? 'نجح الاتصال' : 'فشل الاتصال') })
    } catch (e) {
      if (alive.current) setTestRes({ ok: false, message: e?.message || 'فشل اختبار الاتصال' })
    } finally {
      if (alive.current) setTesting(false)
      try { const s = await api.einvoice.settings(); if (alive.current && !dirty) apply(s) } catch { /* ignore */ }
    }
  }

  const missing = Array.isArray(server.missing) ? server.missing : []
  const secretHint = (has, masked) => has ? `محفوظ${masked ? ` ••••${masked}` : ' ••••'} — اتركه فارغاً للإبقاء عليه` : 'غير مُدخَل'

  return (
    <div className="flex flex-col gap-4">
      <div className="px-3 py-2.5 rounded-erp text-sm leading-6" style={{ background: 'var(--amber-lt)', color: 'var(--amber)' }}>
        <b>تنبيه:</b> {server.disclaimer || ETA_DISCLAIMER}.
      </div>

      <div className="card flex flex-col gap-2">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="text-sm font-semibold">حالة الجاهزية</div>
          <span className={`badge ${server.ready ? 'badge-green' : 'badge-amber'}`}>
            {server.ready ? 'جاهز للإرسال' : 'غير جاهز'}
          </span>
        </div>
        {!server.ready && missing.length > 0 && (
          <ul className="list-disc pr-5 text-sm leading-7" style={{ color: 'var(--amber)' }}>
            {missing.map((m, i) => <li key={i}>{String(m)}</li>)}
          </ul>
        )}
        {server.last_connection_at && (
          <div className="text-xs text-erp-muted">
            آخر اختبار اتصال: {fmtEtaDateTime(server.last_connection_at)} —{' '}
            <span style={{ color: server.last_connection_ok ? 'var(--green)' : 'var(--red)' }}>
              {server.last_connection_ok ? 'ناجح' : (server.last_connection_error || 'فاشل')}
            </span>
          </div>
        )}
      </div>

      <Section title="التفعيل والبيئة">
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" checked={!!form.enabled} onChange={e => set('enabled', e.target.checked)} />
          <span>تفعيل الإرسال للفاتورة الإلكترونية</span>
        </label>
        <Grid>
          <Select label="البيئة" value={form.environment || 'preprod'} onChange={e => set('environment', e.target.value)}>
            <option value="preprod">تجريبية (Pre-production)</option>
            <option value="prod">إنتاج (فعلي)</option>
            <option value="custom">مخصصة</option>
          </Select>
        </Grid>
        {isProd && (
          <div className="px-3 py-2 rounded-erp text-xs leading-6" style={{ background: 'var(--red-lt)', color: 'var(--red)' }}>
            البيئة الإنتاجية الفعلية: المستندات المقبولة يُعتد بها ضريبياً ولا يمكن التراجع عنها.
          </div>
        )}
        {isCustom && (
          <Grid>
            <Input label="رابط الهوية المخصص (https)" value={form.custom_id_base_url || ''} {...ltrInput}
                   placeholder="https://id.example.com" error={errors.custom_id_base_url}
                   onChange={e => set('custom_id_base_url', e.target.value)} />
            <Input label="رابط الـ API المخصص (https)" value={form.custom_api_base_url || ''} {...ltrInput}
                   placeholder="https://api.example.com" error={errors.custom_api_base_url}
                   onChange={e => set('custom_api_base_url', e.target.value)} />
          </Grid>
        )}
        {(server.effective_id_base_url || server.effective_api_base_url) && (
          <div className="text-xs text-erp-muted leading-6">
            <div>الهوية الفعّالة: <span dir="ltr" style={{ unicodeBidi: 'isolate' }}>{server.effective_id_base_url || '—'}</span></div>
            <div>الـ API الفعّال: <span dir="ltr" style={{ unicodeBidi: 'isolate' }}>{server.effective_api_base_url || '—'}</span></div>
          </div>
        )}
      </Section>

      <Section title="بيانات المُصدِر (الورشة)" hint="تُستخدم كبيانات issuer داخل مستند ETA.">
        <Grid>
          <Input label="رقم التسجيل الضريبي (9 أرقام)" value={form.tax_registration_number || ''} {...ltrInput}
                 inputMode="numeric" maxLength={9} error={errors.tax_registration_number}
                 onChange={e => set('tax_registration_number', e.target.value.replace(/\s/g, ''))} />
          <Input label="كود النشاط (taxpayerActivityCode)" value={form.activity_code || ''} {...ltrInput}
                 inputMode="numeric" maxLength={6} error={errors.activity_code}
                 onChange={e => set('activity_code', e.target.value.replace(/\s/g, ''))} />
          <Input label="اسم المُصدِر" value={form.issuer_name || ''} onChange={e => set('issuer_name', e.target.value)} />
          <Input label="كود الفرع (branch)" value={form.branch_id || ''} {...ltrInput}
                 onChange={e => set('branch_id', e.target.value)} placeholder="0" />
          <Input label="المحافظة" value={form.governate || ''} onChange={e => set('governate', e.target.value)} />
          <Input label="المدينة / المنطقة" value={form.region_city || ''} onChange={e => set('region_city', e.target.value)} />
          <Input label="الشارع" value={form.street || ''} onChange={e => set('street', e.target.value)} />
          <Input label="رقم المبنى" value={form.building_number || ''} onChange={e => set('building_number', e.target.value)} />
        </Grid>
      </Section>

      <Section title="بيانات الاعتماد (ERP على بوابة الضرائب)"
               hint="تُحفظ مشفّرة ولا تُعرض مرة أخرى. اتركها فارغة للإبقاء على القيمة المحفوظة.">
        <Grid>
          <Input label="Client ID" type="text" autoComplete="off" {...ltrInput}
                 value={secrets.client_id} onChange={e => setSecret('client_id', e.target.value)}
                 placeholder={secretHint(server.has_client_id, server.client_id_masked)} />
          <Input label="Client Secret" type="password" autoComplete="new-password" {...ltrInput}
                 value={secrets.client_secret} onChange={e => setSecret('client_secret', e.target.value)}
                 placeholder={secretHint(server.has_client_secret)} />
        </Grid>
        {server.has_client_secret && (
          <div>
            <button type="button" className="btn btn-sm btn-danger" disabled={saving || dirty}
                    title={dirty ? 'احفظ أو تراجع عن التغييرات أولاً' : ''}
                    onClick={() => setConfirm('clear_client_secret')}>حذف الـ Client Secret المحفوظ</button>
          </div>
        )}
      </Section>

      <Section title="التوقيع الرقمي" hint="مستند ETA يحتاج توقيعاً رقمياً بشهادة معتمدة. النظام لا يحتفظ بالشهادة؛ يتصل بخدمة توقيع خارجية.">
        <Grid>
          <Select label="وضع التوقيع" value={form.signer_mode || 'none'} error={errors.signer_mode}
                  onChange={e => set('signer_mode', e.target.value)}>
            <option value="none">بدون توقيع</option>
            <option value="mock" disabled={isProd}>محاكاة (للاختبار فقط)</option>
            <option value="external">خدمة توقيع خارجية</option>
          </Select>
        </Grid>
        {form.signer_mode === 'mock' && (
          <div className="px-3 py-2 rounded-erp text-xs leading-6" style={{ background: 'var(--amber-lt)', color: 'var(--amber)' }}>
            وضع المحاكاة لا ينتج توقيعاً حقيقياً ولن تقبله مصلحة الضرائب — للاختبار الداخلي فقط.
          </div>
        )}
        {form.signer_mode === 'external' && (
          <Grid>
            <Input label="رابط خدمة التوقيع (https)" value={form.signer_url || ''} {...ltrInput}
                   placeholder="https://signer.example.com/sign" error={errors.signer_url}
                   onChange={e => set('signer_url', e.target.value)} />
            <Input label="رمز الوصول لخدمة التوقيع (Token)" type="password" autoComplete="new-password" {...ltrInput}
                   value={secrets.signer_token} onChange={e => setSecret('signer_token', e.target.value)}
                   placeholder={secretHint(server.has_signer_token)} />
          </Grid>
        )}
        {server.has_signer_token && (
          <div>
            <button type="button" className="btn btn-sm btn-danger" disabled={saving || dirty}
                    title={dirty ? 'احفظ أو تراجع عن التغييرات أولاً' : ''}
                    onClick={() => setConfirm('clear_signer_token')}>حذف رمز خدمة التوقيع المحفوظ</button>
          </div>
        )}
      </Section>

      <Section title="القيم الافتراضية للأصناف" hint="تُستخدم لبنود الفاتورة التي لا تحمل كوداً خاصاً بها.">
        <Grid>
          <Select label="نوع كود الصنف" value={form.default_item_type || 'EGS'} onChange={e => set('default_item_type', e.target.value)}>
            <option value="EGS">EGS</option>
            <option value="GS1">GS1</option>
          </Select>
          <Input label="كود الصنف الافتراضي" value={form.default_item_code || ''} {...ltrInput}
                 onChange={e => set('default_item_code', e.target.value)} />
          <Input label="كود الوحدة الافتراضي (unitType)" value={form.default_unit_type || ''} {...ltrInput}
                 onChange={e => set('default_unit_type', e.target.value)} placeholder="EA" />
          <Input label="كود ضريبة القيمة المضافة (T1 subtype)" value={form.vat_subtype || ''} {...ltrInput}
                 onChange={e => set('vat_subtype', e.target.value)} placeholder="V009" />
          <Input label="كود الضريبة الصفرية (subtype)" value={form.zero_vat_subtype || ''} {...ltrInput}
                 onChange={e => set('zero_vat_subtype', e.target.value)} placeholder="V001" />
        </Grid>
      </Section>

      <div className="card flex flex-col gap-3">
        <div className="flex gap-2 flex-wrap items-center">
          <button className="btn btn-primary" onClick={onSaveClick} disabled={saving || !dirty}
                  style={{ opacity: saving || !dirty ? 0.5 : 1 }}>
            {saving ? '… جاري الحفظ' : '💾 حفظ الإعدادات'}
          </button>
          <button className="btn" onClick={testConn} disabled={testing || saving || dirty}
                  style={{ opacity: testing || saving || dirty ? 0.5 : 1 }}
                  title={dirty ? 'احفظ التغييرات أولاً ثم اختبر الاتصال' : 'يجلب رمز دخول فقط — لا يرسل أي مستند'}>
            {testing ? '… جاري الاختبار' : '🔌 اختبار الاتصال'}
          </button>
          {dirty && !saving && <span className="text-xs text-erp-muted">توجد تغييرات غير محفوظة</span>}
        </div>
        {testRes && (
          <div className="px-3 py-2 rounded-erp text-sm"
               style={testRes.ok ? { background: 'var(--green-lt)', color: 'var(--green)' } : { background: 'var(--red-lt)', color: 'var(--red)' }}>
            {testRes.message}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirm === 'prod'} danger title="تفعيل البيئة الإنتاجية"
        message={'ستعمل الفاتورة الإلكترونية على بيئة مصلحة الضرائب الفعلية، وأي مستند يُقبل يُعتد به ضريبياً ولا يمكن التراجع عنه.\nالتكامل لم يُجرَّب على البيئة الفعلية. هل أنت متأكد؟'}
        confirmLabel="نعم، احفظ" onCancel={() => setConfirm(null)} onConfirm={() => doSave()}
      />
      <ConfirmDialog
        open={confirm === 'clear_client_secret'} danger title="حذف Client Secret"
        message="سيتوقف الإرسال حتى تُدخل سراً جديداً. متابعة؟" confirmLabel="حذف"
        onCancel={() => setConfirm(null)} onConfirm={() => doSave({ clear_client_secret: true })}
      />
      <ConfirmDialog
        open={confirm === 'clear_signer_token'} danger title="حذف رمز خدمة التوقيع"
        message="سيتوقف التوقيع الخارجي حتى تُدخل رمزاً جديداً. متابعة؟" confirmLabel="حذف"
        onCancel={() => setConfirm(null)} onConfirm={() => doSave({ clear_signer_token: true })}
      />
    </div>
  )
}
