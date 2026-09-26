/**
 * Orders — طلبات العملاء (متعدد البنود)
 * forms_analysis_report: كانت 2/5 — محتاج multi-line + delivery_date + print
 */
import { useState, useMemo, useCallback, useEffect } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import SearchInput from '../components/ui/SearchInput'
import { PageSpinner } from '../components/ui/Spinner'
import ConfirmDialog from '../components/ui/ConfirmDialog'

const STATUS_MAP = {
  pending:       { label: 'معلق',         badge: 'badge-amber' },
  in_progress:   { label: 'جاري التنفيذ', badge: 'badge-blue'  },
  in_production: { label: 'في الإنتاج',   badge: 'badge-blue'  },
  delivered:     { label: 'مُسلَّم',      badge: 'badge-green' },
  cancelled:     { label: 'ملغي',         badge: 'badge-red'   },
}

const EMPTY_LINE = { description: '', qty: '1', unit: 'قطعة', unit_price: '', notes: '' }

export default function Orders() {
  const { canWrite } = useAuth()
  const write = canWrite('sales')
  const toast = useToast()

  const { data: orders, loading, reload } = useApi(() =>
    api.get('/customers/orders/all').catch(bgErr('قائمة الطلبات', []))
  )
  const { data: customers } = useApi(() => api.customers.list())

  const [q, setQ] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [modal, setModal]       = useState(null)  // null | 'add' | 'view'
  const [form, setForm]         = useState({})
  const [lines, setLines]       = useState([{ ...EMPTY_LINE }])
  const [editingOrderId, setEditingOrderId] = useState(null)
  const [viewOrder, setViewOrder] = useState(null)
  const [confirmCancelId, setConfirmCancelId] = useState(null)

  // ── WO modal (إنشاء أمر إنتاج من الطلب) ──────────────────────
  const [woModal, setWoModal]   = useState(null)
  const [woForm, setWoForm]     = useState({})
  const { data: allEqs }        = useApi(() => api.equipment.listAll())

  // ── Computed ──────────────────────────────────────────────────
  const lineTotal = lines.reduce((s, l) => s + ((+l.qty || 0) * (+l.unit_price || 0)), 0)

  const filtered = useMemo(() => {
    if (!orders) return []
    let list = orders
    if (statusFilter) list = list.filter(o => o.status === statusFilter)
    const lq = q.toLowerCase()
    return !lq ? list : list.filter(o =>
      o.code?.toLowerCase().includes(lq) ||
      o.customer_name?.toLowerCase().includes(lq) ||
      (o.description || '').toLowerCase().includes(lq)
    )
  }, [orders, q, statusFilter])

  // ── Lines helpers ─────────────────────────────────────────────
  const addLine    = ()          => setLines(ls => [...ls, { ...EMPTY_LINE }])
  const removeLine = (i)         => setLines(ls => ls.filter((_, idx) => idx !== i))
  const setLine    = (i, k, v)   => setLines(ls => ls.map((l, idx) => idx === i ? { ...l, [k]: v } : l))

  // ── Open add modal ────────────────────────────────────────────
  const openAdd = async () => {
    const { code } = await api.customers.nextOrderCode().catch(bgErr('كود الطلب التالي', { code: '' }))
    setForm({ code, status: 'pending', order_date: today(), delivery_date: '' })
    setLines([{ ...EMPTY_LINE }])
    setModal('add')
  }

  const openEditOrder = (o) => {
    setForm({
      customer_id:   o.customer_id,
      location_id:   o.location_id || '',
      code:          o.code,
      order_date:    o.order_date || today(),
      delivery_date: o.delivery_date || '',
      status:        o.status,
      notes:         o.notes || '',
    })
    setLines((o.lines || []).map(l => ({
      description: l.description,
      qty:         l.qty,
      unit:        l.unit,
      unit_price:  l.unit_price,
      notes:       l.notes || '',
    })))
    setEditingOrderId(o.id)
    setModal('add')
  }

  // ── Create ────────────────────────────────────────────────────
  const [save, saving] = useAction(async () => {
    if (!form.customer_id) { toast('اختر العميل', 'error'); return }
    const validLines = lines.filter(l => l.description.trim() && +l.qty > 0)
    if (!validLines.length) { toast('أضف بنداً واحداً على الأقل', 'error'); return }
    if (form.order_date && form.delivery_date && form.delivery_date < form.order_date) {
      toast('تاريخ التسليم لازم يكون بعد تاريخ الطلب', 'error'); return
    }
    const orderPayload = {
      code:          form.code,
      customer_id:   Number(form.customer_id),
      location_id:   form.location_id ? Number(form.location_id) : null,
      description:   validLines.map(l => l.description).join('، '),
      quantity:      validLines.reduce((s, l) => s + (+l.qty || 0), 0),
      unit_price:    0,
      order_date:    form.order_date || today(),
      delivery_date: form.delivery_date || null,
      status:        form.status || 'pending',
      notes:         form.notes || null,
      lines: validLines.map(l => ({
        description: l.description,
        qty:         Number(l.qty || 1),
        unit:        l.unit || 'قطعة',
        unit_price:  Number(l.unit_price || 0),
        notes:       l.notes || null,
      })),
    }
    if (editingOrderId) {
      await api.customers.editOrder(editingOrderId, orderPayload)
      toast('✅ تم تعديل الطلب')
    } else {
      await api.customers.createOrder(orderPayload)
      toast('✅ تم إنشاء الطلب')
    }
    setModal(null)
    setEditingOrderId(null)
    reload()
  })

  // ── Status update ─────────────────────────────────────────────
  const [updateStatus, updatingStatus] = useAction(async (id, status) => {
    await api.customers.updateOrder(id, status)
    toast('تم تحديث الحالة')
    reload()
  })

  // ── Create WO from order (via new conversion endpoint) ───────
  const [startProduction, starting] = useAction(async () => {
    if (!woForm.equipment_id) { toast('اختر معدة', 'error'); return }
    await api.sales.toWorkOrder(woModal.order.id, {
      equipment_id:  Number(woForm.equipment_id),
      quantity:      Number(woForm.quantity  || 1),
      quantity_unit: woForm.quantity_unit || 'قطعة',
      notes:         woForm.notes         || null,
    })
    toast('✅ تم إنشاء أمر الإنتاج')
    setWoModal(null)
    reload()
  })

  // ── Advance payment on order ──────────────────────────────────
  const [advModal, setAdvModal]   = useState(null)   // order object
  const [advForm, setAdvForm]     = useState({})
  const { data: treasuries }      = useApi(() => api.treasury.accounts())
  const [payingAdv, setPayingAdv] = useState(false)

  // مواقع العميل المختار
  const [locations, setLocations] = useState([])
  useEffect(() => {
    if (!form.customer_id) { setLocations([]); return }
    api.customers.locations(form.customer_id).then(r => setLocations(Array.isArray(r) ? r : [])).catch(bgErr('عناوين العميل', () => setLocations([])))
  }, [form.customer_id])

  const handleAdvPayment = async () => {
    if (!advForm.amount || Number(advForm.amount) <= 0) {
      toast('أدخل المبلغ', 'error'); return
    }
    setPayingAdv(true)
    try {
      await api.sales.advancePayment(advModal.id, {
        amount:       Number(advForm.amount),
        payment_type: advForm.payment_type || 'cash',
        payment_date: advForm.payment_date || today(),
        treasury_id:  advForm.treasury_id ? Number(advForm.treasury_id) : null,
        reference:    advForm.reference || null,
        notes:        advForm.notes || null,
      })
      toast(`✅ تم تسجيل الدفعة المقدمة ${egp(advForm.amount)}`)
      setAdvModal(null)
    } catch (e) {
      toast(e?.detail || e?.message || 'حدث خطأ', 'error')
    } finally {
      setPayingAdv(false)
    }
  }

  // ── Print ─────────────────────────────────────────────────────
  const handlePrint = useCallback((order) => {
    const url = api.customers.printOrderUrl(order.id)
    window.open(url, '_blank')
  }, [])

  if (loading) return <PageSpinner />

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div className="flex-1 min-w-[180px]">
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالكود أو العميل…" />
        </div>
        <select
          className="field-select text-sm"
          style={{ maxWidth: 130 }}
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
        >
          <option value="">كل الحالات</option>
          {Object.entries(STATUS_MAP).map(([v, { label }]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        <div className="text-xs text-erp-muted">({filtered.length})</div>
        {write && (
          <button className="btn btn-primary btn-sm" onClick={openAdd}>+ طلب جديد</button>
        )}
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr>
              <th>الكود</th>
              <th>العميل</th>
              <th>البنود</th>
              <th>الإجمالي</th>
              <th>الحالة</th>
              <th>تاريخ الطلب</th>
              <th>تاريخ التسليم</th>
              {write && <th>إجراءات</th>}
            </tr>
          </thead>
          <tbody>
            {!filtered.length && (
              <tr>
                <td colSpan={8} className="text-center text-erp-muted py-10">لا توجد طلبات</td>
              </tr>
            )}
            {filtered.map(o => {
              const total  = o.total_price ?? o.total_amount ?? 0
              const s = STATUS_MAP[o.status] || { label: o.status, badge: 'badge-gray' }
              const lineCount = o.lines?.length ?? 1
              return (
                <tr key={o.id} className="cursor-pointer hover:bg-erp-hover"
                    onClick={() => { setViewOrder(o); setModal('view') }}>
                  <td className="font-mono text-xs font-medium">{o.code}</td>
                  <td className="font-medium">{o.customer_name || '—'}</td>
                  <td className="text-xs text-erp-muted">
                    {o.lines?.length
                      ? `${lineCount} بند`
                      : (o.description || '—')}
                  </td>
                  <td className="font-semibold">{egp(total)}</td>
                  <td><span className={`badge ${s.badge}`}>{s.label}</span></td>
                  <td className="text-xs text-erp-muted">{fmtDate(o.order_date)}</td>
                  <td className="text-xs text-erp-muted">
                    {o.delivery_date
                      ? <span style={{ color: 'var(--teal)' }}>{fmtDate(o.delivery_date)}</span>
                      : '—'}
                  </td>
                  {write && (
                    <td onClick={e => e.stopPropagation()}>
                      <div className="flex gap-1 flex-wrap">
                        <button className="btn btn-sm text-xs"
                          onClick={() => handlePrint(o)}
                          title="طباعة">🖨</button>
                        {o.status === 'pending' && (
                          <button className="btn btn-sm text-xs"
                            style={{ background: '#DBEAFE', color: '#1D4ED8' }}
                            onClick={() => openEditOrder(o)}>✏️</button>
                        )}
                        {(o.status === 'pending' || o.status === 'in_progress') && (
                          <button className="btn btn-sm text-xs"
                            style={{ background: 'var(--teal-lt)', color: 'var(--teal)', borderColor: 'var(--teal-md)' }}
                            onClick={() => {
                              setWoModal({ order: o })
                              setWoForm({ equipment_id: '', quantity: 1, quantity_unit: 'قطعة', notes: '' })
                            }}>🏭 إنتاج</button>
                        )}
                        {(o.status === 'pending' || o.status === 'in_progress' || o.status === 'in_production') && (
                          <button className="btn btn-sm text-xs"
                            style={{ background: 'var(--amber-lt)', color: 'var(--amber)', borderColor: 'var(--amber-lt)' }}
                            onClick={() => {
                              setAdvModal(o)
                              setAdvForm({ payment_type: 'cash', payment_date: today(), amount: '', treasury_id: '', reference: '', notes: '' })
                            }}>💰 مقدمة</button>
                        )}
                        {o.status === 'pending' && (
                          <button className="btn btn-sm text-xs"
                            style={{ background: 'var(--blue-lt)', color: 'var(--blue)' }}
                            onClick={() => updateStatus(o.id, 'in_progress')}>▶ تشغيل</button>
                        )}
                        {(o.status === 'in_progress' || o.status === 'in_production') && (
                          <button className="btn btn-sm text-xs"
                            style={{ background: 'var(--green-lt)', color: 'var(--green)' }}
                            onClick={() => updateStatus(o.id, 'delivered')}>✓ تسليم</button>
                        )}
                        {(o.status === 'pending' || o.status === 'in_progress' || o.status === 'in_production') && (
                          <button className="btn btn-sm text-xs btn-danger"
                            onClick={() => setConfirmCancelId(o.id)}>✕</button>
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

      {/* ── Add Order Modal ─────────────────────────────────────── */}
      <Modal open={modal === 'add'} onClose={() => { setModal(null); setEditingOrderId(null) }} title={editingOrderId ? 'تعديل طلب عميل' : 'طلب عميل جديد'} size="lg">
        <div className="flex flex-col gap-4">
          {/* Header fields */}
          <FormGrid cols={locations.length > 0 ? 4 : 3}>
            <Input label="الكود *" value={form.code || ''}
              onChange={e => setForm(f => ({ ...f, code: e.target.value }))}
              placeholder="ORD-0001" />
            <Select label="العميل *" value={form.customer_id || ''}
              onChange={e => setForm(f => ({ ...f, customer_id: e.target.value, location_id: '' }))}>
              <option value="">— اختر عميل —</option>
              {customers?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
            {locations.length > 0 && (
              <Select label="الموقع / الفرع" value={form.location_id || ''}
                onChange={e => setForm(f => ({ ...f, location_id: e.target.value }))}>
                <option value="">— بدون موقع محدد —</option>
                {locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
              </Select>
            )}
            <Select label="الحالة" value={form.status || 'pending'}
              onChange={e => setForm(f => ({ ...f, status: e.target.value }))}>
              {Object.entries(STATUS_MAP).map(([v, { label }]) =>
                <option key={v} value={v}>{label}</option>
              )}
            </Select>
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="تاريخ الطلب *" type="date" value={form.order_date || today()}
              onChange={e => setForm(f => ({ ...f, order_date: e.target.value }))} />
            <Input label="تاريخ التسليم المتوقع" type="date" value={form.delivery_date || ''}
              onChange={e => setForm(f => ({ ...f, delivery_date: e.target.value }))} />
          </FormGrid>

          {/* Lines */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="field-label">بنود الطلب *</span>
              <button className="btn btn-sm btn-primary text-xs" onClick={addLine}>+ بند</button>
            </div>
            <div className="overflow-x-auto border rounded">
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: 'var(--blue-lt)' }}>
                    <th style={{ padding: '6px 8px', textAlign: 'right', minWidth: 200 }}>الوصف / البيان</th>
                    <th style={{ padding: '6px 8px', textAlign: 'center', width: 70 }}>الكمية</th>
                    <th style={{ padding: '6px 8px', textAlign: 'center', width: 80 }}>الوحدة</th>
                    <th style={{ padding: '6px 8px', textAlign: 'center', width: 100 }}>سعر الوحدة</th>
                    <th style={{ padding: '6px 8px', textAlign: 'center', width: 100 }}>الإجمالي</th>
                    <th style={{ width: 30 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '4px 6px' }}>
                        <input className="field-input w-full" value={l.description}
                          onChange={e => setLine(i, 'description', e.target.value)}
                          placeholder="وصف الصنف أو الخدمة" />
                      </td>
                      <td style={{ padding: '4px 6px' }}>
                        <input className="field-input text-center" type="number" min="0" step="0.01"
                          value={l.qty} onChange={e => setLine(i, 'qty', e.target.value)} />
                      </td>
                      <td style={{ padding: '4px 6px' }}>
                        <select className="field-select" value={l.unit}
                          onChange={e => setLine(i, 'unit', e.target.value)}>
                          {['قطعة','مجموعة','متر','كجم','طن','لتر','م²'].map(u =>
                            <option key={u} value={u}>{u}</option>
                          )}
                        </select>
                      </td>
                      <td style={{ padding: '4px 6px' }}>
                        <input className="field-input text-center" type="number" min="0" step="0.01"
                          value={l.unit_price} onChange={e => setLine(i, 'unit_price', e.target.value)}
                          placeholder="0.00" />
                      </td>
                      <td style={{ padding: '4px 6px', textAlign: 'center', fontWeight: 600 }}>
                        {egp((+l.qty || 0) * (+l.unit_price || 0))}
                      </td>
                      <td style={{ padding: '4px 6px', textAlign: 'center' }}>
                        {lines.length > 1 && (
                          <button className="btn btn-sm btn-danger text-xs"
                            onClick={() => removeLine(i)}>✕</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr style={{ background: 'var(--blue-lt)', fontWeight: 700 }}>
                    <td colSpan={4} style={{ padding: '6px 8px', textAlign: 'left' }}>الإجمالي</td>
                    <td style={{ padding: '6px 8px', textAlign: 'center' }}>{egp(lineTotal)}</td>
                    <td></td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>

          <Textarea label="ملاحظات" rows={2} value={form.notes || ''}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? '…' : 'حفظ الطلب'}
          </button>
        </ModalFooter>
      </Modal>

      {/* ── View Order Modal ────────────────────────────────────── */}
      <Modal open={modal === 'view' && !!viewOrder} onClose={() => setModal(null)}
             title={`تفاصيل الطلب — ${viewOrder?.code || ''}`} size="lg">
        {viewOrder && (
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div><span className="text-erp-muted">العميل: </span><b>{viewOrder.customer_name}</b></div>
              {viewOrder.location_name && (
                <div><span className="text-erp-muted">الموقع/الفرع: </span><b>{viewOrder.location_name}</b></div>
              )}
              <div><span className="text-erp-muted">الحالة: </span>
                <span className={`badge ${STATUS_MAP[viewOrder.status]?.badge || 'badge-gray'}`}>
                  {STATUS_MAP[viewOrder.status]?.label || viewOrder.status}
                </span>
              </div>
              <div><span className="text-erp-muted">تاريخ الطلب: </span>{fmtDate(viewOrder.order_date)}</div>
              <div><span className="text-erp-muted">تاريخ التسليم: </span>
                {viewOrder.delivery_date ? fmtDate(viewOrder.delivery_date) : '—'}</div>
            </div>

            {viewOrder.lines?.length > 0 ? (
              <div className="border rounded overflow-hidden">
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: 'var(--blue-lt)' }}>
                      <th style={{ padding: '6px 8px', textAlign: 'right' }}>الوصف</th>
                      <th style={{ padding: '6px 8px', textAlign: 'center' }}>الكمية</th>
                      <th style={{ padding: '6px 8px', textAlign: 'center' }}>سعر الوحدة</th>
                      <th style={{ padding: '6px 8px', textAlign: 'center' }}>الإجمالي</th>
                    </tr>
                  </thead>
                  <tbody>
                    {viewOrder.lines.map((l, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '5px 8px' }}>{l.description}</td>
                        <td style={{ padding: '5px 8px', textAlign: 'center' }}>{l.qty} {l.unit}</td>
                        <td style={{ padding: '5px 8px', textAlign: 'center' }}>{egp(l.unit_price)}</td>
                        <td style={{ padding: '5px 8px', textAlign: 'center', fontWeight: 600 }}>{egp(l.total_price)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr style={{ background: 'var(--blue-lt)', fontWeight: 700 }}>
                      <td colSpan={3} style={{ padding: '6px 8px', textAlign: 'left' }}>الإجمالي</td>
                      <td style={{ padding: '6px 8px', textAlign: 'center' }}>{egp(viewOrder.total_price)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            ) : (
              <div className="text-sm">
                <div><span className="text-erp-muted">الوصف: </span>{viewOrder.description}</div>
                <div><span className="text-erp-muted">الإجمالي: </span><b>{egp(viewOrder.total_price)}</b></div>
              </div>
            )}

            {viewOrder.notes && (
              <div className="text-sm text-erp-muted border-t pt-2">{viewOrder.notes}</div>
            )}
          </div>
        )}
        <ModalFooter>
          {write && viewOrder && (
            <button className="btn" onClick={() => handlePrint(viewOrder)}>🖨 طباعة</button>
          )}
          <button className="btn" onClick={() => setModal(null)}>إغلاق</button>
        </ModalFooter>
      </Modal>

      {/* ── Create Work Order Modal ─────────────────────────────── */}
      <Modal open={!!woModal} onClose={() => setWoModal(null)}
             title={`🏭 إنشاء أمر إنتاج — ${woModal?.order?.code || ''}`}>
        <div className="flex flex-col gap-3">
          <div className="px-3 py-2 rounded text-xs"
               style={{ background: 'var(--blue-lt)', color: 'var(--blue)' }}>
            العميل: <b>{woModal?.order?.customer_name}</b> — إجمالي: <b>{egp(woModal?.order?.total_price)}</b>
          </div>
          <div>
            <label className="field-label">المعدة *</label>
            <select className="field-select w-full" value={woForm.equipment_id || ''}
              onChange={e => setWoForm(f => ({ ...f, equipment_id: e.target.value }))}>
              <option value="">— اختر المعدة —</option>
              {allEqs?.map(eq => (
                <option key={eq.id} value={eq.id}>{eq.code} — {eq.name || eq.name_ar}</option>
              ))}
            </select>
          </div>
          <FormGrid cols={2}>
            <Input label="الكمية" type="number" min="0.001" step="0.001"
              value={woForm.quantity ?? 1}
              onChange={e => setWoForm(f => ({ ...f, quantity: e.target.value }))} />
            <Select label="وحدة الكمية"
              value={woForm.quantity_unit || 'قطعة'}
              onChange={e => setWoForm(f => ({ ...f, quantity_unit: e.target.value }))}>
              {['قطعة','متر','طن','كجم','لتر'].map(u => <option key={u} value={u}>{u}</option>)}
            </Select>
          </FormGrid>
          <Textarea label="ملاحظات (اختياري)" rows={2}
            value={woForm.notes || ''}
            onChange={e => setWoForm(f => ({ ...f, notes: e.target.value }))} />
          <div className="text-xs text-erp-muted">
            سيتم توليد كود أمر الإنتاج واستيراد التكلفة المخططة تلقائياً من BOM المعدة × الكمية.
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setWoModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={startProduction} disabled={starting}>
            {starting ? '⏳ جاري الإنشاء…' : '🏭 إنشاء أمر الإنتاج'}
          </button>
        </ModalFooter>
      </Modal>

      {/* ── Advance Payment Modal ───────────────────────────────── */}
      <Modal open={!!advModal} onClose={() => setAdvModal(null)}
             title={`💰 دفعة مقدمة — ${advModal?.code || ''}`}>
        {advModal && (
          <div className="flex flex-col gap-3">
            <div className="px-3 py-2 rounded text-xs"
                 style={{ background: 'var(--amber-lt)', color: 'var(--amber)' }}>
              العميل: <b>{advModal.customer_name}</b> — إجمالي الطلب: <b>{egp(advModal.total_price)}</b>
            </div>
            <FormGrid cols={2}>
              <Input label="المبلغ *" type="number" min="0.01" step="0.01"
                value={advForm.amount || ''}
                onChange={e => setAdvForm(f => ({ ...f, amount: e.target.value }))}
                placeholder="0.00" />
              <Select label="نوع الدفع"
                value={advForm.payment_type || 'cash'}
                onChange={e => setAdvForm(f => ({ ...f, payment_type: e.target.value }))}>
                <option value="cash">نقدي</option>
                <option value="bank">تحويل بنكي</option>
                <option value="check">شيك</option>
              </Select>
            </FormGrid>
            <FormGrid cols={2}>
              <Input label="تاريخ الدفع" type="date"
                value={advForm.payment_date || today()}
                onChange={e => setAdvForm(f => ({ ...f, payment_date: e.target.value }))} />
              <Select label="الخزنة (اختياري)"
                value={advForm.treasury_id || ''}
                onChange={e => setAdvForm(f => ({ ...f, treasury_id: e.target.value }))}>
                <option value="">— الخزنة الافتراضية —</option>
                {treasuries?.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
              </Select>
            </FormGrid>
            <Input label="المرجع / رقم الشيك (اختياري)"
              value={advForm.reference || ''}
              onChange={e => setAdvForm(f => ({ ...f, reference: e.target.value }))} />
            <Textarea label="ملاحظات (اختياري)" rows={2}
              value={advForm.notes || ''}
              onChange={e => setAdvForm(f => ({ ...f, notes: e.target.value }))} />
          </div>
        )}
        <ModalFooter>
          <button className="btn" onClick={() => setAdvModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={handleAdvPayment} disabled={payingAdv}>
            {payingAdv ? '⏳ جاري التسجيل…' : '💰 تسجيل الدفعة'}
          </button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!confirmCancelId}
        message="هل تريد إلغاء هذا الطلب؟"
        onConfirm={() => { updateStatus(confirmCancelId, 'cancelled'); setConfirmCancelId(null) }}
        onCancel={() => setConfirmCancelId(null)}
      />
    </div>
  )
}
