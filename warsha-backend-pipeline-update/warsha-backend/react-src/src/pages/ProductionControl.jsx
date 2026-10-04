/**
 * ProductionControl — متابعة وضبط الإنتاج
 *
 * الجداول:
 * 1. جدولة الإنتاج  — إنشاء جدول / بدء / إنهاء
 * 2. مراحل التنفيذ  — كل أوامر الإنتاج النشطة مع مراحلها (لا dropdown واحد فقط)
 * 3. ضبط الجودة     — فحوصات مرتبطة بأوامر الإنتاج
 */
import { useState, useCallback } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useToast } from '../store/toast'
import api from '../api/client'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'
import { fmtDate, today } from '../utils/fmt'
import CuttingPlan from './CuttingPlan'

// ── Constants ──────────────────────────────────────────────────────────────────
const TABS = [
  { id: 'schedule', label: 'جدولة الإنتاج' },
  { id: 'stages',   label: 'مراحل التنفيذ' },
  { id: 'qc',       label: 'ضبط الجودة'    },
]

const STAGE_STATUS_AR = {
  pending:     'معلق',
  in_progress: 'جاري',
  done:        'منتهي',
  skipped:     'تخطّي',
}
const STAGE_COLOR = {
  pending:     'var(--text-secondary)',
  in_progress: 'var(--teal)',
  done:        '#15803d',
  skipped:     '#6b7280',
}

const SCH_STATUS_AR = {
  planned:    'مخطط',
  in_progress:'جاري',
  completed:  'منتهي',
  delayed:    'متأخر',
  cancelled:  'ملغي',
}
const SCH_COLOR = {
  planned:    'var(--text-secondary)',
  in_progress:'var(--teal)',
  completed:  '#15803d',
  delayed:    'var(--red)',
  cancelled:  '#6b7280',
}

const QC_COLOR = { pass: '#15803d', fail: 'var(--red)', conditional: '#b45309' }

const ACTIVE_WO_STATUSES = new Set(['draft', 'in_progress', 'in_production'])

