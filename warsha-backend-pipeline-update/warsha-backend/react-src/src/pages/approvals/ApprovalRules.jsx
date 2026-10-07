/**
 * قواعد الموافقة (للمدير فقط): متى يحتاج الإجراء موافقة ومين يوافق.
 * قائمة الإجراءات وأنواع الشروط من GET /approvals/meta.
 */
import { useMemo, useState } from 'react'
import api from '../../api/client'
import { useApi } from '../../hooks/useApi'
import { useAuth } from '../../store/auth'
import { useToast } from '../../store/toast'
import Modal, { ModalFooter } from '../../components/ui/Modal'
import ConfirmDialog from '../../components/ui/ConfirmDialog'
import { Input, Select } from '../../components/ui/Field'
import { PageSpinner } from '../../components/ui/Spinner'
import { actionIcon, describeCondition, roleLabel } from '../../utils/approvals'
import { ErrorBox } from './ApprovalRequests'

const EMPTY_FORM = {
  name: '', action_key: '', condition_type: '', threshold: '',
  required_role: 'admin', valid_hours: '24', exempt_roles: [], is_active: true,
}

// الـ fetchers بترجّع {__err} بدل ما ترمي — عشان useApi ما يعملش toast مزدوج ونعرض الحالة داخل الصفحة
const safe = (fn) => () => fn().catch(e => ({ __err: e }))

