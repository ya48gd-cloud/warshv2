import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'

const TABS = [
  { key: 'register', label: '📝 تسجيل الهالك' },
  { key: 'inventory', label: '📦 مخزون الهالك' },
  { key: 'sell', label: '💰 بيع الهالك' },
  { key: 'sales', label: '🧾 سجل المبيعات' },
  { key: 'analysis', label: '📊 تحليل' },
]

export default function ScrapHub() {
  const [activeTab, setActiveTab] = useState('register')
  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-1 border-b border-erp-border pb-0 overflow-x-auto">
        {TABS.map(tab => (
          <button key={tab.key} className="px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap"
            style={activeTab === tab.key
              ? { color: 'var(--teal)', borderBottom: '2px solid var(--teal)' }
              : { color: 'var(--erp-muted)', borderBottom: '2px solid transparent' }}
            onClick={() => setActiveTab(tab.key)}>
            {tab.label}
          </button>
        ))}
      </div>
      {activeTab === 'register' && <ScrapRegister />}
      {activeTab === 'inventory' && <ScrapInventory />}
      {activeTab === 'sell' && <ScrapSell />}
      {activeTab === 'sales' && <ScrapSalesLog />}
      {activeTab === 'analysis' && <ScrapAnalysis />}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// تسجيل الهالك
// ══════════════════════════════════════════════════════════════
function ScrapRegister() {
  const { canWrite } = useAuth()
  const write = canWrite('production')
  const toast = useToast()
  const { data: scrapData, loading, reload } = useApi(() => api.production.scrapList())
  const { data: materials } = useApi(() => api.inventory.materials())
  const [modal, setModal] = useState(false)
  const [form, setForm] = useState({ date: today(), qty: '' })

  const scraps = scrapData?.records
  const total = scrapData?.total_scrap_cost || 0

  const [save, saving] = useAction(async () => {
    if (!form.material_id || !form.qty || Number(form.qty) <= 0) { toast('اختر المادة وأدخل كمية أكبر من صفر', 'error'); return }
    if (!form.date) { toast('التاريخ مطلوب', 'error'); return }
    await api.production.recordScrap(form)
    toast('تم تسجيل الهالك'); setModal(false); setForm({ date: today(), qty: '' }); reload()
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
                  <td className="font-medium">{s.material}</td>
                  <td>{Number(s.qty).toFixed(1)}</td>
                  <td className="text-erp-muted text-xs">{s.reason || '—'}</td>
                  <td className="font-semibold" style={{ color: 'var(--red)' }}>{egp(s.total_cost)}</td>
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
            {materials?.map(m => <option key={m.id} value={m.id}>{m.name_ar}</option>)}
          </Select>
          <FormGrid cols={2}>
            <Input label="الكمية *" type="number" step="0.01" value={form.qty || ''} onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} />
            <Input label="التاريخ *" type="date" value={form.date || today()} onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
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
// مخزون الهالك
// ══════════════════════════════════════════════════════════════
function ScrapInventory() {
  const { data: inv, loading } = useApi(() => api.production.scrapInventory())

  const totalValue = useMemo(() => (inv || []).reduce((s, r) => s + (r.est_value || 0), 0), [inv])

  if (loading) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="text-sm font-semibold" style={{ color: 'var(--teal)' }}>
        إجمالي القيمة التقديرية للهالك المتاح: {egp(totalValue)}
      </div>
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead><tr><th>الكود</th><th>المادة</th><th>الكمية المتاحة</th><th>الوحدة</th><th>تكلفة الوحدة</th><th>القيمة التقديرية</th></tr></thead>
            <tbody>
              {!inv?.length && <tr><td colSpan={6} className="text-center text-erp-muted py-10">لا يوجد هالك متاح للبيع</td></tr>}
              {inv?.map(r => (
                <tr key={r.material_id}>
                  <td className="text-xs text-erp-muted">{r.material_code}</td>
                  <td className="font-medium">{r.material_name}</td>
                  <td>{Number(r.available_qty).toFixed(2)}</td>
                  <td>{r.unit}</td>
                  <td>{egp(r.unit_cost)}</td>
                  <td className="font-semibold" style={{ color: 'var(--teal)' }}>{egp(r.est_value)}</td>
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
// بيع الهالك
// ══════════════════════════════════════════════════════════════
function ScrapSell() {
  const toast = useToast()
  const { data: inv, loading: loadingInv, reload: reloadInv } = useApi(() => api.production.scrapInventory())
  const { data: treasuries, loading: loadingTr } = useApi(() => api.treasury.accounts())

  const [form, setForm] = useState({ sale_date: today(), payment_method: 'cash', buyer_name: '', treasury_id: '', notes: '' })
  const [lines, setLines] = useState([{ material_id: '', qty: '', unit_price: '' }])

  const addLine = () => setLines(ls => [...ls, { material_id: '', qty: '', unit_price: '' }])
  const removeLine = (i) => setLines(ls => ls.filter((_, idx) => idx !== i))
  const updateLine = (i, field, value) => setLines(ls => ls.map((l, idx) => idx === i ? { ...l, [field]: value } : l))

  const total = useMemo(() => lines.reduce((s, l) => s + (Number(l.qty) || 0) * (Number(l.unit_price) || 0), 0), [lines])

  const [save, saving] = useAction(async () => {
    if (!form.treasury_id) { toast('اختر الخزينة', 'error'); return }
    const validLines = lines.filter(l => l.material_id && Number(l.qty) > 0 && Number(l.unit_price) >= 0)
    if (!validLines.length) { toast('أضف سطر بيع واحد على الأقل', 'error'); return }
    const res = await api.production.createScrapSale({
      sale_date: form.sale_date,
      buyer_name: form.buyer_name || null,
      treasury_id: +form.treasury_id,
      payment_method: form.payment_method,
      notes: form.notes || null,
      lines: validLines.map(l => ({ material_id: +l.material_id, qty: l.qty, unit_price: l.unit_price })),
    })
    if (res?.error) { toast(res.error, 'error'); return }
    toast('تم تسجيل عملية البيع')
    setLines([{ material_id: '', qty: '', unit_price: '' }])
    setForm(f => ({ ...f, buyer_name: '', notes: '' }))
    reloadInv()
  })

  if (loadingInv || loadingTr) return <PageSpinner />
  return (
    <div className="card flex flex-col gap-3">
      <FormGrid cols={3}>
        <Input label="تاريخ البيع *" type="date" value={form.sale_date} onChange={e => setForm(f => ({ ...f, sale_date: e.target.value }))} />
        <Input label="اسم المشتري" value={form.buyer_name} onChange={e => setForm(f => ({ ...f, buyer_name: e.target.value }))} />
        <Select label="الخزينة *" value={form.treasury_id} onChange={e => setForm(f => ({ ...f, treasury_id: e.target.value }))}>
          <option value="">— اختر —</option>
          {treasuries?.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
        </Select>
      </FormGrid>
      <Select label="طريقة الدفع" value={form.payment_method} onChange={e => setForm(f => ({ ...f, payment_method: e.target.value }))}>
        <option value="cash">نقدي</option>
        <option value="bank">بنكي</option>
      </Select>

      <div className="flex flex-col gap-2">
        <div className="text-sm font-semibold">السطور</div>
        {lines.map((line, i) => (
          <div key={i} className="flex gap-2 items-end">
            <div style={{ flex: 2 }}>
              <Select label={i === 0 ? 'المادة' : ''} value={line.material_id} onChange={e => updateLine(i, 'material_id', e.target.value)}>
                <option value="">— اختر —</option>
                {inv?.map(r => <option key={r.material_id} value={r.material_id}>{r.material_name} (متاح {Number(r.available_qty).toFixed(2)} {r.unit})</option>)}
              </Select>
            </div>
            <div style={{ flex: 1 }}>
              <Input label={i === 0 ? 'الكمية' : ''} type="number" step="0.01" value={line.qty} onChange={e => updateLine(i, 'qty', e.target.value)} />
            </div>
            <div style={{ flex: 1 }}>
              <Input label={i === 0 ? 'سعر الوحدة' : ''} type="number" step="0.01" value={line.unit_price} onChange={e => updateLine(i, 'unit_price', e.target.value)} />
            </div>
            <div style={{ flex: 1 }}>
              <div className="text-xs text-erp-muted mb-1">{i === 0 ? 'الإجمالي' : ''}</div>
              <div className="font-semibold py-1">{egp((Number(line.qty) || 0) * (Number(line.unit_price) || 0))}</div>
            </div>
            {lines.length > 1 && (
              <button className="btn btn-sm" onClick={() => removeLine(i)} title="حذف السطر">✕</button>
            )}
          </div>
        ))}
        <button className="btn btn-sm" onClick={addLine} style={{ alignSelf: 'flex-start' }}>+ سطر جديد</button>
      </div>

      <Textarea label="ملاحظات" value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} rows={2} />

      <div className="flex items-center justify-between border-t border-erp-border pt-3">
        <div className="text-base font-bold" style={{ color: 'var(--teal)' }}>الإجمالي: {egp(total)}</div>
        <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ عملية البيع'}</button>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// سجل المبيعات
// ══════════════════════════════════════════════════════════════
function ScrapSalesLog() {
  const { data: sales, loading } = useApi(() => api.production.scrapSales())

  const total = useMemo(() => (sales || []).reduce((s, r) => s + (r.total_amount || 0), 0), [sales])

  if (loading) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="text-sm font-semibold" style={{ color: 'var(--teal)' }}>
        إجمالي مبيعات الهالك: {egp(total)}
      </div>
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead><tr><th>الكود</th><th>التاريخ</th><th>المشتري</th><th>الخزينة</th><th>طريقة الدفع</th><th>عدد السطور</th><th>الإجمالي</th></tr></thead>
            <tbody>
              {!sales?.length && <tr><td colSpan={7} className="text-center text-erp-muted py-10">لا يوجد مبيعات هالك مسجلة</td></tr>}
              {sales?.map(s => (
                <tr key={s.id}>
                  <td className="font-medium">{s.code}</td>
                  <td className="text-xs text-erp-muted">{fmtDate(s.sale_date)}</td>
                  <td>{s.buyer_name || '—'}</td>
                  <td>{s.treasury_name || '—'}</td>
                  <td className="text-xs text-erp-muted">{s.payment_method === 'cash' ? 'نقدي' : 'بنكي'}</td>
                  <td>{s.lines_count}</td>
                  <td className="font-semibold" style={{ color: 'var(--teal)' }}>{egp(s.total_amount)}</td>
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
// تحليل الهالك
// ══════════════════════════════════════════════════════════════
function ScrapAnalysis() {
  const { data, loading } = useApi(() => api.reports.scrapAnalysis({}))

  if (loading) return <PageSpinner />
  if (!data) return <div className="text-center text-erp-muted py-10">لا توجد بيانات</div>

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-4">
        <div className="card flex-1">
          <div className="text-xs text-erp-muted">إجمالي تكلفة الهالك</div>
          <div className="text-xl font-bold" style={{ color: 'var(--red)' }}>{egp(data.total_scrap_cost)}</div>
        </div>
        <div className="card flex-1">
          <div className="text-xs text-erp-muted">عدد السجلات</div>
          <div className="text-xl font-bold">{data.total_records}</div>
        </div>
      </div>

      <div className="flex gap-4 flex-wrap">
        <div className="card flex-1" style={{ minWidth: 280 }}>
          <div className="text-sm font-semibold mb-2">حسب السبب</div>
          <table className="erp-table">
            <thead><tr><th>السبب</th><th>التكلفة</th></tr></thead>
            <tbody>
              {data.by_reason?.map((r, i) => (
                <tr key={i}><td>{r.reason}</td><td className="font-semibold" style={{ color: 'var(--red)' }}>{egp(r.cost)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card flex-1" style={{ minWidth: 280 }}>
          <div className="text-sm font-semibold mb-2">أعلى المواد (هالك)</div>
          <table className="erp-table">
            <thead><tr><th>المادة</th><th>التكلفة</th></tr></thead>
            <tbody>
              {data.by_material?.map((r, i) => (
                <tr key={i}><td>{r.material}</td><td className="font-semibold" style={{ color: 'var(--red)' }}>{egp(r.cost)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead><tr><th>التاريخ</th><th>أمر الإنتاج</th><th>المادة</th><th>الكمية</th><th>السبب</th><th>المرحلة</th><th>التكلفة</th></tr></thead>
            <tbody>
              {!data.records?.length && <tr><td colSpan={7} className="text-center text-erp-muted py-10">لا يوجد هالك مسجل</td></tr>}
              {data.records?.map(r => (
                <tr key={r.id}>
                  <td className="text-xs text-erp-muted">{fmtDate(r.date)}</td>
                  <td>{r.wo_code}</td>
                  <td className="font-medium">{r.material}</td>
                  <td>{Number(r.qty).toFixed(2)}</td>
                  <td className="text-erp-muted text-xs">{r.reason || '—'}</td>
                  <td className="text-erp-muted text-xs">{r.stage || '—'}</td>
                  <td className="font-semibold" style={{ color: 'var(--red)' }}>{egp(r.total_cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
