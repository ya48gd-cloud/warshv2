/**
 * Field Service — المشاريع الخارجية
 * Workspace UI: Project Cards + Tabs (نظرة عامة / العمالة / العهدة / الزيارات)
 */
import { useState, useMemo, useEffect } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth }  from '../store/auth'
import { useToast } from '../store/toast'
import api, { bgErr }   from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import Modal, { ModalFooter }  from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import SearchInput  from '../components/ui/SearchInput'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

// ── Constants ───────────────────────────────────────────────────
const ORDER_TYPE_AR = {
  installation:'تركيب', maintenance:'صيانة دورية', repair:'إصلاح طارئ',
  inspection:'فحص', commissioning:'تشغيل أولي', training:'تدريب', other:'أخرى',
}
const PRIORITY_AR    = { low:'منخفضة', normal:'عادية', high:'عالية', urgent:'عاجلة' }
const STATUS_AR      = { pending:'معلق', assigned:'مُكلَّف', in_progress:'جاري', on_hold:'متوقف', completed:'مكتمل', cancelled:'ملغي' }
const PRIORITY_BADGE = { low:'badge-blue', normal:'badge-green', high:'badge-amber', urgent:'badge-red' }
const STATUS_BADGE   = { pending:'badge-gray', assigned:'badge-blue', in_progress:'badge-amber', on_hold:'badge-red', completed:'badge-green', cancelled:'badge-red' }
const TOOL_STATUS_AR = { withdrawn:'مسحوب', returned:'مرتجع', damaged:'تالف' }
const TOOL_BADGE     = { withdrawn:'badge-amber', returned:'badge-green', damaged:'badge-red' }

const ASSET_STATUS_AR    = { active:'نشطة', retired:'متوقفة', sold:'مباعة' }
const ASSET_STATUS_BADGE = { active:'badge-green', retired:'badge-gray', sold:'badge-blue' }
const MOVEMENT_TYPE_AR   = {
  manufactured:'تصنيع', delivered:'تسليم للعميل', installed:'تركيب',
  relocated:'نقل لموقع آخر', maintained:'صيانة', returned:'إرجاع',
}
const MOVEMENT_TYPE_BADGE = {
  manufactured:'badge-gray', delivered:'badge-blue', installed:'badge-green',
  relocated:'badge-amber', maintained:'badge-amber', returned:'badge-red',
}
const MOVEMENT_TYPES = ['installed', 'relocated', 'maintained', 'returned']

// التاريخ المحلي YYYY-MM-DD (today() في utils بيرجع UTC)
const localToday = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// اسم المادة — موديل Material مفيهوش حقل name (name_ar / name_en بس)،
// فكانت قائمة اختيار العدة بتظهر فاضية.
const matName = (m) => m?.name_ar || m?.name || m?.name_en || m?.code || `#${m?.id}`

// بناء payload نظيف لأمر الخدمة — كان الفورم بيتبعت زي ما هو، فـ location_id: ''
// (بيتضبط تلقائي عند تغيير العميل) أو planned_date: '' كانوا بيرجعوا 422
// ويمنعوا إنشاء/تعديل أي مشروع.
const buildOrderPayload = (f) => ({
  code:            String(f.code || '').trim(),
  customer_id:     Number(f.customer_id),
  location_id:     f.location_id ? Number(f.location_id) : null,
  asset_id:        f.asset_id ? Number(f.asset_id) : null,
  order_type:      f.order_type || 'maintenance',
  title:           String(f.title || '').trim(),
  description:     f.description || null,
  priority:        f.priority || 'normal',
  planned_date:    f.planned_date || null,
  estimated_hours: (f.estimated_hours === '' || f.estimated_hours == null) ? null : Number(f.estimated_hours),
  notes:           f.notes || null,
})

const validateOrderForm = (f) => {
  if (!String(f.code || '').trim())  return 'الكود مطلوب'
  if (!String(f.title || '').trim()) return 'العنوان مطلوب'
  if (!f.customer_id)                return 'اختر العميل'
  if (f.estimated_hours !== '' && f.estimated_hours != null && (!Number.isFinite(Number(f.estimated_hours)) || Number(f.estimated_hours) < 0))
    return 'الساعات التقديرية لا يمكن أن تكون سالبة'
  return null
}