// ── Stages card for a single work order ────────────────────────────────────────
function WOStagesCard({ wo }) {
  const toast = useToast()
  const [expanded, setExpanded]     = useState(false)
  const [stages, setStages]         = useState(null)
  const [loading, setLoading]       = useState(false)
  const [acting, setActing]         = useState(false)
  const [cuttingStageId, setCuttingStageId] = useState(null)
  const [bomResult, setBomResult] = useState(null)

  // ── Team (عمالة أمر الإنتاج) ──────────────────────────────────
  const [team, setTeam]             = useState(null)
  const [workers, setWorkers]       = useState(null)
  const [teamForm, setTeamForm]     = useState({ worker_id: '', role: 'فني', hourly_rate: '', planned_hours: '' })
  const [teamBusy, setTeamBusy]     = useState(false)

  const loadTeam = useCallback(async () => {
    try {
      const [t, w] = await Promise.all([
        api.production.team.list(wo.id),
        workers || api.workers.list(),
      ])
      setTeam(t)
      if (!workers) setWorkers(w)
    } catch (e) {
      toast(e?.message || 'خطأ في تحميل الفريق', 'error')
    }
  }, [wo.id, toast, workers])

  const loadStages = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api.stages.list(wo.id)
      setStages(d)
    } catch (e) {
      toast(e?.message || 'خطأ في تحميل المراحل', 'error')
    } finally {
      setLoading(false)
    }
  }, [wo.id, toast])

  const toggle = () => {
    if (!expanded && stages === null) { loadStages(); loadTeam() }
    setExpanded(v => !v)
  }

  const handleAddMember = async () => {
    if (teamBusy) return
    if (!teamForm.worker_id) { toast('اختر عامل', 'error'); return }
    // الباك-إند يقبل Decimal بدون قيد — نمنع القيم السالبة من الواجهة حتى لا تُحسب تكلفة عمالة سالبة
    if (Number(teamForm.hourly_rate) < 0 || Number(teamForm.planned_hours) < 0) {
      toast('أجر الساعة والساعات المخططة لا يمكن أن تكون سالبة', 'error'); return
    }
    setTeamBusy(true)
    try {
      await api.production.team.add(wo.id, {
        worker_id: Number(teamForm.worker_id),
        role: teamForm.role || 'فني',
        hourly_rate: Number(teamForm.hourly_rate) || 0,
        planned_hours: Number(teamForm.planned_hours) || 0,
      })
      toast('✅ تمت إضافة العامل للفريق')
      setTeamForm({ worker_id: '', role: 'فني', hourly_rate: '', planned_hours: '' })
      loadTeam()
    } catch (e) {
      toast(e?.message || 'حدث خطأ', 'error')
    } finally {
      setTeamBusy(false)
    }
  }

  const handleUpdateHours = async (mid, hours, prev) => {
    // حقل فاضي كان بيبعت actual_hours= → خطأ 422 خام من الخادم؛ وقيمة سالبة كانت بتتقبل
    if (hours === '' || hours == null) return
    const h = Number(hours)
    if (!Number.isFinite(h) || h < 0) { toast('ساعات العمل الفعلية يجب أن تكون رقماً غير سالب', 'error'); return }
    if (prev != null && Number(prev) === h) return
    try {
      await api.production.team.hours(wo.id, mid, hours)
      loadTeam()
    } catch (e) {
      toast(e?.message || 'حدث خطأ', 'error')
    }
  }

  const handleRemoveMember = async (mid, name) => {
    if (!window.confirm(`حذف ${name || 'العامل'} من فريق العمل؟`)) return
    try {
      await api.production.team.remove(wo.id, mid)
      toast('تم حذف العامل من الفريق')
      loadTeam()
    } catch (e) {
      toast(e?.message || 'حدث خطأ', 'error')
    }
  }

  const act = async (fn) => {
    if (acting) return
    setActing(true)
    try { await fn() } catch (e) { toast(e?.message || 'حدث خطأ', 'error') } finally { setActing(false) }
  }

  const handleSeed = () => act(async () => {
    await api.stages.seed(wo.id)
    toast('✅ تم إنشاء المراحل الافتراضية')
    loadStages()
  })

  const handleApplyParametricBOM = () => act(async () => {
    const res = await api.accounting.applyParametricBOM(wo.id)
    setBomResult(res)
    toast('✅ تم تطبيق BOM المتري حسب الطول المخطط')
  })

  const handleStart = (id) => act(async () => {
    await api.stages.start(id)
    toast('✅ بدأت المرحلة')
    loadStages()
  })

  const handleComplete = (id) => act(async () => {
    await api.stages.complete(id)
    toast('✅ انتهت المرحلة')
    loadStages()
  })

  const handleSkip = (id, name) => act(async () => {
    if (!window.confirm(`تخطّي مرحلة "${name || ''}"؟`)) return
    await api.stages.skip(id)
    toast('تم تخطّي المرحلة')
    loadStages()
  })

  const pct = stages?.completion_pct ?? 0
  const stageList = stages?.stages ?? []

  return (
    <div className="card p-0 overflow-hidden">
      {/* WO header row */}
      <button
        className="w-full text-right"
        style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12, background: 'var(--surface)', border: 'none', cursor: 'pointer' }}
        onClick={toggle}
      >
        <span style={{ fontSize: 18 }}>{expanded ? '▼' : '▶'}</span>
        <span className="font-mono font-semibold text-sm">{wo.code}</span>
        <span className="text-sm text-erp-muted flex-1">
          {wo.equipment_name || '—'}
        </span>
        {stages !== null && (
          <span className="text-xs px-2 py-1 rounded"
                style={{ background: pct === 100 ? '#dcfce7' : 'var(--teal-lt)', color: pct === 100 ? '#15803d' : 'var(--teal)' }}>
            {pct}% مكتمل
          </span>
        )}
        <span className={`badge ${wo.status === 'in_progress' || wo.status === 'in_production' ? 'badge-blue' : 'badge-amber'}`}>
          {wo.status === 'in_production' ? 'في الإنتاج' : wo.status === 'in_progress' ? 'جاري التنفيذ' : 'مسودة'}
        </span>
      </button>

      {wo.planned_length ? (
        <div style={{ padding: '8px 16px', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="text-xs text-erp-muted">طول التصنيع المخطط: {wo.planned_length} م</span>
          <button className="btn btn-sm text-xs" style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}
            disabled={acting}
            onClick={(e) => { e.stopPropagation(); handleApplyParametricBOM() }}>
            {acting ? '…' : '⚙️ تطبيق BOM المتري حسب الطول'}
          </button>
        </div>
      ) : null}

      {bomResult?.materials_summary?.length > 0 && (
        <div style={{ padding: '0 16px 12px', borderTop: '1px solid var(--border)' }}>
          <div className="text-xs font-semibold py-2">ملخص المواد المطلوبة لهذا الأمر</div>
          <table className="erp-table" style={{ fontSize: 12 }}>
            <thead>
              <tr><th>المادة</th><th>الكمية</th><th>من النسالة</th></tr>
            </thead>
            <tbody>
              {bomResult.materials_summary.map(m => (
                <tr key={m.material_id}>
                  <td>{m.material_name}</td>
                  <td className="font-semibold">
                    {m.oversized_warning ? (
                      <span style={{ color: '#B91C1C' }} title={m.oversized_warning}>⚠️ {m.count} {m.unit_label}</span>
                    ) : m.precise ? (
                      <>
                        {m.precise.precise_value} {m.precise.precise_unit}
                        <span className="text-erp-muted font-normal"> (من {m.unit_label} {m.precise.unit_size} {m.precise.precise_unit} — الباقي {m.precise.remainder} {m.precise.precise_unit} نسالة)</span>
                      </>
                    ) : m.whole_count != null ? (
                      <>
                        {m.whole_count} {m.unit_label} كامل
                        {m.fraction_pct > 0 && (
                          <span className="text-erp-muted font-normal"> + {m.fraction_pct}% من {m.unit_label}</span>
                        )}
                      </>
                    ) : (
                      <>{m.count} {m.unit_label}</>
                    )}
                  </td>
                  <td className="text-erp-muted">{m.remnant_covered ? `${m.remnant_covered} مغطّى من نسالة` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Expanded stages */}
      {expanded && (
        <div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border)' }}>
          {loading && <div className="py-4 text-center text-erp-muted text-sm">⏳ جاري التحميل…</div>}

          {!loading && stages !== null && (
            <>
              {/* Progress bar */}
              <div style={{ background: 'var(--border)', borderRadius: 6, height: 6, margin: '12px 0 16px' }}>
                <div style={{ background: 'var(--teal)', height: '100%', borderRadius: 6, width: `${pct}%`, transition: 'width .3s' }} />
              </div>

              {stageList.length === 0 ? (
                <div className="flex gap-3 items-center py-2">
                  <span className="text-sm text-erp-muted">لا توجد مراحل بعد.</span>
                  <button className="btn btn-sm" onClick={handleSeed} disabled={acting}>
                    {acting ? '…' : '+ إنشاء المراحل الافتراضية'}
                  </button>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  {stageList.map((s, i) => {
                    const isCutting = (s.stage_name || '').includes('قطع')
                    // الباك-إند يمنع بدء مرحلة قبل إنهاء/تخطّي كل ما يسبقها بالترتيب (400 error).
                    // بدل ما نسيب المستخدم يضغط "بدء" ويشوف رسالة خطأ بعد استدعاء الشبكة،
                    // نعطّل الزر مقدماً ونوضّح السبب.
                    const canStart = stageList.slice(0, i).every(st => st.status === 'done' || st.status === 'skipped')
                    return (
                    <div key={s.id} className="flex flex-col gap-2">
                    <div
                         style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
                                  background: 'var(--bg)', border: '1px solid var(--border)',
                                  borderRadius: 8, borderRight: `4px solid ${STAGE_COLOR[s.status] || '#ccc'}` }}>
                      {/* Circle */}
                      <div style={{ width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
                                    background: s.status === 'done' ? '#15803d' : s.status === 'in_progress' ? 'var(--teal)' : 'var(--border)',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                    color: '#fff', fontSize: 12, fontWeight: 700 }}>
                        {s.status === 'done' ? '✓' : i + 1}
                      </div>
                      <div style={{ flex: 1 }}>
                        <div className="font-medium text-sm">{s.stage_name}</div>
                        <div className="text-xs text-erp-muted">
                          {STAGE_STATUS_AR[s.status] || s.status}
                          {s.actual_hours  ? ` — ${s.actual_hours} ساعة فعلية`   : ''}
                          {s.planned_hours && !s.actual_hours ? ` — ${s.planned_hours} ساعة مخططة` : ''}
                        </div>
                      </div>
                      <div className="flex gap-1">
                        {isCutting && (
                          <button className="btn btn-sm text-xs"
                            style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}
                            onClick={() => setCuttingStageId(v => v === s.id ? null : s.id)}>
                            🧩 مخطط التقطيع {cuttingStageId === s.id ? '▲' : '▼'}
                          </button>
                        )}
                        {s.status === 'pending' && (
                          <button className="btn btn-sm text-xs"
                            style={canStart
                              ? { background: 'var(--teal-lt)', color: 'var(--teal)' }
                              : { background: 'var(--border)', color: 'var(--text-secondary)', cursor: 'not-allowed' }}
                            onClick={() => handleStart(s.id)} disabled={acting || !canStart}
                            title={canStart ? '' : 'أنهِ أو تخطَّ المراحل السابقة أولاً'}>▶ بدء</button>
                        )}
                        {s.status === 'in_progress' && (
                          <button className="btn btn-sm text-xs"
                            style={{ background: 'var(--green-lt)', color: 'var(--green)' }}
                            onClick={() => handleComplete(s.id)} disabled={acting}>✓ إنهاء</button>
                        )}
                        {(s.status === 'pending' || s.status === 'in_progress') && (
                          <button className="btn btn-sm text-xs"
                            style={{ color: 'var(--text-secondary)' }}
                            onClick={() => handleSkip(s.id, s.stage_name)} disabled={acting}>تخطّي</button>
                        )}
                      </div>
                    </div>
                    {isCutting && cuttingStageId === s.id && (
                      <div style={{ paddingRight: 14 }}>
                        <CuttingPlan equipmentId={wo.equipment_id} plannedLength={wo.planned_length} />
                      </div>
                    )}
                    </div>
                    )
                  })}
                </div>
              )}
            </>
          )}

          {/* فريق العمل (العمالة) */}
          <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
            <div className="font-medium text-sm" style={{ marginBottom: 8 }}>👷 فريق العمل</div>

            {team && team.length > 0 && (
              <div className="flex flex-col gap-2" style={{ marginBottom: 12 }}>
                {team.map(m => (
                  <div key={m.id}
                       style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
                                background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8 }}>
                    <div style={{ flex: 1 }}>
                      <div className="font-medium text-sm">{m.worker_name}</div>
                      <div className="text-xs text-erp-muted">
                        {m.role} — {m.hourly_rate} ج/ساعة — مخطط {m.planned_hours} ساعة — تكلفة فعلية {Number(m.labor_cost || 0).toFixed(2)} ج
                      </div>
                    </div>
                    <input type="number" className="input input-sm" style={{ width: 90 }}
                           min="0" step="0.5"
                           defaultValue={m.actual_hours}
                           onBlur={e => handleUpdateHours(m.id, e.target.value, m.actual_hours)}
                           title="ساعات العمل الفعلية" />
                    <button className="btn btn-sm text-xs" style={{ color: 'var(--red)' }}
                            onClick={() => handleRemoveMember(m.id, m.worker_name)}>حذف</button>
                  </div>
                ))}
              </div>
            )}
            {team && team.length === 0 && (
              <div className="text-sm text-erp-muted" style={{ marginBottom: 12 }}>لا يوجد عمال مسجلين على هذا الأمر.</div>
            )}

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <Select label="العامل" value={teamForm.worker_id}
                      onChange={e => setTeamForm(f => ({ ...f, worker_id: e.target.value }))}>
                <option value="">— اختر —</option>
                {workers?.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
              </Select>
              <Input label="الدور" value={teamForm.role}
                     onChange={e => setTeamForm(f => ({ ...f, role: e.target.value }))} />
              <Input label="أجر الساعة" type="number" min="0" value={teamForm.hourly_rate}
                     onChange={e => setTeamForm(f => ({ ...f, hourly_rate: e.target.value }))} />
              <Input label="ساعات مخططة" type="number" min="0" value={teamForm.planned_hours}
                     onChange={e => setTeamForm(f => ({ ...f, planned_hours: e.target.value }))} />
              <button className="btn btn-sm" onClick={handleAddMember} disabled={teamBusy}>{teamBusy ? '…' : '+ إضافة للفريق'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────
export default function ProductionControl() {
  const [tab, setTab]         = useState('schedule')
  const toast                 = useToast()
  const [acting, setActing]   = useState(false)
  const act = async (fn) => {
    if (acting) return
    setActing(true)
    try { await fn() } catch (e) { toast(e?.message || 'حدث خطأ', 'error') } finally { setActing(false) }
  }

  // ── Work orders (shared across tabs) ──────────────────────────────────────
  const { data: allWOs, loading: wosLoading, reload: reloadWOs } = useApi(() => api.accounting.workOrders())

  // ── Schedule tab ──────────────────────────────────────────────────────────
  const { data: schedules, loading: schLoading, reload: reloadSch } = useApi(
    () => api.schedule.list(),
    [tab === 'schedule']
  )
  const [showCreateSch, setShowCreateSch] = useState(false)
  const [schForm, setSchForm] = useState({ work_order_id: '', scheduled_start: '', scheduled_end: '', priority: '5' })

  const handleCreateSchedule = () => act(async () => {
    if (!schForm.work_order_id) { toast('اختر أمر الإنتاج', 'error'); return }
    // الباك-إند يرفض التاريخين لو فاضيين (كلاهما مطلوب) أو لو النهاية قبل البداية —
    // كنا بنسيب الرسالة الخام تظهر من الـ API. تحقق مبكر بدل رسالة validation مربكة.
    if (!schForm.scheduled_start || !schForm.scheduled_end) { toast('حدد تاريخ البدء وتاريخ الانتهاء المخططين', 'error'); return }
    if (schForm.scheduled_end < schForm.scheduled_start) { toast('تاريخ الانتهاء يجب أن يكون بعد تاريخ البدء', 'error'); return }
    const pr = Number(schForm.priority)
    if (!Number.isInteger(pr) || pr < 1 || pr > 10) { toast('الأولوية يجب أن تكون رقماً صحيحاً من 1 إلى 10', 'error'); return }
    await api.schedule.create({
      ...schForm,
      work_order_id: Number(schForm.work_order_id),
      priority:      Number(schForm.priority),
    })
    toast('✅ تمت الجدولة')
    setShowCreateSch(false)
    setSchForm({ work_order_id: '', scheduled_start: '', scheduled_end: '', priority: '5' })
    reloadSch()
  })

  // بدء الجدول يحوّل أمر الإنتاج من draft إلى in_progress في الباك-إند → نعيد تحميل الأوامر أيضاً
  const handleStartSch    = (id) => act(async () => { await api.schedule.start(id);    toast('✅ بدأ الإنتاج');      reloadSch(); reloadWOs() })
  const handleCompleteSch = (id) => act(async () => {
    if (!window.confirm('تأكيد إنهاء هذا الجدول؟')) return
    await api.schedule.complete(id); toast('✅ تم إنهاء الجدول'); reloadSch()
  })

  // ── Stages tab — active WOs ───────────────────────────────────────────────
  const activeWOs = (allWOs || []).filter(wo => ACTIVE_WO_STATUSES.has(wo.status))

  // ── QC tab ────────────────────────────────────────────────────────────────
  const [selectedWO, setSelectedWO] = useState('')
  const { data: qcDash, reload: reloadQCDash } = useApi(() => api.qc.dashboard(), [tab === 'qc'])
  const { data: inspections, loading: qcLoading, reload: reloadQC } = useApi(
    () => api.qc.list(selectedWO ? { work_order_id: selectedWO } : {}),
    [tab === 'qc', selectedWO]
  )
  const [showQC, setShowQC] = useState(false)
  const [qcForm, setQcForm] = useState({
    work_order_id: '', inspection_date: today(), inspection_type: 'final',
    qty_inspected: '', qty_passed: '', qty_failed: '0', qty_rework: '0',
    failure_reason: '', corrective_action: '',
  })

  const handleCreateQC = () => act(async () => {
    // نفس التحقق المطبّق في الباك-إند (production_v2.QCIn.totals_match) — نتحقق منه هنا
    // مبكراً بدل ما نسيب رسالة pydantic خام (أرقام/أسماء حقول إنجليزية) تظهر للمستخدم.
    const qtyInspected = Number(qcForm.qty_inspected)
    const qtyPassed     = Number(qcForm.qty_passed)
    const qtyFailed     = Number(qcForm.qty_failed || 0)
    const qtyRework     = Number(qcForm.qty_rework || 0)
    if (!qcForm.qty_inspected || !(qtyInspected > 0)) { toast('أدخل الكمية المفحوصة', 'error'); return }
    if (qcForm.qty_passed === '' || qcForm.qty_passed == null || qtyPassed < 0) { toast('أدخل الكمية المقبولة', 'error'); return }
    if (!Number.isFinite(qtyFailed) || qtyFailed < 0 || !Number.isFinite(qtyRework) || qtyRework < 0) {
      toast('الكمية المرفوضة وكمية إعادة العمل لا يمكن أن تكون سالبة', 'error'); return
    }
    if (!qcForm.inspection_date) { toast('حدد تاريخ الفحص', 'error'); return }
    if (qtyPassed + qtyFailed + qtyRework > qtyInspected + 0.001) {
      toast('مجموع الكميات (قبول + رفض + إعادة عمل) لا يجب أن يتجاوز الكمية المفحوصة', 'error')
      return
    }
    const r = await api.qc.create({
      ...qcForm,
      work_order_id: qcForm.work_order_id ? Number(qcForm.work_order_id) : null,
      // حقل فاضي ('') كان بيتبعت كما هو → Decimal('') يفشل بخطأ 422 خام
      qty_inspected: qtyInspected,
      qty_passed:    qtyPassed,
      qty_failed:    qtyFailed,
      qty_rework:    qtyRework,
      failure_reason:    qcForm.failure_reason || null,
      corrective_action: qcForm.corrective_action || null,
    })
    const msg = `نتيجة: ${r.result_ar}${r.rework_work_order ? ` — أُنشئ أمر إعادة عمل ${r.rework_work_order.code}` : ''}`
    toast(msg)
    setShowQC(false)
    setQcForm(f => ({ ...f, qty_inspected: '', qty_passed: '', qty_failed: '0', qty_rework: '0', failure_reason: '', corrective_action: '' }))
    reloadQC()
    reloadQCDash()
    // فحص فاشل/مشروط يُنشئ أمر إعادة عمل جديد → حدّث قائمة الأوامر
    if (r.rework_work_order) reloadWOs()
  })

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col gap-4" dir="rtl">
      {/* Page header */}
      <div className="card flex items-center gap-3 py-3">
        <h2 className="font-bold text-lg flex-1">متابعة وضبط الإنتاج</h2>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, borderBottom: '2px solid var(--border)', paddingBottom: 0, overflowX: 'auto' }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            style={{ padding: '8px 20px', border: 'none', borderRadius: '6px 6px 0 0', whiteSpace: 'nowrap',
                     cursor: 'pointer', fontWeight: tab === t.id ? 700 : 400,
                     background: tab === t.id ? 'var(--teal)' : 'var(--surface)',
                     color: tab === t.id ? '#fff' : 'var(--text-secondary)' }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ── SCHEDULE TAB ─────────────────────────────────────────────────────── */}
      {tab === 'schedule' && (
        <div className="flex flex-col gap-3">
          <div className="flex justify-end">
            <button className="btn btn-primary btn-sm" onClick={() => setShowCreateSch(true)}>
              + جدولة أمر إنتاج
            </button>
          </div>
          {schLoading ? <PageSpinner /> : (
            <div className="card p-0 overflow-x-auto">
              <table className="erp-table">
                <thead>
                  <tr>
                    <th>أمر الإنتاج</th>
                    <th>البداية المخططة</th>
                    <th>النهاية المخططة</th>
                    <th>الأولوية</th>
                    <th>الحالة</th>
                    <th>التأخر</th>
                    <th>إجراء</th>
                  </tr>
                </thead>
                <tbody>
                  {!schedules?.length && (
                    <tr><td colSpan={7} className="text-center text-erp-muted py-10">لا توجد جداول</td></tr>
                  )}
                  {schedules?.map(s => (
                    <tr key={s.id}>
                      <td className="font-mono font-semibold text-sm">{s.work_order_code || s.work_order_id}</td>
                      <td className="text-xs text-erp-muted">{fmtDate(s.scheduled_start)}</td>
                      <td className="text-xs text-erp-muted">{fmtDate(s.scheduled_end)}</td>
                      <td>
                        <span style={{ background: 'var(--teal)', color: '#fff', padding: '2px 8px', borderRadius: 10, fontSize: 12 }}>
                          P{s.priority}
                        </span>
                      </td>
                      <td>
                        <span style={{ color: SCH_COLOR[s.status] || 'inherit', fontWeight: 600 }}>
                          {SCH_STATUS_AR[s.status] || s.status}
                        </span>
                      </td>
                      <td>
                        {s.is_delayed
                          ? <span style={{ color: 'var(--red)', fontWeight: 700 }}>⚠ {s.delay_days} يوم</span>
                          : <span className="text-erp-muted">—</span>}
                      </td>
                      <td>
                        <div className="flex gap-1">
                          {s.status === 'planned'     && <button className="btn btn-sm text-xs" style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }} onClick={() => handleStartSch(s.id)}    disabled={acting}>▶ بدء</button>}
                          {s.status === 'in_progress' && <button className="btn btn-sm text-xs" style={{ background: 'var(--green-lt)', color: 'var(--green)' }} onClick={() => handleCompleteSch(s.id)} disabled={acting}>✓ إنهاء</button>}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── STAGES TAB ───────────────────────────────────────────────────────── */}
      {tab === 'stages' && (
        <div className="flex flex-col gap-3">
          {wosLoading ? <PageSpinner /> : (
            <>
              {activeWOs.length === 0 ? (
                <div className="card text-center py-10 text-erp-muted">
                  لا توجد أوامر إنتاج نشطة حالياً
                </div>
              ) : (
                <>
                  <div className="text-sm text-erp-muted px-1">
                    {activeWOs.length} أمر إنتاج نشط — اضغط على أي أمر لعرض مراحله
                  </div>
                  {activeWOs.map(wo => (
                    <WOStagesCard key={wo.id} wo={wo} />
                  ))}
                </>
              )}
            </>
          )}
        </div>
      )}

      {/* ── QC TAB ───────────────────────────────────────────────────────────── */}
      {tab === 'qc' && (
        <div className="flex flex-col gap-3">
          {/* KPI cards */}
          {qcDash && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 12 }}>
              {[
                ['إجمالي الفحوصات', qcDash.total_inspections, 'var(--teal)'],
                ['ناجح',            qcDash.passed,            '#15803d'],
                ['مرفوض',           qcDash.failed,            'var(--red)'],
                ['معدل القبول',     `${qcDash.pass_rate_pct}%`,
                  qcDash.pass_rate_pct >= 90 ? '#15803d' : qcDash.pass_rate_pct >= 70 ? '#b45309' : 'var(--red)'],
              ].map(([lbl, val, color]) => (
                <div key={lbl} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '12px 16px', borderTop: `3px solid ${color}` }}>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{lbl}</div>
                  <div style={{ fontSize: 22, fontWeight: 700, color }}>{val}</div>
                </div>
              ))}
            </div>
          )}

          {/* Toolbar */}
          <div className="flex gap-3 items-center justify-between flex-wrap">
            <div className="flex gap-2 items-center">
              <span className="text-sm font-medium">فلتر:</span>
              <select className="field-select text-sm" style={{ width: 180 }}
                value={selectedWO} onChange={e => setSelectedWO(e.target.value)}>
                <option value="">كل أوامر الإنتاج</option>
                {allWOs?.map(wo => <option key={wo.id} value={wo.id}>{wo.code}</option>)}
              </select>
            </div>
            <button className="btn btn-primary btn-sm" onClick={() => setShowQC(true)}>
              + فحص جودة جديد
            </button>
          </div>

          {/* Inspections table */}
          {qcLoading ? <PageSpinner /> : (
            <div className="card p-0 overflow-x-auto">
              <table className="erp-table text-sm">
                <thead>
                  <tr>
                    <th>التاريخ</th>
                    <th>نوع الفحص</th>
                    <th>مفحوص</th>
                    <th>قبول</th>
                    <th>رفض</th>
                    <th>معدل القبول</th>
                    <th>النتيجة</th>
                  </tr>
                </thead>
                <tbody>
                  {!inspections?.length && (
                    <tr><td colSpan={7} className="text-center text-erp-muted py-10">لا توجد فحوصات</td></tr>
                  )}
                  {inspections?.map(q => (
                    <tr key={q.id}>
                      <td className="text-xs text-erp-muted">{fmtDate(q.inspection_date)}</td>
                      <td>{q.inspection_type_ar}</td>
                      <td>{q.qty_inspected}</td>
                      <td style={{ color: '#15803d', fontWeight: 600 }}>{q.qty_passed}</td>
                      <td style={{ color: q.qty_failed > 0 ? 'var(--red)' : 'inherit' }}>{q.qty_failed}</td>
                      <td>
                        <div style={{ background: 'var(--border)', borderRadius: 4, height: 6, width: 80 }}>
                          <div style={{ background: q.pass_rate >= 90 ? '#15803d' : q.pass_rate >= 70 ? '#b45309' : 'var(--red)', height: '100%', borderRadius: 4, width: `${q.pass_rate}%` }} />
                        </div>
                        <span style={{ fontSize: 11 }}>{q.pass_rate}%</span>
                      </td>
                      <td>
                        <span style={{ color: QC_COLOR[q.result] || 'inherit', fontWeight: 700 }}>
                          {q.result_ar}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Schedule Modal ─────────────────────────────────────────────────────── */}
      <Modal open={showCreateSch} onClose={() => setShowCreateSch(false)} title="جدولة أمر إنتاج">
        <div className="flex flex-col gap-3">
          <Select label="أمر الإنتاج *" value={schForm.work_order_id}
            onChange={e => setSchForm(f => ({ ...f, work_order_id: e.target.value }))}>
            <option value="">— اختر أمر إنتاج —</option>
            {allWOs?.map(wo => <option key={wo.id} value={wo.id}>{wo.code}</option>)}
          </Select>
          <FormGrid cols={2}>
            <Input label="تاريخ البدء *" type="date" value={schForm.scheduled_start}
              onChange={e => setSchForm(f => ({ ...f, scheduled_start: e.target.value }))} />
            <Input label="تاريخ الانتهاء *" type="date" value={schForm.scheduled_end}
              onChange={e => setSchForm(f => ({ ...f, scheduled_end: e.target.value }))} />
          </FormGrid>
          <Input label="الأولوية (1=أعلى)" type="number" min="1" max="10"
            value={schForm.priority}
            onChange={e => setSchForm(f => ({ ...f, priority: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setShowCreateSch(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={handleCreateSchedule} disabled={acting}>
            {acting ? '⏳ جاري…' : '✅ جدولة'}
          </button>
        </ModalFooter>
      </Modal>

      {/* ── QC Modal ──────────────────────────────────────────────────────────── */}
      <Modal open={showQC} onClose={() => setShowQC(false)} title="فحص جودة جديد" size="lg">
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Select label="أمر الإنتاج" value={qcForm.work_order_id}
              onChange={e => setQcForm(f => ({ ...f, work_order_id: e.target.value }))}>
              <option value="">— اختر أمر إنتاج —</option>
              {allWOs?.map(wo => <option key={wo.id} value={wo.id}>{wo.code}</option>)}
            </Select>
            <Input label="تاريخ الفحص" type="date" value={qcForm.inspection_date}
              onChange={e => setQcForm(f => ({ ...f, inspection_date: e.target.value }))} />
          </FormGrid>
          <Select label="نوع الفحص" value={qcForm.inspection_type}
            onChange={e => setQcForm(f => ({ ...f, inspection_type: e.target.value }))}>
            <option value="incoming">استلام مواد</option>
            <option value="in_process">أثناء الإنتاج</option>
            <option value="final">فحص نهائي</option>
            <option value="outgoing">فحص تسليم</option>
          </Select>
          <FormGrid cols={2}>
            <Input label="الكمية المفحوصة *" type="number" min="0" value={qcForm.qty_inspected}
              onChange={e => setQcForm(f => ({ ...f, qty_inspected: e.target.value }))} />
            <Input label="الكمية المقبولة *" type="number" min="0" value={qcForm.qty_passed}
              onChange={e => setQcForm(f => ({ ...f, qty_passed: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="الكمية المرفوضة" type="number" min="0" value={qcForm.qty_failed}
              onChange={e => setQcForm(f => ({ ...f, qty_failed: e.target.value }))} />
            <Input label="كمية إعادة العمل" type="number" min="0" value={qcForm.qty_rework}
              onChange={e => setQcForm(f => ({ ...f, qty_rework: e.target.value }))} />
          </FormGrid>
          <Textarea label="سبب الرفض (اختياري)" rows={2} value={qcForm.failure_reason}
            onChange={e => setQcForm(f => ({ ...f, failure_reason: e.target.value }))} />
          <Textarea label="الإجراء التصحيحي (اختياري)" rows={2} value={qcForm.corrective_action}
            onChange={e => setQcForm(f => ({ ...f, corrective_action: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setShowQC(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={handleCreateQC} disabled={acting}>
            {acting ? '⏳ جاري…' : '✅ حفظ الفحص'}
          </button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
