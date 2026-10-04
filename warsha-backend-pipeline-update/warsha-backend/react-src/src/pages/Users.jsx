import { useState } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth, ROLE_META } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, FormGrid } from '../components/ui/Field'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

const ROLE_DESCS = {
  admin:      { icon: '🔑', desc: 'وصول كامل — إدارة المستخدمين وجميع الأقسام' },
  accountant: { icon: '📊', desc: 'المبيعات، العملاء، الرواتب، التقارير المالية' },
  production: { icon: '🏭', desc: 'المخزون، أوامر الإنتاج، BOM، MRP' },
  viewer:     { icon: '👁', desc: 'قراءة فقط — لوحة التحكم والتقارير' },
}

const PERM_MATRIX = {
  modules: [
    { key: 'dashboard',  label: 'لوحة التحكم' },
    { key: 'inventory',  label: 'المخزون' },
    { key: 'production', label: 'الإنتاج' },
    { key: 'sales',      label: 'المبيعات' },
    { key: 'workers',    label: 'العمال' },
    { key: 'customers',  label: 'العملاء' },
    { key: 'users',      label: 'المستخدمون' },
  ],
  read: {
    admin:      ['dashboard','inventory','production','sales','workers','customers','users'],
    accountant: ['dashboard','inventory','production','sales','workers','customers'],
    production: ['dashboard','inventory','production','sales'],
    viewer:     ['dashboard','inventory','sales','customers'],
  },
  write: {
    admin:      ['dashboard','inventory','production','sales','workers','customers','users'],
    accountant: ['sales','workers','customers'],
    production: ['inventory','production'],
    viewer:     [],
  },
}

function RolePicker({ value, onChange }) {
  return (
    <div className="flex flex-col gap-2">
      {Object.entries(ROLE_META).map(([key, meta]) => {
        const desc = ROLE_DESCS[key]
        const sel = value === key
        return (
          <div
            key={key}
            className="flex items-center gap-3 px-3 py-2.5 rounded-erp border-2 cursor-pointer transition-all"
            style={{
              borderColor: sel ? 'var(--teal)' : 'var(--border)',
              background: sel ? 'var(--teal-lt)' : undefined,
            }}
            onClick={() => onChange(key)}
          >
            <span className="text-xl">{desc?.icon}</span>
            <div>
              <div className="font-semibold text-sm">{meta.label}</div>
              <div className="text-xs text-erp-muted">{desc?.desc}</div>
            </div>
            {sel && <div className="mr-auto text-teal text-sm">✓</div>}
          </div>
        )
      })}
    </div>
  )
}

