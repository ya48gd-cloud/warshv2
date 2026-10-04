import { useState } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useToast } from '../store/toast'
import api from '../api/client'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

const STATUS_AR = { draft:'مسودة', approved:'موافق عليه', partial:'مستلم جزئياً', received:'مستلم كاملاً', cancelled:'ملغي' }
const STATUS_COLOR = { draft:'var(--text-secondary)', approved:'var(--teal)', partial:'#b45309', received:'#15803d', cancelled:'var(--red)' }

export default function PurchaseOrders() {
  const { data: orders, loading, reload } = useApi(() => api.purchaseOrders.list(), [])
  const { data: dash, reload: reloadDash } = useApi(() => api.purchaseOrders.dashboard(), [])
  const { data: suppliers } = useApi(() => api.purchases.suppliers(), [])
  const { data: materials } = useApi(() => api.inventory.materials({ limit: 500 }), [])
  const toast = useToast()
  const [showCreate, setShowCreate] = useState(false)
  const [editingId, setEditingId]   = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailData, setDetailData] = useState(null)
  const [receiveMode, setReceiveMode] = useState(false)
  const [receiveQtys, setReceiveQtys] = useState({})
  const [confirmCancel, setConfirmCancel] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(null)
  const [form, setForm] = useState({ supplier_id:'', order_date: new Date().toISOString().slice(0,10), expected_date:'', tax_pct:'0', discount_amt:'0', notes:'', lines:[] })
  const [newLine, setNewLine] = useState({ material_id:'', description:'', ordered_qty:'', unit:'كجم', unit_cost:'' })
  const [acting, setActing] = useState(false)
  const act = async (fn) => {
    if (acting) return
    setActing(true)
    try { await fn() } catch (e) { toast(e.message || 'حدث خطأ', 'error') } finally { setActing(false) }
  }

  const EMPTY_FORM = () => ({ supplier_id:'', order_date: new Date().toISOString().slice(0,10), expected_date:'', tax_pct:'0', discount_amt:'0', notes:'', lines:[] })
  const EMPTY_NEW_LINE = { material_id:'', description:'', ordered_qty:'', unit:'كجم', unit_cost:'' }

  const openCreate = () => {
    // إعادة ضبط النموذج — كان الضغط على "إلغاء" بعد فتح تعديل يسيب editingId
    // والبيانات القديمة، فزر "أمر شراء جديد" يفتح في وضع التعديل للأمر السابق.
    setEditingId(null)
    setForm(EMPTY_FORM())
    setNewLine({ ...EMPTY_NEW_LINE })
    setShowCreate(true)
  }
  const closeCreate = () => { setShowCreate(false); setEditingId(null) }

  const removeFormLine = (idx) => setForm(f => ({ ...f, lines: f.lines.filter((_, i) => i !== idx) }))

  const openDetail = async (id) => {
    setDetail(id)
    setDetailData(null)
    setReceiveMode(false)
    let d
    try {
      d = await api.purchaseOrders.get(id)
    } catch (e) {
      toast(e.message || 'خطأ في تحميل أمر الشراء', 'error')
      setDetail(null)
      return
    }
    setDetailData(d)
    const init = {}
    // نستخدم remaining_qty الجاهزة من الباك-إند (محسوبة بـ Decimal) بدل طرح
    // ordered_qty - received_qty هنا كـ float — كان ممكن ينتج أخطاء تقريب
    // زي 0.30000000000000004 تظهر في حقل الكمية المستلمة الافتراضي.
    d.lines?.forEach(l => { init[l.id] = String(l.remaining_qty) })
    setReceiveQtys(init)
  }

  const addLine = () => {
    if (!String(newLine.description || '').trim() || newLine.ordered_qty === '' || newLine.unit_cost === '' || newLine.unit_cost == null) {
      toast('أدخل الوصف والكمية والسعر قبل إضافة البند', 'error')
      return
    }
    const q = Number(newLine.ordered_qty), c = Number(newLine.unit_cost)
    if (!Number.isFinite(q) || q <= 0) { toast('الكمية يجب أن تكون أكبر من صفر', 'error'); return }
    if (!Number.isFinite(c) || c < 0) { toast('سعر الوحدة لا يمكن أن يكون سالباً', 'error'); return }
    if (String(newLine.description).length > 300) { toast('وصف البند طويل جداً (الحد 300 حرف)', 'error'); return }
    if (newLine.unit && String(newLine.unit).length > 20) { toast('الوحدة طويلة جداً (الحد 20 حرف)', 'error'); return }
    setForm(f => ({ ...f, lines: [...f.lines, { ...newLine, description: String(newLine.description).trim(), material_id: newLine.material_id || null }] }))
    setNewLine({ ...EMPTY_NEW_LINE })
  }

  const openEdit = async (po) => {
    try {
      const d = await api.purchaseOrders.get(po.id)
      setForm({
        supplier_id: d.supplier_id || '',
        order_date: d.order_date,
        expected_date: d.expected_date || '',
        tax_pct: d.tax_pct || '0',
        discount_amt: d.discount_amt || '0',
        notes: d.notes || '',
        lines: (d.lines || []).map(l => ({
          material_id: l.material_id || '',
          description: l.description,
          ordered_qty: l.ordered_qty,
          unit: l.unit,
          unit_cost: l.unit_cost,
        })),
      })
      setEditingId(po.id)
      setShowCreate(true)
    } catch { toast('خطأ في تحميل أمر الشراء', 'error') }
  }

  const handleCreate = async () => {
    await act(async () => {
      if (!form.supplier_id) { toast('اختر المورد — أمر الشراء لا يمكن إنشاؤه بدون مورد', 'error'); return }
      // البند المكتوب في صف "إضافة بند" من غير ما يتضغط + كان بيتجاهل بصمت
      // والأمر ميتنشأش — ناخده تلقائيًا لو بياناته كاملة وصحيحة
      let lines = form.lines
      const pendingDesc = String(newLine.description || '').trim()
      if (pendingDesc) {
        const q = Number(newLine.ordered_qty), c = Number(newLine.unit_cost)
        if (!Number.isFinite(q) || q <= 0 || newLine.unit_cost === '' || !Number.isFinite(c) || c < 0) {
          toast(`البند "${pendingDesc}" لم يُضف — أكمل الكمية والسعر ثم اضغط + أو امسحه`, 'error'); return
        }
        lines = [...lines, { ...newLine, description: pendingDesc, material_id: newLine.material_id || null }]
        setForm(f => ({ ...f, lines }))
        setNewLine({ ...EMPTY_NEW_LINE })
      }
      if (!lines.length) { toast('أضف بنداً واحداً على الأقل', 'error'); return }
      if (form.order_date && form.expected_date && form.expected_date < form.order_date) {
        toast('تاريخ التوريد المتوقع لازم يكون بعد تاريخ الأمر', 'error'); return
      }
      if (!form.order_date) { toast('تاريخ الأمر مطلوب', 'error'); return }
      const taxN = Number(form.tax_pct || 0), discN = Number(form.discount_amt || 0)
      if (!Number.isFinite(taxN) || taxN < 0 || taxN > 100) { toast('نسبة الضريبة يجب أن تكون بين 0 و 100', 'error'); return }
      if (!Number.isFinite(discN) || discN < 0) { toast('الخصم لا يمكن أن يكون سالباً', 'error'); return }
      const linesSubtotal = lines.reduce((s, l) => s + (Number(l.ordered_qty) || 0) * (Number(l.unit_cost) || 0), 0)
      if (discN > linesSubtotal + 1e-9) { toast('الخصم لا يمكن أن يتجاوز إجمالي البنود', 'error'); return }
      const payload = {
        ...form,
        supplier_id: form.supplier_id ? Number(form.supplier_id) : null,
        tax_pct: form.tax_pct || '0',
        discount_amt: form.discount_amt || '0',
        expected_date: form.expected_date || null,
        lines: lines.map(l => ({
          ...l,
          material_id: l.material_id ? Number(l.material_id) : null,
          ordered_qty: String(l.ordered_qty),
          unit_cost: String(l.unit_cost),
        }))
      }
      if (editingId) {
        await api.purchaseOrders.update(editingId, payload)
        toast('تم تعديل أمر الشراء')
      } else {
        await api.purchaseOrders.create(payload)
        toast('تم إنشاء أمر الشراء')
      }
      setShowCreate(false)
      setEditingId(null)
      setForm(EMPTY_FORM())
      reload(); reloadDash()
    })
  }

  const handleApprove = async (id) => {
    await act(async () => {
      await api.purchaseOrders.approve(id)
      toast('تمت الموافقة')
      if (detail === id) openDetail(id)
      reload(); reloadDash()
    })
  }

  const handleReceive = async () => {
    await act(async () => {
      const lines = Object.entries(receiveQtys)
        .filter(([,q]) => parseFloat(q) > 0)
        .map(([line_id, q]) => ({ line_id: Number(line_id), received_qty: q }))
      if (!lines.length) { toast('أدخل كميات الاستلام', 'error'); return }
      // الكمية المستلمة لا تتجاوز المتبقي (نفس تحقق الباك-إند)
      for (const rl of lines) {
        const ln = detailData?.lines?.find(l => l.id === rl.line_id)
        if (ln && Number(rl.received_qty) > Number(ln.remaining_qty) + 1e-9) {
          toast(`الكمية المستلمة لـ "${ln.description}" أكبر من المتبقي (${ln.remaining_qty})`, 'error'); return
        }
      }
      const res = await api.purchaseOrders.receive(detail, {
        receive_date: new Date().toISOString().slice(0,10),
        lines, create_invoice: true
      })
      toast(`تم الاستلام${res.invoice_id ? ` — فاتورة ${res.invoice_id} أُنشئت` : ''}`)
      setReceiveMode(false); openDetail(detail); reload(); reloadDash()
    })
  }

  const handleCancel = async (id) => {
    await act(async () => {
      await api.purchaseOrders.cancel(id)
      toast('تم الإلغاء')
      setDetail(null); reload(); reloadDash()
    })
  }

  const handleDelete = async (id) => {
    await act(async () => {
      await api.purchaseOrders.delete(id)
      toast('تم حذف أمر الشراء')
      setDetail(null); reload(); reloadDash()
    })
  }

  if (loading) return <PageSpinner />

  return (
    <div className="p-4 max-w-6xl mx-auto" dir="rtl">
      {/* Dashboard */}
      {dash && (
        <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))', gap:12, marginBottom:20 }}>
          {[
            ['إجمالي الأوامر', dash.total_orders, 'var(--teal)'],
            ['مسودة', dash.draft, '#6b7280'],
            ['موافق عليه', dash.approved, 'var(--teal)'],
            ['مستلم جزئياً', dash.partial, '#b45309'],
          ].map(([label, val, color]) => (
            <div key={label} style={{ background:'var(--surface)', border:'1px solid var(--border)', borderRadius:10, padding:'14px 16px', borderTop:`3px solid ${color}` }}>
              <div style={{ fontSize:11, color:'var(--text-secondary)', marginBottom:4 }}>{label}</div>
              <div style={{ fontSize:26, fontWeight:700, color }}>{val ?? 0}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16, flexWrap:'wrap', gap:8 }}>
        <h2 style={{ fontSize:20, fontWeight:700 }}>أوامر الشراء</h2>
        <button className="btn-primary" onClick={openCreate}>+ أمر شراء جديد</button>
      </div>

      {/* Table */}
      <div style={{ background:'var(--surface)', borderRadius:10, border:'1px solid var(--border)', overflow:'hidden' }}>
        <div style={{ overflowX:'auto' }}>
        <table className="erp-table">
          <thead><tr>
            <th>الكود</th><th>المورد</th><th>التاريخ</th><th>الإجمالي</th><th>الحالة</th><th>إجراء</th>
          </tr></thead>
          <tbody>
            {(!orders || orders.length === 0) && (
              <tr><td colSpan={6} style={{ textAlign:'center', padding:32, color:'var(--text-secondary)' }}>لا توجد أوامر شراء</td></tr>
            )}
            {orders?.map(o => (
              <tr key={o.id} style={{ cursor:'pointer' }} onClick={() => openDetail(o.id)}>
                <td style={{ fontWeight:600, color:'var(--teal)' }}>{o.code}</td>
                <td>{o.supplier_name || '—'}</td>
                <td>{o.order_date}</td>
                <td>{Number(o.total || 0).toLocaleString('ar-EG', { minimumFractionDigits:2 })} ج</td>
                <td><span style={{ color: STATUS_COLOR[o.status], fontWeight:600 }}>{STATUS_AR[o.status] || o.status}</span></td>
                <td onClick={e => e.stopPropagation()}>
                  {o.status === 'draft' && (
                    <button className="btn-sm" style={{ background:'#DBEAFE', marginLeft:4 }} onClick={() => openEdit(o)}>✏️</button>
                  )}
                  {o.status === 'draft' && (
                    <button className="btn-sm btn-teal" disabled={acting} onClick={() => handleApprove(o.id)}>موافقة</button>
                  )}
                  {o.status !== 'received' && o.status !== 'cancelled' && o.status !== 'partial' && (
                    <button className="btn-sm btn-danger" style={{ marginRight:4 }} onClick={() => setConfirmCancel(o.id)}>إلغاء</button>
                  )}
                  {(o.status === 'draft' || o.status === 'cancelled') && (
                    <button className="btn-sm btn-danger" style={{ marginRight:4 }} onClick={() => setConfirmDelete(o.id)}>🗑️</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </div>

      {/* Detail Panel */}
      {detail && detailData && (
        <Modal title={`أمر شراء: ${detailData.code}`} onClose={() => setDetail(null)} size="lg">
          <div style={{ marginBottom:12, display:'flex', gap:12, flexWrap:'wrap' }}>
            <span><b>المورد:</b> {detailData.supplier_name || '—'}</span>
            <span><b>التاريخ:</b> {detailData.order_date}</span>
            {detailData.expected_date && <span><b>التوريد المتوقع:</b> {detailData.expected_date}</span>}
            <span><b>الحالة:</b> <span style={{ color: STATUS_COLOR[detailData.status], fontWeight:700 }}>{STATUS_AR[detailData.status] || detailData.status}</span></span>
            <span><b>الإجمالي:</b> {Number(detailData.total || 0).toLocaleString('ar-EG', { minimumFractionDigits:2 })} ج</span>
          </div>
          <div style={{ overflowX:'auto', marginBottom:16 }}>
          <table className="erp-table text-sm">
            <thead><tr><th>المادة/الوصف</th><th>مطلوب</th><th>مستلم</th><th>متبقي</th><th>سعر الوحدة</th></tr></thead>
            <tbody>
              {detailData.lines?.map(l => (
                <tr key={l.id}>
                  <td>{l.description}</td>
                  <td>{l.ordered_qty}</td>
                  <td style={{ color:'var(--teal)' }}>{l.received_qty}</td>
                  <td style={{ color: l.remaining_qty > 0 ? '#b45309' : 'var(--text-secondary)' }}>{l.remaining_qty}</td>
                  <td>{l.unit_cost} ج</td>
                </tr>
              ))}
              {!detailData.lines?.length && (
                <tr><td colSpan={5} style={{ textAlign:'center', color:'var(--text-secondary)' }}>لا توجد بنود</td></tr>
              )}
            </tbody>
          </table>
          </div>
          {receiveMode ? (
            <div>
              <h4 style={{ marginBottom:8, fontWeight:600 }}>أدخل الكميات المستلمة:</h4>
              {detailData.lines?.filter(l => l.remaining_qty > 0).map(l => (
                <div key={l.id} style={{ display:'flex', gap:8, alignItems:'center', marginBottom:8 }}>
                  <span style={{ flex:1 }}>{l.description} (متبقي: {l.remaining_qty})</span>
                  <input type="number" min="0" step="any" style={{ width:100, padding:'4px 8px', border:'1px solid var(--border)', borderRadius:6 }}
                    value={receiveQtys[l.id] || ''} max={l.remaining_qty}
                    onChange={e => setReceiveQtys(r => ({ ...r, [l.id]: e.target.value }))}
                  />
                </div>
              ))}
              <div style={{ display:'flex', gap:8, marginTop:12 }}>
                <button className="btn-primary" onClick={handleReceive} disabled={acting}>{acting ? '…' : 'تأكيد الاستلام وإنشاء فاتورة'}</button>
                <button className="btn-secondary" onClick={() => setReceiveMode(false)} disabled={acting}>إلغاء</button>
              </div>
            </div>
          ) : (
            <div style={{ display:'flex', gap:8, flexWrap:'wrap' }}>
              {detailData.status === 'draft' && <button className="btn-teal" disabled={acting} onClick={() => handleApprove(detail)}>✓ موافقة</button>}
              {['approved','partial'].includes(detailData.status) && <button className="btn-primary" onClick={() => setReceiveMode(true)}>📦 تسجيل استلام</button>}
              <button className="btn-secondary" style={{ gap:4 }}
                onClick={() => api.print(`/purchase-orders/${detail}/print`)}>
                🖨️ طباعة الأمر
              </button>
              {['partial','received'].includes(detailData.status) && (
                <button className="btn-secondary"
                  onClick={() => api.print(`/purchase-orders/${detail}/grn/print`)}>
                  📋 سند الاستلام
                </button>
              )}
            </div>
          )}
        </Modal>
      )}

      {/* Create Modal */}
      {showCreate && (
        <Modal title={editingId ? 'تعديل أمر شراء' : 'إنشاء أمر شراء جديد'} onClose={closeCreate} size="lg">
          <FormGrid>
            <Select label="المورد *" value={form.supplier_id} onChange={e => setForm(f => ({ ...f, supplier_id: e.target.value }))}>
              <option value="">— اختر مورداً (مطلوب) —</option>
              {suppliers?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
            <Input label="تاريخ الأمر *" type="date" value={form.order_date} onChange={e => setForm(f => ({ ...f, order_date: e.target.value }))} />
            <Input label="تاريخ التوريد المتوقع" type="date" min={form.order_date || undefined} value={form.expected_date || ''} onChange={e => setForm(f => ({ ...f, expected_date: e.target.value }))} />
            <Input label="نسبة الضريبة %" type="number" min="0" max="100" step="any" value={form.tax_pct} onChange={e => setForm(f => ({ ...f, tax_pct: e.target.value }))} />
            <Input label="خصم (ج)" type="number" min="0" step="any" value={form.discount_amt || '0'} onChange={e => setForm(f => ({ ...f, discount_amt: e.target.value }))} />
            <Textarea label="ملاحظات" value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} span={2} />
          </FormGrid>
          <div style={{ marginTop:16, padding:12, background:'var(--bg)', borderRadius:8, border:'1px solid var(--border)' }}>
            <h4 style={{ fontWeight:600, marginBottom:8 }}>إضافة بند:</h4>
            <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(110px,1fr))', gap:8, alignItems:'end' }}>
              <div>
                <Select label="المادة" value={newLine.material_id} onChange={e => {
                  const val = e.target.value
                  const m = materials?.find(m => String(m.id) === String(val))
                  setNewLine(n => ({ ...n, material_id: val, description: m?.name_ar || n.description, unit: m?.unit || n.unit, unit_cost: m?.unit_cost ?? n.unit_cost }))
                }}>
                  <option value="">— اختر مادة —</option>
                  {materials?.map(m => <option key={m.id} value={m.id}>{m.code} — {m.name_ar}</option>)}
                </Select>
              </div>
              <Input label="الوصف *" value={newLine.description} maxLength={300} onChange={e => setNewLine(n => ({ ...n, description: e.target.value }))} />
              <Input label="الكمية" type="number" min="0" step="any" value={newLine.ordered_qty} onChange={e => setNewLine(n => ({ ...n, ordered_qty: e.target.value }))} />
              <Input label="الوحدة" maxLength={20} value={newLine.unit} onChange={e => setNewLine(n => ({ ...n, unit: e.target.value }))} />
              <Input label="سعر الوحدة" type="number" min="0" step="any" value={newLine.unit_cost} onChange={e => setNewLine(n => ({ ...n, unit_cost: e.target.value }))} />
              <button type="button" className="btn-teal" style={{ padding:'8px 14px' }} onClick={addLine} title="إضافة البند">+</button>
            </div>
            {form.lines.length > 0 && (
              <div style={{ overflowX:'auto', marginTop:8 }}>
              <table className="erp-table text-sm">
                <thead><tr><th>الوصف</th><th>الكمية</th><th>الوحدة</th><th>السعر</th><th>الإجمالي</th><th></th></tr></thead>
                <tbody>
                  {form.lines.map((l,i) => (
                    <tr key={i}>
                      <td>{l.description}</td><td>{l.ordered_qty}</td><td>{l.unit}</td><td>{l.unit_cost}</td>
                      <td>{(Number(l.ordered_qty) * Number(l.unit_cost)).toFixed(2)}</td>
                      <td><button type="button" className="btn-sm btn-danger" title="حذف البند" onClick={() => removeFormLine(i)}>✕</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            )}
          </div>
          <ModalFooter>
            <button className="btn-primary" onClick={handleCreate} disabled={!form.supplier_id || !form.order_date || form.lines.length === 0 || acting}>{acting ? '…' : (editingId ? 'حفظ التعديلات' : 'إنشاء الأمر')}</button>
            <button className="btn-secondary" onClick={closeCreate}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      <ConfirmDialog open={!!confirmCancel} message="هل تريد إلغاء هذا الأمر؟" danger
        onConfirm={() => { handleCancel(confirmCancel); setConfirmCancel(null) }}
        onCancel={() => setConfirmCancel(null)} />

      <ConfirmDialog open={!!confirmDelete} message="هل تريد حذف هذا الأمر نهائياً؟" danger
        onConfirm={() => { handleDelete(confirmDelete); setConfirmDelete(null) }}
        onCancel={() => setConfirmDelete(null)} />
    </div>
  )
}
