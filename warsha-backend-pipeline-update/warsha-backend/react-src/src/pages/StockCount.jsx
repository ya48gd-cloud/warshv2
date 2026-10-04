/**
 * جرد المخزون — لكل مخزن.
 *
 * الجرد بيتفتح على مخزن واحد، وورقته فيها أصناف المخزن ده بس بالرصيد
 * الدفتري الحالي. العدّ بيتكتب في الجدول نفسه (Enter ينقل للصنف اللي بعده)،
 * والفرق وقيمته بيظهروا فورًا. الإغلاق بيسوّي الأصناف المعدودة بس على
 * المخزن ده ويسجّل قيد الزيادة/العجز. ينفع جرد أكتر من مخزن في نفس الوقت.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import api, { bgErr } from '../api/client'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import { egp, fmtDate } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Select, Textarea } from '../components/ui/Field'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import SearchInput from '../components/ui/SearchInput'
import { PageSpinner } from '../components/ui/Spinner'

const STATUS = {
  in_progress: { label: 'مفتوح', cls: 'badge-amber' },
  completed:   { label: 'مغلق ومُسوّى', cls: 'badge-green' },
  cancelled:   { label: 'ملغي', cls: 'badge-gray' },
}
const FILTERS = [
  { key: 'all',       label: 'الكل' },
  { key: 'pending',   label: 'لم يُعد' },
  { key: 'diff',      label: 'فيه فرق' },
  { key: 'match',     label: 'مطابق' },
]
const n3 = (v) => Number(v || 0).toLocaleString('en-US', { maximumFractionDigits: 3 })

function CountSheet({ countId, write, onChanged }) {
  const toast = useToast()
  const { data: sheet, loading, reload } = useApi(() => api.production.stockCount(countId), [countId])
  const { data: materials } = useApi(() => api.inventory.materials().catch(bgErr('المواد', [])))
  const [filter, setFilter] = useState('all')
  const [q, setQ] = useState('')
  const [drafts, setDrafts] = useState({})
  const [saving, setSaving] = useState(null)
  const [confirm, setConfirm] = useState(null) // 'close' | 'cancel'
  const [found, setFound] = useState({ material_id: '', qty: '' })
  const inputs = useRef({})

  useEffect(() => { setDrafts({}); setFilter('all'); setQ('') }, [countId])

  const open = sheet?.status === 'in_progress'
  const lines = sheet?.lines || []
  const shown = useMemo(() => {
    const lq = q.trim().toLowerCase()
    return lines.filter(l => {
      if (filter === 'pending' && l.actual_qty != null) return false
      if (filter === 'diff' && !(l.actual_qty != null && Math.abs(l.difference) > 1e-9)) return false
      if (filter === 'match' && !(l.actual_qty != null && Math.abs(l.difference) <= 1e-9)) return false
      if (lq && !(`${l.name || ''} ${l.code || ''}`.toLowerCase().includes(lq))) return false
      return true
    })
  }, [lines, filter, q])

  const stats = useMemo(() => {
    let counted = 0, surplus = 0, shortage = 0, diffs = 0
    for (const l of lines) {
      if (l.actual_qty == null) continue
      counted++
      const v = Number(l.difference_value || 0)
      if (Math.abs(l.difference) > 1e-9) diffs++
      if (v > 0) surplus += v; else shortage += -v
    }
    return { counted, total: lines.length, surplus, shortage, diffs }
  }, [lines])

  const save = async (l, raw) => {
    const txt = String(raw ?? '').trim()
    if (txt === '') return
    const val = Number(txt)
    if (!Number.isFinite(val) || val < 0) { toast('الكمية المعدودة لازم تكون رقم صفر أو أكبر', 'error'); return }
    if (l.actual_qty != null && Math.abs(val - l.actual_qty) < 1e-9) return
    setSaving(l.material_id)
    try {
      await api.production.scanLine(countId, { material_id: l.material_id, actual_qty: val })
      setDrafts(d => { const x = { ...d }; delete x[l.material_id]; return x })
      await reload(); onChanged?.()
    } catch (e) { toast(e.message || 'تعذّر حفظ الكمية', 'error') }
    finally { setSaving(null) }
  }

  const focusNext = (idx) => {
    const next = shown[idx + 1]
    if (next) inputs.current[next.material_id]?.focus()
  }

  const [unscan] = useAction(async (mid) => {
    await api.production.unscanLine(countId, mid)
    await reload(); onChanged?.()
  })

  const [addFound, addingFound] = useAction(async () => {
    if (!found.material_id) { toast('اختر الصنف', 'error'); return }
    const val = Number(found.qty)
    if (found.qty === '' || !Number.isFinite(val) || val < 0) { toast('أدخل الكمية المعدودة', 'error'); return }
    await api.production.scanLine(countId, { material_id: Number(found.material_id), actual_qty: val })
    toast('تمت إضافة الصنف لورقة الجرد'); setFound({ material_id: '', qty: '' })
    await reload(); onChanged?.()
  })

  const [doClose, closing] = useAction(async () => {
    const r = await api.production.closeCount(countId)
    toast(`تم إغلاق الجرد — ${r.adjustments} تسوية (زيادة ${egp(r.surplus_value)} · عجز ${egp(r.shortage_value)})`)
    await reload(); onChanged?.()
  })
  const [doCancel, cancelling] = useAction(async () => {
    await api.production.cancelCount(countId)
    toast('تم إلغاء الجرد من غير أي تسوية'); await reload(); onChanged?.()
  })

  if (loading && !sheet) return <PageSpinner />
  if (!sheet) return null

  const onSheet = new Set(lines.map(l => l.material_id))
  const notOnSheet = (materials || []).filter(m => !onSheet.has(m.id))
  const pct = stats.total ? Math.round(stats.counted / stats.total * 100) : 0
  const st = STATUS[sheet.status] || { label: sheet.status, cls: 'badge-gray' }

  return (
    <div className="flex flex-col gap-3">
      {/* رأس الورقة */}
      <div className="card flex flex-wrap items-center gap-4 py-3">
        <div className="flex-1 min-w-48">
          <div className="flex items-center gap-2">
            <span className="font-semibold">جرد {sheet.warehouse_name}</span>
            <span className={`badge ${st.cls}`}>{st.label}</span>
            <span className="text-xs text-erp-muted">#{sheet.id} · {fmtDate(sheet.date)}</span>
          </div>
          <div className="flex items-center gap-2 mt-2">
            <div style={{ flex: 1, height: 6, background: 'var(--erp-border)', borderRadius: 3, overflow: 'hidden', maxWidth: 260 }}>
              <div style={{ height: '100%', width: `${pct}%`, background: 'var(--teal)' }} />
            </div>
            <span className="text-xs text-erp-muted">تم عدّ {stats.counted} من {stats.total} صنف</span>
          </div>
          {sheet.notes && <div className="text-xs text-erp-muted mt-1">{sheet.notes}</div>}
        </div>
        <div className="text-xs flex flex-col gap-0.5">
          <span style={{ color: 'var(--green)' }}>زيادة: <b className="font-mono">{egp(stats.surplus)}</b></span>
          <span style={{ color: 'var(--red)' }}>عجز: <b className="font-mono">{egp(stats.shortage)}</b></span>
        </div>
        {open && write && (
          <div className="flex gap-2">
            <button className="btn btn-sm" onClick={() => setConfirm('cancel')} disabled={cancelling}>إلغاء الجرد</button>
            <button className="btn btn-primary btn-sm" onClick={() => setConfirm('close')} disabled={closing || !stats.counted}
              title={!stats.counted ? 'سجّل كمية صنف واحد على الأقل' : ''}>
              {closing ? '…' : '✓ إغلاق وتسوية'}
            </button>
          </div>
        )}
      </div>

      {/* فلاتر */}
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map(f => {
          const c = f.key === 'all' ? lines.length
            : f.key === 'pending' ? lines.filter(l => l.actual_qty == null).length
            : f.key === 'diff' ? stats.diffs
            : stats.counted - stats.diffs
          return (
            <button key={f.key} className="btn btn-sm"
              style={filter === f.key ? { background: 'var(--blue)', color: '#fff', borderColor: 'var(--blue)' } : {}}
              onClick={() => setFilter(f.key)}>{f.label} <span className="text-[10px] opacity-75">({c})</span></button>
          )
        })}
        <div className="flex-1 min-w-48"><SearchInput value={q} onChange={setQ} placeholder="بحث بالكود أو الاسم…" /></div>
      </div>

      {/* ورقة الجرد */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr>
                <th>الصنف</th><th>الدفتري</th><th style={{ width: 150 }}>المعدود فعليًا</th>
                <th>الفرق</th><th>قيمة الفرق</th>{open && write && <th style={{ width: 110 }}></th>}
              </tr>
            </thead>
            <tbody>
              {!shown.length && (
                <tr><td colSpan={6} className="text-center text-erp-muted py-8">
                  {lines.length ? 'لا توجد أصناف بالفلتر ده' : 'مفيش أصناف فيها رصيد في المخزن ده — أضف أي صنف لقيته من تحت'}
                </td></tr>
              )}
              {shown.map((l, idx) => {
                const counted = l.actual_qty != null
                const diff = Number(l.difference || 0)
                const hasDiff = counted && Math.abs(diff) > 1e-9
                const draft = drafts[l.material_id]
                return (
                  <tr key={l.material_id} style={{ background: hasDiff ? (diff > 0 ? 'var(--green-lt, #ecfdf5)' : 'var(--red-lt)') : undefined }}>
                    <td>
                      <div className="font-medium text-sm">{l.name}</div>
                      <div className="text-[10px] text-erp-muted font-mono">{l.code} · {l.unit}</div>
                    </td>
                    <td className="font-mono">{n3(l.book_qty)}</td>
                    <td>
                      {open && write ? (
                        <input type="number" min="0" step="any" className="field-input w-full font-mono"
                          ref={el => { inputs.current[l.material_id] = el }}
                          aria-label={`الكمية المعدودة — ${l.name}`}
                          value={draft ?? (counted ? String(l.actual_qty) : '')}
                          placeholder="—"
                          disabled={saving === l.material_id}
                          onChange={e => setDrafts(d => ({ ...d, [l.material_id]: e.target.value }))}
                          onBlur={e => save(l, e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); save(l, e.currentTarget.value); focusNext(idx) } }} />
                      ) : (
                        <span className="font-mono">{counted ? n3(l.actual_qty) : '—'}</span>
                      )}
                    </td>
                    <td className="font-mono font-semibold" style={{ color: !counted ? 'var(--erp-muted)' : diff > 0 ? 'var(--green)' : diff < 0 ? 'var(--red)' : 'var(--teal)' }}>
                      {!counted ? '—' : hasDiff ? `${diff > 0 ? '+' : ''}${n3(diff)}` : '✓ مطابق'}
                    </td>
                    <td className="font-mono text-sm" style={{ color: diff > 0 ? 'var(--green)' : diff < 0 ? 'var(--red)' : 'var(--erp-muted)' }}>
                      {hasDiff ? egp(l.difference_value) : '—'}
                    </td>
                    {open && write && (
                      <td>
                        <div className="flex gap-1">
                          {!counted && (
                            <button className="btn btn-sm text-xs" title="الكمية الفعلية = الدفتري"
                              onClick={() => save(l, String(l.book_qty))}>= مطابق</button>
                          )}
                          {counted && (
                            <button className="btn btn-sm text-xs" title="مسح الكمية المعدودة"
                              onClick={() => unscan(l.material_id)}>↺ مسح</button>
                          )}
                        </div>
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* صنف لقيناه ومش في الورقة */}
      {open && write && (
        <div className="card flex flex-wrap items-end gap-3 py-3">
          <div className="flex-1 min-w-48">
            <Select label="صنف موجود فعليًا ومش في الورقة" value={found.material_id}
              onChange={e => setFound(f => ({ ...f, material_id: e.target.value }))}>
              <option value="">— اختر —</option>
              {notOnSheet.map(m => <option key={m.id} value={m.id}>{m.code} — {m.name_ar}</option>)}
            </Select>
          </div>
          <div style={{ width: 140 }}>
            <label className="field-label">الكمية المعدودة</label>
            <input type="number" min="0" step="any" className="field-input w-full" value={found.qty}
              onChange={e => setFound(f => ({ ...f, qty: e.target.value }))} />
          </div>
          <button className="btn btn-sm" onClick={addFound} disabled={addingFound}>{addingFound ? '…' : '+ إضافة للورقة'}</button>
        </div>
      )}

      <ConfirmDialog open={confirm === 'close'} onClose={() => setConfirm(null)} onConfirm={doClose}
        title={`إغلاق جرد ${sheet.warehouse_name}`} confirmLabel="إغلاق وتسوية"
        message={[
          `هيتم تسوية ${stats.diffs} صنف فيهم فرق في ${sheet.warehouse_name} بس:`,
          `زيادة ${egp(stats.surplus)} · عجز ${egp(stats.shortage)} (هيتسجل قيد تسوية بالقيمة دي).`,
          stats.total - stats.counted > 0 ? `${stats.total - stats.counted} صنف ما اتعدّش — رصيده هيفضل زي ما هو.` : '',
          'الإجراء ده مالوش رجوع.',
        ].filter(Boolean).join('\n')} />
      <ConfirmDialog open={confirm === 'cancel'} onClose={() => setConfirm(null)} onConfirm={doCancel} danger
        title="إلغاء الجرد" confirmLabel="إلغاء الجرد"
        message="هيتقفل الجرد من غير أي تعديل على الأرصدة، والكميات اللي اتسجلت فيه مش هتتطبق." />
    </div>
  )
}

export default function StockCount() {
  const { canWrite } = useAuth()
  const write = canWrite('inventory')
  const toast = useToast()
  const { data: counts, loading, reload } = useApi(() => api.production.stockCounts())
  const { data: summary } = useApi(() => api.warehouses.stockSummary().catch(bgErr('المخازن', [])))
  const [selected, setSelected] = useState(null)
  const [newModal, setNewModal] = useState(false)
  const [form, setForm] = useState({ warehouse_id: '', notes: '' })

  const list = Array.isArray(counts) ? counts : []
  const openCounts = list.filter(c => c.status === 'in_progress')
  const history = list.filter(c => c.status !== 'in_progress')
  const busyWh = new Set(openCounts.map(c => c.warehouse_id))
  const allOpen = openCounts.some(c => c.warehouse_id == null)
  const warehouses = (Array.isArray(summary) ? summary : []).filter(w => w.is_active !== false)

  // افتراضيًا: أول جرد مفتوح
  useEffect(() => {
    if (selected == null && openCounts.length) setSelected(openCounts[0].id)
  }, [counts]) // eslint-disable-line react-hooks/exhaustive-deps

  const [start, starting] = useAction(async () => {
    if (!form.warehouse_id) { toast('اختر المخزن اللي هيتجرد', 'error'); return }
    const r = await api.production.startCount({ warehouse_id: Number(form.warehouse_id), notes: form.notes || null })
    toast(`تم فتح الجرد — ${r.items} صنف في الورقة`)
    setNewModal(false); setForm({ warehouse_id: '', notes: '' })
    await reload(); setSelected(r.id)
  })

  if (loading && !counts) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="font-semibold">جرد المخزون</div>
        <div className="text-xs text-erp-muted">جرد كل مخزن لوحده — ينفع أكتر من جرد مفتوح لمخازن مختلفة</div>
        <div className="flex-1" />
        {write && <button className="btn btn-primary btn-sm" onClick={() => setNewModal(true)} disabled={allOpen}
          title={allOpen ? 'فيه جرد قديم مفتوح على كل المخازن — أغلقه الأول' : ''}>+ جرد مخزن</button>}
      </div>

      {/* الجرد المفتوح */}
      {openCounts.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {openCounts.map(c => (
            <button key={c.id} type="button" onClick={() => setSelected(c.id)} aria-pressed={selected === c.id}
              className="rounded-lg px-3 py-2 text-right"
              style={{ border: `1px solid ${selected === c.id ? 'var(--amber)' : 'var(--erp-border)'}`,
                       background: selected === c.id ? 'var(--amber-lt)' : 'var(--surface, #fff)' }}>
              <div className="text-sm font-semibold">🔴 {c.warehouse_name}</div>
              <div className="text-[11px] text-erp-muted">#{c.id} · تم عدّ {c.scanned_items}/{c.total_items}</div>
            </button>
          ))}
        </div>
      )}

      {selected != null
        ? <CountSheet countId={selected} write={write} onChanged={reload} />
        : !openCounts.length && (
          <div className="card text-center text-erp-muted py-10">
            مفيش جرد مفتوح. {write ? 'اضغط "+ جرد مخزن" واختار المخزن اللي هتعدّه.' : ''}
          </div>
        )}

      {/* السجل */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">سجل عمليات الجرد</div>
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead><tr><th>#</th><th>التاريخ</th><th>المخزن</th><th>الحالة</th><th>المعدود / الأصناف</th><th></th></tr></thead>
            <tbody>
              {!history.length && <tr><td colSpan={6} className="text-center text-erp-muted py-8">لا توجد عمليات جرد سابقة</td></tr>}
              {history.map(c => {
                const st = STATUS[c.status] || { label: c.status, cls: 'badge-gray' }
                return (
                  <tr key={c.id}>
                    <td className="font-mono text-xs">{c.id}</td>
                    <td className="text-xs text-erp-muted">{fmtDate(c.date)}</td>
                    <td>{c.warehouse_name}</td>
                    <td><span className={`badge ${st.cls}`}>{st.label}</span></td>
                    <td className="font-mono">{c.scanned_items} / {c.total_items}</td>
                    <td><button className="btn btn-sm text-xs" onClick={() => setSelected(c.id)}>عرض الورقة</button></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <Modal open={newModal} onClose={() => setNewModal(false)} title="جرد مخزن جديد" size="sm">
        <div className="flex flex-col gap-3">
          <Select label="المخزن *" value={form.warehouse_id} onChange={e => setForm(f => ({ ...f, warehouse_id: e.target.value }))}>
            <option value="">— اختر المخزن —</option>
            {warehouses.map(w => (
              <option key={w.id} value={w.id} disabled={busyWh.has(w.id)}>
                {w.name_ar} — {Number(w.items_count || 0)} صنف{busyWh.has(w.id) ? ' (عليه جرد مفتوح)' : ''}
              </option>
            ))}
          </Select>
          <Textarea label="ملاحظات" value={form.notes} maxLength={1000}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} placeholder="مثلاً: جرد نهاية الشهر" />
          <div className="text-xs text-erp-muted">
            الورقة هتتعمل بأصناف المخزن ده ورصيدها الحالي. أي صنف تلاقيه ومش في الورقة تقدر تضيفه وقت العدّ.
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setNewModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={start} disabled={starting}>{starting ? '…' : 'فتح الجرد'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