// ══════════════════════════════════════════════════════════════
// PROJECT CARD
// ══════════════════════════════════════════════════════════════
function ProjectCard({ order, onClick, write, onQuickStatus, onDelete }) {
  const st  = STATUS_BADGE[order.status]  || 'badge-gray'
  const pri = PRIORITY_BADGE[order.priority] || 'badge-blue'
  const visitCount   = order.visits?.length || 0
  const workerCount  = order.assignments?.length || 0
  // مقارنة نصية بالتاريخ المحلي — new Date('YYYY-MM-DD') كان بيعلّم مشاريع النهارده "متأخرة"، وكمان الملغاة
  const hasAlert     = order.status === 'on_hold' || (order.planned_date && String(order.planned_date).slice(0, 10) < localToday() && !['completed', 'cancelled'].includes(order.status))

  return (
    <div
      className="card flex flex-col gap-3 cursor-pointer hover:shadow-md transition-shadow"
      style={{ borderTop: `3px solid var(--${order.status === 'completed' ? 'green' : order.status === 'in_progress' ? 'amber' : order.status === 'on_hold' ? 'red' : 'blue'})` }}
      onClick={onClick}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="font-mono text-[10px] badge badge-blue mb-1">{order.code}</div>
          <div className="font-semibold text-sm leading-tight truncate">{order.title}</div>
          <div className="text-xs text-erp-muted mt-0.5">{order.customer_name}</div>
        </div>
        <div className="flex flex-col gap-1 items-end">
          <span className={`badge ${st} text-[10px]`}>{STATUS_AR[order.status]}</span>
          <span className={`badge ${pri} text-[10px]`}>{PRIORITY_AR[order.priority]}</span>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded p-1.5" style={{ background:'var(--bg-secondary)' }}>
          <div className="text-xs font-semibold" style={{ color:'var(--blue)' }}>{workerCount}</div>
          <div className="text-[10px] text-erp-muted">فني</div>
        </div>
        <div className="rounded p-1.5" style={{ background:'var(--bg-secondary)' }}>
          <div className="text-xs font-semibold" style={{ color:'var(--teal)' }}>{visitCount}</div>
          <div className="text-[10px] text-erp-muted">زيارة</div>
        </div>
        <div className="rounded p-1.5" style={{ background:'var(--bg-secondary)' }}>
          <div className="text-xs font-semibold" style={{ color:'var(--amber)' }}>{egp(order.total_cost || 0)}</div>
          <div className="text-[10px] text-erp-muted">التكلفة</div>
        </div>
      </div>

      {/* Alert */}
      {hasAlert && (
        <div className="text-[11px] px-2 py-1 rounded" style={{ background:'#FAEEDA', color:'#854F0B' }}>
          {order.status === 'on_hold' ? '⏸ المشروع متوقف' : '⚠ تجاوز التاريخ المخطط'}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between text-[10px] text-erp-muted border-t border-erp-border pt-2">
        <span>{ORDER_TYPE_AR[order.order_type] || order.order_type}</span>
        {order.planned_date && <span>{fmtDate(order.planned_date)}</span>}
      </div>

      {/* Quick actions */}
      {write && (
        <div className="flex gap-1" onClick={e => e.stopPropagation()}>
          {['pending', 'assigned'].includes(order.status) && (
            <button className="btn btn-sm text-[11px] flex-1" style={{ color:'var(--amber)' }}
              onClick={() => onQuickStatus(order.id, 'in_progress')}>▶ بدء</button>
          )}
          {order.status === 'in_progress' && (
            <button className="btn btn-sm text-[11px] flex-1" style={{ color:'var(--green)' }}
              onClick={() => { if (window.confirm(`إتمام المشروع "${order.title}"؟`)) onQuickStatus(order.id, 'completed') }}>✔ إتمام</button>
          )}
          {/* الحذف مسموح بالباك-إند للمعلّق/الملغي فقط — كان ConfirmDialog الحذف موجود من غير أي زرار يفتحه */}
          {onDelete && ['pending', 'cancelled'].includes(order.status) && (
            <button className="btn btn-sm btn-danger text-[11px]" title="حذف المشروع"
              onClick={() => onDelete(order)}>🗑</button>
          )}
        </div>
      )}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// LABOR TAB
// ══════════════════════════════════════════════════════════════
function LaborTab({ orderId, write, onChanged }) {
  const toast = useToast()
  const { data: workers } = useApi(() => api.workers.list())
  const { data: entries, reload } = useApi(() => api.fieldService.listLabor(orderId), [orderId])
  const [modal, setModal] = useState(false)
  const [form, setForm]   = useState({ entry_date: localToday(), days: 1, daily_wage: '', allowance: 0, advance: 0 })

  const totalPay     = (entries || []).reduce((s, e) => s + (Number(e.total_pay) || 0), 0)
  const totalAdvance = (entries || []).reduce((s, e) => s + (Number(e.advance) || 0),   0)

  const computed = (Number(form.days) || 0) * (Number(form.daily_wage) || 0) + (Number(form.allowance) || 0)

  const [save, saving] = useAction(async () => {
    if (!form.worker_id)  { toast('اختر العامل',  'error'); return }
    if (!form.entry_date) { toast('أدخل التاريخ', 'error'); return }
    if (form.daily_wage === '' || form.daily_wage == null) { toast('أدخل الأجر اليومي', 'error'); return }
    const days = Number(form.days)
    if (!Number.isFinite(days) || days <= 0) { toast('عدد الأيام يجب أن يكون أكبر من صفر', 'error'); return }
    for (const [k, label] of [['daily_wage', 'الأجر اليومي'], ['allowance', 'البدل'], ['advance', 'السلفة']]) {
      const v = Number(form[k] || 0)
      if (!Number.isFinite(v) || v < 0) { toast(`${label} لا يمكن أن يكون سالباً`, 'error'); return }
    }
    await api.fieldService.addLabor(orderId, {
      worker_id:  Number(form.worker_id),
      entry_date: form.entry_date,
      days,
      daily_wage: Number(form.daily_wage) || 0,
      allowance:  Number(form.allowance)  || 0,
      advance:    Number(form.advance)    || 0,
      notes:      form.notes || null,
    })
    toast('✅ تمت الإضافة')
    setModal(false)
    setForm({ entry_date: localToday(), days: 1, daily_wage: '', allowance: 0, advance: 0 })
    reload(); onChanged?.()   // تكلفة العمالة في رأس المشروع كانت بتفضل قديمة
  })

  const [doDelete, deletingLabor] = useAction(async (id) => {
    await api.fieldService.deleteLabor(orderId, id)
    toast('تم الحذف'); reload(); onChanged?.()
  })

  return (
    <div className="flex flex-col gap-4">
      {/* Summary KPIs */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'إجمالي المستحقات', val: egp(totalPay),     color: 'var(--teal)'  },
          { label: 'إجمالي السلف',     val: egp(totalAdvance), color: 'var(--amber)' },
          { label: 'الصافي',           val: egp(totalPay - totalAdvance), color: 'var(--green)' },
        ].map(k => (
          <div key={k.label} className="card text-center py-3">
            <div className="text-sm font-bold" style={{ color: k.color }}>{k.val}</div>
            <div className="text-[11px] text-erp-muted mt-1">{k.label}</div>
          </div>
        ))}
      </div>

      {/* Toolbar */}
      <div className="flex justify-between items-center">
        <div className="text-sm font-semibold">تكاليف العمالة</div>
        {write && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ إضافة</button>}
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr>
              <th>العامل</th><th>التاريخ</th><th>الأيام</th>
              <th>الأجر/يوم</th><th>بدل معيشة</th><th>سلفة</th>
              <th style={{ color:'var(--teal)' }}>الإجمالي</th>
              {write && <th />}
            </tr>
          </thead>
          <tbody>
            {!entries?.length && (
              <tr><td colSpan={8} className="text-center text-erp-muted py-8">لا توجد قيود عمالة — اضغط &quot;إضافة&quot;</td></tr>
            )}
            {entries?.map(e => (
              <tr key={e.id}>
                <td className="font-medium">{e.worker_name}</td>
                <td className="text-xs text-erp-muted">{fmtDate(e.entry_date)}</td>
                <td className="text-center">{e.days}</td>
                <td>{egp(e.daily_wage)}</td>
                <td>{egp(e.allowance)}</td>
                <td style={{ color:'var(--amber)' }}>{egp(e.advance)}</td>
                <td className="font-semibold" style={{ color:'var(--teal)' }}>{egp(e.total_pay)}</td>
                {write && (
                  <td>
                    <button className="btn btn-sm btn-danger text-xs" disabled={deletingLabor}
                      onClick={() => { if (window.confirm(`حذف قيد عمالة "${e.worker_name}"؟`)) doDelete(e.id) }}>🗑</button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Modal */}
      <Modal open={modal} onClose={() => setModal(false)} title="إضافة قيد عمالة" size="sm">
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Select label="العامل *" value={form.worker_id || ''}
              onChange={e => setForm(f => ({ ...f, worker_id: e.target.value }))}>
              <option value="">— اختر —</option>
              {(workers || []).filter(w => w.is_active).map(w =>
                <option key={w.id} value={w.id}>{w.name}</option>
              )}
            </Select>
            <Input label="التاريخ *" type="date" value={form.entry_date}
              onChange={e => setForm(f => ({ ...f, entry_date: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={3}>
            <Input label="عدد الأيام" type="number" step="0.5" min="0.5" value={form.days}
              onChange={e => setForm(f => ({ ...f, days: e.target.value }))} />
            <Input label="الأجر اليومي (ج) *" type="number" step="0.01" min="0" value={form.daily_wage}
              onChange={e => setForm(f => ({ ...f, daily_wage: e.target.value }))} />
            <Input label="بدل معيشة/سفر (ج)" type="number" step="0.01" min="0" value={form.allowance}
              onChange={e => setForm(f => ({ ...f, allowance: e.target.value }))} />
          </FormGrid>
          <Input label="سلفة (ج)" type="number" step="0.01" min="0" value={form.advance}
            onChange={e => setForm(f => ({ ...f, advance: e.target.value }))} />
          {computed > 0 && (
            <div className="flex justify-between px-3 py-2 rounded text-sm"
              style={{ background:'var(--teal-lt)' }}>
              <span className="text-erp-muted">إجمالي المستحق</span>
              <span className="font-bold" style={{ color:'var(--teal)' }}>{egp(computed)}</span>
            </div>
          )}
          <Textarea label="ملاحظات" rows={1} value={form.notes || ''}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
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
// TOOLS TAB — عهدة العدة
// ══════════════════════════════════════════════════════════════
function ToolsTab({ orderId, write, onChanged }) {
  const toast = useToast()
  const { data: materials } = useApi(() => api.inventory.materials())
  const { data: workers   } = useApi(() => api.workers.list())
  const { data: tools, reload } = useApi(() => api.fieldService.listTools(orderId), [orderId])

  const [withdrawModal, setWithdrawModal] = useState(false)
  const [returnModal,   setReturnModal]   = useState(null)   // tool row
  const [wForm, setWForm] = useState({ withdrawn_date: localToday(), qty: 1 })
  const [rForm, setRForm] = useState({ returned_date: localToday(), returned_qty: 1, damaged_qty: 0 })

  const openCount    = (tools || []).filter(t => t.status === 'withdrawn').length
  const damagedCount = (tools || []).filter(t => t.status === 'damaged').length

  const [saveWithdraw, savingW] = useAction(async () => {
    if (!wForm.material_id) { toast('اختر العدة', 'error'); return }
    if (!wForm.withdrawn_date) { toast('أدخل تاريخ السحب', 'error'); return }
    const qty = Number(wForm.qty)
    if (!Number.isFinite(qty) || qty <= 0) { toast('الكمية يجب أن تكون أكبر من صفر', 'error'); return }
    const mat = (materials || []).find(m => String(m.id) === String(wForm.material_id))
    const avail = mat ? Number(mat.available_qty ?? mat.stock_qty) : NaN
    if (Number.isFinite(avail) && qty > avail + 1e-9) {
      toast(`الكمية المطلوبة (${qty}) أكبر من المتاح بالمخزون (${avail})`, 'error'); return
    }
    await api.fieldService.withdrawTool(orderId, {
      material_id:    Number(wForm.material_id),
      worker_id:      wForm.worker_id ? Number(wForm.worker_id) : null,
      qty,
      withdrawn_date: wForm.withdrawn_date,
      notes:          wForm.notes || null,
    })
    toast('✅ تم تسجيل السحب')
    setWithdrawModal(false)
    setWForm({ withdrawn_date: localToday(), qty: 1 })
    reload(); onChanged?.()
  })

  const [saveReturn, savingR] = useAction(async () => {
    if (!returnModal) return
    if (!rForm.returned_date) { toast('أدخل تاريخ الإرجاع', 'error'); return }
    // كان Number(...) || qty بيحوّل "صفر مرتجع" (كله تالف/مفقود) لإرجاع الكمية كاملة
    const retQty = rForm.returned_qty === '' ? 0 : Number(rForm.returned_qty)
    const dmgQty = rForm.damaged_qty === '' ? 0 : Number(rForm.damaged_qty)
    if (!Number.isFinite(retQty) || !Number.isFinite(dmgQty) || retQty < 0 || dmgQty < 0) {
      toast('الكمية لا يمكن أن تكون سالبة', 'error'); return
    }
    if (retQty + dmgQty > Number(returnModal.qty) + 1e-9) {
      toast(`إجمالي المرتجع والتالف (${retQty + dmgQty}) أكبر من الكمية المسحوبة (${returnModal.qty})`, 'error'); return
    }
    if (retQty + dmgQty === 0) { toast('أدخل الكمية المرتجعة أو التالفة', 'error'); return }
    await api.fieldService.returnTool(orderId, returnModal.id, {
      returned_date: rForm.returned_date,
      returned_qty:  retQty,
      damaged_qty:   dmgQty,
      notes:         rForm.notes || null,
    })
    toast('✅ تم تسجيل الإرجاع')
    setReturnModal(null)
    reload(); onChanged?.()
  })

  const [doDelete, deletingTool] = useAction(async (id) => {
    await api.fieldService.deleteTool(orderId, id)
    toast('تم الحذف'); reload(); onChanged?.()
  })

  return (
    <div className="flex flex-col gap-4">
      {/* KPIs */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'إجمالي المسحوب',  val: (tools||[]).length,  color:'var(--blue)'  },
          { label: 'عهدة مفتوحة',     val: openCount,           color:'var(--amber)' },
          { label: 'تالف أو مفقود',   val: damagedCount,        color:'var(--red)'   },
        ].map(k => (
          <div key={k.label} className="card text-center py-3">
            <div className="text-lg font-bold" style={{ color: k.color }}>{k.val}</div>
            <div className="text-[11px] text-erp-muted mt-1">{k.label}</div>
          </div>
        ))}
      </div>

      {/* Toolbar */}
      <div className="flex justify-between items-center">
        <div className="text-sm font-semibold">عهدة العدة والأدوات</div>
        {write && <button className="btn btn-primary btn-sm" onClick={() => setWithdrawModal(true)}>+ سحب عدة</button>}
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr>
              <th>العدة / المادة</th><th>المستلم</th><th>الكمية</th>
              <th>تاريخ السحب</th><th>تاريخ الإرجاع</th><th>الحالة</th>
              {write && <th />}
            </tr>
          </thead>
          <tbody>
            {!tools?.length && (
              <tr><td colSpan={7} className="text-center text-erp-muted py-8">لا توجد عهدة مسجلة — اضغط &quot;سحب عدة&quot;</td></tr>
            )}
            {tools?.map(t => (
              <tr key={t.id}>
                <td className="font-medium">{t.material_name}</td>
                <td className="text-xs text-erp-muted">{t.worker_name || '—'}</td>
                <td className="text-center">{t.qty} {t.material_unit}</td>
                <td className="text-xs">{fmtDate(t.withdrawn_date)}</td>
                <td className="text-xs">{t.returned_date ? fmtDate(t.returned_date) : <span className="text-erp-muted">—</span>}</td>
                <td>
                  <span className={`badge ${TOOL_BADGE[t.status] || 'badge-gray'} text-[11px]`}>
                    {TOOL_STATUS_AR[t.status] || t.status}
                  </span>
                </td>
                {write && (
                  <td>
                    <div className="flex gap-1">
                      {t.status === 'withdrawn' && (
                        <button className="btn btn-sm text-[11px]"
                          style={{ color:'var(--green)', borderColor:'var(--green-lt)' }}
                          onClick={() => { setRForm({ returned_date: localToday(), returned_qty: t.qty, damaged_qty: 0 }); setReturnModal(t) }}>
                          ↩ إرجاع
                        </button>
                      )}
                      {t.status === 'withdrawn' && (
                        <button className="btn btn-sm btn-danger text-xs" disabled={deletingTool}
                          onClick={() => { if (window.confirm(`حذف سحب "${t.material_name}" وإرجاع الكمية للمخزون؟`)) doDelete(t.id) }}>🗑</button>
                      )}
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Withdraw Modal */}
      <Modal open={withdrawModal} onClose={() => setWithdrawModal(false)} title="سحب عدة على المشروع" size="sm">
        <div className="flex flex-col gap-3">
          <Select label="العدة / المادة *" value={wForm.material_id || ''}
            onChange={e => setWForm(f => ({ ...f, material_id: e.target.value }))}>
            <option value="">— اختر —</option>
            {(Array.isArray(materials) ? materials : []).map(m => (
              <option key={m.id} value={m.id}>
                {matName(m)}{m.unit ? ` (${m.unit})` : ''}
                {(m.available_qty ?? m.stock_qty) != null ? ` — متاح: ${Number(m.available_qty ?? m.stock_qty)}` : ''}
              </option>
            ))}
          </Select>
          <FormGrid cols={2}>
            <Input label="الكمية" type="number" step="0.001" min="0.001" value={wForm.qty}
              onChange={e => setWForm(f => ({ ...f, qty: e.target.value }))} />
            <Input label="تاريخ السحب *" type="date" value={wForm.withdrawn_date}
              onChange={e => setWForm(f => ({ ...f, withdrawn_date: e.target.value }))} />
          </FormGrid>
          <Select label="المستلم (اختياري)" value={wForm.worker_id || ''}
            onChange={e => setWForm(f => ({ ...f, worker_id: e.target.value }))}>
            <option value="">— اختياري —</option>
            {(workers || []).filter(w => w.is_active).map(w =>
              <option key={w.id} value={w.id}>{w.name}</option>
            )}
          </Select>
          <Textarea label="ملاحظات" rows={1} value={wForm.notes || ''}
            onChange={e => setWForm(f => ({ ...f, notes: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setWithdrawModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={saveWithdraw} disabled={savingW}>{savingW ? '…' : 'سحب'}</button>
        </ModalFooter>
      </Modal>

      {/* Return Modal */}
      <Modal open={!!returnModal} onClose={() => setReturnModal(null)}
        title={`إرجاع: ${returnModal?.material_name}`} size="sm">
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="الكمية المرتجعة" type="number" step="0.001" min="0" value={rForm.returned_qty}
              onChange={e => setRForm(f => ({ ...f, returned_qty: e.target.value }))} />
            <Input label="الكمية التالفة" type="number" step="0.001" min="0" value={rForm.damaged_qty}
              onChange={e => setRForm(f => ({ ...f, damaged_qty: e.target.value }))} />
          </FormGrid>
          <Input label="تاريخ الإرجاع *" type="date" value={rForm.returned_date}
            onChange={e => setRForm(f => ({ ...f, returned_date: e.target.value }))} />
          <Textarea label="ملاحظات" rows={1} value={rForm.notes || ''}
            onChange={e => setRForm(f => ({ ...f, notes: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setReturnModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={saveReturn} disabled={savingR}>{savingR ? '…' : 'تأكيد الإرجاع'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// VISITS TAB
// ══════════════════════════════════════════════════════════════
function VisitsTab({ order, write, onReload }) {
  const toast   = useToast()
  const { data: workers } = useApi(() => api.workers.list())
  const [modal, setModal] = useState(false)
  const [form,  setForm]  = useState({ visit_date: localToday(), status: 'completed' })
  const [assignModal, setAssignModal] = useState(false)
  const [assignIds, setAssignIds]     = useState([])

  const [saveVisit, saving] = useAction(async () => {
    if (!form.visit_date) { toast('تاريخ الزيارة مطلوب', 'error'); return }
    if (form.labor_hours != null && form.labor_hours !== '' && (!Number.isFinite(Number(form.labor_hours)) || Number(form.labor_hours) < 0)) {
      toast('ساعات العمل لا يمكن أن تكون سالبة', 'error'); return
    }
    if (form.arrival_time && form.departure_time && form.departure_time < form.arrival_time) {
      toast('وقت المغادرة يجب أن يكون بعد وقت الوصول', 'error'); return
    }
    await api.fieldService.recordVisit(order.id, {
      ...form,
      labor_hours:    form.labor_hours ? Number(form.labor_hours) : null,
      arrival_time:   form.arrival_time || null,
      departure_time: form.departure_time || null,
    })
    toast('✅ تم تسجيل الزيارة')
    setModal(false)
    setForm({ visit_date: localToday(), status: 'completed' })
    onReload()
  })

  const [assign, assigning] = useAction(async () => {
    if (!assignIds.length) { toast('اختر فني على الأقل', 'error'); return }
    await api.fieldService.assignWorkers(order.id, { worker_ids: assignIds.map(Number), role: 'technician' })
    toast('✅ تم التعيين')
    setAssignModal(false)
    setAssignIds([])
    onReload()
  })

  const [removeAssignment] = useAction(async (assignmentId) => {
    await api.fieldService.removeAssignment(order.id, assignmentId)
    toast('تم إلغاء التعيين')
    onReload()
  })

  return (
    <div className="flex flex-col gap-4">

      {/* Assigned workers */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm font-semibold">الفنيون المعينون ({order.assignments?.length || 0})</div>
          {write && order.status !== 'completed' && order.status !== 'cancelled' && (
            <button className="btn btn-sm text-xs btn-primary" onClick={() => setAssignModal(true)}>+ تعيين فني</button>
          )}
        </div>
        {!order.assignments?.length
          ? <div className="text-xs text-erp-muted">لا يوجد فنيون معينون</div>
          : <div className="flex flex-wrap gap-2">
              {order.assignments.map(a => (
                <div key={a.id} className="flex items-center gap-2 px-3 py-1.5 rounded-full text-xs"
                  style={{ background:'var(--blue-lt)', color:'var(--blue)' }}>
                  👷 {a.worker_name}
                  {write && order.status !== 'completed' && order.status !== 'cancelled' && (
                    <button
                      className="text-xs leading-none"
                      style={{ color:'var(--red)' }}
                      title="إلغاء التعيين"
                      onClick={() => { if (window.confirm(`إلغاء تعيين "${a.worker_name}"؟`)) removeAssignment(a.id) }}
                    >✕</button>
                  )}
                </div>
              ))}
            </div>
        }
      </div>

      {/* Visits */}
      <div className="flex justify-between items-center">
        <div className="text-sm font-semibold">الزيارات ({order.visits?.length || 0})</div>
        {write && order.status !== 'completed' && order.status !== 'cancelled' && (
          <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ تسجيل زيارة</button>
        )}
      </div>

      {!order.visits?.length
        ? <div className="card text-center text-erp-muted py-8 text-sm">لا توجد زيارات مسجلة</div>
        : order.visits.map(v => (
            <div key={v.id} className="card">
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="font-medium text-sm">{fmtDate(v.visit_date)}</div>
                <div className="flex gap-2">
                  {v.labor_hours && <span className="badge badge-blue text-[10px]">{v.labor_hours} ساعة</span>}
                  {v.arrival_time && <span className="text-[10px] text-erp-muted">{v.arrival_time} — {v.departure_time}</span>}
                </div>
              </div>
              {v.work_done     && <div className="text-xs text-erp-muted mb-1">✅ {v.work_done}</div>}
              {v.materials_used && <div className="text-xs text-erp-muted mb-1">🔧 {v.materials_used}</div>}
              {v.next_action   && <div className="text-xs" style={{ color:'var(--amber)' }}>▶ الخطوة التالية: {v.next_action}</div>}
            </div>
          ))
      }

      {/* Visit Modal */}
      <Modal open={modal} onClose={() => setModal(false)} title="تسجيل زيارة ميدانية">
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="تاريخ الزيارة *" type="date" value={form.visit_date}
              onChange={e => setForm(f => ({ ...f, visit_date: e.target.value }))} />
            <Input label="ساعات العمل" type="number" step="0.5" min="0" value={form.labor_hours || ''}
              onChange={e => setForm(f => ({ ...f, labor_hours: +e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="وقت الوصول" type="time" value={form.arrival_time || ''}
              onChange={e => setForm(f => ({ ...f, arrival_time: e.target.value }))} />
            <Input label="وقت المغادرة" type="time" value={form.departure_time || ''}
              onChange={e => setForm(f => ({ ...f, departure_time: e.target.value }))} />
          </FormGrid>
          <Textarea label="الأعمال المنجزة" value={form.work_done || ''}
            onChange={e => setForm(f => ({ ...f, work_done: e.target.value }))} rows={2} />
          <Textarea label="المواد المستخدمة" value={form.materials_used || ''}
            onChange={e => setForm(f => ({ ...f, materials_used: e.target.value }))} rows={2} />
          <Textarea label="الخطوة التالية" value={form.next_action || ''}
            onChange={e => setForm(f => ({ ...f, next_action: e.target.value }))} rows={1} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={saveVisit} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>

      {/* Assign Modal */}
      <Modal open={assignModal} onClose={() => setAssignModal(false)} title="تعيين فنيين" size="sm">
        <div className="flex flex-col gap-3">
          <label className="field-label">اختر الفنيين (يمكن اختيار أكثر من واحد)</label>
          <div className="flex flex-col gap-1 max-h-48 overflow-y-auto">
            {(workers || []).filter(w => w.is_active).map(w => (
              <label key={w.id} className="flex items-center gap-2 px-3 py-2 rounded cursor-pointer hover:bg-erp-secondary">
                <input type="checkbox" className="rounded"
                  checked={assignIds.includes(String(w.id))}
                  onChange={e => setAssignIds(prev =>
                    e.target.checked ? [...prev, String(w.id)] : prev.filter(x => x !== String(w.id))
                  )} />
                <span className="text-sm">{w.name}</span>
              </label>
            ))}
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setAssignModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={assign} disabled={assigning || !assignIds.length}>
            {assigning ? '…' : `تعيين (${assignIds.length})`}
          </button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// PROJECT WORKSPACE — داخل المشروع
// ══════════════════════════════════════════════════════════════
function ProjectWorkspace({ orderId, write, customers, onBack, onReload }) {
  const toast = useToast()
  const { data: order, loading, reload } = useApi(() => api.fieldService.getOrder(orderId), [orderId])
  const [activeTab, setActiveTab] = useState('visits')
  const [editModal, setEditModal] = useState(false)
  const [form, setForm] = useState({})

  // مواقع العميل المختار
  const [locations, setLocations] = useState([])
  useEffect(() => {
    if (!form.customer_id) { setLocations([]); return }
    api.customers.locations(form.customer_id).then(r => setLocations(Array.isArray(r) ? r : [])).catch(bgErr('عناوين العميل', () => setLocations([])))
  }, [form.customer_id])

  const handleReload = () => { reload(); onReload() }

  const [updateStatusRaw, updatingStatus] = useAction(async (status) => {
    await api.fieldService.updateOrderStatus(order.id, { status })
    toast('تم تحديث الحالة')
    handleReload()
  })
  // تأكيد قبل الإلغاء/الإتمام (لا رجوع منهما من الواجهة) + منع الضغط المزدوج
  const updateStatus = (status) => {
    if (updatingStatus) return
    if (status === 'cancelled' && !window.confirm(`إلغاء المشروع "${order.title}"؟`)) return
    if (status === 'completed' && !window.confirm(`إتمام المشروع "${order.title}"؟`)) return
    updateStatusRaw(status)
  }

  const [save, saving] = useAction(async () => {
    const err = validateOrderForm(form)
    if (err) { toast(err, 'error'); return }
    await api.fieldService.updateOrder(order.id, buildOrderPayload(form))
    toast('✅ تم الحفظ')
    setEditModal(false)
    handleReload()
  })

  if (loading || !order) return <PageSpinner />

  const st  = STATUS_BADGE[order.status]   || 'badge-gray'
  const pri = PRIORITY_BADGE[order.priority] || 'badge-blue'

  const tabs = [
    { id: 'visits',  label: 'الزيارات'    },
    { id: 'labor',   label: 'العمالة'     },
    { id: 'tools',   label: 'العهدة'      },
  ]

  return (
    <div className="flex flex-col gap-4">

      {/* Back + Edit */}
      <div className="flex items-center gap-3">
        <button className="btn btn-sm text-xs" onClick={onBack}>← المشاريع</button>
        {write && <button className="btn btn-sm text-xs" onClick={() => { setForm({ ...order }); setEditModal(true) }}>✏️ تعديل</button>}
      </div>

      {/* Project Header */}
      <div className="card" style={{ borderRight: `4px solid var(--${order.status === 'completed' ? 'green' : order.status === 'in_progress' ? 'amber' : 'blue'})` }}>
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="font-mono text-xs badge badge-blue mb-1">{order.code}</div>
            <div className="text-lg font-bold">{order.title}</div>
            <div className="text-sm text-erp-muted">
              {order.customer_name}{order.location_name ? ` — ${order.location_name}` : ''} — {ORDER_TYPE_AR[order.order_type]}
            </div>
            {order.planned_date && <div className="text-xs text-erp-muted mt-1">التاريخ المخطط: {fmtDate(order.planned_date)}</div>}
          </div>
          <div className="flex gap-2 flex-wrap">
            <span className={`badge ${st}`}>{STATUS_AR[order.status]}</span>
            <span className={`badge ${pri}`}>{PRIORITY_AR[order.priority]}</span>
          </div>
        </div>

        {/* KPI mini row */}
        <div className="grid grid-cols-4 gap-3 mt-4">
          {[
            { label: 'تكلفة العمالة',  val: egp(order.labor_cost),    color: 'var(--blue)'  },
            { label: 'تكلفة المواد',   val: egp(order.materials_cost), color: 'var(--amber)' },
            { label: 'إجمالي التكلفة', val: egp(order.total_cost),    color: 'var(--teal)'  },
            { label: 'ساعات فعلية',    val: `${order.actual_hours || 0} س`, color: 'var(--muted)' },
          ].map(k => (
            <div key={k.label} className="text-center">
              <div className="text-sm font-bold" style={{ color: k.color }}>{k.val}</div>
              <div className="text-[10px] text-erp-muted">{k.label}</div>
            </div>
          ))}
        </div>

        {/* Status actions */}
        {write && (
          <div className="flex gap-2 flex-wrap mt-4 pt-3 border-t border-erp-border">
            {['pending', 'assigned'].includes(order.status) && <button className="btn btn-sm text-xs" style={{ color:'var(--amber)' }} disabled={updatingStatus} onClick={() => updateStatus('in_progress')}>▶ بدء التنفيذ</button>}
            {order.status === 'in_progress' && <button className="btn btn-sm text-xs" style={{ color:'var(--green)' }} onClick={() => updateStatus('completed')}>✔ إتمام المشروع</button>}
            {order.status === 'in_progress' && <button className="btn btn-sm text-xs" style={{ color:'var(--amber)' }} onClick={() => updateStatus('on_hold')}>⏸ إيقاف مؤقت</button>}
            {order.status === 'on_hold'     && <button className="btn btn-sm text-xs" style={{ color:'var(--blue)'  }} onClick={() => updateStatus('in_progress')}>▶ استئناف</button>}
            {!['completed','cancelled'].includes(order.status) && (
              <button className="btn btn-sm text-xs" style={{ color:'var(--red)' }} onClick={() => updateStatus('cancelled')}>✕ إلغاء</button>
            )}
          </div>
        )}
      </div>

      {/* Description */}
      {order.description && (
        <div className="card py-2 px-3 text-sm text-erp-muted">{order.description}</div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-erp-border pb-0">
        {tabs.map(t => (
          <button key={t.id}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === t.id
                ? 'border-teal-500 text-teal-600'
                : 'border-transparent text-erp-muted hover:text-erp-primary'
            }`}
            style={{ marginBottom: '-1px', ...(activeTab === t.id ? { borderColor:'var(--teal)', color:'var(--teal)' } : {}) }}
            onClick={() => setActiveTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'visits' && <VisitsTab order={order} write={write} onReload={handleReload} />}
      {activeTab === 'labor'  && <LaborTab  orderId={order.id} write={write} onChanged={handleReload} />}
      {activeTab === 'tools'  && <ToolsTab  orderId={order.id} write={write} onChanged={handleReload} />}

      {/* Edit Modal */}
      <Modal open={editModal} onClose={() => setEditModal(false)} title="تعديل المشروع">
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="الكود *" value={form.code || ''} onChange={e => setForm(f => ({ ...f, code: e.target.value }))} />
            <Input label="العنوان *" value={form.title || ''} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Select label="العميل *" value={form.customer_id || ''} onChange={e => setForm(f => ({ ...f, customer_id: +e.target.value, location_id: '' }))}>
              <option value="">اختر عميل…</option>
              {(customers || []).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
            <Select label="نوع الخدمة" value={form.order_type || 'maintenance'} onChange={e => setForm(f => ({ ...f, order_type: e.target.value }))}>
              {Object.entries(ORDER_TYPE_AR).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </FormGrid>
          {locations.length > 0 && (
            <FormGrid cols={2}>
              <Select label="الموقع / الفرع" value={form.location_id || ''} onChange={e => setForm(f => ({ ...f, location_id: e.target.value }))}>
                <option value="">— بدون موقع محدد —</option>
                {locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
              </Select>
            </FormGrid>
          )}
          <FormGrid cols={2}>
            <Select label="الأولوية" value={form.priority || 'normal'} onChange={e => setForm(f => ({ ...f, priority: e.target.value }))}>
              {Object.entries(PRIORITY_AR).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
            <Input label="تاريخ مخطط" type="date" value={form.planned_date || ''} onChange={e => setForm(f => ({ ...f, planned_date: e.target.value }))} />
          </FormGrid>
          <Textarea label="الوصف" value={form.description || ''} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} rows={2} />
          <Textarea label="ملاحظات" value={form.notes || ''} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} rows={2} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setEditModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// ASSET PROFILE — بروفايل المعدة (Timeline + التكاليف)
// ══════════════════════════════════════════════════════════════
function AssetProfile({ assetId, write, onBack, onReload }) {
  const toast = useToast()
  const { data: asset, loading, reload } = useApi(() => api.fieldService.getAsset(assetId), [assetId])
  const [locations, setLocations] = useState([])
  const [moveModal, setMoveModal] = useState(false)
  const [form, setForm] = useState({ event_type: 'relocated', event_date: localToday() })

  useEffect(() => {
    if (!asset?.customer_id) { setLocations([]); return }
    api.customers.locations(asset.customer_id).then(r => setLocations(Array.isArray(r) ? r : [])).catch(bgErr('عناوين العميل', () => setLocations([])))
  }, [asset?.customer_id])

  const handleReload = () => { reload(); onReload?.() }

  const [saveMove, saving] = useAction(async () => {
    if (!form.event_type) { toast('اختر نوع الحركة', 'error'); return }
    if (['installed', 'relocated'].includes(form.event_type) && !form.to_location_id) {
      toast('اختر الموقع الجديد', 'error'); return
    }
    await api.fieldService.addMovement(assetId, {
      event_type: form.event_type,
      to_location_id: form.to_location_id ? Number(form.to_location_id) : null,
      event_date: form.event_date || localToday(),
      notes: form.notes || null,
    })
    toast('✅ تم تسجيل الحركة')
    setMoveModal(false)
    setForm({ event_type: 'relocated', event_date: localToday() })
    handleReload()
  })

  if (loading || !asset) return <PageSpinner />

  const st = ASSET_STATUS_BADGE[asset.status] || 'badge-gray'

  return (
    <div className="flex flex-col gap-4">
      {/* Back */}
      <div className="flex items-center gap-3">
        <button className="btn btn-sm text-xs" onClick={onBack}>← معدات العملاء</button>
      </div>

      {/* Header */}
      <div className="card" style={{ borderRight: `4px solid var(--${asset.in_warranty ? 'green' : 'blue'})` }}>
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="text-lg font-bold">{asset.name}</div>
            <div className="text-sm text-erp-muted">
              {asset.customer_name}{asset.model ? ` — موديل ${asset.model}` : ''}{asset.serial_number ? ` — SN: ${asset.serial_number}` : ''}
            </div>
            <div className="text-xs text-erp-muted mt-1">
              الموقع الحالي: {asset.current_location_name || asset.location || '—'}
              {asset.source_work_order && <span> — أمر إنتاج المصدر: {asset.source_work_order.code}</span>}
            </div>
          </div>
          <div className="flex flex-col gap-1 items-end">
            <span className={`badge ${st}`}>{ASSET_STATUS_AR[asset.status] || asset.status}</span>
            {asset.warranty_end && (
              <span className={`badge ${asset.in_warranty ? 'badge-green' : 'badge-gray'} text-[10px]`}>
                {asset.in_warranty ? 'تحت الضمان' : 'انتهى الضمان'} حتى {fmtDate(asset.warranty_end)}
              </span>
            )}
          </div>
        </div>

        {/* KPI mini row */}
        <div className="grid grid-cols-4 gap-3 mt-4">
          {[
            { label: 'تاريخ التسليم', val: asset.delivery_date ? fmtDate(asset.delivery_date) : '—', color: 'var(--blue)' },
            { label: 'عدد الحركات',   val: asset.movements?.length || 0,      color: 'var(--teal)' },
            { label: 'أوامر الخدمة',  val: asset.service_history?.length || 0, color: 'var(--amber)' },
            { label: 'تكلفة الصيانة',  val: egp(asset.maintenance_cost_total || 0), color: 'var(--red)' },
          ].map(k => (
            <div key={k.label} className="text-center">
              <div className="text-sm font-bold" style={{ color: k.color }}>{k.val}</div>
              <div className="text-[10px] text-erp-muted">{k.label}</div>
            </div>
          ))}
        </div>

        {asset.source_work_order && (
          <div className="flex justify-between items-center text-xs mt-3 pt-3 border-t border-erp-border">
            <span className="text-erp-muted">تكلفة التصنيع (أمر الإنتاج {asset.source_work_order.code})</span>
            <span className="font-semibold" style={{ color: 'var(--teal)' }}>{egp(asset.source_work_order.actual_cost || asset.source_work_order.planned_cost || 0)}</span>
          </div>
        )}

        {asset.notes && <div className="text-xs text-erp-muted mt-3 pt-3 border-t border-erp-border">{asset.notes}</div>}
      </div>

      {/* Movements Timeline */}
      <div className="flex justify-between items-center">
        <div className="text-sm font-semibold">سجل حركة المعدة</div>
        {write && <button className="btn btn-primary btn-sm" onClick={() => setMoveModal(true)}>+ تسجيل حركة</button>}
      </div>

      {!asset.movements?.length
        ? <div className="card text-center text-erp-muted py-8 text-sm">لا توجد حركات مسجلة</div>
        : (
          <div className="flex flex-col gap-2">
            {asset.movements.map(m => (
              <div key={m.id} className="card py-2 px-3 flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  <span className={`badge ${MOVEMENT_TYPE_BADGE[m.event_type] || 'badge-gray'} text-[11px]`}>
                    {m.event_type_ar}
                  </span>
                  <span className="text-xs">
                    {m.from_location_name || m.to_location_name
                      ? `${m.from_location_name || '—'} ← ${m.to_location_name || '—'}`
                      : (m.notes || '')}
                  </span>
                </div>
                <div className="text-[11px] text-erp-muted">{fmtDate(m.event_date)}</div>
              </div>
            ))}
          </div>
        )
      }

      {/* Service History */}
      <div className="text-sm font-semibold mt-2">سجل أوامر الخدمة الميدانية</div>
      {!asset.service_history?.length
        ? <div className="card text-center text-erp-muted py-8 text-sm">لا توجد أوامر خدمة مرتبطة بهذه المعدة</div>
        : (
          <div className="card p-0 overflow-hidden">
            <table className="erp-table">
              <thead>
                <tr><th>الكود</th><th>العنوان</th><th>النوع</th><th>الحالة</th><th>تاريخ الإتمام</th></tr>
              </thead>
              <tbody>
                {asset.service_history.map(s => (
                  <tr key={s.id}>
                    <td className="font-mono text-xs">{s.code}</td>
                    <td>{s.title}</td>
                    <td className="text-xs">{s.order_type_ar}</td>
                    <td><span className={`badge ${STATUS_BADGE[s.status] || 'badge-gray'} text-[11px]`}>{s.status_ar}</span></td>
                    <td className="text-xs">{s.completion_date ? fmtDate(s.completion_date) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      }

      {/* Movement Modal */}
      <Modal open={moveModal} onClose={() => setMoveModal(false)} title="تسجيل حركة للمعدة" size="sm">
        <div className="flex flex-col gap-3">
          <Select label="نوع الحركة *" value={form.event_type}
            onChange={e => setForm(f => ({ ...f, event_type: e.target.value }))}>
            {MOVEMENT_TYPES.map(t => <option key={t} value={t}>{MOVEMENT_TYPE_AR[t]}</option>)}
          </Select>
          {['installed', 'relocated'].includes(form.event_type) && (
            <Select label="الموقع الجديد *" value={form.to_location_id || ''}
              onChange={e => setForm(f => ({ ...f, to_location_id: e.target.value }))}>
              <option value="">— اختر —</option>
              {locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            </Select>
          )}
          <Input label="تاريخ الحركة *" type="date" value={form.event_date}
            onChange={e => setForm(f => ({ ...f, event_date: e.target.value }))} />
          <Textarea label="ملاحظات" rows={2} value={form.notes || ''}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setMoveModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={saveMove} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// ASSETS VIEW — معدات العملاء
// ══════════════════════════════════════════════════════════════
function AssetsView({ write, customers }) {
  const toast = useToast()
  const { data: assets, loading, reload } = useApi(() => api.fieldService.assets())
  const [view, setView] = useState('list')   // 'list' | 'profile'
  const [activeAsset, setActiveAsset] = useState(null)
  const [q, setQ] = useState('')
  const [customerFilter, setCustomerFilter] = useState('')
  const [modal, setModal] = useState(false)
  const [editTarget, setEditTarget] = useState(null)
  const [form, setForm] = useState({})
  const [delTarget, setDel] = useState(null)

  const [locations, setLocations] = useState([])
  useEffect(() => {
    if (!form.customer_id) { setLocations([]); return }
    api.customers.locations(form.customer_id).then(r => setLocations(Array.isArray(r) ? r : [])).catch(bgErr('عناوين العميل', () => setLocations([])))
  }, [form.customer_id])

  const filtered = useMemo(() => {
    if (!assets) return []
    const lq = q.toLowerCase()
    let res = !lq ? assets : assets.filter(a =>
      a.name?.toLowerCase().includes(lq) ||
      a.serial_number?.toLowerCase().includes(lq) ||
      a.model?.toLowerCase().includes(lq) ||
      a.customer_name?.toLowerCase().includes(lq)
    )
    if (customerFilter) res = res.filter(a => a.customer_id === Number(customerFilter))
    return res
  }, [assets, q, customerFilter])

  const kpis = useMemo(() => ({
    total:      (assets || []).length,
    inWarranty: (assets || []).filter(a => a.in_warranty).length,
    active:     (assets || []).filter(a => a.status === 'active').length,
  }), [assets])

  const openAdd = () => {
    setEditTarget(null)
    setForm({ status: 'active' })
    setModal(true)
  }
  const openEdit = (a) => {
    setEditTarget(a)
    setForm({ ...a })
    setModal(true)
  }

  const [save, saving] = useAction(async () => {
    if (!String(form.name || '').trim() || !form.customer_id) { toast('الاسم والعميل مطلوبان', 'error'); return }
    if (form.delivery_date && form.warranty_end && form.warranty_end < form.delivery_date) {
      toast('نهاية الضمان يجب أن تكون بعد تاريخ التسليم', 'error'); return
    }
    const payload = {
      customer_id: Number(form.customer_id),
      equipment_id: form.equipment_id ? Number(form.equipment_id) : null,
      name: String(form.name).trim(),
      model: form.model || null,
      serial_number: form.serial_number || null,
      delivery_date: form.delivery_date || null,
      warranty_end: form.warranty_end || null,
      location: form.location || null,
      notes: form.notes || null,
    }
    if (editTarget) await api.fieldService.updateAsset(editTarget.id, payload)
    else await api.fieldService.createAsset(payload)
    toast('✅ تم الحفظ')
    setModal(false)
    reload()
  })

  const [del, deleting] = useAction(async () => {
    await api.fieldService.deleteAsset(delTarget.id)
    toast('تم الحذف'); setDel(null); reload()
  })

  if (loading) return <PageSpinner />

  if (view === 'profile' && activeAsset) {
    return (
      <AssetProfile
        assetId={activeAsset}
        write={write}
        onBack={() => { setView('list'); setActiveAsset(null) }}
        onReload={reload}
      />
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* KPI row */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label:'إجمالي المعدات',   val: kpis.total,      color:'var(--blue)'  },
          { label:'تحت الضمان',       val: kpis.inWarranty, color:'var(--green)' },
          { label:'نشطة',             val: kpis.active,     color:'var(--teal)'  },
        ].map(k => (
          <div key={k.label} className="card text-center py-3">
            <div className="text-xl font-bold" style={{ color: k.color }}>{k.val}</div>
            <div className="text-xs text-erp-muted mt-1">{k.label}</div>
          </div>
        ))}
      </div>

      {/* Toolbar */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div className="flex-1 min-w-40">
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالاسم أو الرقم التسلسلي أو العميل…" />
        </div>
        <select className="field-select text-sm" style={{ width:'auto' }}
          value={customerFilter} onChange={e => setCustomerFilter(e.target.value)}>
          <option value="">كل العملاء</option>
          {(customers || []).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {write && <button className="btn btn-primary btn-sm" onClick={openAdd}>+ معدة جديدة</button>}
      </div>

      {/* Table */}
      {!filtered.length
        ? (
          <div className="card text-center py-16 text-erp-muted">
            <div className="text-4xl mb-3">⚙️</div>
            <div className="text-sm font-medium mb-1">لا توجد معدات</div>
            <div className="text-xs">اضغط &quot;+ معدة جديدة&quot; للبدء</div>
          </div>
        )
        : (
          <div className="card p-0 overflow-hidden">
            <table className="erp-table">
              <thead>
                <tr>
                  <th>المعدة</th><th>العميل</th><th>الموديل / SN</th>
                  <th>الموقع الحالي</th><th>الضمان</th><th>الحالة</th>
                  {write && <th />}
                </tr>
              </thead>
              <tbody>
                {filtered.map(a => (
                  <tr key={a.id} className="cursor-pointer hover:bg-erp-secondary"
                    onClick={() => { setActiveAsset(a.id); setView('profile') }}>
                    <td className="font-medium">{a.name}</td>
                    <td className="text-xs text-erp-muted">{a.customer_name}</td>
                    <td className="text-xs">{a.model || '—'}{a.serial_number ? ` / ${a.serial_number}` : ''}</td>
                    <td className="text-xs">{a.current_location_name || a.location || '—'}</td>
                    <td>
                      {a.warranty_end
                        ? <span className={`badge ${a.in_warranty ? 'badge-green' : 'badge-gray'} text-[11px]`}>
                            {a.in_warranty ? 'تحت الضمان' : 'منتهي'}
                          </span>
                        : <span className="text-erp-muted text-xs">—</span>
                      }
                    </td>
                    <td><span className={`badge ${ASSET_STATUS_BADGE[a.status] || 'badge-gray'} text-[11px]`}>{ASSET_STATUS_AR[a.status] || a.status}</span></td>
                    {write && (
                      <td onClick={e => e.stopPropagation()}>
                        <div className="flex gap-1">
                          <button className="btn btn-sm text-[11px]" onClick={() => openEdit(a)}>✏️</button>
                          <button className="btn btn-sm btn-danger text-xs" onClick={() => setDel(a)}>🗑</button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      }

      {/* Create/Edit Modal */}
      <Modal open={modal} onClose={() => setModal(false)} title={editTarget ? 'تعديل معدة' : 'معدة جديدة'}>
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Select label="العميل *" value={form.customer_id || ''} onChange={e => setForm(f => ({ ...f, customer_id: +e.target.value }))}>
              <option value="">اختر عميل…</option>
              {(customers || []).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
            <Input label="اسم المعدة *" value={form.name || ''} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="الموديل" value={form.model || ''} onChange={e => setForm(f => ({ ...f, model: e.target.value }))} />
            <Input label="الرقم التسلسلي" value={form.serial_number || ''} onChange={e => setForm(f => ({ ...f, serial_number: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="تاريخ التسليم" type="date" value={form.delivery_date || ''} onChange={e => setForm(f => ({ ...f, delivery_date: e.target.value }))} />
            <Input label="نهاية الضمان" type="date" value={form.warranty_end || ''} onChange={e => setForm(f => ({ ...f, warranty_end: e.target.value }))} />
          </FormGrid>
          <Input label="الموقع (نصي)" value={form.location || ''} onChange={e => setForm(f => ({ ...f, location: e.target.value }))} placeholder="مثال: المصنع الرئيسي" />
          {editTarget?.current_location_name && (
            <div className="text-xs text-erp-muted">الموقع البنيوي الحالي: {editTarget.current_location_name} (يُعدَّل من بروفايل المعدة بتسجيل حركة)</div>
          )}
          <Textarea label="ملاحظات" rows={2} value={form.notes || ''} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!delTarget}
        title="حذف المعدة"
        message={`هل تريد حذف "${delTarget?.name}"؟`}
        confirmLabel="حذف"
        confirmVariant="danger"
        onConfirm={del}
        onCancel={() => setDel(null)}
        loading={deleting}
      />
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ══════════════════════════════════════════════════════════════
export default function FieldService() {
  const { canWrite } = useAuth()
  const write = canWrite('field_service')
  const toast = useToast()

  const { data: orders, loading, reload } = useApi(() => api.fieldService.orders())
  const { data: customers } = useApi(() => api.customers.list())

  const [mainTab, setMainTab] = useState('projects')  // 'projects' | 'assets'

  const [view,       setView]       = useState('list')  // 'list' | 'workspace' | 'create'
  const [activeOrder, setActiveOrder] = useState(null)
  const [q,          setQ]          = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [createModal, setCreateModal] = useState(false)
  const [form,       setForm]       = useState({})
  const [delTarget,  setDel]        = useState(null)

  // مواقع العميل المختار
  const [locations, setLocations] = useState([])
  useEffect(() => {
    if (!form.customer_id) { setLocations([]); return }
    api.customers.locations(form.customer_id).then(r => setLocations(Array.isArray(r) ? r : [])).catch(bgErr('عناوين العميل', () => setLocations([])))
  }, [form.customer_id])

  const filtered = useMemo(() => {
    if (!orders) return []
    const lq = q.toLowerCase()
    let res = !lq ? orders : orders.filter(o =>
      o.code?.toLowerCase().includes(lq) ||
      o.title?.toLowerCase().includes(lq) ||
      o.customer_name?.toLowerCase().includes(lq)
    )
    if (statusFilter) res = res.filter(o => o.status === statusFilter)
    return res
  }, [orders, q, statusFilter])

  const kpis = useMemo(() => ({
    total:       (orders||[]).length,
    in_progress: (orders||[]).filter(o => o.status === 'in_progress').length,
    completed:   (orders||[]).filter(o => o.status === 'completed').length,
    totalCost:   (orders||[]).reduce((s, o) => s + Number(o.total_cost||0), 0),
  }), [orders])

  const openAdd = () => {
    setForm({ order_type:'maintenance', priority:'normal', planned_date: localToday(), status:'pending' })
    setCreateModal(true)
  }

  const [save, saving] = useAction(async () => {
    if (!form.title || !form.customer_id || !form.code) { toast('كود وعنوان والعميل مطلوبان', 'error'); return }
    const err = validateOrderForm(form)
    if (err) { toast(err, 'error'); return }
    await api.fieldService.createOrder(buildOrderPayload(form))
    toast('✅ تم إنشاء المشروع')
    setCreateModal(false)
    reload()
  })

  const [del, deleting] = useAction(async () => {
    await api.fieldService.deleteOrder(delTarget.id)
    toast('تم الحذف'); setDel(null); reload()
  })

  const [quickStatus] = useAction(async (id, status) => {
    await api.fieldService.updateOrderStatus(id, { status })
    toast('تم تحديث الحالة'); reload()
  })

  if (loading) return <PageSpinner />

  // ── Workspace view ───────────────────────────────────────────
  if (view === 'workspace' && activeOrder) {
    return (
      <ProjectWorkspace
        orderId={activeOrder}
        write={write}
        customers={customers}
        onBack={() => { setView('list'); setActiveOrder(null) }}
        onReload={reload}
      />
    )
  }

  // ── Main tabs ────────────────────────────────────────────────
  const mainTabs = [
    { id: 'projects', label: 'المشاريع الخارجية' },
    { id: 'assets',   label: 'معدات العملاء' },
  ]

  const tabsBar = (
    <div className="flex gap-1 border-b border-erp-border pb-0">
      {mainTabs.map(t => (
        <button key={t.id}
          className="px-4 py-2 text-sm font-medium border-b-2 transition-colors"
          style={{ marginBottom: '-1px', ...(mainTab === t.id ? { borderColor:'var(--teal)', color:'var(--teal)' } : { borderColor:'transparent', color:'var(--text-muted)' }) }}
          onClick={() => setMainTab(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  )

  if (mainTab === 'assets') {
    return (
      <div className="flex flex-col gap-4">
        {tabsBar}
        <AssetsView write={write} customers={customers} />
      </div>
    )
  }

  // ── List view ────────────────────────────────────────────────
  return (
    <div className="flex flex-col gap-4">
      {tabsBar}

      {/* KPI row */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label:'إجمالي المشاريع',  val: kpis.total,                  color:'var(--blue)'  },
          { label:'جاري التنفيذ',     val: kpis.in_progress,            color:'var(--amber)' },
          { label:'مكتملة',           val: kpis.completed,              color:'var(--green)' },
          { label:'إجمالي التكاليف',  val: egp(kpis.totalCost),         color:'var(--teal)'  },
        ].map(k => (
          <div key={k.label} className="card text-center py-3">
            <div className="text-xl font-bold" style={{ color: k.color }}>{k.val}</div>
            <div className="text-xs text-erp-muted mt-1">{k.label}</div>
          </div>
        ))}
      </div>

      {/* Toolbar */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div className="flex-1 min-w-40">
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالكود أو العنوان أو العميل…" />
        </div>
        <select className="field-select text-sm" style={{ width:'auto' }}
          value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
          <option value="">كل الحالات</option>
          {Object.entries(STATUS_AR).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        {write && (
          <button className="btn btn-primary btn-sm" onClick={openAdd}>+ مشروع جديد</button>
        )}
      </div>

      {/* Project Cards Grid */}
      {!filtered.length
        ? (
          <div className="card text-center py-16 text-erp-muted">
            <div className="text-4xl mb-3">🔧</div>
            <div className="text-sm font-medium mb-1">لا توجد مشاريع</div>
            <div className="text-xs">اضغط &quot;+ مشروع جديد&quot; للبدء</div>
          </div>
        )
        : (
          <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
            {filtered.map(o => (
              <ProjectCard
                key={o.id}
                order={o}
                write={write}
                onClick={() => { setActiveOrder(o.id); setView('workspace') }}
                onQuickStatus={quickStatus}
                onDelete={write ? setDel : undefined}
              />
            ))}
          </div>
        )
      }

      {/* Create Modal */}
      <Modal open={createModal} onClose={() => setCreateModal(false)} title="مشروع خارجي جديد">
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="الكود *" value={form.code || ''} onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="FS-001" />
            <Input label="العنوان *" value={form.title || ''} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Select label="العميل *" value={form.customer_id || ''} onChange={e => setForm(f => ({ ...f, customer_id: +e.target.value, location_id: '' }))}>
              <option value="">اختر عميل…</option>
              {(customers || []).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
            <Select label="نوع الخدمة" value={form.order_type || 'maintenance'} onChange={e => setForm(f => ({ ...f, order_type: e.target.value }))}>
              {Object.entries(ORDER_TYPE_AR).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </FormGrid>
          {locations.length > 0 && (
            <FormGrid cols={2}>
              <Select label="الموقع / الفرع" value={form.location_id || ''} onChange={e => setForm(f => ({ ...f, location_id: e.target.value }))}>
                <option value="">— بدون موقع محدد —</option>
                {locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
              </Select>
            </FormGrid>
          )}
          <FormGrid cols={2}>
            <Select label="الأولوية" value={form.priority || 'normal'} onChange={e => setForm(f => ({ ...f, priority: e.target.value }))}>
              {Object.entries(PRIORITY_AR).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
            <Input label="تاريخ مخطط" type="date" value={form.planned_date || ''} onChange={e => setForm(f => ({ ...f, planned_date: e.target.value }))} />
          </FormGrid>
          <Textarea label="الوصف" value={form.description || ''} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} rows={2} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setCreateModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إنشاء'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!delTarget}
        title="حذف المشروع"
        message={`هل تريد حذف "${delTarget?.title}"؟`}
        confirmLabel="حذف"
        confirmVariant="danger"
        onConfirm={del}
        onCancel={() => setDel(null)}
        loading={deleting}
      />
    </div>
  )
}
