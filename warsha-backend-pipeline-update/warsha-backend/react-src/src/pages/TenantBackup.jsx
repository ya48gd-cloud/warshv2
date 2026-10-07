/**
 * النسخ الاحتياطي — تصدير بيانات الورشة (للمدير فقط)
 *
 * ملف تصدير للأرشفة والنقل (JSON / ZIP) لبيانات ورشتك أنت فقط، بدون كلمات المرور أو الأسرار.
 * ليس نسخة قابلة للاسترجاع التلقائي — النسخ الكاملة والاسترجاع من اختصاص مدير النظام.
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useToast } from '../store/toast'
import { useAuth } from '../store/auth'
import api from '../api/client'
import { PageSpinner } from '../components/ui/Spinner'

const FORMATS = [
  { value: 'zip',  label: 'ZIP مضغوط — ملف JSON لكل جدول (موصى به)' },
  { value: 'json', label: 'ملف JSON واحد' },
]

const STATUS = {
  success: ['ناجح', 'badge-green'],
  failed:  ['فشل',  'badge-red'],
  running: ['جارٍ', 'badge-amber'],
}

function formatBytes(n) {
  if (n == null || isNaN(n)) return '—'
  // LRM حتى لا يُعرض "1.4 MB" معكوساً داخل نص عربي RTL
  if (n < 1024) return `\u200E${n} B`
  const units = ['KB', 'MB', 'GB']
  let v = n / 1024, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `\u200E${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`
}

// الخادم بيرجّع ISO مع Z؛ لو جاء بدونها نفترض UTC
function fmtDateTime(s) {
  if (!s) return '—'
  let str = String(s)
  if (!/([zZ]|[+-]\d\d:?\d\d)$/.test(str)) str = str.replace(' ', 'T') + 'Z'
  const d = new Date(str)
  if (isNaN(d.getTime())) return '—'
  try {
    return new Intl.DateTimeFormat('ar-EG-u-nu-latn', { dateStyle: 'medium', timeStyle: 'short' }).format(d)
  } catch {
    return d.toLocaleString()
  }
}

function totalRows(tables) {
  if (!tables || typeof tables !== 'object') return null
  const vals = Object.values(tables).map(Number).filter(Number.isFinite)
  return vals.length ? vals.reduce((a, b) => a + b, 0) : 0
}

export default function TenantBackup() {
  const toast = useToast()
  const { user: me } = useAuth()
  const [format, setFormat] = useState('zip')
  const [exporting, setExporting] = useState(false)
  const [runs, setRuns] = useState({ data: null, loading: true, error: null })
  const exportingRef = useRef(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const isAdmin = me?.role === 'admin'

  const loadRuns = useCallback(async () => {
    setRuns(s => ({ ...s, loading: !s.data, error: null }))
    try {
      const data = await api.backup.tenantRuns(20)
      if (alive.current) setRuns({ data, loading: false, error: null })
    } catch (err) {
      if (alive.current) setRuns(s => ({ data: s.data, loading: false, error: err?.message || 'تعذّر تحميل سجل التصدير' }))
    }
  }, [])

  useEffect(() => { if (isAdmin) loadRuns() }, [isAdmin, loadRuns])

  const doExport = async () => {
    if (exportingRef.current) return  // حماية من الضغط المزدوج
    exportingRef.current = true
    setExporting(true)
    try {
      await api.backup.exportTenant(format)
      toast('تم تجهيز الملف وبدأ التنزيل')
    } catch (err) {
      // 409 = تصدير جارٍ بالفعل، 403 = صلاحية، شبكة = رسالة الاتصال — نعرض رسالة الخادم كما هي
      toast(err?.message || 'فشل تصدير البيانات', 'error')
    } finally {
      exportingRef.current = false
      if (alive.current) setExporting(false)
      loadRuns()
    }
  }

  if (!isAdmin) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-erp-muted">
        <span className="text-5xl">🔒</span>
        <p className="font-medium">هذه الصفحة للمديرين فقط</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="card flex flex-col gap-4">
        <div className="text-sm font-semibold">تصدير بيانات الورشة</div>
        <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 items-end">
          <div>
            <label className="field-label">صيغة الملف</label>
            <select className="field-input w-full" value={format}
                    onChange={e => setFormat(e.target.value)} disabled={exporting}>
              {FORMATS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
            </select>
          </div>
          <div>
            <button className="btn btn-primary" onClick={doExport} disabled={exporting}>
              {exporting ? '… جاري تجهيز الملف' : '⬇ تصدير بيانات الورشة'}
            </button>
          </div>
        </div>
        {exporting && (
          <div className="text-xs text-erp-muted">
            جاري تجميع بياناتك — قد يستغرق الأمر بعض الوقت مع كثرة البيانات. لا تغلق الصفحة حتى يبدأ التنزيل.
          </div>
        )}
        <div className="px-3 py-2.5 rounded-erp text-xs leading-6"
             style={{ background: 'var(--amber-lt)', color: 'var(--amber)' }}>
          يشمل التصدير بيانات ورشتك فقط (عملاء، موردون، مخزون، فواتير، قيود...).
          لا يشمل كلمات المرور ولا مفاتيح النظام ولا الملفات المرفوعة.
          <br />
          هذا تصدير للأرشفة والنقل <b>وليس نسخة قابلة للاسترجاع التلقائي</b> — للنسخ الكاملة للنظام تواصل مع مدير النظام.
          الملف يحتوي على بيانات عملك فاحتفظ به في مكان آمن.
        </div>
      </div>

      <div className="card flex flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-semibold">آخر عمليات التصدير</div>
          <button className="btn btn-sm" onClick={loadRuns} disabled={runs.loading && !runs.data}>↻ تحديث</button>
        </div>

        {runs.loading && !runs.data ? (
          <PageSpinner />
        ) : runs.error && !runs.data ? (
          <div className="flex items-center justify-between gap-3 text-sm px-3 py-2.5 rounded-erp"
               style={{ background: 'var(--red-lt)', color: 'var(--red)' }}>
            <span>تعذّر تحميل سجل التصدير ({runs.error})</span>
            <button className="btn btn-sm" onClick={loadRuns}>إعادة المحاولة</button>
          </div>
        ) : (runs.data || []).length === 0 ? (
          <div className="text-center text-erp-muted text-sm py-8">لم يتم تصدير بيانات من قبل</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            {runs.error && (
              <div className="text-xs mb-2" style={{ color: 'var(--amber)' }}>
                تعذّر تحديث السجل ({runs.error}) — المعروض آخر بيانات تم تحميلها.
              </div>
            )}
            <table className="erp-table" style={{ minWidth: 640 }}>
              <thead>
                <tr>
                  <th>التاريخ</th><th>بواسطة</th><th>الصيغة</th><th>عدد السجلات</th><th>الحجم</th><th>النتيجة</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.map(r => {
                  const [label, cls] = STATUS[r.status] || [r.status || '—', 'badge-gray']
                  const rows = totalRows(r.tables)
                  return (
                    <tr key={r.id}>
                      <td className="whitespace-nowrap">{fmtDateTime(r.started_at)}</td>
                      <td>{r.created_by || '—'}</td>
                      <td>{r.format === 'json' ? 'JSON' : r.format === 'zip' ? 'ZIP' : '—'}</td>
                      <td>{rows == null ? '—' : rows.toLocaleString('en-US')}</td>
                      <td>{r.status === 'success' ? formatBytes(r.size_bytes) : '—'}</td>
                      <td>
                        <span className={`badge ${cls}`}>{label}</span>
                        {r.status === 'failed' && r.error && (
                          <div className="text-xs mt-1" style={{ color: 'var(--red)' }}>{r.error}</div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
