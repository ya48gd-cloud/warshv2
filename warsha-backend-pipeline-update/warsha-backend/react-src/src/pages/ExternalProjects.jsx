/**
 * ExternalProjects.jsx — المشاريع الخارجية
 * نظام متكامل: فريق + حضور + عدة + مواد + مواصلات + مصاريف + تقدم + تحصيل + ملخص تكاليف
 */
import { useState, useMemo, useCallback, useEffect } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'
import ConfirmDialog from '../components/ui/ConfirmDialog'

// ── Status labels ─────────────────────────────────────────────
const STATUS_MAP = {
  draft:       { label: 'مسودة',          cls: 'badge-gray'  },
  approved:    { label: 'معتمد',           cls: 'badge-blue'  },
  mobilizing:  { label: 'جاهز للسفر',     cls: 'badge-amber' },
  in_progress: { label: 'جاري التنفيذ',   cls: 'badge-amber' },
  testing:     { label: 'اختبار وتشغيل',  cls: 'badge-blue'  },
  handover:    { label: 'تسليم',           cls: 'badge-green' },
  warranty:    { label: 'تحت الضمان',     cls: 'badge-teal'  },
  closed:      { label: 'مغلق',           cls: 'badge-green' },
  cancelled:   { label: 'ملغي',           cls: 'badge-red'   },
}
const TYPE_MAP = {
  installation:  'تركيب جديد',
  maintenance:   'صيانة شاملة',
  repair:        'إصلاح',
  commissioning: 'تشغيل أولي',
  expansion:     'توسعة',
  warranty:      'صيانة ضمان',
}
const EXPENSE_TYPES = {
  food:          '🍽️ وجبات',
  accommodation: '🏨 مبيت',
  transport_local: '🚕 موصلات داخلية',
  tools_small:   '🔧 أدوات صغيرة',
  communication: '📱 اتصالات',
  medical:       '🏥 علاج طبي',
  electricity:   '🔌 كهرباء موقع',
  protocol:      '🎁 بروتوكول',
  other:         '🔀 أخرى',
}
const TRIP_TYPES = {
  team_go:         '🚐 ذهاب الفريق',
  team_return:     '↩️ عودة الفريق',
  equipment_go:    '🚛 شحن معدات (ذهاب)',
  equipment_return:'↩️ شحن معدات (عودة)',
  materials:       '📦 توريد مواد',
  supervision:     '👔 زيارة إدارية',
  other:           '🔀 أخرى',
}
const TOOL_TYPES = {
  hand_tool:   '🔨 أدوات يدوية',
  power_tool:  '⚡ أدوات كهربائية',
  welding:     '🔥 معدات لحام',
  measuring:   '📏 أجهزة قياس',
  lifting:     '🏗️ معدات رفع',
  hydraulic:   '💧 هيدروليك',
  machine:     '⚙️ ماكينة',
}

// ── helpers ───────────────────────────────────────────────────
const badge = (map, val) => {
  const s = map[val] || { label: val, cls: 'badge-gray' }
  return <span className={`badge ${s.cls}`}>{s.label}</span>
}

