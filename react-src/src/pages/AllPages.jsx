// ─────────────────────────────────────────────────────────────────────────────
// This file is a barrel — each page is exported as a named component.
// Split into individual files if you prefer; they're separated by clear markers.
// ─────────────────────────────────────────────────────────────────────────────
import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp, fmtDate, today, WO_STATUS, QUOTE_STATUS } from '../utils/fmt'
import { StatusBadge } from '../components/ui/Badge'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import SearchInput from '../components/ui/SearchInput'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

// ══════════════════════════════════════════════════════════════
// QUOTATIONS
// ══════════════════════════════════════════════════════════════
export function Quotations() {
  const { canWrite } = useAuth()
  const write = canWrite('sales')
  const toast = useToast()
  const { data: quotes, loading, reload } = useApi(() => api.sales.quotations())
  const { data: customers } = useApi(() => api.customers.list())
  const [q, setQ] = useState('')
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState({})

  const filtered = useMemo(() => {
    if (!quotes) return []
    const lq = q.toLowerCase()
    return !lq ? quotes : quotes.filter(x => x.number?.toLowerCase().includes(lq) || x.customer_name?.toLowerCase().includes(lq))
  }, [quotes, q])

  const openAdd = () => { setForm({ status: 'draft', date: today(), valid_until: today() }); setModal({ mode: 'add' }) }
  const openEdit = (item) => { setForm({ ...item }); setModal({ mode: 'edit', item }) }

  const [save, saving] = useAction(async () => {
    if (!form.customer_id) { toast('اختر العميل', 'error'); return }
    if (modal?.mode === 'edit') await api.sales.updateQuote(form.id, form)
    else await api.sales.createQuote(form)
    toast('تم الحفظ'); setModal(null); reload()
  })

  if (loading) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="card flex items-center gap-3 py-3">
        <div className="flex-1"><SearchInput value={q} onChange={setQ} placeholder="بحث برقم العرض أو العميل…" /></div>
        {write && <button className="btn btn-primary btn-sm" onClick={openAdd}>+ عرض جديد</button>}
      </div>
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead><tr><th>الرقم</th><th>العميل</th><th>التاريخ</th><th>صالح حتى</th><th>الإجمالي</th><th>الحالة</th>{write && <th>إجراءات</th>}</tr></thead>
            <tbody>
              {!filtered.length && <tr><td colSpan={7} className="text-center text-erp-muted py-10">لا توجد عروض</td></tr>}
              {filtered.map(qt => (
                <tr key={qt.id}>
                  <td><span className="font-mono text-xs">{qt.number}</span></td>
                  <td className="font-medium">{qt.customer_name}</td>
                  <td className="text-erp-muted text-xs">{fmtDate(qt.date)}</td>
                  <td className="text-xs">{fmtDate(qt.valid_until)}</td>
                  <td className="font-semibold">{egp(qt.total)}</td>
                  <td><StatusBadge map={QUOTE_STATUS} value={qt.status} /></td>
                  {write && <td><button className="btn btn-sm" onClick={() => openEdit(qt)}>✏️</button></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <Modal open={!!modal} onClose={() => setModal(null)} title={modal?.mode === 'edit' ? 'تعديل عرض سعر' : 'عرض سعر جديد'}>
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Select label="العميل *" value={form.customer_id || ''} onChange={e => setForm(f => ({ ...f, customer_id: +e.target.value }))}>
              <option value="">— اختر —</option>
              {customers?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
            <Select label="الحالة" value={form.status || 'draft'} onChange={e => setForm(f => ({ ...f, status: e.target.value }))}
              options={Object.entries(QUOTE_STATUS).map(([v, { label }]) => ({ value: v, label }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="التاريخ" type="date" value={form.date || today()} onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
            <Input label="صالح حتى" type="date" value={form.valid_until || today()} onChange={e => setForm(f => ({ ...f, valid_until: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="الإجمالي" type="number" value={form.total || ''} onChange={e => setForm(f => ({ ...f, total: +e.target.value }))} />
            <Input label="رقم العرض" value={form.number || ''} onChange={e => setForm(f => ({ ...f, number: e.target.value }))} placeholder="QT-2025-001" />
          </FormGrid>
          <Textarea label="ملاحظات" value={form.notes || ''} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} rows={2} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// PAYROLL
// ══════════════════════════════════════════════════════════════
export function Payroll() {
  const { canWrite } = useAuth()
  const write = canWrite('workers')
  const toast = useToast()
  const { data: runs, loading, reload } = useApi(() => api.workers.payrollRuns())
  const [modal, setModal] = useState(false)
  const [form, setForm] = useState({ week_start: today() })
  const [detail, setDetail] = useState(null)
  const { data: detailData } = useApi(() => detail ? api.workers.payrollDetail(detail) : Promise.resolve(null), [detail])

  const [save, saving] = useAction(async () => {
    await api.workers.createPayroll(form)
    toast('تم إنشاء كشف الرواتب'); setModal(false); reload()
  })

  if (loading) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        {write && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ كشف رواتب جديد</button>}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-0 overflow-hidden lg:col-span-1">
          <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">كشوف الرواتب</div>
          {!runs?.length ? <div className="p-6 text-center text-erp-muted text-sm">لا توجد كشوف</div> :
            runs.map(r => (
              <div key={r.id} className={`px-4 py-3 border-b border-erp-border cursor-pointer hover:bg-erp transition-colors ${detail === r.id ? 'bg-teal-lt' : ''}`}
                   onClick={() => setDetail(r.id)}>
                <div className="font-medium text-sm">أسبوع: {fmtDate(r.week_start)}</div>
                <div className="flex items-center justify-between mt-1">
                  <span className="text-xs text-erp-muted">{r.worker_count} عامل</span>
                  <span className="font-semibold text-sm">{egp(r.total)}</span>
                </div>
              </div>
            ))
          }
        </div>
        <div className="card lg:col-span-2">
          {!detail ? (
            <div className="text-center text-erp-muted py-10 text-sm">
              <div className="text-3xl mb-3">💵</div>اختر كشف رواتب
            </div>
          ) : !detailData ? <PageSpinner /> : (
            <div>
              <div className="text-sm font-semibold mb-3">تفاصيل الكشف</div>
              <table className="erp-table">
                <thead><tr><th>العامل</th><th>أيام العمل</th><th>الأجر</th><th>السلف</th><th>الصافي</th></tr></thead>
                <tbody>
                  {detailData.lines?.map(l => (
                    <tr key={l.id}>
                      <td className="font-medium">{l.worker_name}</td>
                      <td>{l.days_worked}</td>
                      <td>{egp(l.gross)}</td>
                      <td className="text-xs" style={{ color: 'var(--red)' }}>{egp(l.advances)}</td>
                      <td className="font-semibold">{egp(l.net)}</td>
                    </tr>
                  ))}
                  <tr style={{ background: 'var(--teal-lt)' }}>
                    <td colSpan={4} className="font-semibold">الإجمالي</td>
                    <td className="font-bold">{egp(detailData.total)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      <Modal open={modal} onClose={() => setModal(false)} title="كشف رواتب جديد" size="sm">
        <Input label="بداية الأسبوع" type="date" value={form.week_start || today()} onChange={e => setForm(f => ({ ...f, week_start: e.target.value }))} />
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إنشاء'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// ATTENDANCE
// ══════════════════════════════════════════════════════════════
export function Attendance() {
  const { canWrite } = useAuth()
  const write = canWrite('workers')
  const toast = useToast()

  // Default to last Saturday
  const lastSat = () => {
    const d = new Date()
    const day = d.getDay() // 0=Sun,6=Sat
    const diff = day === 6 ? 0 : (day + 1)
    d.setDate(d.getDate() - diff)
    return d.toISOString().slice(0,10)
  }
  const [date, setDate] = useState(lastSat())
  const { data: recs, loading, reload } = useApi(() => api.attendance.list(date), [date])
  const { data: workers } = useApi(() => api.workers.list())
  const [bulk, setBulk] = useState({})
  const [bulkMode, setBulkMode] = useState(false)

  const initBulk = () => {
    const init = {}
    workers?.forEach(w => { init[w.id] = recs?.find(r => r.worker_id === w.id)?.status || 'present' })
    setBulk(init); setBulkMode(true)
  }

  const [saveBulk, saving] = useAction(async () => {
    const lines = Object.entries(bulk).map(([worker_id, status]) => ({ worker_id: +worker_id, status, att_date: date, date }))
    await api.attendance.bulk(lines)
    toast('تم حفظ الحضور'); setBulkMode(false); reload()
  })

  const STATUS = {
    present: { label:'حاضر',     color:'var(--green)'    },
    absent:  { label:'غائب',     color:'var(--red)'      },
    half:    { label:'نصف يوم',  color:'var(--amber-md)' },
    leave:   { label:'إجازة',    color:'var(--blue)'     },
  }

  // Status summary
  const summary = useMemo(() => {
    const src = bulkMode ? Object.values(bulk) : (recs || []).map(r => r.status)
    return Object.fromEntries(Object.keys(STATUS).map(k => [k, src.filter(s => s===k).length]))
  }, [recs, bulk, bulkMode])

  if (loading && !workers) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div>
          <label className="field-label">تاريخ الحضور</label>
          <input className="field-input" type="date" value={date} onChange={e => { setDate(e.target.value); setBulkMode(false) }} />
        </div>
        {write && !bulkMode && (
          <button className="btn btn-primary btn-sm mt-4" onClick={initBulk}>📝 تسجيل حضور اليوم</button>
        )}
        {write && bulkMode && (
          <>
            <button className="btn btn-sm mt-4" onClick={() => {
              const all = {}; workers?.forEach(w => { all[w.id]='present' }); setBulk(all)
            }}>✓ الكل حاضر</button>
            <button className="btn btn-primary btn-sm mt-4" onClick={saveBulk} disabled={saving}>
              {saving ? '…' : '💾 حفظ'}
            </button>
            <button className="btn btn-sm mt-4" onClick={() => setBulkMode(false)}>إلغاء</button>
          </>
        )}
      </div>

      {/* Summary badges */}
      <div className="flex gap-2 flex-wrap">
        {Object.entries(STATUS).map(([k,v]) => (
          <div key={k} className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold"
               style={{ background: v.color+'22', color: v.color }}>
            {v.label}: {summary[k] || 0}
          </div>
        ))}
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr><th>#</th><th>العامل</th><th>الوظيفة</th><th>الحالة</th></tr>
            </thead>
            <tbody>
              {!workers?.length && (
                <tr><td colSpan={4} className="text-center text-erp-muted py-10">لا يوجد عمال</td></tr>
              )}
              {workers?.map((w, idx) => {
                const rec = recs?.find(r => r.worker_id === w.id)
                const cur = bulk[w.id] || rec?.status
                const st  = STATUS[cur]
                return (
                  <tr key={w.id}>
                    <td className="text-erp-muted text-xs w-8">{idx+1}</td>
                    <td className="font-medium">{w.name}</td>
                    <td className="text-erp-muted text-xs">{w.job_title}</td>
                    <td>
                      {bulkMode ? (
                        <div className="flex gap-1">
                          {Object.entries(STATUS).map(([k,v]) => (
                            <button key={k}
                              className="px-2 py-0.5 rounded text-xs font-medium border transition-all"
                              style={{
                                background: bulk[w.id]===k ? v.color : 'transparent',
                                color:      bulk[w.id]===k ? '#fff'   : v.color,
                                borderColor: v.color,
                              }}
                              onClick={() => setBulk(b => ({...b, [w.id]:k}))}>
                              {v.label}
                            </button>
                          ))}
                        </div>
                      ) : cur ? (
                        <span className="badge text-xs font-semibold"
                          style={{ background: (st?.color||'var(--muted)')+'22', color: st?.color||'var(--muted)' }}>
                          {st?.label || cur}
                        </span>
                      ) : (
                        <span className="text-erp-muted text-xs">—</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// COSTING
// ══════════════════════════════════════════════════════════════
export function Costing() {
  const { data: wos } = useApi(() => api.accounting.workOrders())
  const [woId, setWoId] = useState('')
  const { data: lines, loading, reload } = useApi(() => woId ? api.accounting.costLines(woId) : Promise.resolve([]), [woId])
  const { data: journals } = useApi(() => woId ? api.accounting.journals(woId) : Promise.resolve([]), [woId])
  const { canWrite } = useAuth()
  const write = canWrite('production')
  const toast = useToast()
  const [modal, setModal] = useState(false)
  const [form, setForm] = useState({ type: 'material', amount: '' })

  const total = useMemo(() => (lines || []).reduce((s, l) => s + (l.amount || 0), 0), [lines])

  const [save, saving] = useAction(async () => {
    if (!woId || !form.amount) { toast('اختر الأمر وأدخل المبلغ', 'error'); return }
    await api.accounting.createCL({ ...form, work_order_id: +woId })
    toast('تمت الإضافة'); setModal(false); reload()
  })

  const [doDelete] = useAction(async (id) => {
    await api.accounting.deleteCL(id)
    toast('تم الحذف'); reload()
  })

  if (loading && woId) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="card flex items-center gap-3 py-3">
        <Select value={woId} onChange={e => setWoId(e.target.value)} label="">
          <option value="">— اختر أمر إنتاج —</option>
          {wos?.map(w => <option key={w.id} value={w.id}>{w.code}{w.equipment_name_ar ? ' — ' + w.equipment_name_ar : w.equipment ? ' — ' + w.equipment : ''}</option>)}
        </Select>
        {write && woId && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ سطر تكلفة</button>}
        {woId && (
          <div className="mr-auto text-sm font-semibold" style={{ color: 'var(--teal)' }}>
            الإجمالي: {egp(total)}
          </div>
        )}
      </div>
      {woId && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">سطور التكلفة</div>
            <table className="erp-table">
              <thead><tr><th>النوع</th><th>الوصف</th><th>المبلغ</th>{write && <th>حذف</th>}</tr></thead>
              <tbody>
                {!lines?.length && <tr><td colSpan={4} className="text-center text-erp-muted py-6">لا توجد تكاليف</td></tr>}
                {lines?.map(l => (
                  <tr key={l.id}>
                    <td><span className={`badge ${l.type === 'material' ? 'badge-blue' : l.type === 'labor' ? 'badge-amber' : 'badge-gray'}`}>
                      {l.type === 'material' ? 'مواد' : l.type === 'labor' ? 'عمالة' : 'أخرى'}
                    </span></td>
                    <td className="text-erp-muted text-xs">{l.description || '—'}</td>
                    <td className="font-semibold">{egp(l.amount)}</td>
                    {write && <td><button className="btn btn-sm btn-danger text-xs" onClick={() => doDelete(l.id)}>🗑</button></td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">القيود المحاسبية</div>
            <table className="erp-table">
              <thead><tr><th>التاريخ</th><th>الحساب</th><th>مدين</th><th>دائن</th></tr></thead>
              <tbody>
                {!journals?.length && <tr><td colSpan={4} className="text-center text-erp-muted py-6">لا توجد قيود</td></tr>}
                {journals?.map((j, i) => (
                  <tr key={i}>
                    <td className="text-xs text-erp-muted">{fmtDate(j.date)}</td>
                    <td className="text-sm">{j.account}</td>
                    <td className="font-medium text-sm" style={{ color: 'var(--blue)' }}>{j.debit ? egp(j.debit) : '—'}</td>
                    <td className="font-medium text-sm" style={{ color: 'var(--green)' }}>{j.credit ? egp(j.credit) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <Modal open={modal} onClose={() => setModal(false)} title="سطر تكلفة جديد" size="sm">
        <div className="flex flex-col gap-3">
          <Select label="النوع" value={form.cost_type || 'material'} onChange={e => setForm(f => ({ ...f, cost_type: e.target.value }))}
            options={[{ value: 'material', label: 'مواد' }, { value: 'labor', label: 'عمالة' }, { value: 'other', label: 'أخرى' }]} />
          <Input label="الوصف" value={form.description || ''} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
          <Input label="المبلغ *" type="number" value={form.amount || ''} onChange={e => setForm(f => ({ ...f, amount: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إضافة'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// ORDERS
// ══════════════════════════════════════════════════════════════
export function Orders() {
  const { canWrite } = useAuth()
  const write = canWrite('sales')
  const toast = useToast()
  const { data: customers } = useApi(() => api.customers.list())
  const [custId, setCustId] = useState('')
  const { data: orders, loading, reload } = useApi(
    () => custId ? api.customers.orders(custId) : Promise.resolve([]),
    [custId]
  )
  const [modal, setModal] = useState(false)
  const [form, setForm] = useState({ date: today() })

  const [save, saving] = useAction(async () => {
    if (!custId) { toast('اختر العميل', 'error'); return }
    await api.customers.createOrder({ ...form, customer_id: +custId })
    toast('تم إنشاء الطلب'); setModal(false); reload()
  })

  if (loading && custId) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="card flex items-center gap-3 py-3">
        <Select value={custId} onChange={e => setCustId(e.target.value)} label="">
          <option value="">— اختر عميل —</option>
          {customers?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>
        {write && custId && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ طلب جديد</button>}
      </div>
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead><tr><th>الكود</th><th>التاريخ</th><th>الوصف</th><th>الإجمالي</th><th>الحالة</th></tr></thead>
            <tbody>
              {!orders?.length && <tr><td colSpan={5} className="text-center text-erp-muted py-10">{custId ? 'لا توجد طلبات' : 'اختر عميلاً'}</td></tr>}
              {orders?.map(o => (
                <tr key={o.id}>
                  <td><span className="font-mono text-xs">{o.code}</span></td>
                  <td className="text-erp-muted text-xs">{fmtDate(o.date)}</td>
                  <td className="text-sm">{o.description || '—'}</td>
                  <td className="font-semibold">{egp(o.total)}</td>
                  <td><span className={`badge ${o.status === 'done' ? 'badge-green' : o.status === 'in_progress' ? 'badge-amber' : 'badge-blue'}`}>
                    {o.status === 'done' ? 'منتهي' : o.status === 'in_progress' ? 'جاري' : 'جديد'}
                  </span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <Modal open={modal} onClose={() => setModal(false)} title="طلب جديد" size="sm">
        <div className="flex flex-col gap-3">
          <Input label="الكود" value={form.code || ''} onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="ORD-2025-001" />
          <Input label="التاريخ" type="date" value={form.date} onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
          <Input label="الإجمالي" type="number" value={form.total || ''} onChange={e => setForm(f => ({ ...f, total: +e.target.value }))} />
          <Textarea label="الوصف" value={form.description || ''} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} rows={2} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إنشاء'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// PRODUCTION
// ══════════════════════════════════════════════════════════════
export function Production() {
  const { canWrite } = useAuth()
  const write = canWrite('production')
  const toast = useToast()
  const { data: wos } = useApi(() => api.accounting.workOrders())
  const [woId, setWoId] = useState('')
  const { data: reservations, reload } = useApi(
    () => woId ? api.production.reservations(woId) : Promise.resolve([]),
    [woId]
  )
  const { data: materials } = useApi(() => api.inventory.materials())
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState({ qty: '' })

  const [doReserve, reserving] = useAction(async () => {
    if (!woId || !form.material_id || !form.qty) { toast('اختر المادة والكمية', 'error'); return }
    await api.production.reserve({ work_order_id: +woId, material_id: +form.material_id, qty: +form.qty })
    toast('تم الحجز'); setModal(null); reload()
  })

  const [doIssue] = useAction(async (id) => {
    await api.production.issue({ reservation_id: id })
    toast('تم الصرف'); reload()
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="card flex items-center gap-3 py-3">
        <Select value={woId} onChange={e => setWoId(e.target.value)} label="">
          <option value="">— اختر أمر إنتاج —</option>
          {wos?.map(w => <option key={w.id} value={w.id}>{w.code}{w.equipment_name_ar ? ' — ' + w.equipment_name_ar : w.equipment ? ' — ' + w.equipment : ''}</option>)}
        </Select>
        {write && woId && <button className="btn btn-primary btn-sm" onClick={() => setModal('reserve')}>+ حجز مواد</button>}
      </div>
      {woId && (
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">المواد المحجوزة</div>
          <table className="erp-table">
            <thead><tr><th>المادة</th><th>الكمية المحجوزة</th><th>المصروفة</th><th>الحالة</th>{write && <th>صرف</th>}</tr></thead>
            <tbody>
              {!reservations?.length && <tr><td colSpan={5} className="text-center text-erp-muted py-8">لا توجد حجوزات</td></tr>}
              {reservations?.map(r => (
                <tr key={r.id}>
                  <td className="font-medium">{r.material_name}</td>
                  <td>{Number(r.reserved_qty).toFixed(1)}</td>
                  <td>{Number(r.issued_qty || 0).toFixed(1)}</td>
                  <td><span className={`badge ${r.status === 'issued' ? 'badge-green' : 'badge-amber'}`}>
                    {r.status === 'issued' ? 'تم الصرف' : 'محجوز'}
                  </span></td>
                  {write && <td>
                    {r.status !== 'issued' && (
                      <button className="btn btn-sm text-xs" onClick={() => doIssue(r.id)}>📤 صرف</button>
                    )}
                  </td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Modal open={modal === 'reserve'} onClose={() => setModal(null)} title="حجز مواد" size="sm">
        <div className="flex flex-col gap-3">
          <Select label="المادة *" value={form.material_id || ''} onChange={e => setForm(f => ({ ...f, material_id: e.target.value }))}>
            <option value="">— اختر —</option>
            {materials?.map(m => <option key={m.id} value={m.id}>{m.name_ar} (متوفر: {Number(m.stock_qty || 0).toFixed(1)})</option>)}
          </Select>
          <Input label="الكمية *" type="number" step="0.1" value={form.qty} onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={doReserve} disabled={reserving}>{reserving ? '…' : 'حجز'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// MRP
// ══════════════════════════════════════════════════════════════
export function MRP() {
  const { canWrite } = useAuth()
  const write = canWrite('production')
  const toast = useToast()
  const { data: suggestions, loading, reload } = useApi(() => api.production.mrp())

  const [runMrp, running] = useAction(async () => {
    await api.production.runMrp()
    toast('تم تشغيل MRP'); reload()
  })

  if (loading) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-erp-muted">اقتراحات الشراء المحتسبة تلقائياً من خلال MRP</p>
        {write && (
          <button className="btn btn-primary btn-sm" onClick={runMrp} disabled={running}>
            {running ? '⏳ جاري…' : '🔄 تشغيل MRP'}
          </button>
        )}
      </div>
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead><tr><th>المادة</th><th>الكمية الحالية</th><th>النقص</th><th>الكمية المقترحة</th><th>التاريخ المتوقع</th><th>الأولوية</th></tr></thead>
            <tbody>
              {!suggestions?.length && <tr><td colSpan={6} className="text-center text-erp-muted py-10">لا توجد اقتراحات — المخزون كافٍ أو شغّل MRP أولاً</td></tr>}
              {suggestions?.map((s, i) => (
                <tr key={i}>
                  <td className="font-medium">{s.material_name}</td>
                  <td className="text-sm">{Number(s.current_stock ?? s.stock_qty ?? 0).toFixed(1)}</td>
                  <td className="font-semibold" style={{ color: 'var(--red)' }}>{Number(s.shortage ?? s.required_qty ?? 0).toFixed(1)}</td>
                  <td className="font-bold" style={{ color: 'var(--teal)' }}>{Number(s.suggested_qty ?? s.qty ?? 0).toFixed(1)}</td>
                  <td className="text-erp-muted text-xs">{fmtDate(s.expected_by)}</td>
                  <td><span className={`badge ${s.priority === 'high' ? 'badge-red' : s.priority === 'medium' ? 'badge-amber' : 'badge-blue'}`}>
                    {s.priority === 'high' ? 'عاجل' : s.priority === 'medium' ? 'متوسط' : 'منخفض'}
                  </span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// SCRAP
// ══════════════════════════════════════════════════════════════
export function Scrap() {
  const { canWrite } = useAuth()
  const write = canWrite('production')
  const toast = useToast()
  const { data: scraps, loading, reload } = useApi(() => api.production.scrapList())
  const { data: materials } = useApi(() => api.inventory.materials())
  const [modal, setModal] = useState(false)
  const [form, setForm] = useState({ date: today(), qty: '' })

  const total = useMemo(() => (scraps || []).reduce((s, r) => s + (r.cost || 0), 0), [scraps])

  const [save, saving] = useAction(async () => {
    if (!form.material_id || !form.qty) { toast('اختر المادة والكمية', 'error'); return }
    await api.production.recordScrap(form)
    toast('تم تسجيل الهالك'); setModal(false); reload()
  })

  if (loading) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold" style={{ color: 'var(--red)' }}>
          إجمالي تكلفة الهالك: {egp(total)}
        </div>
        {write && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ تسجيل هالك</button>}
      </div>
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead><tr><th>التاريخ</th><th>المادة</th><th>الكمية</th><th>السبب</th><th>التكلفة</th></tr></thead>
            <tbody>
              {!scraps?.length && <tr><td colSpan={5} className="text-center text-erp-muted py-10">لا يوجد هالك مسجل</td></tr>}
              {scraps?.map(s => (
                <tr key={s.id}>
                  <td className="text-xs text-erp-muted">{fmtDate(s.date)}</td>
                  <td className="font-medium">{s.material_name}</td>
                  <td>{Number(s.qty).toFixed(1)} {s.unit}</td>
                  <td className="text-erp-muted text-xs">{s.reason || '—'}</td>
                  <td className="font-semibold" style={{ color: 'var(--red)' }}>{egp(s.cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <Modal open={modal} onClose={() => setModal(false)} title="تسجيل هالك" size="sm">
        <div className="flex flex-col gap-3">
          <Select label="المادة *" value={form.material_id || ''} onChange={e => setForm(f => ({ ...f, material_id: +e.target.value }))}>
            <option value="">— اختر —</option>
            {materials?.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
          </Select>
          <FormGrid cols={2}>
            <Input label="الكمية *" type="number" step="0.01" value={form.qty || ''} onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} />
            <Input label="التاريخ" type="date" value={form.date || today()} onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
          </FormGrid>
          <Textarea label="السبب" value={form.reason || ''} onChange={e => setForm(f => ({ ...f, reason: e.target.value }))} rows={2} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'تسجيل'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// STOCK COUNT
// ══════════════════════════════════════════════════════════════
export function StockCount() {
  const { canWrite } = useAuth()
  const write = canWrite('inventory')
  const toast = useToast()
  const { data: counts, loading, reload } = useApi(() => api.production.stockCounts())
  const [active, setActive] = useState(null)
  const [scanForm, setScanForm] = useState({ material_id: '', actual_qty: '' })
  const { data: materials } = useApi(() => api.inventory.materials())

  const openCount = counts?.find(c => c.status === 'open')

  const [startCount, starting] = useAction(async () => {
    await api.production.startCount()
    toast('تم بدء الجرد'); reload()
  })

  const [scan, scanning] = useAction(async () => {
    if (!scanForm.material_id || scanForm.actual_qty === '') { toast('اختر المادة وأدخل الكمية', 'error'); return }
    await api.production.scanLine(openCount.id, { material_id: +scanForm.material_id, actual_qty: +scanForm.actual_qty })
    toast('تم تسجيل السطر'); setScanForm({ material_id: '', actual_qty: '' }); reload()
  })

  const [closeCount, closing] = useAction(async () => {
    await api.production.closeCount(openCount.id)
    toast('تم إغلاق الجرد وتعديل المخزون'); reload()
  })

  if (loading) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      {/* Active count banner */}
      {openCount ? (
        <div className="flex items-center justify-between px-4 py-3 rounded-erp"
             style={{ background: 'var(--amber-lt)', border: '1px solid var(--amber-md)' }}>
          <div>
            <span className="font-semibold text-sm" style={{ color: 'var(--amber)' }}>🔴 جرد مفتوح</span>
            <span className="text-xs text-erp-muted mr-2">بدأ: {fmtDate(openCount.started_at)}</span>
          </div>
          {write && (
            <button className="btn btn-sm" style={{ color: 'var(--red)', borderColor: 'var(--red-lt)' }}
                    onClick={closeCount} disabled={closing}>
              {closing ? '…' : '✓ إغلاق الجرد'}
            </button>
          )}
        </div>
      ) : (
        write && (
          <div className="flex justify-end">
            <button className="btn btn-primary btn-sm" onClick={startCount} disabled={starting}>
              {starting ? '…' : '+ بدء جرد جديد'}
            </button>
          </div>
        )
      )}

      {/* Scan line */}
      {openCount && write && (
        <div className="card">
          <div className="text-sm font-semibold mb-3">تسجيل كمية فعلية</div>
          <div className="flex items-end gap-3">
            <div className="flex-1">
              <Select label="المادة" value={scanForm.material_id} onChange={e => setScanForm(f => ({ ...f, material_id: e.target.value }))}>
                <option value="">— اختر —</option>
                {materials?.map(m => <option key={m.id} value={m.id}>{m.name_ar} (دفتري: {Number(m.stock_qty || 0).toFixed(1)})</option>)}
              </Select>
            </div>
            <div style={{ width: 120 }}>
              <Input label="الكمية الفعلية" type="number" step="0.1" value={scanForm.actual_qty}
                     onChange={e => setScanForm(f => ({ ...f, actual_qty: e.target.value }))} />
            </div>
            <button className="btn btn-primary btn-sm mb-0.5" onClick={scan} disabled={scanning}>
              {scanning ? '…' : '📥 تسجيل'}
            </button>
          </div>
        </div>
      )}

      {/* History */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">سجل عمليات الجرد</div>
        <table className="erp-table">
          <thead><tr><th>التاريخ</th><th>الحالة</th><th>الخطوط</th><th>المعدّل</th></tr></thead>
          <tbody>
            {!counts?.length && <tr><td colSpan={4} className="text-center text-erp-muted py-10">لا توجد عمليات جرد</td></tr>}
            {counts?.map(c => (
              <tr key={c.id}>
                <td className="text-xs text-erp-muted">{fmtDate(c.started_at)}</td>
                <td><span className={`badge ${c.status === 'open' ? 'badge-amber' : 'badge-green'}`}>
                  {c.status === 'open' ? 'مفتوح' : 'مغلق'}
                </span></td>
                <td>{c.line_count || 0}</td>
                <td className="font-medium">{c.adjusted_lines || 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
