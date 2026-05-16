import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp, fmtDate } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, FormGrid, Field } from '../components/ui/Field'
import SearchInput from '../components/ui/SearchInput'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

const JOB_TITLES = [
  { value: 'mechanic',  label: 'ميكانيكي' },
  { value: 'welder',    label: 'لحام' },
  { value: 'driver',    label: 'سائق' },
  { value: 'helper',    label: 'مساعد' },
  { value: 'engineer',  label: 'مهندس' },
  { value: 'carpenter', label: 'نجار' },
  { value: 'painter',   label: 'دهان' },
  { value: 'other',     label: 'أخرى' },
]

export default function Workers() {
  const { canWrite } = useAuth()
  const toast = useToast()
  const write = canWrite('workers')

  const { data: workers, loading, reload } = useApi(() => api.workers.list())
  const [q, setQ] = useState('')
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState({})
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [profileId, setProfileId] = useState(null)

  const filtered = useMemo(() => {
    if (!workers) return []
    const lq = q.toLowerCase()
    return !lq ? workers : workers.filter(w =>
      w.name?.toLowerCase().includes(lq) ||
      w.job_title?.toLowerCase().includes(lq) ||
      w.code?.toLowerCase().includes(lq)
    )
  }, [workers, q])

  // backend expects: code, name, job_title, daily_wage, phone, national_id, hire_date, notes
  const openAdd = () => {
    setForm({ job_title: 'mechanic', daily_wage: '', code: '' })
    setModal({ mode: 'add' })
  }
  const openEdit = (item) => {
    setForm({ ...item })
    setModal({ mode: 'edit', item })
  }

  const [save, saving] = useAction(async () => {
    if (!form.name?.trim())       { toast('الاسم مطلوب', 'error'); return }
    if (!form.code?.trim())       { toast('الكود مطلوب', 'error'); return }
    if (!form.daily_wage || Number(form.daily_wage) <= 0) { toast('الأجر اليومي مطلوب', 'error'); return }

    const payload = {
      code:        form.code,
      name:        form.name,
      job_title:   form.job_title || 'other',
      daily_wage:  Number(form.daily_wage),
      phone:       form.phone || null,
      national_id: form.national_id || null,
      hire_date:   form.hire_date || null,
      notes:       form.notes || null,
    }

    if (modal?.mode === 'edit') await api.workers.update(form.id, payload)
    else await api.workers.create(payload)
    toast('تم الحفظ')
    setModal(null)
    reload()
  })

  const [doDelete] = useAction(async () => {
    await api.workers.delete(deleteTarget.id)
    toast('تم الحذف')
    setDeleteTarget(null)
    reload()
  })

  if (profileId) return <WorkerProfile workerId={profileId} onBack={() => setProfileId(null)} />
  if (loading) return <PageSpinner />

  const totalDailyWage = filtered.reduce((s, w) => s + (w.daily_wage || 0), 0)
  const weeklyTotal    = totalDailyWage * 6

  return (
    <div className="flex flex-col gap-4">
      {/* Summary bar */}
      <div className="grid grid-cols-3 gap-3">
        <div className="card text-center py-3">
          <div className="text-xs text-erp-muted mb-1">عدد العمال</div>
          <div className="text-xl font-bold" style={{ color: 'var(--teal)' }}>{filtered.length}</div>
        </div>
        <div className="card text-center py-3">
          <div className="text-xs text-erp-muted mb-1">إجمالي الأجور اليومية</div>
          <div className="text-lg font-bold" style={{ color: 'var(--blue)' }}>{egp(totalDailyWage)}</div>
        </div>
        <div className="card text-center py-3">
          <div className="text-xs text-erp-muted mb-1">الأجر الأسبوعي التقديري</div>
          <div className="text-lg font-bold" style={{ color: 'var(--green)' }}>{egp(weeklyTotal)}</div>
        </div>
      </div>

      <div className="card flex items-center gap-3 py-3">
        <div className="flex-1"><SearchInput value={q} onChange={setQ} placeholder="بحث بالاسم أو الكود أو الوظيفة…" /></div>
        {write && <button className="btn btn-primary btn-sm" onClick={openAdd}>+ عامل جديد</button>}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
        {!filtered.length && (
          <div className="col-span-full text-center text-erp-muted py-10">لا يوجد عمال</div>
        )}
        {filtered.map(w => {
          const jobLabel = JOB_TITLES.find(j => j.value === w.job_title)?.label || w.job_title || '—'
          const weeklyWage = (w.daily_wage || 0) * 6
          return (
            <div key={w.id} className="card hover:shadow-float transition-all">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2.5">
                  <div className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0"
                       style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
                    {(w.name || '؟').charAt(0)}
                  </div>
                  <div>
                    <div className="font-semibold text-sm">{w.name}</div>
                    <div className="text-xs text-erp-muted">{jobLabel}</div>
                    <div className="font-mono text-[10px] text-erp-muted mt-0.5">{w.code}</div>
                  </div>
                </div>
                <div className="flex gap-1">
                  <button className="btn btn-sm text-xs" style={{color:'var(--blue)',borderColor:'var(--blue-lt)'}}
                    onClick={() => setProfileId(w.id)}>بروفايل</button>
                  {write && <>
                    <button className="btn btn-sm text-xs" onClick={() => openEdit(w)}>✏️</button>
                    <button className="btn btn-sm btn-danger text-xs" onClick={() => setDeleteTarget(w)}>🗑</button>
                  </>}
                </div>
              </div>
              <div className="mt-3 pt-3 border-t border-erp-border grid grid-cols-2 gap-2 text-xs">
                <div>
                  <div className="text-erp-muted">الأجر اليومي</div>
                  <div className="font-bold text-sm" style={{ color: 'var(--teal)' }}>{egp(w.daily_wage || 0)}</div>
                </div>
                <div>
                  <div className="text-erp-muted">الأجر الأسبوعي</div>
                  <div className="font-semibold">{egp(weeklyWage)}</div>
                </div>
                <div>
                  <div className="text-erp-muted">تاريخ الانضمام</div>
                  <div className="font-semibold">{fmtDate(w.hire_date)}</div>
                </div>
                {w.phone && (
                  <div>
                    <div className="text-erp-muted">الهاتف</div>
                    <div className="font-mono text-xs">{w.phone}</div>
                  </div>
                )}
              </div>
              {w.national_id && (
                <div className="mt-1.5 text-[11px] text-erp-muted font-mono">
                  الرقم القومي: {w.national_id}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <Modal open={!!modal} onClose={() => setModal(null)} title={modal?.mode === 'edit' ? 'تعديل عامل' : 'عامل جديد'}>
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input
              label="الكود *"
              value={form.code || ''}
              onChange={e => setForm(f => ({ ...f, code: e.target.value }))}
              placeholder="WRK-001"
            />
            <Input
              label="الاسم *"
              value={form.name || ''}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              placeholder="أحمد محمد"
            />
          </FormGrid>
          <FormGrid cols={2}>
            <Select
              label="الوظيفة"
              value={form.job_title || 'mechanic'}
              onChange={e => setForm(f => ({ ...f, job_title: e.target.value }))}
              options={JOB_TITLES}
            />
            <Input
              label="الأجر اليومي (ج) *"
              type="number"
              step="0.01"
              min="0"
              value={form.daily_wage || ''}
              onChange={e => setForm(f => ({ ...f, daily_wage: e.target.value }))}
              placeholder="200.00"
            />
          </FormGrid>
          {/* Show weekly wage preview */}
          {form.daily_wage && Number(form.daily_wage) > 0 && (
            <div className="px-3 py-2 rounded-erp text-xs flex justify-between"
                 style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
              <span>الأجر الأسبوعي التقديري (× 6 أيام):</span>
              <span className="font-bold">{egp(Number(form.daily_wage) * 6)}</span>
            </div>
          )}
          <FormGrid cols={2}>
            <Input
              label="تاريخ الانضمام"
              type="date"
              value={form.hire_date || ''}
              onChange={e => setForm(f => ({ ...f, hire_date: e.target.value }))}
            />
            <Input
              label="رقم الهاتف"
              value={form.phone || ''}
              onChange={e => setForm(f => ({ ...f, phone: e.target.value }))}
              placeholder="010xxxxxxxx"
            />
          </FormGrid>
          <FormGrid cols={2}>
            <Input
              label="الرقم القومي"
              value={form.national_id || ''}
              onChange={e => setForm(f => ({ ...f, national_id: e.target.value }))}
            />
            <Input
              label="ملاحظات"
              value={form.notes || ''}
              onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
            />
          </FormGrid>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog open={!!deleteTarget} onClose={() => setDeleteTarget(null)} onConfirm={doDelete}
        title="حذف عامل" message={`حذف "${deleteTarget?.name}"؟`} danger />
    </div>
  )
}

// ── Worker Profile (exported for direct use) ──────────────────
export function WorkerProfile({ workerId, onBack }) {
  const toast = useToast()
  const { canWrite } = useAuth()
  const write = canWrite('workers')

  const { data: profile, loading, reload } = useApi(
    () => api.get(`/workers/${workerId}/profile`),
    [workerId]
  )
  const [advModal, setAdvModal] = useState(false)
  const [advForm, setAdvForm] = useState({ amount: '', advance_type: 'advance', date: new Date().toISOString().slice(0,10), notes: '' })
  const [saving, setSaving] = useState(false)

  const saveAdv = async () => {
    if (!advForm.amount || Number(advForm.amount) <= 0) { toast('أدخل المبلغ', 'error'); return }
    setSaving(true)
    try {
      await api.post('/workers/advances', {
        worker_id: workerId,
        amount: Number(advForm.amount),
        advance_type: advForm.advance_type,
        date: advForm.date,
        notes: advForm.notes || null,
      })
      toast(advForm.advance_type === 'bonus' ? 'تمت إضافة المنحة' : 'تمت إضافة السلفة')
      setAdvModal(false)
      setAdvForm({ amount: '', advance_type: 'advance', date: new Date().toISOString().slice(0,10), notes: '' })
      reload()
    } catch(e) { toast(e.message, 'error') }
    finally { setSaving(false) }
  }

  if (loading) return <div className="p-10 text-center animate-pulse text-erp-muted">جاري التحميل…</div>
  if (!profile) return null

  const w = profile.worker || profile
  const advances = profile.advances || []
  const payroll_lines = profile.payroll_lines || []

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <button className="btn btn-sm" onClick={onBack}>← العودة</button>
        <h2 className="text-base font-semibold">{w.name}</h2>
        <span className="text-erp-muted text-sm">{w.job_title}</span>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: 'الأجر اليومي',     val: `${Number(w.daily_wage||0).toFixed(0)} ج`,    color: 'var(--teal)'  },
          { label: 'الأجر الأسبوعي',   val: `${(Number(w.daily_wage||0)*6).toFixed(0)} ج`, color: 'var(--blue)'  },
          { label: 'سلف قائمة',        val: `${Number(profile.pending_advances||0).toFixed(0)} ج`, color: 'var(--red)' },
          { label: 'منح غير محسوبة',   val: `${Number(profile.pending_bonus||0).toFixed(0)} ج`, color: 'var(--green)' },
        ].map(k => (
          <div key={k.label} className="card text-center py-3">
            <div className="text-xs text-erp-muted mb-1">{k.label}</div>
            <div className="text-lg font-bold" style={{ color: k.color }}>{k.val}</div>
          </div>
        ))}
      </div>

      <div className="flex gap-2">
        {write && (
          <>
            <button className="btn btn-sm" style={{ color:'var(--red)', borderColor:'var(--red-lt)' }}
              onClick={() => { setAdvForm(f => ({ ...f, advance_type:'advance' })); setAdvModal(true) }}>
              💸 إضافة سلفة
            </button>
            <button className="btn btn-sm" style={{ color:'var(--green)', borderColor:'var(--green-lt)' }}
              onClick={() => { setAdvForm(f => ({ ...f, advance_type:'bonus' })); setAdvModal(true) }}>
              🎁 إضافة منحة
            </button>
          </>
        )}
      </div>

      <div className="grid gap-4" style={{ gridTemplateColumns:'1fr 1fr' }}>
        {/* Advances */}
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">
            السلف والمنح ({advances.length})
          </div>
          {!advances.length ? (
            <div className="p-6 text-center text-erp-muted text-sm">لا توجد سلف أو منح</div>
          ) : (
            <table className="erp-table">
              <thead><tr><th>التاريخ</th><th>النوع</th><th>المبلغ</th><th>الحالة</th></tr></thead>
              <tbody>
                {advances.map(a => (
                  <tr key={a.id}>
                    <td className="text-xs text-erp-muted">{a.date}</td>
                    <td><span className={`badge ${a.advance_type==='bonus' ? 'badge-green' : 'badge-amber'}`}>
                      {a.advance_type==='bonus' ? 'منحة' : 'سلفة'}
                    </span></td>
                    <td className="font-semibold" style={{ color: a.advance_type==='bonus' ? 'var(--green)' : 'var(--red)' }}>
                      {Number(a.amount).toFixed(0)} ج
                    </td>
                    <td><span className={`badge ${a.is_settled ? 'badge-gray' : 'badge-blue'}`}>
                      {a.is_settled ? 'محسوب' : 'قائم'}
                    </span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Payroll history */}
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">
            آخر الرواتب
          </div>
          {!payroll_lines.length ? (
            <div className="p-6 text-center text-erp-muted text-sm">لا يوجد سجل رواتب</div>
          ) : (
            <table className="erp-table">
              <thead><tr><th>الأسبوع</th><th>الأيام</th><th>الإجمالي</th><th>الصافي</th></tr></thead>
              <tbody>
                {payroll_lines.slice(0,8).map((l,i) => (
                  <tr key={i}>
                    <td className="text-xs text-erp-muted">{l.week_start}</td>
                    <td>{Number(l.days_worked||0).toFixed(1)}</td>
                    <td>{Number(l.gross_amount||0).toFixed(0)} ج</td>
                    <td className="font-semibold" style={{ color:'var(--teal)' }}>{Number(l.net_amount||0).toFixed(0)} ج</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Advance/Bonus Modal */}
      {advModal && (
        <div className="modal-backdrop" onClick={e => e.target===e.currentTarget && setAdvModal(false)}>
          <div className="modal-box" style={{ maxWidth:400 }}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold">
                {advForm.advance_type==='bonus' ? '🎁 إضافة منحة' : '💸 إضافة سلفة'}
              </h2>
              <button onClick={() => setAdvModal(false)} className="text-erp-muted text-xl">×</button>
            </div>
            <div className="flex flex-col gap-3">
              <div className="flex gap-4 px-3 py-2 rounded-erp" style={{ background:'var(--bg)' }}>
                {[{v:'advance',l:'سلفة'},{v:'bonus',l:'منحة'}].map(o => (
                  <label key={o.v} className="flex items-center gap-1.5 cursor-pointer text-sm">
                    <input type="radio" checked={advForm.advance_type===o.v}
                      onChange={() => setAdvForm(f => ({...f, advance_type:o.v}))} />
                    {o.l}
                  </label>
                ))}
              </div>
              <div><label className="field-label">المبلغ (ج) *</label>
                <input className="field-input w-full" type="number" step="1" min="1"
                  value={advForm.amount} onChange={e => setAdvForm(f => ({...f, amount:e.target.value}))} /></div>
              <div><label className="field-label">التاريخ</label>
                <input className="field-input w-full" type="date" value={advForm.date}
                  onChange={e => setAdvForm(f => ({...f, date:e.target.value}))} /></div>
              <div><label className="field-label">ملاحظات</label>
                <input className="field-input w-full" value={advForm.notes}
                  onChange={e => setAdvForm(f => ({...f, notes:e.target.value}))} /></div>
            </div>
            <div className="flex gap-2 justify-end mt-5 pt-4 border-t border-erp-border">
              <button className="btn" onClick={() => setAdvModal(false)}>إلغاء</button>
              <button className="btn btn-primary" onClick={saveAdv} disabled={saving}>{saving?'…':'حفظ'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