export default function Users() {
  const { user: me } = useAuth()
  const toast = useToast()

  const { data: users, loading, error: loadError, reload } = useApi(() => api.users.list())

  const [modal, setModal] = useState(null)   // null | { mode: 'add'|'edit', item? }
  const [pwModal, setPwModal] = useState(null)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [deactivateTarget, setDeactivateTarget] = useState(null)
  const [togglingId, setTogglingId] = useState(null)
  const [form, setForm] = useState({})
  const [pwForm, setPwForm] = useState('')

  const openAdd = () => { setForm({ role: 'viewer' }); setModal({ mode: 'add' }) }
  const openEdit = (u) => { setForm({ ...u }); setModal({ mode: 'edit', item: u }) }

  const [save, saving] = useAction(async () => {
    if (saving) return
    // الباك (login) بيعمل strip لاسم المستخدم قبل المطابقة — لو اتسجل بمسافة
    // في الأول/الآخر المستخدم مش هيقدر يدخل أبداً، فنقصّه هنا قبل الإرسال.
    const username = (form.username || '').trim()
    if (!username) { toast('اسم المستخدم مطلوب', 'error'); return }
    if (modal?.mode === 'add' && (!form.password || form.password.length < 6)) {
      toast('كلمة المرور 6 أحرف على الأقل', 'error'); return
    }
    if (modal?.mode === 'add' && form.password !== form.confirm_password) {
      toast('كلمة المرور غير متطابقة — تأكد من إدخالها مرتين بشكل صحيح', 'error'); return
    }
    if (!form.role || !ROLE_META[form.role]) { toast('اختر دوراً صالحاً', 'error'); return }
    const fullName = (form.full_name || '').trim()
    if (modal?.mode === 'edit') {
      // تنبيه: المدير بيغيّر دوره هو شخصياً لدور أقل — هيفقد صلاحية إدارة المستخدمين فوراً
      if (form.id === me?.id && modal?.item?.role === 'admin' && form.role !== 'admin') {
        if (!window.confirm('أنت على وشك إزالة صلاحية المدير من حسابك الحالي — لن تستطيع إدارة المستخدمين بعد ذلك. هل تريد المتابعة؟')) return
      }
      await api.users.update(form.id, { full_name: fullName, role: form.role })
    }
    else await api.users.create({ username, password: form.password, full_name: fullName || null, role: form.role })
    toast(modal?.mode === 'edit' ? 'تم التحديث' : 'تم إنشاء المستخدم')
    setModal(null)
    reload()
  })

  const [resetPw, resetLoading] = useAction(async () => {
    if (resetLoading) return
    if (!pwForm || pwForm.length < 6) { toast('كلمة المرور قصيرة (6 أحرف على الأقل)', 'error'); return }
    await api.users.resetPassword(pwModal.id, pwForm)
    toast('تم إعادة تعيين كلمة المرور')
    setPwModal(null)
    setPwForm('')
  })

  const [toggleActive] = useAction(async (u) => {
    if (togglingId) return
    setTogglingId(u.id)
    try {
      await api.users.update(u.id, { is_active: !u.is_active })
      toast(u.is_active ? 'تم تعطيل الحساب' : 'تم تفعيل الحساب')
      reload()
    } finally {
      setTogglingId(null)
    }
  })

  const [doDelete] = useAction(async () => {
    await api.users.delete(deleteTarget.id)
    toast('تم حذف المستخدم')
    setDeleteTarget(null)
    reload()
  })

  if (me?.role !== 'admin') {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-erp-muted">
        <span className="text-5xl">🔒</span>
        <p className="font-medium">هذه الصفحة للمديرين فقط</p>
      </div>
    )
  }

  if (loading) return <PageSpinner />

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-erp-muted">{users?.length || 0} مستخدم</p>
        <button className="btn btn-primary btn-sm" onClick={openAdd}>+ مستخدم جديد</button>
      </div>

      {loadError && (
        <div className="card flex items-center justify-between gap-3 text-sm"
             style={{ background: 'var(--red-lt)', color: 'var(--red)' }}>
          <span>تعذّر تحميل المستخدمين: {loadError}</span>
          <button className="btn btn-sm" onClick={reload}>إعادة المحاولة</button>
        </div>
      )}

      {/* User cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {!users?.length && !loadError && (
          <div className="col-span-3 card text-center text-erp-muted py-12">
            <div className="text-4xl mb-3">👥</div>
            <div className="font-medium mb-1">لا يوجد مستخدمون</div>
            <div className="text-sm">أضف مستخدماً جديداً للبدء</div>
          </div>
        )}
        {users?.map(u => {
          const meta = ROLE_META[u.role]
          const isSelf = u.id === me?.id
          return (
            <div key={u.id} className="card" style={{ opacity: u.is_active ? 1 : 0.6 }}>
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2.5">
                  <div className="w-10 h-10 rounded-full flex items-center justify-center font-bold text-sm flex-shrink-0"
                       style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
                    {(u.full_name || u.username)?.charAt(0)?.toUpperCase()}
                  </div>
                  <div>
                    <div className="font-semibold text-sm flex items-center gap-1.5">
                      {u.full_name || u.username}
                      {isSelf && <span className="badge badge-teal text-[10px]">أنت</span>}
                    </div>
                    <div className="text-[11px] text-erp-muted font-mono">@{u.username}</div>
                  </div>
                </div>
                <span className={`badge ${meta?.color} text-[11px]`}>{meta?.icon} {meta?.label}</span>
              </div>

              <div className="flex items-center justify-between mt-3 pt-3 border-t border-erp-border">
                <span className="text-xs" style={{ color: u.is_active ? 'var(--green)' : 'var(--muted)' }}>
                  {u.is_active ? '● نشط' : '○ معطّل'}
                </span>
                <div className="flex gap-1">
                  <button className="btn btn-sm text-xs" onClick={() => openEdit(u)} title="تعديل">✏️</button>
                  <button className="btn btn-sm text-xs" onClick={() => { setPwModal(u); setPwForm('') }} title="إعادة تعيين كلمة المرور">🔑</button>
                  {!isSelf && (
                    <button
                      className="btn btn-sm text-xs"
                      onClick={() => (u.is_active ? setDeactivateTarget(u) : toggleActive(u))}
                      disabled={togglingId === u.id}
                      title={u.is_active ? 'تعطيل' : 'تفعيل'}
                    >{u.is_active ? '⏸' : '▶️'}</button>
                  )}
                  {!isSelf && (
                    <button className="btn btn-sm btn-danger text-xs" onClick={() => setDeleteTarget(u)} title="حذف">🗑</button>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Permissions Matrix */}
      <div className="card">
        <div className="text-sm font-semibold mb-4">جدول الصلاحيات</div>
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr>
                <th>القسم</th>
                {Object.entries(ROLE_META).map(([k, m]) => (
                  <th key={k} className="text-center">{m.icon} {m.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PERM_MATRIX.modules.map(mod => (
                <tr key={mod.key}>
                  <td className="font-medium">{mod.label}</td>
                  {Object.keys(ROLE_META).map(role => {
                    const canW = PERM_MATRIX.write[role]?.includes(mod.key)
                    const canR = PERM_MATRIX.read[role]?.includes(mod.key)
                    return (
                      <td key={role} className="text-center">
                        {canW
                          ? <span className="text-xs font-semibold" style={{ color: 'var(--green)' }}>✓ كتابة</span>
                          : canR
                            ? <span className="text-xs" style={{ color: 'var(--blue)' }}>◎ قراءة</span>
                            : <span className="text-xs text-erp-muted">—</span>
                        }
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Add / Edit Modal */}
      <Modal open={!!modal} onClose={() => setModal(null)} title={modal?.mode === 'edit' ? `تعديل: ${form.username}` : 'مستخدم جديد'}>
        <div className="flex flex-col gap-4">
          <FormGrid cols={2}>
            <Input
              label="اسم المستخدم *"
              value={form.username || ''}
              onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
              disabled={modal?.mode === 'edit'}
              placeholder="ahmed.ali"
            />
            <Input
              label="الاسم الكامل"
              value={form.full_name || ''}
              onChange={e => setForm(f => ({ ...f, full_name: e.target.value }))}
            />
          </FormGrid>
          {modal?.mode === 'add' && (
            <>
              <Input
                label="كلمة المرور * (6 أحرف على الأقل)"
                type="password"
                value={form.password || ''}
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                autoComplete="new-password"
              />
              <Input
                label="تأكيد كلمة المرور *"
                type="password"
                value={form.confirm_password || ''}
                onChange={e => setForm(f => ({ ...f, confirm_password: e.target.value }))}
                autoComplete="new-password"
              />
            </>
          )}
          <div>
            <label className="field-label mb-2 block">الدور *</label>
            <RolePicker value={form.role || 'viewer'} onChange={role => setForm(f => ({ ...f, role }))} />
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>

      {/* Reset Password Modal */}
      <Modal open={!!pwModal} onClose={() => setPwModal(null)} title={`إعادة تعيين كلمة مرور: ${pwModal?.username}`} size="sm">
        <Input
          label="كلمة المرور الجديدة *"
          type="password"
          value={pwForm}
          onChange={e => setPwForm(e.target.value)}
          autoComplete="new-password"
          autoFocus
        />
        <ModalFooter>
          <button className="btn" onClick={() => setPwModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={resetPw} disabled={resetLoading}>{resetLoading ? '…' : 'تعيين'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={doDelete}
        title="حذف مستخدم"
        message={`هل تريد حذف المستخدم "${deleteTarget?.username}"؟ لا يمكن التراجع.`}
        danger
      />

      <ConfirmDialog
        open={!!deactivateTarget}
        onClose={() => setDeactivateTarget(null)}
        onConfirm={() => { const t = deactivateTarget; if (t) toggleActive(t) }}
        title="تعطيل مستخدم"
        message={`هل تريد تعطيل حساب "${deactivateTarget?.username}"؟ لن يستطيع تسجيل الدخول حتى يُعاد تفعيله.`}
        confirmLabel="تعطيل"
        danger
      />
    </div>
  )
}
