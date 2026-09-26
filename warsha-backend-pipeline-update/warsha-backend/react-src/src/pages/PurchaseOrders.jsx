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
  const { data: dash } = useApi(() => api.purchaseOrders.dashboard(), [])
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
  const [form, setForm] = useState({ supplier_id:'', order_date: new Date().toISOString().slice(0,10), tax_pct:'0', notes:'', lines:[] })
  const [newLine, setNewLine] = useState({ material_id:'', description:'', ordered_qty:'', unit:'كجم', unit_cost:'' })
  const [acting, setActing] = useState(false)
  const act = async (fn) => {
    if (acting) return
    setActing(true)
    try { await fn() } catch (e) { toast(e.message || 'حدث خطأ', 'error') } finally { setActing(false) }
  }

  const openDetail = async (id) => {
    setDetail(id)
    setDetailData(null)
    setReceiveMode(false)
    const d = await api.purchaseOrders.get(id)
    setDetailData(d)
    const init = {}
    d.lines?.forEach(l => { init[l.id] = String(l.ordered_qty - l.received_qty) })
    setReceiveQtys(init)
  }

  const addLine = () => {
    if (!newLine.description || !newLine.ordered_qty || !newLine.unit_cost) return
    setForm(f => ({ ...f, lines: [...f.lines, { ...newLine, material_id: newLine.material_id || null }] }))
    setNewLine({ material_id:'', description:'', ordered_qty:'', unit:'كجم', unit_cost:'' })
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
      if (!form.lines.length) { toast('أضف بنداً واحداً على الأقل', 'error'); return }
      if (form.order_date && form.expected_date && form.expected_date < form.order_date) {
        toast('تاريخ التوريد المتوقع لازم يكون بعد تاريخ الأمر', 'error'); return
      }
      const payload = {
        ...form,
        supplier_id: form.supplier_id ? Number(form.supplier_id) : null,
        tax_pct: form.tax_pct || '0',
        lines: form.lines.map(l => ({
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
      setForm({ supplier_id:'', order_date: new Date().toISOString().slice(0,10), tax_pct:'0', notes:'', lines:[] })
      reload()
    })
  }

  const handleApprove = async (id) => {
    await act(async () => {
      await api.purchaseOrders.approve(id)
      toast('تمت الموافقة')
      openDetail(id); reload()
    })
  }

  const handleReceive = async () => {
    await act(async () => {
      const lines = Object.entries(receiveQtys)
        .filter(([,q]) => parseFloat(q) > 0)
        .map(([line_id, q]) => ({ line_id: Number(line_id), received_qty: q }))
      if (!lines.length) { toast('أدخل كميات الاستلام', 'error'); return }
      const res = await api.purchaseOrders.receive(detail, {
        receive_date: new Date().toISOString().slice(0,10),
        lines, create_invoice: true
      })
      toast(`تم الاستلام${res.invoice_id ? ` — فاتورة ${res.invoice_id} أُنشئت` : ''}`)
      setReceiveMode(false); openDetail(detail); reload()
    })
  }

  const handleCancel = async (id) => {
    await act(async () => {
      await api.purchaseOrders.cancel(id)
      toast('تم الإلغاء')
      setDetail(null); reload()
    })
  }

  const handleDelete = async (id) => {
    await act(async () => {
      await api.purchaseOrders.delete(id)
      toast('تم حذف أمر الشراء')
      setDetail(null); reload()
    })
  }

  if (loading) return <PageSpinner />

  return (
    <div className="p-4 max-w-6xl mx-auto" dir="rtl">
      {/* Dashboard */}
      {dash && (
        <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:12, marginBottom:20 }}>
          {[
            ['إجمالي الأوامر', dash.total_orders, 'var(--teal)'],
            ['مسودة', dash.draft, '#6b7280'],
            ['موافق عليه', dash.approved, 'var(--teal)'],
            ['مستلم جزئياً', dash.partial, '#b45309'],
          ].map(([label, val, color]) => (
            <div key={label} style={{ background:'var(--surface)', border:'1px solid var(--border)', borderRadius:10, padding:'14px 16px', borderTop:`3px solid ${color}` }}>
              <div style={{ fontSize:11, color:'var(--text-secondary)', marginBottom:4 }}>{label}</div>
              <div style={{ fontSize:26, fontWeight:700, color }}>{val}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16 }}>
        <h2 style={{ fontSize:20, fontWeight:700 }}>أوامر الشراء</h2>
        <button className="btn-primary" onClick={() => setShowCreate(true)}>+ أمر شراء جديد</button>
      </div>

      {/* Table */}
      <div style={{ background:'var(--surface)', borderRadius:10, border:'1px solid var(--border)', overflow:'hidden' }}>
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
                <td>{Number(o.total).toLocaleString('ar-EG', { minimumFractionDigits:2 })} ج</td>
                <td><span style={{ color: STATUS_COLOR[o.status], fontWeight:600 }}>{STATUS_AR[o.status]}</span></td>
                <td onClick={e => e.stopPropagation()}>
                  {o.status === 'draft' && (
                    <button className="btn-sm" style={{ background:'#DBEAFE', marginLeft:4 }} onClick={() => openEdit(o)}>✏️</button>
                  )}
                  {o.status === 'draft' && (
                    <button className="btn-sm btn-teal" onClick={() => handleApprove(o.id)}>موافقة</button>
                  )}
                  {o.status !== 'received' && o.status !== 'cancelled' && (
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

      {/* Detail Panel */}
      {detail && detailData && (
        <Modal title={`أمر شراء: ${detailData.code}`} onClose={() => setDetail(null)} size="lg">
          <div style={{ marginBottom:12, display:'flex', gap:12, flexWrap:'wrap' }}>
            <span><b>المورد:</b> {detailData.supplier_name || '—'}</span>
            <span><b>التاريخ:</b> {detailData.order_date}</span>
            <span><b>الحالة:</b> <span style={{ color: STATUS_COLOR[detailData.status], fontWeight:700 }}>{STATUS_AR[detailData.status]}</span></span>
            <span><b>الإجمالي:</b> {Number(detailData.total).toLocaleString('ar-EG')} ج</span>
          </div>
          <table className="erp-table text-sm" style={{ marginBottom:16 }}>
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
            </tbody>
          </table>
          {receiveMode ? (
            <div>
              <h4 style={{ marginBottom:8, fontWeight:600 }}>أدخل الكميات المستلمة:</h4>
              {detailData.lines?.filter(l => l.remaining_qty > 0).map(l => (
                <div key={l.id} style={{ display:'flex', gap:8, alignItems:'center', marginBottom:8 }}>
                  <span style={{ flex:1 }}>{l.description} (متبقي: {l.remaining_qty})</span>
                  <input type="number" style={{ width:100, padding:'4px 8px', border:'1px solid var(--border)', borderRadius:6 }}
                    value={receiveQtys[l.id] || ''} max={l.remaining_qty}
                    onChange={e => setReceiveQtys(r => ({ ...r, [l.id]: e.target.value }))}
                  />
                </div>
              ))}
              <div style={{ display:'flex', gap:8, marginTop:12 }}>
                <button className="btn-primary" onClick={handleReceive} disabled={acting}>{acting ? '…' : 'تأكيد الاستلام وإنشاء فاتورة'}</button>
                <button className="btn-secondary" onClick={() => setReceiveMode(false)}>إلغاء</button>
              </div>
            </div>
          ) : (
            <div style={{ display:'flex', gap:8, flexWrap:'wrap' }}>
              {detailData.status === 'draft' && <button className="btn-teal" onClick={() => handleApprove(detail)}>✓ موافقة</button>}
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
        <Modal title={editingId ? 'تعديل أمر شراء' : 'إنشاء أمر شراء جديد'} onClose={() => { setShowCreate(false); setEditingId(null) }} size="lg">
          <FormGrid>
            <Select label="المورد *" value={form.supplier_id} onChange={e => setForm(f => ({ ...f, supplier_id: e.target.value }))}>
              <option value="">— اختر مورداً (مطلوب) —</option>
              {suppliers?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
            <Input label="تاريخ الأمر" type="date" value={form.order_date} onChange={e => setForm(f => ({ ...f, order_date: e.target.value }))} />
            <Input label="نسبة الضريبة %" value={form.tax_pct} onChange={e => setForm(f => ({ ...f, tax_pct: e.target.value }))} />
            <Textarea label="ملاحظات" value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} span={2} />
          </FormGrid>
          <div style={{ marginTop:16, padding:12, background:'var(--bg)', borderRadius:8, border:'1px solid var(--border)' }}>
            <h4 style={{ fontWeight:600, marginBottom:8 }}>إضافة بند:</h4>
            <div style={{ display:'grid', gridTemplateColumns:'2fr 1fr 1fr 1fr auto', gap:8, alignItems:'end' }}>
              <div>
                <Select label="المادة" value={newLine.material_id} onChange={e => {
                  const val = e.target.value
                  const m = materials?.find(m => String(m.id) === String(val))
                  setNewLine(n => ({ ...n, material_id: val, description: m?.name_ar || n.description, unit: m?.unit || n.unit, unit_cost: m?.unit_cost || n.unit_cost }))
                }}>
                  <option value="">— اختر مادة —</option>
                  {materials?.map(m => <option key={m.id} value={m.id}>{m.code} — {m.name_ar}</option>)}
                </Select>
              </div>
              <Input label="الكمية" type="number" value={newLine.ordered_qty} onChange={e => setNewLine(n => ({ ...n, ordered_qty: e.target.value }))} />
              <Input label="الوحدة" value={newLine.unit} onChange={e => setNewLine(n => ({ ...n, unit: e.target.value }))} />
              <Input label="سعر الوحدة" type="number" value={newLine.unit_cost} onChange={e => setNewLine(n => ({ ...n, unit_cost: e.target.value }))} />
              <button className="btn-teal" style={{ padding:'8px 14px' }} onClick={addLine}>+</button>
            </div>
            {form.lines.length > 0 && (
              <table className="erp-table text-sm" style={{ marginTop:8 }}>
                <thead><tr><th>الوصف</th><th>الكمية</th><th>الوحدة</th><th>السعر</th><th>الإجمالي</th></tr></thead>
                <tbody>
                  {form.lines.map((l,i) => (
                    <tr key={i}>
                      <td>{l.description}</td><td>{l.ordered_qty}</td><td>{l.unit}</td><td>{l.unit_cost}</td>
                      <td>{(Number(l.ordered_qty) * Number(l.unit_cost)).toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <ModalFooter>
            <button className="btn-primary" onClick={handleCreate} disabled={!form.supplier_id || !form.order_date || form.lines.length === 0 || acting}>{acting ? '…' : 'إنشاء الأمر'}</button>
            <button className="btn-secondary" onClick={() => setShowCreate(false)}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      <ConfirmDialog open={!!confirmCancel} message="هل تريد إلغاء هذا الأمر؟"
        onConfirm={() => { handleCancel(confirmCancel); setConfirmCancel(null) }}
        onCancel={() => setConfirmCancel(null)} />

      <ConfirmDialog open={!!confirmDelete} message="هل تريد حذف هذا الأمر نهائياً؟"
        onConfirm={() => { handleDelete(confirmDelete); setConfirmDelete(null) }}
        onCancel={() => setConfirmDelete(null)} />
    </div>
  )
}