// ════════════════════════════════════════════════════════════
// PROJECT LIST — القائمة الرئيسية
// ════════════════════════════════════════════════════════════
export default function ExternalProjects() {
  const { canWrite } = useAuth()
  const write = canWrite('production')
  const toast = useToast()

  const { data: projects, loading, reload } = useApi(() => api.extProjects.list())
  const { data: dashboard } = useApi(() => api.extProjects.dashboard())
  const { data: customers } = useApi(() => api.customers.list())

  const [selected, setSelected] = useState(null)  // فتح صفحة تفاصيل
  const [modal, setModal] = useState(false)
  const [confirmAction, setConfirmAction] = useState(null) // { fn, msg }
  const [filterStatus, setFilterStatus] = useState('all')
  const [form, setForm] = useState({
    code: '', title: '', project_type: 'installation', client_id: '', location_id: '',
    governorate: '', city: '', address: '', distance_km: '',
    contract_value: '', planned_start: today(), planned_end: '',
    warranty_months: 12, description: '', notes: '',
  })

  // مواقع العميل المختار
  const [projLocations, setProjLocations] = useState([])
  useEffect(() => {
    if (!form.client_id) { setProjLocations([]); return }
    api.customers.locations(form.client_id).then(r => setProjLocations(Array.isArray(r) ? r : [])).catch(bgErr('عناوين العميل', () => setProjLocations([])))
  }, [form.client_id])

  const filtered = useMemo(() => {
    if (!projects) return []
    if (filterStatus === 'all') return projects
    return projects.filter(p => p.status === filterStatus)
  }, [projects, filterStatus])

  const [save, saving] = useAction(async () => {
    if (!form.client_id) { toast('اختر العميل', 'error'); return }
    if (!form.title.trim()) { toast('أدخل اسم المشروع', 'error'); return }
    if (!form.code.trim()) { toast('أدخل كود المشروع', 'error'); return }
    if (form.planned_start && form.planned_end && form.planned_end < form.planned_start) {
      toast('تاريخ الانتهاء المخطط لازم يكون بعد تاريخ البدء', 'error'); return
    }
    await api.extProjects.create({
      ...form,
      client_id: +form.client_id,
      location_id: form.location_id ? +form.location_id : null,
      contract_value: +form.contract_value || 0,
      distance_km: form.distance_km ? +form.distance_km : null,
      warranty_months: +form.warranty_months || 12,
      planned_start: form.planned_start || null,
      planned_end: form.planned_end || null,
    })
    toast('✅ تم إنشاء المشروع')
    setModal(false)
    reload()
  })

  if (loading) return <PageSpinner />
  if (selected) return (
    <ProjectDetail
      projectId={selected}
      onBack={() => { setSelected(null); reload() }}
    />
  )

  return (
    <div className="flex flex-col gap-4">

      {/* KPI Cards */}
      {dashboard && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {[
            { label: 'إجمالي المشاريع',  val: dashboard.total_projects, color: 'var(--blue)', suffix: '' },
            { label: 'إجمالي العقود',    val: egp(dashboard.total_contract), color: 'var(--teal)' },
            { label: 'إجمالي التكاليف', val: egp(dashboard.total_cost), color: 'var(--amber)' },
            { label: 'هامش الربح',       val: egp(dashboard.total_margin), color: dashboard.total_margin >= 0 ? 'var(--green)' : 'var(--red)' },
            { label: 'محصّل',            val: egp(dashboard.total_collected), color: 'var(--green)' },
            { label: 'متأخر التحصيل',   val: egp(dashboard.total_outstanding), color: 'var(--red)' },
          ].map(k => (
            <div key={k.label} className="kpi-card">
              <div className="text-xs text-erp-muted mb-1">{k.label}</div>
              <div className="text-base font-bold" style={{ color: k.color }}>{k.val}</div>
            </div>
          ))}
        </div>
      )}

      {/* Toolbar */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div className="flex gap-2 flex-wrap">
          {['all', ...Object.keys(STATUS_MAP)].map(s => (
            <button
              key={s}
              onClick={() => setFilterStatus(s)}
              className={`px-3 py-1 rounded-full text-xs font-medium border transition-all ${
                filterStatus === s ? 'bg-teal text-white border-teal' : 'border-erp-border text-erp-muted'
              }`}
              style={{ background: filterStatus === s ? 'var(--teal)' : undefined }}
            >
              {s === 'all' ? 'الكل' : STATUS_MAP[s]?.label}
              {s !== 'all' && dashboard?.by_status?.[s] ? ` (${dashboard.by_status[s]})` : ''}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        {write && (
          <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>
            + مشروع جديد
          </button>
        )}
      </div>

      {/* Projects Grid */}
      {!filtered.length ? (
        <div className="card text-center py-16 text-erp-muted">
          <div className="text-4xl mb-3">🏗️</div>
          <p>لا توجد مشاريع خارجية</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {filtered.map(p => (
            <ProjectCard
              key={p.id}
              project={p}
              onClick={() => setSelected(p.id)}
            />
          ))}
        </div>
      )}

      {/* Modal إنشاء مشروع */}
      <Modal open={modal} onClose={() => setModal(false)} title="مشروع خارجي جديد" size="lg">
        <div className="flex flex-col gap-4">
          <FormGrid cols={2}>
            <Input label="كود المشروع *" value={form.code}
              onChange={e => setForm(f => ({ ...f, code: e.target.value }))}
              placeholder="EXT-2024-001" />
            <Select label="نوع المشروع" value={form.project_type}
              onChange={e => setForm(f => ({ ...f, project_type: e.target.value }))}>
              {Object.entries(TYPE_MAP).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </FormGrid>
          <Input label="اسم المشروع *" value={form.title}
            onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
            placeholder="مثال: تركيب خط أعلاف — شركة الدلتا — المنصورة" />
          <FormGrid cols={2}>
            <Select label="العميل *" value={form.client_id}
              onChange={e => setForm(f => ({ ...f, client_id: e.target.value, location_id: '' }))}>
              <option value="">— اختر عميل —</option>
              {customers?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
            {projLocations.length > 0 && (
              <Select label="موقع العميل" value={form.location_id}
                onChange={e => setForm(f => ({ ...f, location_id: e.target.value }))}>
                <option value="">— بدون موقع محدد —</option>
                {projLocations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
              </Select>
            )}
            <Input label="قيمة العقد (ج)" type="number" value={form.contract_value}
              onChange={e => setForm(f => ({ ...f, contract_value: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={3}>
            <Input label="المحافظة" value={form.governorate}
              onChange={e => setForm(f => ({ ...f, governorate: e.target.value }))} />
            <Input label="المدينة" value={form.city}
              onChange={e => setForm(f => ({ ...f, city: e.target.value }))} />
            <Input label="المسافة (كم)" type="number" value={form.distance_km}
              onChange={e => setForm(f => ({ ...f, distance_km: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="تاريخ البدء المخطط" type="date" value={form.planned_start}
              onChange={e => setForm(f => ({ ...f, planned_start: e.target.value }))} />
            <Input label="تاريخ الانتهاء المخطط" type="date" value={form.planned_end}
              onChange={e => setForm(f => ({ ...f, planned_end: e.target.value }))} />
          </FormGrid>
          <Textarea label="وصف العمل" rows={2} value={form.description}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? '…' : '✅ إنشاء المشروع'}
          </button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ── Project Card ──────────────────────────────────────────────
function ProjectCard({ project: p, onClick }) {
  const costs = p.costs || {}
  const marginColor = costs.margin >= 0 ? 'var(--green)' : 'var(--red)'
  const progress = p.completion_pct || 0

  return (
    <div
      className="card cursor-pointer hover:shadow-lg transition-shadow"
      style={{ borderRight: '3px solid var(--teal)' }}
      onClick={onClick}
    >
      <div className="flex items-start justify-between mb-2">
        <div>
          <div className="font-mono text-xs text-erp-muted">{p.code}</div>
          <div className="font-semibold text-sm mt-0.5 leading-tight">{p.title}</div>
        </div>
        {badge(STATUS_MAP, p.status)}
      </div>

      <div className="text-xs text-erp-muted mb-3">
        🏢 {p.client_name}
        {p.city && ` | 📍 ${p.city}${p.governorate ? '، ' + p.governorate : ''}`}
        {p.distance_km && ` | 🛣️ ${p.distance_km} كم`}
      </div>

      {/* Progress bar */}
      <div className="mb-3">
        <div className="flex justify-between text-xs text-erp-muted mb-1">
          <span>الإنجاز</span>
          <span className="font-semibold">{progress}%</span>
        </div>
        <div className="progress-track">
          <div className="progress-fill"
            style={{ width: `${progress}%`, background: progress === 100 ? 'var(--green)' : 'var(--teal)' }} />
        </div>
      </div>

      {/* Cost summary */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <div className="text-erp-muted">قيمة العقد</div>
          <div className="font-semibold" style={{ color: 'var(--teal)' }}>{egp(p.contract_value)}</div>
        </div>
        <div>
          <div className="text-erp-muted">التكاليف</div>
          <div className="font-semibold" style={{ color: 'var(--amber)' }}>{egp(costs.total)}</div>
        </div>
        <div>
          <div className="text-erp-muted">هامش الربح</div>
          <div className="font-semibold" style={{ color: marginColor }}>{egp(costs.margin)}</div>
        </div>
        <div>
          <div className="text-erp-muted">فريق العمل</div>
          <div className="font-semibold">{p.team_count || 0} عامل</div>
        </div>
      </div>

      {/* Dates */}
      <div className="flex gap-3 mt-3 text-xs text-erp-muted border-t border-erp-border pt-2">
        <span>📅 {fmtDate(p.planned_start)}</span>
        <span>→</span>
        <span>{fmtDate(p.planned_end)}</span>
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════
// PROJECT DETAIL — صفحة التفاصيل بالتابات
// ════════════════════════════════════════════════════════════
function ProjectDetail({ projectId, onBack }) {
  const { canWrite } = useAuth()
  const write = canWrite('production')
  const toast = useToast()
  const [tab, setTab] = useState('overview')

  const { data: p, loading, reload } = useApi(
    () => api.extProjects.get(projectId),
    [projectId]
  )
  const { data: workers } = useApi(() => api.workers.list())
  const { data: materials } = useApi(() => api.inventory.materials())
  const { data: customers } = useApi(() => api.customers.list())

  const [statusLoading, setStatusLoading] = useState(false)
  const changeStatus = async (status) => {
    setStatusLoading(true)
    try {
      await api.extProjects.updateStatus(projectId, status)
      toast('✅ تم تحديث الحالة')
      reload()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setStatusLoading(false)
    }
  }

  const [creatingCert, setCreatingCert] = useState(false)
  const createWCC = async () => {
    if (creatingCert) return
    setCreatingCert(true)
    try {
      const loc = [p.city, p.governorate, p.address].filter(Boolean).join('، ')
      await api.workCompletionCerts.create({
        customer_id:      p.client_id,
        project_name:     p.title,
        project_location: loc || null,
        date:             today(),
        start_date:       p.actual_start || p.planned_start || null,
        end_date:         p.actual_end   || p.planned_end   || null,
        contract_value:   p.contract_value || 0,
        warranty_months:  p.warranty_months || 12,
        scope_summary:    p.description || null,
        items:            [],
      })
      toast('✅ تم إنشاء شهادة إتمام الأعمال — يمكن فتحها من قسم الشهادات')
    } catch (e) {
      toast(e.message || 'فشل إنشاء الشهادة', 'error')
    } finally {
      setCreatingCert(false)
    }
  }

  const TABS = [
    { key: 'overview',  label: '📋 نظرة عامة' },
    { key: 'team',      label: '👷 الفريق والرواتب' },
    { key: 'attendance',label: '📅 الحضور اليومي' },
    { key: 'tools',     label: '🔧 العدة والأدوات' },
    { key: 'materials', label: '🧱 المواد' },
    { key: 'trips',     label: '🚛 المواصلات' },
    { key: 'expenses',  label: '💰 المصاريف اليومية' },
    { key: 'progress',  label: '📈 تقارير التقدم' },
    { key: 'payments',  label: '💳 التحصيل' },
    { key: 'costs',     label: '📊 ملخص التكاليف' },
  ]

  if (loading) return <PageSpinner />
  if (!p) return null

  const costs = p.costs || {}

  return (
    <div className="flex flex-col gap-3">
      {/* Header */}
      <div className="card">
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <button className="btn btn-sm text-xs" onClick={onBack}>← العودة</button>
            <div>
              <div className="font-mono text-xs text-erp-muted">{p.code}</div>
              <div className="text-lg font-bold">{p.title}</div>
              <div className="text-sm text-erp-muted">
                🏢 {p.client_name}
                {p.city && ` | 📍 ${p.city}`}
                {p.governorate && `، ${p.governorate}`}
                {p.distance_km && ` | 🛣️ ${p.distance_km} كم`}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {badge(STATUS_MAP, p.status)}
            {write && (
              <select
                className="field-select text-xs"
                style={{ width: 150 }}
                value={p.status}
                disabled={statusLoading}
                onChange={e => changeStatus(e.target.value)}
              >
                {Object.entries(STATUS_MAP).map(([v, { label }]) =>
                  <option key={v} value={v}>{label}</option>
                )}
              </select>
            )}
            <button
              className="btn btn-sm text-xs"
              style={{ background: '#475569', color: '#fff', border: 'none' }}
              onClick={() => api.extProjects.printReport(p.id)}
              title="طباعة تقرير المشروع"
            >
              🖨️ طباعة التقرير
            </button>
            {write && (
              <button
                className="btn btn-sm text-xs"
                style={{ background: 'var(--teal)', color: '#fff', border: 'none' }}
                onClick={createWCC}
                disabled={creatingCert}
                title="إنشاء شهادة إتمام أعمال من بيانات المشروع"
              >
                {creatingCert ? '…' : '📜 شهادة إتمام'}
              </button>
            )}
          </div>
        </div>

        {/* KPI mini-row */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-4">
          {[
            { label: 'قيمة العقد', val: egp(p.contract_value), color: 'var(--teal)' },
            { label: 'إجمالي التكاليف', val: egp(costs.total), color: 'var(--amber)' },
            { label: 'هامش الربح', val: `${costs.margin_pct || 0}%  (${egp(costs.margin)})`, color: costs.margin >= 0 ? 'var(--green)' : 'var(--red)' },
            { label: 'محصّل', val: egp(costs.collected), color: 'var(--green)' },
            { label: 'متبقي التحصيل', val: egp(costs.outstanding), color: 'var(--red)' },
          ].map(k => (
            <div key={k.label} className="text-center py-2 px-3 rounded-erp" style={{ background: 'var(--bg)' }}>
              <div className="text-xs text-erp-muted">{k.label}</div>
              <div className="text-sm font-bold mt-0.5" style={{ color: k.color }}>{k.val}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 overflow-x-auto pb-1">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className="px-3 py-2 rounded-erp text-xs font-medium whitespace-nowrap transition-all"
            style={{
              background: tab === t.key ? 'var(--teal)' : 'var(--surface)',
              color: tab === t.key ? '#fff' : 'var(--muted)',
              border: '1px solid var(--border)',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div>
        {tab === 'overview'   && <TabOverview p={p} />}
        {tab === 'team'       && <TabTeam p={p} workers={workers} write={write} toast={toast} reload={reload} />}
        {tab === 'attendance' && <TabAttendance p={p} write={write} toast={toast} reload={reload} />}
        {tab === 'tools'      && <TabTools p={p} write={write} toast={toast} reload={reload} />}
        {tab === 'materials'  && <TabMaterials p={p} materials={materials} write={write} toast={toast} reload={reload} />}
        {tab === 'trips'      && <TabTrips p={p} write={write} toast={toast} reload={reload} />}
        {tab === 'expenses'   && <TabExpenses p={p} workers={workers} write={write} toast={toast} reload={reload} />}
        {tab === 'progress'   && <TabProgress p={p} workers={workers} write={write} toast={toast} reload={reload} />}
        {tab === 'payments'   && <TabPayments p={p} write={write} toast={toast} reload={reload} />}
        {tab === 'costs'      && <TabCostSummary p={p} />}
      </div>
    </div>
  )
}

// ── Tab: Overview ─────────────────────────────────────────────
function TabOverview({ p }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div className="card">
        <div className="text-sm font-semibold mb-3">📋 بيانات المشروع</div>
        <table className="w-full text-sm">
          <tbody>
            {[
              ['الكود', p.code],
              ['النوع', TYPE_MAP[p.project_type] || p.project_type],
              ['العميل', p.client_name],
              ['الموقع', [p.address, p.city, p.governorate].filter(Boolean).join('، ')],
              ['المسافة', p.distance_km ? p.distance_km + ' كم' : '—'],
              ['المعدة', p.equipment_desc || '—'],
              ['بدء مخطط', fmtDate(p.planned_start)],
              ['انتهاء مخطط', fmtDate(p.planned_end)],
              ['بدء فعلي', fmtDate(p.actual_start)],
              ['انتهاء فعلي', fmtDate(p.actual_end)],
              ['الضمان', p.warranty_months + ' شهر'],
              ['نهاية الضمان', fmtDate(p.warranty_end)],
            ].map(([k, v]) => (
              <tr key={k} className="border-b border-erp-border">
                <td className="py-1.5 text-erp-muted w-1/3">{k}</td>
                <td className="py-1.5 font-medium">{v || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {p.description && (
          <div className="mt-3 p-3 rounded-erp text-sm" style={{ background: 'var(--bg)' }}>
            {p.description}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-4">
        {/* Cost breakdown */}
        <div className="card">
          <div className="text-sm font-semibold mb-3">📊 توزيع التكاليف</div>
          {[
            { label: '👷 عمالة',            val: p.costs?.labor,     color: 'var(--blue)' },
            { label: '🧱 مواد',             val: p.costs?.materials,  color: 'var(--amber)' },
            { label: '🚛 مواصلات',          val: p.costs?.trips,      color: 'var(--teal)' },
            { label: '💰 مصاريف يومية',     val: p.costs?.expenses,   color: 'var(--red)' },
          ].map(item => {
            const pct = p.costs?.total > 0 ? Math.round(item.val / p.costs.total * 100) : 0
            return (
              <div key={item.label} className="mb-2">
                <div className="flex justify-between text-xs mb-1">
                  <span>{item.label}</span>
                  <span className="font-semibold">{egp(item.val || 0)} ({pct}%)</span>
                </div>
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${pct}%`, background: item.color }} />
                </div>
              </div>
            )
          })}
          <div className="border-t border-erp-border pt-2 flex justify-between font-semibold text-sm mt-2">
            <span>الإجمالي</span>
            <span style={{ color: 'var(--teal)' }}>{egp(p.costs?.total)}</span>
          </div>
        </div>

        {/* Last progress */}
        {p.progress?.[0] && (
          <div className="card">
            <div className="text-sm font-semibold mb-2">📈 آخر تقرير ({fmtDate(p.progress[0].report_date)})</div>
            <div className="mb-2">
              <div className="flex justify-between text-xs mb-1">
                <span>نسبة الإنجاز</span>
                <span className="font-bold">{p.progress[0].completion_pct}%</span>
              </div>
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${p.progress[0].completion_pct}%`, background: 'var(--teal)' }} />
              </div>
            </div>
            {p.progress[0].work_done && <p className="text-xs text-erp-muted">{p.progress[0].work_done}</p>}
            {p.progress[0].issues && (
              <p className="text-xs mt-1" style={{ color: 'var(--red)' }}>⚠️ {p.progress[0].issues}</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Tab: Team ─────────────────────────────────────────────────
function TabTeam({ p, workers, write, toast, reload }) {
  const [modal, setModal] = useState(false)
  const [advModal, setAdvModal] = useState(null)  // member for advance
  const [form, setForm] = useState({ worker_id: '', role: 'فني', daily_rate: '', planned_days: '', joined_date: today() })
  const [advAmount, setAdvAmount] = useState('')

  const [save, saving] = useAction(async () => {
    if (!form.worker_id || !form.daily_rate || Number(form.daily_rate) <= 0) { toast('اختر العامل وأدخل أجر يومي أكبر من صفر', 'error'); return }
    // اجر الورشة الأساسي للمقارنة
    const w = workers?.find(x => x.id === +form.worker_id)
    await api.extProjects.addMember(p.id, {
      ...form,
      worker_id: +form.worker_id,
      daily_rate: +form.daily_rate,
      planned_days: +form.planned_days || 0,
    })
    toast('✅ تمت إضافة العامل للفريق')
    setModal(false)
    reload()
  })

  const [saveAdv, savingAdv] = useAction(async () => {
    if (!advAmount) { toast('أدخل المبلغ', 'error'); return }
    await api.extProjects.addSiteAdvance(p.id, advModal.id, +advAmount)
    toast('✅ تم تسجيل السلفة')
    setAdvModal(null)
    setAdvAmount('')
    reload()
  })

  const [removeMember] = useAction(async (mid) => {
    if (!window.confirm('حذف العامل من الفريق؟')) return
    await api.extProjects.removeMember(p.id, mid)
    toast('تم الحذف')
    reload()
  })

  const totalLaborGross = (p.team || []).reduce((s, m) => s + m.gross_amount, 0)
  const totalAdvances   = (p.team || []).reduce((s, m) => s + m.site_advances, 0)
  const totalNet        = (p.team || []).reduce((s, m) => s + m.net_amount, 0)

  return (
    <div className="flex flex-col gap-4">
      {/* Summary */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'إجمالي المستحقات', val: egp(totalLaborGross), color: 'var(--blue)' },
          { label: 'السلف في الموقع',  val: egp(totalAdvances),   color: 'var(--red)' },
          { label: 'الصافي للصرف',     val: egp(totalNet),        color: 'var(--teal)' },
        ].map(k => (
          <div key={k.label} className="card text-center py-2">
            <div className="text-xs text-erp-muted">{k.label}</div>
            <div className="font-bold text-base" style={{ color: k.color }}>{k.val}</div>
          </div>
        ))}
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-erp-border">
          <div className="font-semibold text-sm">👷 فريق المشروع</div>
          {write && (
            <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ إضافة عامل</button>
          )}
        </div>
        <table className="erp-table">
          <thead>
            <tr>
              <th>العامل</th><th>الدور</th>
              <th>الأجر اليومي</th><th>أجر الورشة</th>
              <th>الأيام المخططة</th><th>الأيام الفعلية</th>
              <th>المستحق</th><th>السلف</th><th>الصافي</th>
              {write && <th />}
            </tr>
          </thead>
          <tbody>
            {!(p.team?.length) && (
              <tr><td colSpan={10} className="text-center text-erp-muted py-8">لا يوجد فريق — أضف عمال للمشروع</td></tr>
            )}
            {(p.team || []).map(m => (
              <tr key={m.id}>
                <td>
                  <div className="font-medium text-sm">{m.worker_name}</div>
                  <div className="text-xs text-erp-muted">{m.job_title}</div>
                </td>
                <td className="text-xs">{m.role}</td>
                <td className="font-semibold" style={{ color: 'var(--teal)' }}>{egp(m.daily_rate)}</td>
                <td className="text-xs text-erp-muted">{egp(m.base_daily_wage)}</td>
                <td className="text-center">{m.planned_days}</td>
                <td className="text-center font-semibold">{m.actual_days}</td>
                <td className="font-semibold" style={{ color: 'var(--blue)' }}>{egp(m.gross_amount)}</td>
                <td style={{ color: 'var(--red)' }} className="text-sm">{egp(m.site_advances)}</td>
                <td className="font-bold" style={{ color: 'var(--green)' }}>{egp(m.net_amount)}</td>
                {write && (
                  <td>
                    <div className="flex gap-1">
                      <button className="btn btn-sm text-xs"
                        style={{ color: 'var(--amber)' }}
                        onClick={() => { setAdvModal(m); setAdvAmount('') }}
                        title="تسجيل سلفة">💰 سلفة</button>
                      <button className="btn btn-sm btn-danger text-xs"
                        onClick={() => removeMember(m.id)}>✕</button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card p-3 text-xs text-erp-muted" style={{ background: 'var(--blue-lt)', border: '1px solid var(--blue-lt)' }}>
        <strong style={{ color: 'var(--blue)' }}>ربط الرواتب:</strong> أيام الحضور على هذا المشروع تُسجَّل بحالة <code>on_project</code> في سجل الحضور اليومي.
        عند توليد كشف الرواتب، يتحسب الأجر اليومي للمشروع (<span style={{ color: 'var(--teal)' }}>تشمل بدل السفر</span>) بدل الأجر الأساسي للأيام دي.
      </div>

      {/* Modal إضافة عامل */}
      <Modal open={modal} onClose={() => setModal(false)} title="إضافة عامل للمشروع" size="sm">
        <div className="flex flex-col gap-3">
          <Select label="العامل *" value={form.worker_id}
            onChange={e => {
              const w = workers?.find(x => x.id === +e.target.value)
              setForm(f => ({ ...f, worker_id: e.target.value, daily_rate: w ? w.daily_wage : f.daily_rate }))
            }}>
            <option value="">— اختر —</option>
            {workers?.map(w => (
              <option key={w.id} value={w.id}>{w.name} ({w.job_title}) — {egp(w.daily_wage)}/يوم</option>
            ))}
          </Select>
          <Input label="الدور في المشروع" value={form.role}
            onChange={e => setForm(f => ({ ...f, role: e.target.value }))}
            placeholder="مثال: لحام رئيسي / كهربائي / مساعد" />
          <FormGrid cols={2}>
            <Input label="الأجر اليومي للمشروع (ج) *" type="number" value={form.daily_rate}
              onChange={e => setForm(f => ({ ...f, daily_rate: e.target.value }))}
              placeholder="شامل بدل السفر والمعيشة" />
            <Input label="الأيام المخططة" type="number" value={form.planned_days}
              onChange={e => setForm(f => ({ ...f, planned_days: e.target.value }))} />
          </FormGrid>
          <Input label="تاريخ الانضمام" type="date" value={form.joined_date}
            onChange={e => setForm(f => ({ ...f, joined_date: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إضافة'}</button>
        </ModalFooter>
      </Modal>

      {/* Modal سلفة */}
      <Modal open={!!advModal} onClose={() => setAdvModal(null)} title={`سلفة في الموقع — ${advModal?.worker_name}`} size="sm">
        <Input label="المبلغ (ج) *" type="number" value={advAmount}
          onChange={e => setAdvAmount(e.target.value)} />
        <ModalFooter>
          <button className="btn" onClick={() => setAdvModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={saveAdv} disabled={savingAdv}>{savingAdv ? '…' : 'تسجيل'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ── Tab: Attendance ───────────────────────────────────────────
function TabAttendance({ p, write, toast, reload }) {
  const [selDate, setSelDate] = useState(today())
  const { data: attData, reload: reloadAtt } = useApi(
    () => api.extProjects.getAttendance(p.id, selDate),
    [p.id, selDate]
  )
  const [bulk, setBulk] = useState({})
  const [bulkMode, setBulkMode] = useState(false)

  const initBulk = () => {
    const init = {}
    ;(p.team || []).forEach(m => {
      const rec = attData?.find(a => a.worker_id === m.worker_id)
      init[m.worker_id] = rec?.status || 'on_project'
    })
    setBulk(init)
    setBulkMode(true)
  }

  const [saveBulk, saving] = useAction(async () => {
    const records = Object.entries(bulk).map(([wid, status]) => ({
      worker_id: +wid, status,
    }))
    await api.extProjects.recordAttendance(p.id, { work_date: selDate, records })
    toast('✅ تم تسجيل الحضور وسيُحسب في كشف الرواتب')
    setBulkMode(false)
    reloadAtt()
    reload()
  })

  const STATUS_ATT = {
    on_project: { label: 'حاضر في الموقع', color: 'var(--green)' },
    half:       { label: 'نصف يوم',        color: 'var(--amber-md)' },
    absent:     { label: 'غائب',           color: 'var(--red)' },
    leave:      { label: 'إجازة',          color: 'var(--blue)' },
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div>
          <label className="field-label">تاريخ الحضور</label>
          <input className="field-input" type="date" value={selDate}
            onChange={e => { setSelDate(e.target.value); setBulkMode(false) }} />
        </div>
        {write && !bulkMode && (
          <button className="btn btn-primary btn-sm mt-4" onClick={initBulk}>📝 تسجيل الحضور</button>
        )}
        {write && bulkMode && (
          <>
            <button className="btn btn-sm mt-4" onClick={() => {
              const all = {}
              ;(p.team || []).forEach(m => { all[m.worker_id] = 'on_project' })
              setBulk(all)
            }}>✓ الكل حاضر</button>
            <button className="btn btn-primary btn-sm mt-4" onClick={saveBulk} disabled={saving}>
              {saving ? '…' : '💾 حفظ'}
            </button>
            <button className="btn btn-sm mt-4" onClick={() => setBulkMode(false)}>إلغاء</button>
          </>
        )}
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead><tr><th>العامل</th><th>الدور</th><th>الأجر اليومي</th><th>الحضور</th></tr></thead>
          <tbody>
            {!(p.team?.length) && <tr><td colSpan={4} className="text-center text-erp-muted py-8">أضف عمال للمشروع أولاً</td></tr>}
            {(p.team || []).map(m => {
              const rec = attData?.find(a => a.worker_id === m.worker_id)
              const cur = bulk[m.worker_id] || rec?.status
              const st  = STATUS_ATT[cur]
              return (
                <tr key={m.worker_id}>
                  <td className="font-medium">{m.worker_name}</td>
                  <td className="text-xs text-erp-muted">{m.role}</td>
                  <td className="font-semibold text-sm" style={{ color: 'var(--teal)' }}>{egp(m.daily_rate)}</td>
                  <td>
                    {bulkMode ? (
                      <div className="flex gap-1 flex-wrap">
                        {Object.entries(STATUS_ATT).map(([k, v]) => (
                          <button key={k}
                            className="px-2 py-0.5 rounded text-xs font-medium border transition-all"
                            style={{
                              background: bulk[m.worker_id] === k ? v.color : 'transparent',
                              color: bulk[m.worker_id] === k ? '#fff' : v.color,
                              borderColor: v.color,
                            }}
                            onClick={() => setBulk(b => ({ ...b, [m.worker_id]: k }))}>
                            {v.label}
                          </button>
                        ))}
                      </div>
                    ) : cur ? (
                      <span className="badge text-xs"
                        style={{ background: (st?.color || 'var(--muted)') + '22', color: st?.color || 'var(--muted)' }}>
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

      <div className="card p-3 text-xs" style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
        <strong>تنبيه الرواتب:</strong> "حاضر في الموقع" يُسجَّل كـ <code>on_project</code> في سجل الحضور العام.
        عند توليد كشف الرواتب أسبوعياً، يتحسب تلقائياً بالأجر اليومي للمشروع بدل الأجر الأساسي.
      </div>
    </div>
  )
}

// ── Tab: Tools ────────────────────────────────────────────────
function TabTools({ p, write, toast, reload }) {
  const [modal, setModal] = useState(false)
  const [retModal, setRetModal] = useState(null)
  const [confirmAction, setConfirmAction] = useState(null)
  const [form, setForm] = useState({ tool_name: '', tool_type: 'hand_tool', quantity: 1, unit: 'قطعة', condition_out: 'good', withdrawn_date: today() })
  const [retForm, setRetForm] = useState({ returned_qty: '', condition_in: 'good' })

  const [save, saving] = useAction(async () => {
    if (!form.tool_name) { toast('أدخل اسم الأداة', 'error'); return }
    await api.extProjects.addTool(p.id, form)
    toast('✅ تمت الإضافة')
    setModal(false)
    reload()
  })

  const [returnTool, returning] = useAction(async () => {
    await api.extProjects.returnTool(p.id, retModal.id, retForm)
    toast('✅ تم تسجيل الإرجاع')
    setRetModal(null)
    reload()
  })

  const [deleteTool] = useAction(async (tid) => {
    await api.extProjects.deleteTool(p.id, tid)
    toast('تم الحذف')
    reload()
  })

  const statusMap = { withdrawn: 'مع الفريق', returned: 'تم الإرجاع', damaged: 'تالف', lost: 'مفقود' }
  const statusColor = { withdrawn: 'var(--amber)', returned: 'var(--green)', damaged: 'var(--red)', lost: 'var(--red)' }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-between items-center">
        <p className="text-sm text-erp-muted">كل عدة تخرج من الورشة لازم تُسجَّل هنا وتُسجَّل لما ترجع</p>
        {write && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ إضافة عدة</button>}
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr><th>الأداة</th><th>النوع</th><th>الكمية</th><th>الحالة عند الخروج</th>
                <th>تاريخ الخروج</th><th>تاريخ الإرجاع</th><th>الحالة</th>{write && <th />}</tr>
          </thead>
          <tbody>
            {!(p.tools?.length) && <tr><td colSpan={8} className="text-center text-erp-muted py-8">لا توجد عدة مسجّلة</td></tr>}
            {(p.tools || []).map(t => (
              <tr key={t.id}>
                <td className="font-medium">{t.tool_name}</td>
                <td className="text-xs">{TOOL_TYPES[t.tool_type] || t.tool_type}</td>
                <td className="text-center">{t.quantity} {t.unit}</td>
                <td className="text-xs">{t.condition_out === 'excellent' ? 'ممتازة' : t.condition_out === 'good' ? 'جيدة' : 'متلفة جزئياً'}</td>
                <td className="text-xs text-erp-muted">{fmtDate(t.withdrawn_date)}</td>
                <td className="text-xs">{t.returned_date ? fmtDate(t.returned_date) : '—'}</td>
                <td>
                  <span className="badge text-xs"
                    style={{ background: (statusColor[t.status] || 'var(--muted)') + '22', color: statusColor[t.status] || 'var(--muted)' }}>
                    {statusMap[t.status] || t.status}
                  </span>
                </td>
                {write && (
                  <td>
                    <div className="flex gap-1">
                      {t.status === 'withdrawn' && (
                        <button className="btn btn-sm text-xs" style={{ color: 'var(--green)' }}
                          onClick={() => { setRetModal(t); setRetForm({ returned_qty: t.quantity, condition_in: 'good' }) }}>
                          ↩️ إرجاع
                        </button>
                      )}
                      <button className="btn btn-sm btn-danger text-xs" onClick={() => setConfirmAction({ fn: () => deleteTool(t.id), msg: 'هل تريد حذف هذه الأداة؟' })}>✕</button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Modal open={modal} onClose={() => setModal(false)} title="إضافة عدة للمشروع" size="sm">
        <div className="flex flex-col gap-3">
          <Input label="اسم الأداة / الجهاز *" value={form.tool_name}
            onChange={e => setForm(f => ({ ...f, tool_name: e.target.value }))} placeholder="مثال: جلاخ كهربائي Bosch 180mm" />
          <FormGrid cols={2}>
            <Select label="النوع" value={form.tool_type}
              onChange={e => setForm(f => ({ ...f, tool_type: e.target.value }))}>
              {Object.entries(TOOL_TYPES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
            <Select label="الحالة عند الخروج" value={form.condition_out}
              onChange={e => setForm(f => ({ ...f, condition_out: e.target.value }))}>
              <option value="excellent">ممتازة</option>
              <option value="good">جيدة</option>
              <option value="fair">متلفة جزئياً</option>
            </Select>
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="الكمية" type="number" step="1" value={form.quantity}
              onChange={e => setForm(f => ({ ...f, quantity: e.target.value }))} />
            <Input label="تاريخ الخروج" type="date" value={form.withdrawn_date}
              onChange={e => setForm(f => ({ ...f, withdrawn_date: e.target.value }))} />
          </FormGrid>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إضافة'}</button>
        </ModalFooter>
      </Modal>

      <Modal open={!!retModal} onClose={() => setRetModal(null)} title={`إرجاع: ${retModal?.tool_name}`} size="sm">
        <div className="flex flex-col gap-3">
          <Input label="الكمية المرجعة" type="number" value={retForm.returned_qty}
            onChange={e => setRetForm(f => ({ ...f, returned_qty: e.target.value }))} />
          <Select label="الحالة عند الإرجاع" value={retForm.condition_in}
            onChange={e => setRetForm(f => ({ ...f, condition_in: e.target.value }))}>
            <option value="excellent">ممتازة</option>
            <option value="good">جيدة</option>
            <option value="fair">متلفة جزئياً</option>
            <option value="damaged">تالفة</option>
          </Select>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setRetModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={returnTool} disabled={returning}>{returning ? '…' : 'تسجيل الإرجاع'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!confirmAction}
        message={confirmAction?.msg || 'هل تريد الحذف؟'}
        onConfirm={() => { confirmAction?.fn(); setConfirmAction(null) }}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  )
}

// ── Tab: Materials ────────────────────────────────────────────
function TabMaterials({ p, materials, write, toast, reload }) {
  const [modal, setModal] = useState(false)
  const [confirmAction, setConfirmAction] = useState(null)
  const [form, setForm] = useState({ source: 'stock', material_id: '', description: '', quantity: '', unit: 'قطعة', unit_cost: '', usage_date: today() })

  const [save, saving] = useAction(async () => {
    if (!form.description && !form.material_id) { toast('أدخل وصف المادة', 'error'); return }
    if (!form.quantity || Number(form.quantity) <= 0) { toast('أدخل كمية أكبر من صفر', 'error'); return }
    await api.extProjects.addMaterial(p.id, {
      ...form,
      material_id: form.material_id ? +form.material_id : null,
      quantity: +form.quantity,
      unit_cost: +form.unit_cost || 0,
    })
    toast('✅ تمت الإضافة')
    setModal(false)
    reload()
  })

  const [deleteMat] = useAction(async (mid) => {
    await api.extProjects.deleteMaterial(p.id, mid)
    toast('تم الحذف')
    reload()
  })

  const stockTotal = (p.materials || []).filter(m => m.source === 'stock').reduce((s, m) => s + m.total_cost, 0)
  const localTotal = (p.materials || []).filter(m => m.source === 'local_purchase').reduce((s, m) => s + m.total_cost, 0)

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'من المخزون', val: egp(stockTotal), color: 'var(--blue)' },
          { label: 'شراء محلي', val: egp(localTotal), color: 'var(--amber)' },
          { label: 'إجمالي المواد', val: egp(stockTotal + localTotal), color: 'var(--teal)' },
        ].map(k => (
          <div key={k.label} className="card text-center py-2">
            <div className="text-xs text-erp-muted">{k.label}</div>
            <div className="font-bold" style={{ color: k.color }}>{k.val}</div>
          </div>
        ))}
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="flex justify-between px-4 py-3 border-b border-erp-border">
          <div className="font-semibold text-sm">🧱 المواد المستهلكة</div>
          {write && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ إضافة مادة</button>}
        </div>
        <table className="erp-table">
          <thead><tr><th>المصدر</th><th>المادة</th><th>الكمية</th><th>سعر الوحدة</th><th>الإجمالي</th><th>التاريخ</th>{write && <th />}</tr></thead>
          <tbody>
            {!(p.materials?.length) && <tr><td colSpan={7} className="text-center text-erp-muted py-8">لا توجد مواد مسجّلة</td></tr>}
            {(p.materials || []).map(m => (
              <tr key={m.id}>
                <td>
                  <span className={`badge ${m.source === 'stock' ? 'badge-blue' : 'badge-amber'}`}>
                    {m.source === 'stock' ? '📦 مخزون' : '🛒 محلي'}
                  </span>
                </td>
                <td className="font-medium">{m.description}</td>
                <td className="text-center">{m.quantity} {m.unit}</td>
                <td>{egp(m.unit_cost)}</td>
                <td className="font-semibold" style={{ color: 'var(--teal)' }}>{egp(m.total_cost)}</td>
                <td className="text-xs text-erp-muted">{fmtDate(m.usage_date)}</td>
                {write && <td><button className="btn btn-sm btn-danger text-xs" onClick={() => setConfirmAction({ fn: () => deleteMat(m.id), msg: 'هل تريد حذف هذه المادة؟' })}>✕</button></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Modal open={modal} onClose={() => setModal(false)} title="إضافة مادة" size="sm">
        <div className="flex flex-col gap-3">
          <Select label="المصدر *" value={form.source}
            onChange={e => setForm(f => ({ ...f, source: e.target.value, material_id: '', description: '', unit_cost: '' }))}>
            <option value="stock">من مخزون الورشة</option>
            <option value="local_purchase">شراء محلي في الموقع</option>
          </Select>
          {form.source === 'stock' && (
            <Select label="المادة" value={form.material_id}
              onChange={e => {
                const mat = materials?.find(x => x.id === +e.target.value)
                setForm(f => ({ ...f, material_id: e.target.value, description: mat?.name_ar || f.description, unit_cost: mat?.unit_cost || f.unit_cost, unit: mat?.unit || f.unit }))
              }}>
              <option value="">— اختر من المخزون —</option>
              {materials?.map(m => <option key={m.id} value={m.id}>{m.name_ar} ({m.unit}) — {egp(m.unit_cost)}</option>)}
            </Select>
          )}
          <Input label="الوصف *" value={form.description}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
          <FormGrid cols={3}>
            <Input label="الكمية *" type="number" step="0.01" value={form.quantity}
              onChange={e => setForm(f => ({ ...f, quantity: e.target.value }))} />
            <Input label="سعر الوحدة (ج)" type="number" step="0.01" value={form.unit_cost}
              onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))} />
            <Input label="التاريخ" type="date" value={form.usage_date}
              onChange={e => setForm(f => ({ ...f, usage_date: e.target.value }))} />
          </FormGrid>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إضافة'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!confirmAction}
        message={confirmAction?.msg || 'هل تريد الحذف؟'}
        onConfirm={() => { confirmAction?.fn(); setConfirmAction(null) }}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  )
}

// ── Tab: Trips ────────────────────────────────────────────────
function TabTrips({ p, write, toast, reload }) {
  const [modal, setModal] = useState(false)
  const [confirmAction, setConfirmAction] = useState(null)
  const [form, setForm] = useState({
    trip_date: today(), trip_type: 'team_go', vehicle_type: 'company_van',
    vehicle_plate: '', driver_name: '', from_location: 'الورشة', to_location: '',
    distance_km: '', passengers: 1, fuel_cost: '', toll_cost: 0, rental_cost: 0, other_cost: 0,
  })

  const [save, saving] = useAction(async () => {
    await api.extProjects.addTrip(p.id, {
      ...form,
      passengers: +form.passengers || 1,
      fuel_cost: +form.fuel_cost || 0,
      toll_cost: +form.toll_cost || 0,
      rental_cost: +form.rental_cost || 0,
      other_cost: +form.other_cost || 0,
      distance_km: form.distance_km ? +form.distance_km : null,
    })
    toast('✅ تمت الإضافة')
    setModal(false)
    reload()
  })

  const [deleteTrip] = useAction(async (tid) => {
    await api.extProjects.deleteTrip(p.id, tid)
    toast('تم الحذف')
    reload()
  })

  const totalTrips = (p.trips || []).reduce((s, t) => s + t.total_cost, 0)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-between items-center">
        <div className="text-sm font-semibold" style={{ color: 'var(--teal)' }}>
          إجمالي تكاليف المواصلات: {egp(totalTrips)}
        </div>
        {write && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ إضافة رحلة</button>}
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr><th>التاريخ</th><th>النوع</th><th>الوسيلة</th><th>المسار</th>
                <th>المسافة</th><th>الركاب</th><th>التكلفة</th>{write && <th />}</tr>
          </thead>
          <tbody>
            {!(p.trips?.length) && <tr><td colSpan={8} className="text-center text-erp-muted py-8">لا توجد رحلات مسجّلة</td></tr>}
            {(p.trips || []).map(t => (
              <tr key={t.id}>
                <td className="text-xs text-erp-muted">{fmtDate(t.trip_date)}</td>
                <td className="text-xs">{TRIP_TYPES[t.trip_type] || t.trip_type}</td>
                <td className="text-xs">{t.vehicle_type === 'company_van' ? '🚐 سيارة الورشة' : t.vehicle_type === 'truck' ? '🚛 شاحنة' : '🚗 ' + t.vehicle_type}</td>
                <td className="text-xs">{[t.from_location, t.to_location].filter(Boolean).join(' → ')}</td>
                <td className="text-center text-xs">{t.distance_km ? t.distance_km + ' كم' : '—'}</td>
                <td className="text-center">{t.passengers}</td>
                <td className="font-semibold" style={{ color: 'var(--teal)' }}>{egp(t.total_cost)}</td>
                {write && <td><button className="btn btn-sm btn-danger text-xs" onClick={() => setConfirmAction({ fn: () => deleteTrip(t.id), msg: 'هل تريد حذف هذه الرحلة؟' })}>✕</button></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Modal open={modal} onClose={() => setModal(false)} title="إضافة رحلة" size="md">
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="التاريخ *" type="date" value={form.trip_date}
              onChange={e => setForm(f => ({ ...f, trip_date: e.target.value }))} />
            <Select label="نوع الرحلة *" value={form.trip_type}
              onChange={e => setForm(f => ({ ...f, trip_type: e.target.value }))}>
              {Object.entries(TRIP_TYPES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </FormGrid>
          <FormGrid cols={2}>
            <Select label="وسيلة النقل" value={form.vehicle_type}
              onChange={e => setForm(f => ({ ...f, vehicle_type: e.target.value }))}>
              <option value="company_van">سيارة الورشة (ميكروباص/بيك أب)</option>
              <option value="rented_car">سيارة مستأجرة</option>
              <option value="truck">شاحنة</option>
              <option value="pickup">بيك أب</option>
              <option value="bus">أوتوبيس</option>
              <option value="public">مواصلات عامة</option>
            </Select>
            <Input label="رقم اللوحة / السائق" value={form.vehicle_plate}
              onChange={e => setForm(f => ({ ...f, vehicle_plate: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="من" value={form.from_location}
              onChange={e => setForm(f => ({ ...f, from_location: e.target.value }))} />
            <Input label="إلى" value={form.to_location}
              onChange={e => setForm(f => ({ ...f, to_location: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={4}>
            <Input label="المسافة (كم)" type="number" value={form.distance_km}
              onChange={e => setForm(f => ({ ...f, distance_km: e.target.value }))} />
            <Input label="عدد الأفراد" type="number" value={form.passengers}
              onChange={e => setForm(f => ({ ...f, passengers: e.target.value }))} />
            <Input label="وقود (ج)" type="number" value={form.fuel_cost}
              onChange={e => setForm(f => ({ ...f, fuel_cost: e.target.value }))} />
            <Input label="أوتوستراد (ج)" type="number" value={form.toll_cost}
              onChange={e => setForm(f => ({ ...f, toll_cost: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="إيجار (ج)" type="number" value={form.rental_cost}
              onChange={e => setForm(f => ({ ...f, rental_cost: e.target.value }))} />
            <Input label="أخرى (ج)" type="number" value={form.other_cost}
              onChange={e => setForm(f => ({ ...f, other_cost: e.target.value }))} />
          </FormGrid>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إضافة'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!confirmAction}
        message={confirmAction?.msg || 'هل تريد الحذف؟'}
        onConfirm={() => { confirmAction?.fn(); setConfirmAction(null) }}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  )
}

// ── Tab: Expenses ─────────────────────────────────────────────
function TabExpenses({ p, workers, write, toast, reload }) {
  const [modal, setModal] = useState(false)
  const [confirmAction, setConfirmAction] = useState(null)
  const [form, setForm] = useState({ expense_date: today(), expense_type: 'food', description: '', amount: '', persons_count: 1, paid_by: '', receipt_ref: '' })

  const [save, saving] = useAction(async () => {
    if (!form.description || !form.amount || Number(form.amount) <= 0) { toast('أدخل وصف ومبلغ أكبر من صفر', 'error'); return }
    await api.extProjects.addExpense(p.id, {
      ...form,
      amount: +form.amount,
      persons_count: +form.persons_count || 1,
      paid_by: form.paid_by ? +form.paid_by : null,
    })
    toast('✅ تمت الإضافة')
    setModal(false)
    reload()
  })

  const [deleteExp] = useAction(async (eid) => {
    await api.extProjects.deleteExpense(p.id, eid)
    toast('تم الحذف')
    reload()
  })

  // تجميع المصاريف بالنوع
  const byType = {}
  ;(p.expenses || []).forEach(e => {
    byType[e.expense_type] = (byType[e.expense_type] || 0) + e.amount
  })
  const total = Object.values(byType).reduce((s, v) => s + v, 0)

  return (
    <div className="flex flex-col gap-4">
      {/* Summary by type */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {Object.entries(byType).map(([type, amount]) => (
          <div key={type} className="card py-2 px-3 text-center">
            <div className="text-xs text-erp-muted">{EXPENSE_TYPES[type] || type}</div>
            <div className="font-bold text-sm" style={{ color: 'var(--amber)' }}>{egp(amount)}</div>
          </div>
        ))}
        <div className="card py-2 px-3 text-center" style={{ border: '2px solid var(--teal-lt)' }}>
          <div className="text-xs text-erp-muted">الإجمالي</div>
          <div className="font-bold text-sm" style={{ color: 'var(--teal)' }}>{egp(total)}</div>
        </div>
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="flex justify-between px-4 py-3 border-b border-erp-border">
          <div className="font-semibold text-sm">💰 المصاريف اليومية</div>
          {write && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ إضافة مصروف</button>}
        </div>
        <table className="erp-table">
          <thead><tr><th>التاريخ</th><th>النوع</th><th>الوصف</th><th>الأفراد</th><th>المبلغ</th><th>دفعه</th>{write && <th />}</tr></thead>
          <tbody>
            {!(p.expenses?.length) && <tr><td colSpan={7} className="text-center text-erp-muted py-8">لا توجد مصاريف مسجّلة</td></tr>}
            {(p.expenses || []).map(e => (
              <tr key={e.id}>
                <td className="text-xs text-erp-muted">{fmtDate(e.expense_date)}</td>
                <td className="text-xs">{EXPENSE_TYPES[e.expense_type] || e.expense_type}</td>
                <td className="text-sm">{e.description}</td>
                <td className="text-center text-xs">{e.persons_count}</td>
                <td className="font-semibold" style={{ color: 'var(--amber)' }}>{egp(e.amount)}</td>
                <td className="text-xs text-erp-muted">
                  {e.paid_by ? (workers?.find(w => w.id === e.paid_by)?.name || e.paid_by) : '—'}
                </td>
                {write && <td><button className="btn btn-sm btn-danger text-xs" onClick={() => setConfirmAction({ fn: () => deleteExp(e.id), msg: 'هل تريد حذف هذه المصروفات؟' })}>✕</button></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Modal open={modal} onClose={() => setModal(false)} title="إضافة مصروف" size="sm">
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            <Input label="التاريخ *" type="date" value={form.expense_date}
              onChange={e => setForm(f => ({ ...f, expense_date: e.target.value }))} />
            <Select label="النوع *" value={form.expense_type}
              onChange={e => setForm(f => ({ ...f, expense_type: e.target.value }))}>
              {Object.entries(EXPENSE_TYPES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </FormGrid>
          <Input label="الوصف *" value={form.description}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            placeholder="مثال: غداء الفريق في مطعم طريق الإسكندرية" />
          <FormGrid cols={3}>
            <Input label="المبلغ (ج) *" type="number" value={form.amount}
              onChange={e => setForm(f => ({ ...f, amount: e.target.value }))} />
            <Input label="عدد الأفراد" type="number" value={form.persons_count}
              onChange={e => setForm(f => ({ ...f, persons_count: e.target.value }))} />
            <Select label="دفعه" value={form.paid_by}
              onChange={e => setForm(f => ({ ...f, paid_by: e.target.value }))}>
              <option value="">— اختر —</option>
              {workers?.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
            </Select>
          </FormGrid>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إضافة'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!confirmAction}
        message={confirmAction?.msg || 'هل تريد الحذف؟'}
        onConfirm={() => { confirmAction?.fn(); setConfirmAction(null) }}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  )
}

// ── Tab: Progress ─────────────────────────────────────────────
function TabProgress({ p, workers, write, toast, reload }) {
  const [modal, setModal] = useState(false)
  const [confirmAction, setConfirmAction] = useState(null)
  const [form, setForm] = useState({
    report_date: today(), completion_pct: 0, work_done: '', issues: '',
    next_steps: '', client_notes: '', client_approved: false,
    workers_present: p.team?.length || 0, reporter_id: '',
  })

  const [save, saving] = useAction(async () => {
    await api.extProjects.addProgress(p.id, {
      ...form,
      completion_pct: +form.completion_pct,
      workers_present: +form.workers_present,
      reporter_id: form.reporter_id ? +form.reporter_id : null,
    })
    toast('✅ تم حفظ التقرير')
    setModal(false)
    reload()
  })

  const [deleteRep] = useAction(async (rid) => {
    await api.extProjects.deleteProgress(p.id, rid)
    toast('تم الحذف')
    reload()
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        {write && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ تقرير يومي</button>}
      </div>

      {!(p.progress?.length) ? (
        <div className="card text-center py-10 text-erp-muted">لا توجد تقارير تقدم بعد</div>
      ) : (
        p.progress.map(r => (
          <div key={r.id} className="card">
            <div className="flex justify-between items-start">
              <div>
                <div className="font-semibold text-sm">{fmtDate(r.report_date)}</div>
                <div className="flex items-center gap-2 mt-1">
                  <div className="progress-track w-32">
                    <div className="progress-fill" style={{ width: `${r.completion_pct}%`, background: 'var(--teal)' }} />
                  </div>
                  <span className="text-xs font-bold" style={{ color: 'var(--teal)' }}>{r.completion_pct}% إنجاز</span>
                  <span className="text-xs text-erp-muted">{r.workers_present} عامل</span>
                  {r.client_approved && <span className="badge badge-green text-xs">وافق العميل ✓</span>}
                </div>
              </div>
              {write && <button className="btn btn-sm btn-danger text-xs" onClick={() => setConfirmAction({ fn: () => deleteRep(r.id), msg: 'هل تريد حذف هذا التقرير؟' })}>✕</button>}
            </div>
            {r.work_done && <p className="text-sm mt-2">{r.work_done}</p>}
            {r.issues && <p className="text-sm mt-1" style={{ color: 'var(--red)' }}>⚠️ {r.issues}</p>}
            {r.next_steps && <p className="text-sm mt-1 text-erp-muted">التالي: {r.next_steps}</p>}
            {r.client_notes && <p className="text-sm mt-1" style={{ color: 'var(--blue)' }}>العميل: {r.client_notes}</p>}
          </div>
        ))
      )}

      <Modal open={modal} onClose={() => setModal(false)} title="تقرير تقدم يومي" size="md">
        <div className="flex flex-col gap-3">
          <FormGrid cols={3}>
            <Input label="التاريخ *" type="date" value={form.report_date}
              onChange={e => setForm(f => ({ ...f, report_date: e.target.value }))} />
            <Input label="نسبة الإنجاز (%)" type="number" min="0" max="100" value={form.completion_pct}
              onChange={e => setForm(f => ({ ...f, completion_pct: e.target.value }))} />
            <Input label="عدد العمال الحاضرين" type="number" value={form.workers_present}
              onChange={e => setForm(f => ({ ...f, workers_present: e.target.value }))} />
          </FormGrid>
          <Textarea label="ما تم اليوم *" rows={3} value={form.work_done}
            onChange={e => setForm(f => ({ ...f, work_done: e.target.value }))}
            placeholder="وصف تفصيلي للأعمال المنجزة..." />
          <Textarea label="مشاكل وعوائق" rows={2} value={form.issues}
            onChange={e => setForm(f => ({ ...f, issues: e.target.value }))}
            placeholder="لو في مشاكل أو تأخيرات..." />
          <Textarea label="الخطة لليوم التالي" rows={2} value={form.next_steps}
            onChange={e => setForm(f => ({ ...f, next_steps: e.target.value }))} />
          <div className="flex items-center gap-2">
            <input type="checkbox" id="client_approved" checked={form.client_approved}
              onChange={e => setForm(f => ({ ...f, client_approved: e.target.checked }))} />
            <label htmlFor="client_approved" className="text-sm">العميل وافق على اليوم ✓</label>
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ التقرير'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!confirmAction}
        message={confirmAction?.msg || 'هل تريد الحذف؟'}
        onConfirm={() => { confirmAction?.fn(); setConfirmAction(null) }}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  )
}

// ── Tab: Payments ─────────────────────────────────────────────
function TabPayments({ p, write, toast, reload }) {
  const [modal, setModal] = useState(false)
  const [recModal, setRecModal] = useState(null)
  const [confirmAction, setConfirmAction] = useState(null)
  const [form, setForm] = useState({ milestone: '', milestone_pct: 0, due_amount: '', due_date: '' })
  const [recForm, setRecForm] = useState({ paid_amount: '', payment_method: 'cash', reference: '', paid_date: today() })

  const [save, saving] = useAction(async () => {
    if (!form.milestone || !form.due_amount) { toast('بيانات ناقصة', 'error'); return }
    await api.extProjects.addPayment(p.id, { ...form, due_amount: +form.due_amount, milestone_pct: +form.milestone_pct || 0 })
    toast('✅ تمت الإضافة')
    setModal(false)
    reload()
  })

  const [receive, receiving] = useAction(async () => {
    if (!recForm.paid_amount) { toast('أدخل المبلغ', 'error'); return }
    await api.extProjects.receivePayment(p.id, recModal.id, { ...recForm, paid_amount: +recForm.paid_amount })
    toast('✅ تم تسجيل الدفعة')
    setRecModal(null)
    reload()
  })

  const [deletePay] = useAction(async (payid) => {
    await api.extProjects.deletePayment(p.id, payid)
    toast('تم الحذف')
    reload()
  })

  const totalDue = (p.payments || []).reduce((s, pay) => s + pay.due_amount, 0)
  const totalPaid = (p.payments || []).reduce((s, pay) => s + pay.paid_amount, 0)

  const statusColors = { pending: 'var(--amber)', partial: 'var(--blue)', paid: 'var(--green)', overdue: 'var(--red)' }
  const statusLabels = { pending: 'مستحق', partial: 'جزئي', paid: 'محصّل', overdue: 'متأخر' }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'إجمالي العقد', val: egp(p.contract_value), color: 'var(--teal)' },
          { label: 'محصّل', val: egp(totalPaid), color: 'var(--green)' },
          { label: 'متبقي', val: egp(totalDue - totalPaid), color: 'var(--red)' },
        ].map(k => (
          <div key={k.label} className="card text-center py-2">
            <div className="text-xs text-erp-muted">{k.label}</div>
            <div className="font-bold" style={{ color: k.color }}>{k.val}</div>
          </div>
        ))}
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="flex justify-between px-4 py-3 border-b border-erp-border">
          <div className="font-semibold text-sm">💳 جدول الدفعات</div>
          {write && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ دفعة جديدة</button>}
        </div>
        <table className="erp-table">
          <thead>
            <tr><th>المرحلة</th><th>النسبة</th><th>المستحق</th><th>الاستحقاق</th>
                <th>المحصّل</th><th>تاريخ القبض</th><th>الحالة</th>{write && <th />}</tr>
          </thead>
          <tbody>
            {!(p.payments?.length) && <tr><td colSpan={8} className="text-center text-erp-muted py-8">لا توجد دفعات</td></tr>}
            {(p.payments || []).map(pay => (
              <tr key={pay.id}>
                <td className="font-medium">{pay.milestone}</td>
                <td className="text-center">{pay.milestone_pct}%</td>
                <td className="font-semibold" style={{ color: 'var(--teal)' }}>{egp(pay.due_amount)}</td>
                <td className="text-xs text-erp-muted">{fmtDate(pay.due_date)}</td>
                <td className="font-semibold" style={{ color: 'var(--green)' }}>{egp(pay.paid_amount)}</td>
                <td className="text-xs">{fmtDate(pay.paid_date)}</td>
                <td>
                  <span className="badge text-xs"
                    style={{ background: (statusColors[pay.status] || 'var(--muted)') + '22', color: statusColors[pay.status] || 'var(--muted)' }}>
                    {statusLabels[pay.status] || pay.status}
                  </span>
                </td>
                {write && (
                  <td>
                    <div className="flex gap-1">
                      {pay.status !== 'paid' && (
                        <button className="btn btn-sm text-xs" style={{ color: 'var(--green)' }}
                          onClick={() => { setRecModal(pay); setRecForm({ paid_amount: pay.due_amount - pay.paid_amount, payment_method: 'cash', reference: '', paid_date: today() }) }}>
                          💵 قبض
                        </button>
                      )}
                      <button className="btn btn-sm btn-danger text-xs" onClick={() => setConfirmAction({ fn: () => deletePay(pay.id), msg: 'هل تريد حذف هذه الدفعة؟' })}>✕</button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Modal open={modal} onClose={() => setModal(false)} title="إضافة دفعة" size="sm">
        <div className="flex flex-col gap-3">
          <Input label="اسم المرحلة *" value={form.milestone}
            onChange={e => setForm(f => ({ ...f, milestone: e.target.value }))}
            placeholder="مثال: مقدم 30% عند التوقيع" />
          <FormGrid cols={2}>
            <Input label="النسبة (%)" type="number" value={form.milestone_pct}
              onChange={e => setForm(f => ({ ...f, milestone_pct: e.target.value }))} />
            <Input label="المبلغ المستحق (ج) *" type="number" value={form.due_amount}
              onChange={e => setForm(f => ({ ...f, due_amount: e.target.value }))} />
          </FormGrid>
          <Input label="تاريخ الاستحقاق" type="date" value={form.due_date}
            onChange={e => setForm(f => ({ ...f, due_date: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إضافة'}</button>
        </ModalFooter>
      </Modal>

      <Modal open={!!recModal} onClose={() => setRecModal(null)} title={`قبض دفعة — ${recModal?.milestone}`} size="sm">
        <div className="flex flex-col gap-3">
          <Input label="المبلغ المقبوض (ج) *" type="number" value={recForm.paid_amount}
            onChange={e => setRecForm(f => ({ ...f, paid_amount: e.target.value }))} />
          <FormGrid cols={2}>
            <Select label="طريقة الدفع" value={recForm.payment_method}
              onChange={e => setRecForm(f => ({ ...f, payment_method: e.target.value }))}>
              <option value="cash">كاش</option>
              <option value="cheque">شيك</option>
              <option value="transfer">تحويل بنكي</option>
            </Select>
            <Input label="تاريخ القبض" type="date" value={recForm.paid_date}
              onChange={e => setRecForm(f => ({ ...f, paid_date: e.target.value }))} />
          </FormGrid>
          <Input label="رقم المرجع / الشيك" value={recForm.reference}
            onChange={e => setRecForm(f => ({ ...f, reference: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setRecModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={receive} disabled={receiving}>{receiving ? '…' : 'تسجيل القبض'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!confirmAction}
        message={confirmAction?.msg || 'هل تريد الحذف؟'}
        onConfirm={() => { confirmAction?.fn(); setConfirmAction(null) }}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  )
}

// ── Tab: Cost Summary ─────────────────────────────────────────
function TabCostSummary({ p }) {
  const { data: summary, loading } = useApi(() => api.extProjects.costSummary(p.id), [p.id])

  if (loading) return <PageSpinner />
  if (!summary) return null

  const items = [
    { label: '👷 العمالة',          val: summary.labor?.total,     color: 'var(--blue)',  pct: summary.total_cost > 0 ? Math.round(summary.labor?.total / summary.total_cost * 100) : 0 },
    { label: '🧱 المواد',           val: summary.materials?.total, color: 'var(--amber)', pct: summary.total_cost > 0 ? Math.round(summary.materials?.total / summary.total_cost * 100) : 0 },
    { label: '🚛 المواصلات',        val: summary.trips?.total,     color: 'var(--teal)',  pct: summary.total_cost > 0 ? Math.round(summary.trips?.total / summary.total_cost * 100) : 0 },
    { label: '💰 المصاريف اليومية', val: summary.expenses?.total,  color: 'var(--red)',   pct: summary.total_cost > 0 ? Math.round(summary.expenses?.total / summary.total_cost * 100) : 0 },
  ]

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      {/* Cost breakdown */}
      <div className="card">
        <div className="text-sm font-semibold mb-4">📊 توزيع التكاليف</div>
        {items.map(item => (
          <div key={item.label} className="mb-3">
            <div className="flex justify-between text-sm mb-1">
              <span>{item.label}</span>
              <span className="font-semibold">{egp(item.val || 0)} — {item.pct}%</span>
            </div>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${item.pct}%`, background: item.color }} />
            </div>
          </div>
        ))}
        <div className="border-t border-erp-border pt-3 mt-3">
          <div className="flex justify-between font-bold text-sm">
            <span>إجمالي التكاليف</span>
            <span style={{ color: 'var(--amber)' }}>{egp(summary.total_cost)}</span>
          </div>
        </div>
      </div>

      {/* Financial summary */}
      <div className="flex flex-col gap-3">
        <div className="card">
          <div className="text-sm font-semibold mb-3">💵 الملخص المالي</div>
          {[
            ['قيمة العقد الإجمالية', egp(summary.contract_value), 'var(--teal)'],
            ['إجمالي التكاليف', egp(summary.total_cost), 'var(--amber)'],
            ['هامش الربح الإجمالي', `${egp(summary.margin)} (${summary.margin_pct}%)`, summary.margin >= 0 ? 'var(--green)' : 'var(--red)'],
            ['محصّل من العميل', egp(summary.collected), 'var(--green)'],
            ['متبقي للتحصيل', egp(summary.outstanding), 'var(--red)'],
          ].map(([label, val, color]) => (
            <div key={label} className="flex justify-between py-2 border-b border-erp-border text-sm">
              <span className="text-erp-muted">{label}</span>
              <span className="font-bold" style={{ color }}>{val}</span>
            </div>
          ))}
        </div>

        {/* Labor details */}
        {summary.labor?.lines?.length > 0 && (
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-2 border-b border-erp-border text-sm font-semibold">كشف حساب العمال</div>
            <table className="erp-table">
              <thead><tr><th>العامل</th><th>أيام</th><th>أجر/يوم</th><th>إجمالي</th><th>سلف</th><th>صافي</th></tr></thead>
              <tbody>
                {summary.labor.lines.map((l, i) => (
                  <tr key={i}>
                    <td className="font-medium text-xs">{l.worker}</td>
                    <td className="text-center">{l.days}</td>
                    <td>{egp(l.daily_rate)}</td>
                    <td style={{ color: 'var(--blue)' }} className="font-semibold">{egp(l.gross)}</td>
                    <td style={{ color: 'var(--red)' }}>{egp(l.advances)}</td>
                    <td style={{ color: 'var(--green)' }} className="font-bold">{egp(l.net)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
