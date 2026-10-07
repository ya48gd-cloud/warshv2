/**
 * النسخ الاحتياطي (Super Admin) — نسخة كاملة للنظام، قائمة/تنزيل/حذف، واسترجاع آمن.
 *
 * - يعمل داخل لوحة Super Admin (ثيم داكن + توكن sa_token) وليس داخل مصادقة الورش.
 * - مصدر البيانات: GET /super-admin/backups (كل أنواع العمليات) + /status + /restore-instructions.
 * - أزرار التنزيل/الاسترجاع بتعتمد على أعلام السيرفر (downloadable / restorable) وليس على حسابات العميل.
 * - الاسترجاع (SQLite فقط): prepare ثم restore بـ confirm_token + confirm_text، مع تأكيد صريح.
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { saBackupApi, RESTORE_CONFIRM_TEXT } from '../api/superAdminBackup'

// ── ألوان (نفس لوحة SuperAdmin.jsx) ────────────────────────────
const C = {
  bg: '#0F172A', card: '#1E293B', border: '#334155', text: '#F1F5F9', muted: '#94A3B8', dim: '#64748B',
  primary: '#6366F1', green: '#86EFAC', greenBg: '#052E16', greenBr: '#166534',
  red: '#FCA5A5', redBg: '#450A0A', redBr: '#7F1D1D', amber: '#FCD34D', amberBg: '#451A03', amberBr: '#92400E',
}
const FONT = 'Cairo, sans-serif'
const BIG_DOWNLOAD_BYTES = 500 * 1024 * 1024

// ── مساعدات ────────────────────────────────────────────────────
function formatBytes(n) {
  if (n == null || isNaN(n)) return '—'
  // LRM حتى لا يُعرض "1.4 MB" معكوساً داخل نص عربي RTL
  if (n < 1024) return `\u200E${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `\u200E${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`
}

// الخادم بيرجّع ISO مع Z؛ ولو جاء بدونها نفترض UTC (حتى لا يُقرأ كتوقيت محلي)
function parseServerDate(s) {
  if (!s) return null
  let str = String(s)
  if (!/([zZ]|[+-]\d\d:?\d\d)$/.test(str)) str = str.replace(' ', 'T') + 'Z'
  const d = new Date(str)
  return isNaN(d.getTime()) ? null : d
}

function fmtDateTime(s) {
  const d = parseServerDate(s)
  if (!d) return '—'
  try {
    return new Intl.DateTimeFormat('ar-EG-u-nu-latn', { dateStyle: 'medium', timeStyle: 'short' }).format(d)
  } catch {
    return d.toLocaleString()
  }
}

function fmtAge(hours) {
  if (hours == null || isNaN(hours)) return ''
  if (hours < 1) return 'منذ أقل من ساعة'
  if (hours < 48) return `منذ ${Math.floor(hours)} ساعة`
  return `منذ ${Math.floor(hours / 24)} يوم`
}

function fmtDuration(a, b) {
  const s = parseServerDate(a), e = parseServerDate(b)
  if (!s || !e) return '—'
  const sec = Math.max(0, Math.round((e - s) / 1000))
  if (sec < 60) return `${sec} ث`
  return `${Math.floor(sec / 60)} د ${sec % 60} ث`
}

const STATUS_LABEL = { success: ['ناجحة', 'green'], failed: ['فشلت', 'red'], running: ['جارية', 'amber'] }

function opLabel(it) {
  if (it.kind === 'full_backup') return it.trigger === 'scheduled' ? 'نسخ مجدول' : 'نسخ احتياطي يدوي'
  if (it.kind === 'pre_restore') return 'نسخة أمان قبل الاسترجاع'
  if (it.kind === 'restore') return 'استرجاع'
  if (it.kind === 'delete') return 'حذف نسخة'
  return it.kind || '—'
}

function kindLabel(it) {
  if (it.kind === 'pre_restore') return 'نسخة أمان قبل الاسترجاع'
  if (it.kind === 'full_backup') return it.trigger === 'scheduled' ? 'كاملة (مجدولة)' : 'كاملة (يدوية)'
  return it.kind || '—'
}

function byLabel(by) {
  if (by === 'scheduler') return 'الجدولة التلقائية'
  if (by === 'superadmin') return 'السوبر أدمن'
  return by || '—'
}

// ── عناصر واجهة صغيرة ──────────────────────────────────────────
const btnBase = { border: 'none', borderRadius: 8, padding: '8px 16px', fontSize: 13, fontWeight: 700, fontFamily: FONT }
function btnStyle(kind, disabled) {
  const kinds = {
    primary: { background: C.primary, color: '#fff' },
    ghost: { background: 'transparent', color: C.muted, border: `1px solid ${C.border}` },
    outline: { background: 'transparent', color: C.primary, border: `1px solid ${C.primary}` },
    danger: { background: C.redBg, color: C.red, border: `1px solid ${C.redBr}` },
    dangerSolid: { background: '#DC2626', color: '#fff' },
    warn: { background: C.amberBg, color: C.amber, border: `1px solid ${C.amberBr}` },
  }
  return { ...btnBase, ...kinds[kind], cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.55 : 1 }
}
const smallBtn = { padding: '5px 10px', fontSize: 12 }

function Badge({ color = 'gray', children }) {
  const map = {
    green: [C.greenBg, C.green, C.greenBr], red: [C.redBg, C.red, C.redBr],
    amber: [C.amberBg, C.amber, C.amberBr], gray: ['#0F172A', C.muted, C.border], blue: ['#1E1B4B', '#A5B4FC', '#3730A3'],
  }
  const [bg, fg, br] = map[color] || map.gray
  return (
    <span style={{ background: bg, color: fg, border: `1px solid ${br}`, borderRadius: 999, padding: '2px 10px', fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' }}>
      {children}
    </span>
  )
}

function Banner({ kind, children, onClose, action }) {
  const map = {
    ok: [C.greenBg, C.greenBr, C.green], err: [C.redBg, C.redBr, C.red], warn: [C.amberBg, C.amberBr, C.amber], info: ['#0B1B3A', '#1E3A8A', '#93C5FD'],
  }
  const [bg, br, fg] = map[kind] || map.info
  return (
    <div role={kind === 'err' ? 'alert' : 'status'} style={{ background: bg, border: `1px solid ${br}`, borderRadius: 8, padding: '12px 16px', color: fg, fontSize: 14, marginBottom: 16, display: 'flex', gap: 12, alignItems: 'flex-start', justifyContent: 'space-between' }}>
      <div style={{ flex: 1, minWidth: 0, lineHeight: 1.7 }}>{children}</div>
      <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
        {action}
        {onClose && <button onClick={onClose} aria-label="إغلاق" style={{ background: 'transparent', border: 'none', color: fg, cursor: 'pointer', fontSize: 16, lineHeight: 1 }}>×</button>}
      </div>
    </div>
  )
}

function Section({ title, actions, children }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden', marginBottom: 24 }}>
      <div style={{ padding: '14px 20px', borderBottom: `1px solid ${C.border}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ color: C.text, fontWeight: 700 }}>{title}</span>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{actions}</div>
      </div>
      {children}
    </div>
  )
}

function Placeholder({ children }) {
  return <div style={{ padding: 36, textAlign: 'center', color: C.dim, fontSize: 14 }}>{children}</div>
}

function SectionError({ message, onRetry }) {
  return (
    <div style={{ padding: 28, textAlign: 'center' }}>
      <div style={{ color: C.red, fontSize: 14, marginBottom: 12 }}>{message}</div>
      <button onClick={onRetry} style={btnStyle('outline')}>إعادة المحاولة</button>
    </div>
  )
}

const th = { textAlign: 'right', padding: '10px 14px', color: C.muted, fontSize: 12, fontWeight: 700, borderBottom: `1px solid ${C.border}`, whiteSpace: 'nowrap' }
const td = { padding: '10px 14px', color: C.text, fontSize: 13, borderBottom: `1px solid ${C.border}`, verticalAlign: 'middle' }
const ltr = { direction: 'ltr', unicodeBidi: 'embed', fontFamily: 'ui-monospace, Menlo, Consolas, monospace' }

// نافذة داكنة بسيطة (Escape/خلفية تغلق ما لم تكن locked)
function DarkDialog({ title, onClose, locked, width = 560, children }) {
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape' && !locked) onClose() }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [locked, onClose])
  return (
    <div onMouseDown={(e) => { if (e.target === e.currentTarget && !locked) onClose() }}
      style={{ position: 'fixed', inset: 0, background: 'rgba(2,6,23,.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
      <div role="dialog" aria-modal="true" aria-label={title}
        style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 14, width: '100%', maxWidth: width, maxHeight: '92vh', overflowY: 'auto', boxSizing: 'border-box', direction: 'rtl', fontFamily: FONT }}>
        <div style={{ padding: '16px 20px', borderBottom: `1px solid ${C.border}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ color: C.text, fontWeight: 800, fontSize: 16 }}>{title}</span>
          {!locked && <button onClick={onClose} aria-label="إغلاق" style={{ background: 'transparent', border: 'none', color: C.muted, fontSize: 20, cursor: 'pointer' }}>×</button>}
        </div>
        <div style={{ padding: 20 }}>{children}</div>
      </div>
    </div>
  )
}

function InfoRow({ label, children }) {
  if (children == null || children === '' || children === false) return null
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '5px 0', fontSize: 13, flexWrap: 'wrap' }}>
      <span style={{ color: C.muted }}>{label}</span>
      <span style={{ color: C.text, textAlign: 'left', minWidth: 0, wordBreak: 'break-all' }}>{children}</span>
    </div>
  )
}

// ── دليل استرجاع PostgreSQL اليدوي ────────────────────────────
// احتياطي فقط لو فشل تحميل /restore-instructions (المصدر الأساسي هو الخادم)
const FALLBACK_PG = {
  steps_ar: [
    'أوقف تطبيق WarshERP.',
    'نزّل ملف النسخة (.dump) من جدول النسخ المتاحة.',
    'خذ نسخة جديدة من القاعدة الحالية قبل أي شيء.',
    'أنشئ قاعدة فارغة أو استخدم --clean عند الاسترجاع.',
    'نفّذ أمر pg_restore بالأسفل بعد استبدال القيم.',
    'شغّل التطبيق وتحقق من تسجيل الدخول والأرصدة.',
  ],
  commands: [
    { label_ar: 'استرجاع النسخة', command: 'pg_restore --clean --if-exists --no-owner --no-privileges -h <HOST> -U <DB_USER> -d <DB_NAME> <BACKUP_FILE>.dump' },
  ],
  warnings_ar: [],
}

function CopyBlock({ label, command }) {
  const [state, setState] = useState('')
  async function copy() {
    try {
      await navigator.clipboard.writeText(command)
      setState('تم النسخ')
    } catch {
      setState('تعذر النسخ — حدّد النص يدوياً')
    }
    setTimeout(() => setState(''), 2500)
  }
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ color: C.muted, fontSize: 12, marginBottom: 4 }}>{label}</div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'stretch', flexWrap: 'wrap' }}>
        <code style={{ ...ltr, flex: 1, minWidth: 200, background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, padding: '8px 12px', color: '#A5B4FC', fontSize: 12, userSelect: 'all', overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{command}</code>
        <button onClick={copy} style={{ ...btnStyle('ghost'), ...smallBtn }}>{state || '📋 نسخ'}</button>
      </div>
    </div>
  )
}

function PgManualRestoreGuide({ info, failed }) {
  const data = info || FALLBACK_PG
  return (
    <Section title="استرجاع PostgreSQL يدوي">
      <div style={{ padding: 20 }}>
        <Banner kind="warn">
          لا يتم استرجاع PostgreSQL تلقائياً من داخل النظام لحمايتك من فقدان البيانات — اتبع الخطوات التالية يدوياً على الخادم.
          {failed && <div style={{ fontSize: 12, marginTop: 4 }}>(تعذّر تحميل الدليل من الخادم — هذه نسخة عامة)</div>}
        </Banner>
        <ol style={{ color: C.text, fontSize: 14, lineHeight: 2, paddingInlineStart: 22, margin: '0 0 16px' }}>
          {(data.steps_ar || []).map((s, i) => <li key={i}>{s}</li>)}
        </ol>
        {(data.commands || []).map((c, i) => <CopyBlock key={i} label={c.label_ar} command={c.command} />)}
        {(data.warnings_ar || []).map((w, i) => (
          <div key={i} style={{ color: C.amber, fontSize: 13, marginTop: 6 }}>⚠ {w}</div>
        ))}
      </div>
    </Section>
  )
}

// ── كارت الحالة ────────────────────────────────────────────────
function StatusCard({ st, state, onRetry }) {
  if (!st) {
    if (state.error) return <Section title="حالة النسخ الاحتياطي"><SectionError message={state.error} onRetry={onRetry} /></Section>
    return <Section title="حالة النسخ الاحتياطي"><Placeholder>جاري التحميل...</Placeholder></Section>
  }
  const levelKind = st.level === 'ok' ? 'ok' : st.level === 'warning' ? 'warn' : st.level === 'critical' ? 'err' : 'info'
  const ls = st.last_success
  const engineLabel = st.db_engine === 'postgresql' ? 'PostgreSQL' : st.db_engine === 'sqlite' ? 'SQLite' : st.db_engine
  return (
    <Section title="حالة النسخ الاحتياطي" actions={state.error ? <Badge color="amber">آخر تحديث فشل</Badge> : null}>
      <div style={{ padding: 20 }}>
        {st.message_ar && <Banner kind={levelKind}>{st.message_ar}</Banner>}
        {st.tool?.pg_dump_available === false && (
          <Banner kind="err">
            أداة pg_dump غير متوفرة على الخادم — لا يمكن إنشاء نسخ PostgreSQL. ثبّت أدوات PostgreSQL Client
            {st.tool.pg_dump_path_hint ? <> أو اضبط المسار الصحيح (<span style={ltr}>{st.tool.pg_dump_path_hint}</span>)</> : null}.
          </Banner>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16 }}>
          <div style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 10, padding: '12px 16px' }}>
            <div style={{ color: C.muted, fontSize: 12, fontWeight: 700, marginBottom: 6 }}>آخر نسخة ناجحة</div>
            {ls ? (
              <>
                <InfoRow label="التاريخ">{fmtDateTime(ls.finished_at)}</InfoRow>
                <InfoRow label="منذ">{fmtAge(st.age_hours)}</InfoRow>
                <InfoRow label="الحجم">{formatBytes(ls.size_bytes)}</InfoRow>
                <InfoRow label="الملف"><span style={ltr}>{ls.file_name}</span></InfoRow>
              </>
            ) : <div style={{ color: C.red, fontSize: 13 }}>لا توجد نسخة ناجحة حتى الآن</div>}
            {st.last_failure && (
              <div style={{ marginTop: 8, paddingTop: 8, borderTop: `1px dashed ${C.border}`, color: C.red, fontSize: 12, lineHeight: 1.7 }}>
                آخر فشل: {fmtDateTime(st.last_failure.finished_at)}{st.last_failure.error ? ` — ${st.last_failure.error}` : ''}
              </div>
            )}
          </div>
          <div style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 10, padding: '12px 16px' }}>
            <div style={{ color: C.muted, fontSize: 12, fontWeight: 700, marginBottom: 6 }}>الإعدادات (للقراءة فقط — تُضبط من متغيرات بيئة الخادم)</div>
            <InfoRow label="قاعدة البيانات"><Badge color="blue">{engineLabel}</Badge></InfoRow>
            {st.auto_enabled != null && (
              <InfoRow label="الجدولة التلقائية">
                {st.auto_enabled ? `مفعّلة — كل ${st.interval_hours ?? 24} ساعة` : 'معطّلة'}
              </InfoRow>
            )}
            {st.retention != null && <InfoRow label="الاحتفاظ">{`آخر ${st.retention} نسخة`}</InfoRow>}
            {st.keep_pre_restore != null && <InfoRow label="نسخ الأمان قبل الاسترجاع">{`آخر ${st.keep_pre_restore} نسخة`}</InfoRow>}
            {st.stale_hours != null && <InfoRow label="تحذير التقادم بعد">{`${st.stale_hours} ساعة`}</InfoRow>}
            {st.tool?.pg_dump_available != null && (
              <InfoRow label="أداة pg_dump">
                <Badge color={st.tool.pg_dump_available ? 'green' : 'red'}>{st.tool.pg_dump_available ? 'متوفرة' : 'غير متوفرة'}</Badge>
              </InfoRow>
            )}
            {st.backup_dir && <InfoRow label="مجلد النسخ"><span style={ltr}>{st.backup_dir}</span></InfoRow>}
          </div>
        </div>
        <div style={{ marginTop: 16, color: C.muted, fontSize: 12.5, lineHeight: 1.9 }}>
          <div>⚠ النسخ المحفوظة على نفس القرص <b>لا تحمي</b> من فقدان القرص نفسه — وجّه متغير <span style={ltr}>ERP_BACKUP_DIR</span> إلى قرص أو موقع آخر، وحمّل نسخة دورياً إلى مكان خارجي.</div>
          <div>🔒 النسخة لا تحتوي على <span style={ltr}>jwt_secret.key</span> ولا على متغيرات البيئة، لكنها <b>تحتوي على بيانات كل الورش وكلمات المرور المشفّرة (hash)</b> — احتفظ بها في مكان آمن.</div>
        </div>
      </div>
    </Section>
  )
}

// ── نافذة حذف النسخة ──────────────────────────────────────────
function DeleteDialog({ item, onlyRestorable, busy, onCancel, onConfirm }) {
  return (
    <DarkDialog title="حذف النسخة؟ لن يمكن التراجع" onClose={onCancel} locked={busy} width={480}>
      <div style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 10, padding: '10px 14px', marginBottom: 14 }}>
        <InfoRow label="الملف"><span style={ltr}>{item.file_name}</span></InfoRow>
        <InfoRow label="الحجم">{formatBytes(item.size_bytes)}</InfoRow>
        <InfoRow label="التاريخ">{fmtDateTime(item.finished_at || item.started_at)}</InfoRow>
      </div>
      {onlyRestorable && (
        <div style={{ color: C.red, fontSize: 13, marginBottom: 14, lineHeight: 1.7 }}>
          ⛔ هذه آخر نسخة قابلة للاسترجاع موجودة — بعد حذفها لن تتوفر لديك أي نسخة للاسترجاع منها.
        </div>
      )}
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-start', flexWrap: 'wrap' }}>
        <button onClick={onConfirm} disabled={busy} style={btnStyle('dangerSolid', busy)}>{busy ? 'جاري الحذف...' : 'حذف نهائي'}</button>
        <button onClick={onCancel} disabled={busy} style={btnStyle('ghost', busy)}>إلغاء</button>
      </div>
    </DarkDialog>
  )
}

// ── نافذة الاسترجاع (3 خطوات) ──────────────────────────────────
function RestoreDialog({ item, onClose }) {
  const [step, setStep] = useState(1)          // 1 تحذير | 2 تأكيد | 3 تنفيذ/نتيجة
  const [prep, setPrep] = useState({ loading: true, error: null, data: null })
  const [ack, setAck] = useState(false)
  const [input, setInput] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)  // {ok, data} | {ok:false, error, ambiguous, status}
  const firedRef = useRef(false)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const doPrepare = useCallback(async () => {
    setPrep({ loading: true, error: null, data: null })
    try {
      const data = await saBackupApi.prepareRestore(item.id)
      if (alive.current) setPrep({ loading: false, error: null, data })
    } catch (e) {
      if (alive.current) setPrep({ loading: false, error: e.message || 'تعذر تجهيز الاسترجاع', data: null })
    }
  }, [item.id])

  useEffect(() => { doPrepare() }, [doPrepare])

  const expected = prep.data?.confirm_text || RESTORE_CONFIRM_TEXT
  const canRun = !!prep.data && ack && input.trim() === expected && !running

  async function run() {
    if (!canRun || firedRef.current) return
    firedRef.current = true
    setStep(3); setRunning(true); setResult(null)
    try {
      const data = await saBackupApi.restore(item.id, prep.data.confirm_token, input.trim())
      if (alive.current) setResult({ ok: true, data })
    } catch (e) {
      if (alive.current) setResult({ ok: false, error: e.message, status: e.status, ambiguous: !!e.network })
    } finally {
      firedRef.current = false
      if (alive.current) setRunning(false)
    }
  }

  function backToStart() {
    setResult(null); setStep(1); setAck(false); setInput('')
    doPrepare() // التوكن يُستهلك مرة واحدة — نجهّز توكن جديد
  }

  const b = prep.data?.backup
  const done = !!result
  const closeAndRefresh = () => onClose(done, !!result?.ok)

  return (
    <DarkDialog title="استرجاع نسخة احتياطية" onClose={closeAndRefresh} locked={running} width={620}>
      {step === 1 && (
        <>
          <Banner kind="err">
            هذا الإجراء يستبدل قاعدة البيانات الحالية بالكامل بمحتوى النسخة التالية. لا يمكن التراجع إلا من خلال نسخة الأمان التي تُؤخذ تلقائياً قبل الاسترجاع.
          </Banner>
          <div style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 10, padding: '10px 14px', marginBottom: 14 }}>
            <InfoRow label="الملف"><span style={ltr}>{b?.file_name || item.file_name}</span></InfoRow>
            <InfoRow label="التاريخ">{fmtDateTime(b?.created_at || item.finished_at || item.started_at)}</InfoRow>
            <InfoRow label="الحجم">{formatBytes(b?.size_bytes ?? item.size_bytes)}</InfoRow>
            <InfoRow label="SHA-256"><span style={{ ...ltr, fontSize: 11 }}>{b?.checksum_sha256 || item.checksum_sha256}</span></InfoRow>
            <InfoRow label="فحص السلامة">
              {item.integrity_ok === true ? <Badge color="green">✓ سليمة</Badge> : item.integrity_ok === false ? <Badge color="red">✗ تالفة</Badge> : '—'}
            </InfoRow>
          </div>
          {prep.loading && <Placeholder>جاري فحص النسخة وتجهيز الاسترجاع...</Placeholder>}
          {prep.error && (
            <Banner kind="err" action={<button onClick={doPrepare} style={{ ...btnStyle('outline'), ...smallBtn }}>إعادة المحاولة</button>}>
              {prep.error}
            </Banner>
          )}
          {prep.data && (
            <ul style={{ color: C.red, fontSize: 13.5, lineHeight: 1.9, paddingInlineStart: 20, margin: '0 0 16px' }}>
              {(prep.data.warnings_ar || []).map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          )}
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <button onClick={() => setStep(2)} disabled={!prep.data} style={btnStyle('danger', !prep.data)}>متابعة</button>
            <button onClick={closeAndRefresh} style={btnStyle('ghost')}>إلغاء</button>
          </div>
        </>
      )}

      {step === 2 && (
        <>
          <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', color: C.text, fontSize: 14, lineHeight: 1.7, marginBottom: 16, cursor: 'pointer' }}>
            <input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} style={{ marginTop: 6 }} />
            <span>أفهم أن البيانات الحالية (كل الورش والمستخدمين وكلمات المرور وسجل التدقيق والموافقات) ستُستبدل بمحتوى النسخة، وأن الجميع سيحتاجون لتسجيل الدخول من جديد.</span>
          </label>
          <label style={{ color: C.muted, fontSize: 12, fontWeight: 700, display: 'block', marginBottom: 6 }}>
            اكتب <span style={{ ...ltr, color: C.red }}>{expected}</span> للتأكيد
          </label>
          <input value={input} onChange={e => setInput(e.target.value)} autoFocus autoComplete="off" spellCheck={false} dir="ltr"
            onKeyDown={e => { if (e.key === 'Enter') run() }}
            style={{ width: '100%', boxSizing: 'border-box', background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, padding: '10px 14px', color: C.text, fontSize: 14, outline: 'none', marginBottom: 16, ...ltr }}
            placeholder={expected} />
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <button onClick={run} disabled={!canRun} style={btnStyle('dangerSolid', !canRun)}>تنفيذ الاسترجاع</button>
            <button onClick={() => setStep(1)} style={btnStyle('ghost')}>رجوع</button>
          </div>
        </>
      )}

      {step === 3 && (
        <>
          {running && (
            <div style={{ textAlign: 'center', padding: '28px 0', color: C.text }}>
              <div style={{ fontSize: 34, marginBottom: 10 }}>⏳</div>
              <div style={{ fontWeight: 700, marginBottom: 6 }}>جاري الاسترجاع...</div>
              <div style={{ color: C.muted, fontSize: 13 }}>لا تغلق هذه النافذة ولا تعد تحميل الصفحة. قد يستغرق الأمر دقائق.</div>
            </div>
          )}
          {result?.ok && (
            <>
              <Banner kind="ok">
                <div style={{ fontWeight: 800, marginBottom: 4 }}>تم الاسترجاع بنجاح</div>
                {result.data?.message && <div>{result.data.message}</div>}
                {result.data?.restored_from?.file_name && <div>المسترجَع منه: <span style={ltr}>{result.data.restored_from.file_name}</span></div>}
                {result.data?.safety_backup?.file_name && <div>نسخة الأمان: <span style={ltr}>{result.data.safety_backup.file_name}</span></div>}
              </Banner>
              <div style={{ color: C.amber, fontSize: 13.5, marginBottom: 16, lineHeight: 1.8 }}>
                سجّل الدخول من جديد في كل الورش — المستخدمون وكلمات المرور رجعت لحالة النسخة، فالجلسات الحالية تحتاج إعادة دخول.
              </div>
              <button onClick={closeAndRefresh} style={btnStyle('primary')}>إغلاق وتحديث</button>
            </>
          )}
          {result && !result.ok && (
            <>
              <Banner kind="err">
                <div style={{ fontWeight: 800, marginBottom: 4 }}>{result.ambiguous ? 'انقطع الاتصال أثناء الاسترجاع' : 'لم يكتمل الاسترجاع'}</div>
                <div>{result.error}</div>
                {result.ambiguous && <div style={{ marginTop: 6 }}>قد تكون العملية ما زالت جارية أو اكتملت على الخادم — تحقق من «سجل العمليات» بعد التحديث قبل إعادة المحاولة.</div>}
              </Banner>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {!result.ambiguous && <button onClick={backToStart} style={btnStyle('outline')}>رجوع وإعادة التجهيز</button>}
                <button onClick={closeAndRefresh} style={btnStyle('ghost')}>إغلاق وتحديث</button>
              </div>
            </>
          )}
        </>
      )}
    </DarkDialog>
  )
}

// ── الصفحة ─────────────────────────────────────────────────────
const LOG_PAGE = 20

export default function SuperAdminBackups() {
  const [statusState, setStatusState] = useState({ data: null, loading: true, error: null })
  const [listState, setListState] = useState({ data: null, loading: true, error: null })
  const [pgInfo, setPgInfo] = useState({ data: null, failed: false })
  const [notice, setNotice] = useState(null)       // {kind:'ok'|'err'|'warn', text}
  const [busyAction, setBusyAction] = useState(null)
  const [downloadingId, setDownloadingId] = useState(null)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [restoreTarget, setRestoreTarget] = useState(null)
  const [logLimit, setLogLimit] = useState(LOG_PAGE)
  const busyRef = useRef(null)
  const alive = useRef(true)

  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const reload = useCallback(async () => {
    setStatusState(s => ({ ...s, loading: !s.data, error: null }))
    setListState(s => ({ ...s, loading: !s.data, error: null }))
    const [st, ls] = await Promise.allSettled([saBackupApi.status(), saBackupApi.list(200)])
    if (!alive.current) return
    setStatusState(s => st.status === 'fulfilled'
      ? { data: st.value, loading: false, error: null }
      : { data: s.data, loading: false, error: st.reason?.message || 'تعذر تحميل حالة النسخ الاحتياطي' })
    setListState(s => ls.status === 'fulfilled'
      ? { data: ls.value, loading: false, error: null }
      : { data: s.data, loading: false, error: ls.reason?.message || 'تعذر تحميل قائمة النسخ' })
  }, [])

  useEffect(() => { reload() }, [reload])

  const st = statusState.data
  const items = listState.data || []

  // دليل PostgreSQL اليدوي
  const manual = st?.restore_mode === 'manual'
  useEffect(() => {
    if (!manual) return
    let cancelled = false
    saBackupApi.instructions()
      .then(d => { if (!cancelled) setPgInfo({ data: d, failed: false }) })
      .catch(() => { if (!cancelled) setPgInfo({ data: null, failed: true }) })
    return () => { cancelled = true }
  }, [manual])

  // متابعة العمليات الجارية (مثلاً الجدولة): كل 8 ثواني بدون إزعاج عند إخفاء التبويب
  const running = !!st?.running || items.some(i => i.status === 'running')
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => {
      if (document.hidden || busyRef.current) return
      saBackupApi.status().then(d => alive.current && setStatusState({ data: d, loading: false, error: null })).catch(() => {})
      saBackupApi.list(200).then(d => alive.current && setListState({ data: d, loading: false, error: null })).catch(() => {})
    }, 8000)
    return () => clearInterval(t)
  }, [running])

  async function runAction(name, fn) {
    if (busyRef.current) return false
    busyRef.current = name
    setBusyAction(name)
    setNotice(null)
    try { return await fn() }
    finally {
      busyRef.current = null
      if (alive.current) setBusyAction(null)
    }
  }

  async function doCreate() {
    await runAction('create', async () => {
      try {
        const run = await saBackupApi.create()
        setNotice({ kind: 'ok', text: `تم إنشاء النسخة الاحتياطية${run?.file_name ? `: ${run.file_name}` : ''}` })
      } catch (e) {
        setNotice(e.network
          ? { kind: 'warn', text: 'انقطع الاتصال أثناء إنشاء النسخة — قد تكون العملية ما زالت جارية على الخادم. راجع «سجل العمليات» قبل إعادة المحاولة.' }
          : { kind: 'err', text: e.message })
      }
      await reload()
    })
  }

  async function doDelete() {
    const item = deleteTarget
    if (!item) return
    await runAction(`delete:${item.id}`, async () => {
      try {
        const r = await saBackupApi.remove(item.id)
        setNotice({ kind: 'ok', text: r?.message || 'تم حذف النسخة' })
      } catch (e) {
        setNotice({ kind: 'err', text: e.message })
      }
      setDeleteTarget(null)
      await reload()
    })
  }

  async function doVerify(item) {
    await runAction(`verify:${item.id}`, async () => {
      try {
        const r = await saBackupApi.verify(item.id)
        setNotice(r?.ok
          ? { kind: 'ok', text: `الملف سليم: ${item.file_name} — الـ checksum مطابق وفحص السلامة ناجح (${formatBytes(r.size_bytes)})` }
          : { kind: 'err', text: `فحص الملف فشل: ${item.file_name} — ${r?.checksum_match === false ? 'الـ checksum غير مطابق. ' : ''}${r?.integrity && r.integrity !== 'ok' ? r.integrity : ''}` })
      } catch (e) {
        setNotice({ kind: 'err', text: e.message })
        await reload()
      }
    })
  }

  async function doDownload(item) {
    if (downloadingId != null) return
    if ((item.size_bytes || 0) > BIG_DOWNLOAD_BYTES &&
        !window.confirm(`حجم الملف ${formatBytes(item.size_bytes)} — التنزيل داخل المتصفح قد يستهلك ذاكرة كبيرة. متابعة؟`)) return
    setDownloadingId(item.id)
    setNotice(null)
    try {
      await saBackupApi.download(item.id, item.file_name)
    } catch (e) {
      setNotice({ kind: 'err', text: e.message })
      if (e.status === 404 || e.status === 410) reload()
    } finally {
      if (alive.current) setDownloadingId(null)
    }
  }

  const available = items.filter(i =>
    i.file_name && !i.file_deleted && (i.kind === 'full_backup' || i.kind === 'pre_restore') && i.status === 'success')
  const restorableCount = items.filter(i => i.restorable).length
  const pgDumpMissing = st?.tool?.pg_dump_available === false
  const creating = busyAction === 'create'
  const anyBusy = !!busyAction
  const createDisabled = anyBusy || pgDumpMissing || !!st?.running

  return (
    <div style={{ direction: 'rtl', fontFamily: FONT }}>
      {notice && <Banner kind={notice.kind} onClose={() => setNotice(null)}>{notice.kind === 'ok' ? '✅ ' : notice.kind === 'err' ? '❌ ' : '⚠ '}{notice.text}</Banner>}

      <StatusCard st={st} state={statusState} onRetry={reload} />

      {st?.running && !creating && (
        <Banner kind="info">توجد عملية نسخ أو استرجاع جارية الآن على الخادم — سيتم تحديث الحالة تلقائياً.</Banner>
      )}

      {manual && <PgManualRestoreGuide info={pgInfo.data} failed={pgInfo.failed} />}

      <Section
        title="النسخ المتاحة"
        actions={
          <>
            <button onClick={doCreate} disabled={createDisabled}
              title={pgDumpMissing ? 'أداة pg_dump غير متوفرة على الخادم' : undefined}
              style={btnStyle('primary', createDisabled)}>
              {creating ? 'جاري إنشاء النسخة... قد يستغرق دقائق' : '+ نسخة احتياطية جديدة'}
            </button>
            <button onClick={reload} disabled={anyBusy} style={btnStyle('outline', anyBusy)}>↻ تحديث</button>
          </>
        }>
        {listState.loading && !listState.data ? <Placeholder>جاري التحميل...</Placeholder>
          : listState.error && !listState.data ? <SectionError message={listState.error} onRetry={reload} />
          : (
            <>
              {listState.error && <div style={{ padding: '10px 20px', color: C.amber, fontSize: 12 }}>تعذر تحديث القائمة ({listState.error}) — المعروض آخر بيانات تم تحميلها.</div>}
              {available.length === 0 ? <Placeholder>لا توجد نسخ احتياطية بعد — اضغط «+ نسخة احتياطية جديدة»</Placeholder> : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', minWidth: 780, borderCollapse: 'collapse' }}>
                    <thead>
                      <tr>
                        <th style={th}>الملف</th><th style={th}>النوع</th><th style={th}>الحجم</th><th style={th}>التاريخ</th>
                        <th style={th}>بواسطة</th><th style={th}>السلامة</th><th style={th}>إجراءات</th>
                      </tr>
                    </thead>
                    <tbody>
                      {available.map(it => (
                        <tr key={it.id}>
                          <td style={{ ...td, ...ltr, fontSize: 12 }}>{it.file_name}{!it.file_exists && <> <Badge color="red">الملف مفقود</Badge></>}</td>
                          <td style={td}><Badge color={it.kind === 'pre_restore' ? 'amber' : 'blue'}>{kindLabel(it)}</Badge></td>
                          <td style={td}>{formatBytes(it.size_bytes)}</td>
                          <td style={{ ...td, whiteSpace: 'nowrap' }}>{fmtDateTime(it.finished_at || it.started_at)}</td>
                          <td style={td}>{byLabel(it.created_by)}</td>
                          <td style={td}>
                            {it.integrity_ok === true ? <Badge color="green">✓ سليمة</Badge>
                              : it.integrity_ok === false ? <Badge color="red">✗ تالفة</Badge> : <span style={{ color: C.dim }}>—</span>}
                          </td>
                          <td style={td}>
                            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                              <button disabled={!it.downloadable || downloadingId != null} onClick={() => doDownload(it)}
                                style={{ ...btnStyle('outline', !it.downloadable || downloadingId != null), ...smallBtn }}>
                                {downloadingId === it.id ? '...' : '⬇ تنزيل'}
                              </button>
                              <button disabled={anyBusy || !it.file_exists} onClick={() => doVerify(it)}
                                style={{ ...btnStyle('ghost', anyBusy || !it.file_exists), ...smallBtn }}>
                                {busyAction === `verify:${it.id}` ? '...' : '🔍 فحص'}
                              </button>
                              {!manual && it.restorable && (
                                <button disabled={anyBusy} onClick={() => setRestoreTarget(it)}
                                  style={{ ...btnStyle('warn', anyBusy), ...smallBtn }}>♻ استرجاع</button>
                              )}
                              <button disabled={anyBusy} onClick={() => setDeleteTarget(it)}
                                style={{ ...btnStyle('danger', anyBusy), ...smallBtn }}>🗑 حذف</button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
      </Section>

      <Section title="سجل العمليات">
        {listState.loading && !listState.data ? <Placeholder>جاري التحميل...</Placeholder>
          : listState.error && !listState.data ? <SectionError message={listState.error} onRetry={reload} />
          : items.length === 0 ? <Placeholder>لا توجد عمليات مسجلة</Placeholder> : (
            <>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', minWidth: 760, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      <th style={th}>العملية</th><th style={th}>الحالة</th><th style={th}>بواسطة</th>
                      <th style={th}>البداية</th><th style={th}>المدة</th><th style={th}>التفاصيل</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.slice(0, logLimit).map(it => {
                      const [sl, sc] = STATUS_LABEL[it.status] || [it.status, 'gray']
                      const pruned = !!it.details?.pruned_by
                      const detail = it.error || (pruned ? 'حُذفت تلقائياً حسب سياسة الاحتفاظ' : '') || it.file_name || ''
                      const isFile = !it.error && !pruned && !!it.file_name
                      return (
                        <tr key={it.id}>
                          <td style={td}>{opLabel(it)}</td>
                          <td style={td}><Badge color={sc}>{sl}</Badge></td>
                          <td style={td}>{byLabel(it.created_by)}</td>
                          <td style={{ ...td, whiteSpace: 'nowrap' }}>{fmtDateTime(it.started_at)}</td>
                          <td style={td}>{it.status === 'running' ? '—' : fmtDuration(it.started_at, it.finished_at)}</td>
                          <td style={{ ...td, color: it.error ? C.red : C.muted, fontSize: 12, wordBreak: 'break-word' }}>
                            <span style={isFile ? ltr : undefined}>{detail}</span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              {items.length > logLimit && (
                <div style={{ padding: 14, textAlign: 'center' }}>
                  <button onClick={() => setLogLimit(l => l + LOG_PAGE)} style={btnStyle('ghost')}>عرض المزيد</button>
                </div>
              )}
            </>
          )}
      </Section>

      {deleteTarget && (
        <DeleteDialog
          item={deleteTarget}
          onlyRestorable={!!deleteTarget.restorable && restorableCount <= 1}
          busy={busyAction === `delete:${deleteTarget.id}`}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={doDelete}
        />
      )}

      {restoreTarget && (
        <RestoreDialog
          item={restoreTarget}
          onClose={(didRun, ok) => {
            setRestoreTarget(null)
            if (didRun) {
              setNotice(ok ? { kind: 'ok', text: 'تم الاسترجاع بنجاح — سجّل الدخول من جديد في كل الورش.' } : null)
              reload()
            }
          }}
        />
      )}
    </div>
  )
}