export default function ApprovalRules() {
  const { user } = useAuth()
  const toast = useToast()
  const { data: rulesRes, loading, reload } = useApi(safe(() => api.approvals.rules()), [])
  const { data: metaRes } = useApi(safe(() => api.approvals.meta()), [])

  const [modal, setModal] = useState(null)        // null | 'new' | rule
  const [form, setForm] = useState(EMPTY_FORM)
  const [errors, setErrors] = useState({})
  const [saving, setSaving] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(null)
  const [togglingId, setTogglingId] = useState(null)
  const [warnings, setWarnings] = useState([])

  const meta = metaRes && !metaRes.__err ? metaRes : null
  const rules = Array.isArray(rulesRes) ? rulesRes : null
  const actions = meta?.actions || []
  const roles = meta?.roles || [{ key: 'admin', label: 'مدير النظام' }, { key: 'accountant', label: 'محاسب' }]
  const exemptOptions = meta?.exempt_role_options || ['admin', 'accountant', 'production']
  const actionByKey = useMemo(() => Object.fromEntries(actions.map(a => [a.key, a])), [actions])

  if (user?.role !== 'admin') {
    return <ErrorBox error={{ status: 403 }} />
  }
  if (loading && !rulesRes) return <PageSpinner />
  if (rulesRes?.__err) return <ErrorBox error={rulesRes.__err} onRetry={reload} />

  const conditionsFor = (key) => actionByKey[key]?.conditions || []
  const unitOf = (key, type) => conditionsFor(key).find(c => c.type === type)?.unit

  const openNew = () => {
    if (!meta) { toast('تعذّر تحميل قائمة الإجراءات من الخادم', 'error'); return }
    const first = actions[0]
    setForm({ ...EMPTY_FORM, action_key: first?.key || '', condition_type: first?.conditions?.[0]?.type || '' })
    setErrors({}); setModal('new')
  }
  const openEdit = (r) => {
    setForm({
      name: r.name || '', action_key: r.action_key, condition_type: r.condition_type,
      threshold: r.threshold ?? '', required_role: r.required_role, valid_hours: String(r.valid_hours ?? 24),
      exempt_roles: r.exempt_roles || [], is_active: !!r.is_active,
    })
    setErrors({}); setModal(r)
  }
  const setF = (k, v) => { setForm(f => ({ ...f, [k]: v })); setErrors(e => ({ ...e, [k]: undefined })) }

  const onActionChange = (key) => {
    const first = conditionsFor(key)[0]
    setForm(f => ({ ...f, action_key: key, condition_type: first?.type || '', threshold: '' }))
    setErrors({})
  }

  const buildBody = () => {
    const needsThreshold = form.condition_type !== 'always'
    return {
      name: form.name.trim(), action_key: form.action_key, condition_type: form.condition_type,
      threshold: needsThreshold ? Number(form.threshold) : null,
      required_role: form.required_role, valid_hours: Number(form.valid_hours),
      exempt_roles: form.exempt_roles, is_active: !!form.is_active,
    }
  }

  const validate = () => {
    const e = {}
    if (!form.name.trim()) e.name = 'اكتب اسم القاعدة'
    else if (form.name.trim().length > 100) e.name = 'الاسم أطول من ١٠٠ حرف'
    if (!form.action_key) e.action_key = 'اختر الإجراء'
    if (!form.condition_type) e.condition_type = 'اختر نوع الشرط'
    if (form.condition_type && form.condition_type !== 'always') {
      const t = Number(form.threshold)
      if (form.threshold === '' || !Number.isFinite(t) || t <= 0) e.threshold = 'أدخل حدًا أكبر من صفر'
      else if (form.condition_type === 'percent_gt' && t > 100) e.threshold = 'النسبة لا تزيد عن ١٠٠'
    }
    if (!roles.some(r => r.key === form.required_role)) e.required_role = 'اختر دور الموافقة'
    const h = Number(form.valid_hours)
    if (!Number.isInteger(h) || h < 1 || h > 720) e.valid_hours = 'من ١ إلى ٧٢٠ ساعة'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const showWarnings = (res) => {
    const w = Array.isArray(res?.warnings) ? res.warnings : []
    setWarnings(w)
    w.forEach(m => toast(m, 'warn'))
  }

  const save = async () => {
    if (saving) return
    if (!validate()) { toast('راجع بيانات القاعدة', 'error'); return }
    setSaving(true)
    try {
      const body = buildBody()
      const res = modal === 'new' ? await api.approvals.createRule(body) : await api.approvals.updateRule(modal.id, body)
      toast(modal === 'new' ? 'تم إنشاء القاعدة' : 'تم تحديث القاعدة')
      showWarnings(res)
      setModal(null); reload()
    } catch (e) {
      toast(e.message || 'تعذّر حفظ القاعدة', 'error')
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (r) => {
    if (togglingId) return
    setTogglingId(r.id)
    try {
      const res = await api.approvals.updateRule(r.id, {
        name: r.name, action_key: r.action_key, condition_type: r.condition_type,
        threshold: r.condition_type === 'always' ? null : r.threshold,
        required_role: r.required_role, valid_hours: r.valid_hours,
        exempt_roles: r.exempt_roles || [], is_active: !r.is_active,
      })
      toast(r.is_active ? 'تم تعطيل القاعدة' : 'تم تفعيل القاعدة')
      showWarnings(res)
      reload()
    } catch (e) {
      toast(e.message || 'تعذّر التغيير', 'error')
    } finally {
      setTogglingId(null)
    }
  }

  const remove = async (r) => {
    try {
      await api.approvals.deleteRule(r.id)
      toast('تم حذف القاعدة')
      reload()
    } catch (e) {
      toast(e.message || 'تعذّر حذف القاعدة', 'error')   // 409: مرتبطة بطلبات — الرسالة تنصح بالتعطيل
    }
  }

  const curConds = conditionsFor(form.action_key)
  const curUnit = unitOf(form.action_key, form.condition_type)
  const needsThreshold = !!form.condition_type && form.condition_type !== 'always'
  const list = rules || []

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold">⚙️ قواعد الموافقة</h2>
        <button className="btn btn-primary" onClick={openNew}>➕ قاعدة جديدة</button>
      </div>

      <div className="text-xs text-erp-muted">
        لو مفيش قواعد، كل الإجراءات بتتنفّذ مباشرة بدون موافقة. الموافقة بتتطلب مستخدمًا آخر غير مقدّم الطلب.
      </div>

      {warnings.length > 0 && (
        <div className="card text-xs" style={{ background: 'var(--amber-lt)', color: 'var(--amber)', padding: '10px 12px' }} role="alert">
          {warnings.map((w, i) => <div key={i}>⚠️ {w}</div>)}
          <button className="btn btn-sm text-xs" style={{ marginTop: 6 }} onClick={() => setWarnings([])}>إخفاء</button>
        </div>
      )}

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table text-sm">
            <thead>
              <tr><th>الاسم</th><th>الإجراء</th><th>الشرط</th><th>الدور المطلوب</th><th>صلاحية الموافقة</th><th>الاستثناءات</th><th>الحالة</th><th></th></tr>
            </thead>
            <tbody>
              {list.map(r => (
                <tr key={r.id}>
                  <td className="font-medium">{r.name}</td>
                  <td>{actionIcon(r.action_key)} {r.action_label || r.action_key}</td>
                  <td className="text-xs">{describeCondition(r)}</td>
                  <td><span className="badge badge-blue">{roleLabel(r.required_role)}</span></td>
                  <td className="text-xs">{r.valid_hours} ساعة</td>
                  <td className="text-xs">{(r.exempt_roles || []).length ? r.exempt_roles.map(roleLabel).join('، ') : '—'}</td>
                  <td>
                    <span className={`badge ${r.is_active ? 'badge-green' : 'badge-gray'}`}>{r.is_active ? 'مفعّلة' : 'معطّلة'}</span>
                    {r.open_requests > 0 && <div className="text-xs text-erp-muted">{r.open_requests} طلب مفتوح</div>}
                  </td>
                  <td>
                    <div className="flex gap-1">
                      <button className="btn btn-sm text-xs" title="تعديل" onClick={() => openEdit(r)}>✏️</button>
                      <button className="btn btn-sm text-xs" title={r.is_active ? 'تعطيل' : 'تفعيل'}
                        disabled={togglingId === r.id} onClick={() => toggle(r)}>{r.is_active ? '⏸️' : '▶️'}</button>
                      <button className="btn btn-sm text-xs btn-danger" title="حذف" onClick={() => setConfirmDelete(r)}>🗑️</button>
                    </div>
                  </td>
                </tr>
              ))}
              {list.length === 0 && (
                <tr><td colSpan={8} className="text-center text-erp-muted" style={{ padding: '32px 8px' }}>
                  <div style={{ fontSize: 28, marginBottom: 6 }}>🛡️</div>
                  <div>لا توجد قواعد موافقة — كل الإجراءات تُنفّذ مباشرة</div>
                  <button className="btn btn-primary btn-sm" style={{ marginTop: 10 }} onClick={openNew}>أنشئ أول قاعدة</button>
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <ConfirmDialog
        open={!!confirmDelete}
        title="حذف القاعدة؟"
        message={confirmDelete ? 'حذف قاعدة (' + confirmDelete.name + ')؟ الإجراءات المطابقة ستُنفَّذ مباشرة بدون موافقة. لو القاعدة مرتبطة بطلبات سابقة فعطّلها بدل الحذف.' : ''}
        confirmLabel="حذف"
        danger
        onConfirm={() => remove(confirmDelete)}
        onCancel={() => setConfirmDelete(null)}
      />

      <Modal open={!!modal} onClose={() => !saving && setModal(null)}
        title={modal === 'new' ? '➕ قاعدة موافقة جديدة' : '✏️ تعديل القاعدة'}>
        <div className="flex flex-col gap-3">
          <Input label="اسم القاعدة" value={form.name} error={errors.name} maxLength={100}
            onChange={e => setF('name', e.target.value)} />
          <Select label="الإجراء" value={form.action_key} error={errors.action_key}
            disabled={modal !== 'new'} onChange={e => onActionChange(e.target.value)}>
            {actions.map(a => <option key={a.key} value={a.key}>{a.label}</option>)}
            {modal !== 'new' && !actionByKey[form.action_key] && <option value={form.action_key}>{form.action_key}</option>}
          </Select>
          <Select label="الشرط" value={form.condition_type} error={errors.condition_type}
            onChange={e => { setF('condition_type', e.target.value); setF('threshold', '') }}>
            {curConds.map(c => <option key={c.type} value={c.type}>{c.label}</option>)}
            {!curConds.some(c => c.type === form.condition_type) && form.condition_type &&
              <option value={form.condition_type}>{form.condition_type}</option>}
          </Select>
          {needsThreshold ? (
            <div>
              <Input type="number" step="0.01" min="0" inputMode="decimal" dir="ltr"
                label={form.condition_type === 'percent_gt' || curUnit === 'percent' ? 'نسبة الخصم (%)' : 'حد المبلغ (ج)'}
                value={form.threshold} error={errors.threshold}
                onChange={e => setF('threshold', e.target.value)} />
              <div className="text-xs text-erp-muted" style={{ marginTop: 4 }}>تُطلب الموافقة عند تجاوز هذا الحد (أكبر من).</div>
            </div>
          ) : (
            form.condition_type === 'always' && <div className="text-xs text-erp-muted">تُطلب الموافقة دائمًا لهذا الإجراء.</div>
          )}
          <Select label="الدور المطلوب للموافقة" value={form.required_role} error={errors.required_role}
            onChange={e => setF('required_role', e.target.value)}>
            {roles.map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
          </Select>
          <Input type="number" min="1" max="720" step="1" label="مدة صلاحية الموافقة (ساعات)" value={form.valid_hours}
            error={errors.valid_hours} onChange={e => setF('valid_hours', e.target.value)} />
          <div>
            <div className="field-label">أدوار معفاة من هذه القاعدة</div>
            <div className="flex flex-wrap gap-3 text-sm">
              {exemptOptions.map(k => (
                <label key={k} className="flex items-center gap-1">
                  <input type="checkbox" checked={form.exempt_roles.includes(k)}
                    onChange={e => setF('exempt_roles', e.target.checked
                      ? [...form.exempt_roles, k] : form.exempt_roles.filter(x => x !== k))} />
                  {roleLabel(k)}
                </label>
              ))}
            </div>
            <div className="text-xs text-erp-muted" style={{ marginTop: 4 }}>
              مقدّم الطلب لا يمكنه الموافقة على طلبه. لو أنت المستخدم الوحيد بدور مدير النظام وتريد تنفيذ الإجراء بنفسك،
              أعفِ دور المدير أو عطّل القاعدة.
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.is_active} onChange={e => setF('is_active', e.target.checked)} />
            القاعدة مفعّلة
          </label>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)} disabled={saving}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : '💾 حفظ'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
