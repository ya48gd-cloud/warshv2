import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import { useToast } from '../store/toast'
import api from '../api/client'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

const STATUS_AR    = { draft:'مسودة', active:'ساري', expired:'منتهي', cancelled:'ملغي' }
const STATUS_COLOR = { draft:'var(--text-secondary)', active:'var(--teal)', expired:'#b45309', cancelled:'var(--red)' }

const EMPTY_FORM = {
  customer_id: '', quotation_id: '', date: new Date().toISOString().slice(0,10),
  valid_until: '', payment_terms: '', delivery_terms: '', warranty_months: '12', warranty_description: '',
  tax_pct: '0', discount_amt: '0', scope_of_work: '', special_terms: '', notes: '',
  lines: [],
}
const EMPTY_LINE = { item_type:'', model_no:'', description:'', qty:'1', unit:'قطعة', unit_price:'', notes:'', material_id:'' }

export default function SupplyContracts({ onNavigate }) {
  const { data: contracts, loading, reload } = useApi(() => api.supplyContracts.list(), [])
  const { data: customers } = useApi(() => api.customers.list(), [])
  const { data: quotations } = useApi(() => api.sales.quotations(), [])
  const { data: materials }  = useApi(() => api.inventory.materials(), [])
  const toast = useToast()

  const [showCreate, setShowCreate] = useState(false)
  const [detail, setDetail]         = useState(null)
  const [detailData, setDetailData] = useState(null)
  const [form, setForm]             = useState(EMPTY_FORM)
  const [newLine, setNewLine]       = useState(EMPTY_LINE)
  const [confirmDel, setConfirmDel]       = useState(null)
  const [acting, setActing]               = useState(false)
  const [scheduleModal, setScheduleModal] = useState(null)   // contract id
  const [scheduleData, setScheduleData]   = useState(null)   // { installments: [] }
  const [scheduleRows, setScheduleRows]   = useState([{ label: '', pct: '', days_offset: '0' }])
  const [deliverModal, setDeliverModal]   = useState(null)   // contract id
  const EMPTY_DELIVER = {
    delivery_date: new Date().toISOString().slice(0,10),
    received_by: '', receiver_title: '', delivery_notes: '',
    vehicle_plate: '', vehicle_type: '', driver_name: '', driver_license: '', driver_phone: '',
  }
  const [deliverForm, setDeliverForm]     = useState(EMPTY_DELIVER)

  const act = async (fn) => {
    if (acting) return
    setActing(true)
    try { await fn() }
    catch (e) { toast(e.message || 'حدث خطأ', 'error') }
    finally { setActing(false) }
  }

  const openDetail = async (id) => {
    setDetail(id)
    setDetailData(null)
    setScheduleData(null)
    const [d, sched] = await Promise.all([
      api.supplyContracts.get(id),
      api.supplyContracts.getScheduleTemplate(id).catch(() => null),
    ])
    setDetailData(d)
    setScheduleData(sched)
  }

  const openScheduleModal = (contractId) => {
    setScheduleRows([{ label: '', pct: '', days_offset: '0' }])
    setScheduleModal(contractId)
  }

  const handleSaveSchedule = async () => {
    await act(async () => {
      const total = scheduleRows.reduce((s, r) => s + Number(r.pct || 0), 0)
      if (Math.abs(total - 100) > 0.01) {
        toast(`مجموع النسب ${total.toFixed(1)}% — يجب أن يساوي 100%`, 'error')
        return
      }
      const payload = {
        installments: scheduleRows.map((r, i) => ({
          label: r.label || null,
          pct: Number(r.pct),
          days_offset: Number(r.days_offset || 0),
        }))
      }
      await api.supplyContracts.saveScheduleTemplate(scheduleModal, payload, true)
      toast('✅ تم حفظ خطة الأقساط')
      setScheduleModal(null)
      if (detail === scheduleModal) {
        const sched = await api.supplyContracts.getScheduleTemplate(scheduleModal).catch(() => null)
        setScheduleData(sched)
      }
    })
  }

  const openDeliverModal = (contractId) => {
    setDeliverForm(EMPTY_DELIVER)
    setDeliverModal(contractId)
  }

  const handleConfirmDelivery = async () => {
    await act(async () => {
      const res = await api.supplyContracts.deliver(deliverModal, {
        delivery_date:  deliverForm.delivery_date || null,
        received_by:    deliverForm.received_by || null,
        receiver_title: deliverForm.receiver_title || null,
        delivery_notes: deliverForm.delivery_notes || null,
        vehicle_plate:  deliverForm.vehicle_plate || null,
        vehicle_type:   deliverForm.vehicle_type || null,
        driver_name:    deliverForm.driver_name || null,
        driver_license: deliverForm.driver_license || null,
        driver_phone:   deliverForm.driver_phone || null,
      })
      toast(`✅ تم تأكيد التسليم${res.invoice_code ? ` — فاتورة مبيعات ${res.invoice_code} أُنشئت تلقائياً` : ''}`)
      const cid = deliverModal
      setDeliverModal(null)
      openDetail(cid); reload()
    })
  }

  const handleDeleteSchedule = async (contractId) => {
    await act(async () => {
      await api.supplyContracts.deleteScheduleTemplate(contractId)
      toast('تم حذف خطة الأقساط')
      setScheduleData(null)
    })
  }

  const addLine = () => {
    if (!newLine.description || !newLine.unit_price) {
      toast('أدخل الوصف والسعر قبل إضافة البند', 'error')
      return
    }
    setForm(f => ({ ...f, lines: [...f.lines, { ...newLine }] }))
    setNewLine(EMPTY_LINE)
  }

  const removeLine = (i) => setForm(f => ({ ...f, lines: f.lines.filter((_,idx) => idx !== i) }))

  const calcTotal = (lines, tax, disc) => {
    const sub = lines.reduce((s,l) => s + Number(l.qty||0)*Number(l.unit_price||0), 0)
    const taxAmt = sub * Number(tax||0) / 100
    return (sub - Number(disc||0) + taxAmt).toFixed(2)
  }

  const handleCreate = async () => {
    await act(async () => {
      if (!form.customer_id || !form.date) { toast('اختر العميل والتاريخ', 'error'); return }
      if (!form.lines.length) { toast('أضف بنداً واحداً على الأقل', 'error'); return }
      const payload = {
        customer_id:          Number(form.customer_id),
        quotation_id:         form.quotation_id ? Number(form.quotation_id) : null,
        date:                 form.date,
        valid_until:          form.valid_until || null,
        payment_terms:        form.payment_terms || null,
        delivery_terms:       form.delivery_terms || null,
        warranty_months:      Number(form.warranty_months) || 12,
        warranty_description: form.warranty_description || null,
        tax_pct:              form.tax_pct || '0',
        discount_amt:         form.discount_amt || '0',
        scope_of_work:        form.scope_of_work || null,
        special_terms:        form.special_terms || null,
        notes:                form.notes || null,
        lines: form.lines.map((l, i) => ({
          description: l.description,
          model_no:    l.model_no    || null,
          item_type:   l.item_type   || null,
          qty:         String(l.qty),
          unit:        l.unit,
          unit_price:  String(l.unit_price),
          notes:       l.notes       || null,
          material_id: l.material_id ? Number(l.material_id) : null,
          sort_order:  i,
        })),
      }
      await api.supplyContracts.create(payload)
      toast('تم إنشاء عقد الاتفاق')
      setShowCreate(false)
      setForm(EMPTY_FORM)
      reload()
    })
  }

  const handleFromQuotation = async (qid) => {
    await act(async () => {
      const c = await api.supplyContracts.fromQuotation(qid)
      toast(`تم إنشاء عقد الاتفاق ${c.code} من عرض السعر`)
      reload()
      openDetail(c.id)
    })
  }

  const handleActivate = async (id) => {
    await act(async () => {
      await api.supplyContracts.activate(id)
      toast('تم تفعيل العقد')
      openDetail(id); reload()
    })
  }

  const handleCancel = async (id) => {
    await act(async () => {
      await api.supplyContracts.cancel(id)
      toast('تم إيقاف العقد')
      openDetail(id); reload()
    })
  }

  const handleDelete = async (id) => {
    await act(async () => {
      await api.supplyContracts.delete(id)
      toast('تم الحذف')
      setDetail(null); reload()
    })
  }

  if (loading) return <PageSpinner />

  const summary = {
    total:     contracts?.length || 0,
    active:    contracts?.filter(c => c.status === 'active').length || 0,
    draft:     contracts?.filter(c => c.status === 'draft').length  || 0,
    delivered: contracts?.filter(c => c.delivery_confirmed).length  || 0,
    value:     contracts?.filter(c => c.status === 'active').reduce((s,c) => s + Number(c.total), 0) || 0,
  }

  return (
    <div className="p-4 max-w-6xl mx-auto" dir="rtl">

      {/* KPI cards */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:12, marginBottom:20 }}>
        {[
          ['إجمالي العقود',  summary.total,                                        'var(--teal)'],
          ['سارية',           summary.active,                                       '#15803d'],
          ['مُسلَّمة',         summary.delivered,                                    '#7c3aed'],
          ['قيمة السارية',    `${Number(summary.value).toLocaleString('ar-EG')} ج`, '#b45309'],
        ].map(([label, val, color]) => (
          <div key={label} style={{ background:'var(--surface)', border:'1px solid var(--border)', borderRadius:10, padding:'14px 16px', borderTop:`3px solid ${color}` }}>
            <div style={{ fontSize:11, color:'var(--text-secondary)', marginBottom:4 }}>{label}</div>
            <div style={{ fontSize:22, fontWeight:700, color }}>{val}</div>
          </div>
        ))}
      </div>

      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16, gap:8 }}>
        <h2 style={{ fontSize:20, fontWeight:700 }}>📋 عقود الاتفاق</h2>
        <div style={{ display:'flex', gap:8 }}>
          {quotations?.filter(q => ['accepted','sent','draft'].includes(q.status)).length > 0 && (
            <Select label="" value="" onChange={e => e.target.value && handleFromQuotation(e.target.value)} style={{ minWidth:200 }}>
              <option value="">⚡ تحويل عرض سعر → عقد اتفاق</option>
              {quotations?.filter(q => ['accepted','sent','draft'].includes(q.status)).map(q => (
                <option key={q.id} value={q.id}>{q.code} — {q.customer_name}</option>
              ))}
            </Select>
          )}
          {onNavigate && (
            <button className="btn-secondary" onClick={() => onNavigate('deliverynotes')}>🚛 سندات التوريد</button>
          )}
          <button className="btn-primary" onClick={() => setShowCreate(true)}>+ عقد جديد</button>
        </div>
      </div>

      {/* Table */}
      <div style={{ background:'var(--surface)', borderRadius:10, border:'1px solid var(--border)', overflow:'hidden' }}>
        <table className="erp-table">
          <thead><tr>
            <th>الكود</th><th>العميل</th><th>التاريخ</th><th>الصلاحية</th>
            <th>الإجمالي</th><th>الحالة</th><th>التسليم</th><th></th>
          </tr></thead>
          <tbody>
            {(!contracts || contracts.length === 0) && (
              <tr><td colSpan={8} style={{ textAlign:'center', padding:32, color:'var(--text-secondary)' }}>
                لا توجد عقود — أنشئ عقداً جديداً أو حوّل عرض سعر
              </td></tr>
            )}
            {contracts?.map(c => (
              <tr key={c.id} style={{ cursor:'pointer' }} onClick={() => openDetail(c.id)}>
                <td style={{ fontWeight:600, color:'var(--teal)' }}>{c.code}</td>
                <td>{c.customer_name}</td>
                <td>{c.date}</td>
                <td style={{ color: c.valid_until ? 'inherit' : 'var(--text-secondary)' }}>
                  {c.valid_until || '—'}
                </td>
                <td style={{ fontVariantNumeric:'tabular-nums' }}>
                  {Number(c.total).toLocaleString('ar-EG', { minimumFractionDigits:2 })} ج
                </td>
                <td>
                  <span style={{ color: STATUS_COLOR[c.status], fontWeight:600 }}>{STATUS_AR[c.status]}</span>
                </td>
                <td>
                  {c.delivery_confirmed
                    ? <span style={{ color:'#15803d', fontWeight:700, fontSize:12 }}>✅ مُسلَّم</span>
                    : <span style={{ color:'var(--text-secondary)', fontSize:12 }}>—</span>}
                </td>
                <td onClick={e => e.stopPropagation()}>
                  <button className="btn-sm" style={{ marginLeft:4 }}
                    onClick={() => api.print(`/supply-contracts/${c.id}/print`)}>
                    🖨️
                  </button>
                  {(c.status === 'draft' || c.status === 'cancelled') && (
                    <button className="btn-sm btn-danger"
                      onClick={() => setConfirmDel(c.id)}>🗑️</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Detail Modal */}
      {detail && detailData && (
        <Modal title={`عقد الاتفاق: ${detailData.code}`} onClose={() => setDetail(null)} size="lg">
          {/* Delivery confirmed banner */}
          {detailData.delivery_confirmed && (
            <div style={{ background:'#F0FDF4', border:'1.5px solid #86EFAC', borderRadius:10, padding:'10px 16px', marginBottom:14, display:'flex', alignItems:'center', gap:12 }}>
              <span style={{ fontSize:20 }}>✅</span>
              <div>
                <div style={{ fontWeight:800, color:'#15803D', fontSize:13 }}>تم التسليم والاستلام رسمياً</div>
                <div style={{ fontSize:11, color:'#166534' }}>
                  تاريخ التسليم: <b>{detailData.delivery_date}</b>
                  {detailData.received_by && <> &nbsp;|&nbsp; المستلم: <b>{detailData.received_by}</b></>}
                  {detailData.receiver_title && <> — <b>{detailData.receiver_title}</b></>}
                </div>
                {detailData.delivery_notes && <div style={{ fontSize:11, color:'#166534', marginTop:3 }}>ملاحظات: {detailData.delivery_notes}</div>}
              </div>
            </div>
          )}

          {/* Header info */}
          <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:10, marginBottom:14 }}>
            {[
              ['العميل',   detailData.customer_name],
              ['التاريخ',  detailData.date],
              ['الحالة',   <span style={{ color: STATUS_COLOR[detailData.status], fontWeight:700 }}>{STATUS_AR[detailData.status]}</span>],
              ['الصلاحية', detailData.valid_until || '—'],
              ['الضمان',   `${detailData.warranty_months} شهر`],
              ['الإجمالي', `${Number(detailData.total).toLocaleString('ar-EG')} ج`],
            ].map(([k,v]) => (
              <div key={k} style={{ background:'var(--surface)', borderRadius:8, padding:'8px 12px', border:'1px solid var(--border)' }}>
                <div style={{ fontSize:10, color:'var(--text-secondary)', fontWeight:700, letterSpacing:'.05em', marginBottom:3 }}>{k}</div>
                <div style={{ fontWeight:600, fontSize:13 }}>{v}</div>
              </div>
            ))}
          </div>

          {/* Terms */}
          {(detailData.payment_terms || detailData.delivery_terms) && (
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10, marginBottom:14 }}>
              {detailData.payment_terms && (
                <div style={{ background:'var(--surface)', borderRadius:8, padding:'10px 12px', border:'1px solid var(--border)' }}>
                  <div style={{ fontSize:10, fontWeight:700, color:'var(--text-secondary)', marginBottom:4 }}>شروط الدفع</div>
                  <div style={{ fontSize:12, lineHeight:1.7 }}>{detailData.payment_terms}</div>
                </div>
              )}
              {detailData.delivery_terms && (
                <div style={{ background:'var(--surface)', borderRadius:8, padding:'10px 12px', border:'1px solid var(--border)' }}>
                  <div style={{ fontSize:10, fontWeight:700, color:'var(--text-secondary)', marginBottom:4 }}>شروط التسليم</div>
                  <div style={{ fontSize:12, lineHeight:1.7 }}>{detailData.delivery_terms}</div>
                </div>
              )}
            </div>
          )}

          {/* Payment Schedule Template */}
          <div style={{ marginBottom:14 }}>
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:8 }}>
              <div style={{ fontWeight:700, fontSize:13 }}>📅 خطة السداد</div>
              <div style={{ display:'flex', gap:6 }}>
                <button className="btn btn-sm btn-primary" onClick={() => openScheduleModal(detail)}>
                  {scheduleData?.installments?.length ? '✏️ تعديل الخطة' : '+ إضافة خطة أقساط'}
                </button>
                {scheduleData?.installments?.length > 0 && (
                  <button className="btn btn-sm" style={{ color:'var(--red)' }}
                    onClick={() => handleDeleteSchedule(detail)}>
                    🗑️ حذف
                  </button>
                )}
              </div>
            </div>
            {scheduleData?.installments?.length > 0 ? (
              <table className="erp-table text-sm">
                <thead>
                  <tr>
                    <th>القسط</th><th>التسمية</th><th>النسبة</th>
                    <th>الاستحقاق</th><th>المبلغ التقريبي</th>
                  </tr>
                </thead>
                <tbody>
                  {scheduleData.installments.map(ins => {
                    const estAmt = (Number(detailData.total) * ins.pct / 100).toFixed(2)
                    const dayLabel = ins.days_offset === 0 ? 'يوم التسليم'
                      : `خلال ${ins.days_offset} يوم`
                    return (
                      <tr key={ins.installment_no}>
                        <td style={{ textAlign:'center', fontWeight:700 }}>{ins.installment_no}</td>
                        <td>{ins.label || '—'}</td>
                        <td style={{ textAlign:'center', fontWeight:700, color:'var(--primary)' }}>{ins.pct.toFixed(1)}%</td>
                        <td style={{ fontSize:12, color:'var(--text-secondary)' }}>{dayLabel}</td>
                        <td style={{ fontWeight:700 }}>{Number(estAmt).toLocaleString('ar-EG', { minimumFractionDigits:2 })} ج</td>
                      </tr>
                    )
                  })}
                  <tr style={{ background:'var(--surface)', fontWeight:700 }}>
                    <td colSpan={2} style={{ textAlign:'left' }}>الإجمالي</td>
                    <td style={{ textAlign:'center' }}>{scheduleData.total_pct?.toFixed(1)}%</td>
                    <td></td>
                    <td>{Number(detailData.total).toLocaleString('ar-EG', { minimumFractionDigits:2 })} ج</td>
                  </tr>
                </tbody>
              </table>
            ) : (
              <div style={{ padding:'12px 16px', background:'var(--surface)', borderRadius:8,
                border:'1px dashed var(--border)', fontSize:12, color:'var(--text-secondary)', textAlign:'center' }}>
                لا توجد خطة أقساط — ستُنشأ الفاتورة باستحقاق واحد عند التسليم
              </div>
            )}
          </div>

          {/* Lines */}
          <table className="erp-table text-sm" style={{ marginBottom:14 }}>
            <thead><tr><th>الوصف</th><th>الموديل</th><th>الكمية</th><th>سعر الوحدة</th><th>الإجمالي</th></tr></thead>
            <tbody>
              {detailData.lines?.map(l => (
                <tr key={l.id}>
                  <td style={{ fontWeight:600 }}>{l.description}</td>
                  <td style={{ color:'var(--text-secondary)', fontSize:11 }}>{l.model_no || '—'}</td>
                  <td>{l.qty} {l.unit}</td>
                  <td>{Number(l.unit_price).toLocaleString('ar-EG')} ج</td>
                  <td style={{ fontWeight:700 }}>{Number(l.total_price).toLocaleString('ar-EG')} ج</td>
                </tr>
              ))}
              <tr style={{ background:'var(--navy, #0C2340)', color:'#fff' }}>
                <td colSpan={4} style={{ textAlign:'left', fontWeight:700, padding:'10px 12px' }}>إجمالي العقد</td>
                <td style={{ fontWeight:800, fontSize:15, padding:'10px 12px' }}>
                  {Number(detailData.total).toLocaleString('ar-EG', { minimumFractionDigits:2 })} ج
                </td>
              </tr>
            </tbody>
          </table>

          {/* Actions */}
          <div style={{ display:'flex', gap:8, flexWrap:'wrap' }}>
            <button className="btn-secondary"
              onClick={() => api.print(`/supply-contracts/${detail}/print`)}>
              🖨️ طباعة العقد
            </button>
            {detailData.delivery_confirmed && (
              <button className="btn-secondary"
                onClick={() => api.print(`/supply-contracts/${detail}/delivery-slip`)}>
                🚛 وصل التسليم
              </button>
            )}
            {detailData.status === 'draft' && (
              <button className="btn-teal" onClick={() => handleActivate(detail)} disabled={acting}>
                ✓ تفعيل العقد
              </button>
            )}
            {detailData.status === 'active' && !detailData.delivery_confirmed && (
              <button className="btn-primary" onClick={() => openDeliverModal(detail)} disabled={acting}>
                🚛 تأكيد التسليم والاستلام
              </button>
            )}
            {detailData.status === 'active' && (
              <button className="btn-secondary" style={{ color:'var(--red)' }}
                onClick={() => handleCancel(detail)} disabled={acting}>
                ✕ إيقاف العقد
              </button>
            )}
            {(detailData.status === 'draft' || detailData.status === 'cancelled') && (
              <button className="btn-danger" onClick={() => setConfirmDel(detail)} disabled={acting}>
                🗑️ حذف
              </button>
            )}
          </div>
        </Modal>
      )}

      {/* Create Modal */}
      {showCreate && (
        <Modal title="إنشاء عقد اتفاق جديد" onClose={() => { setShowCreate(false); setForm(EMPTY_FORM) }} size="lg">
          <FormGrid>
            <Select label="العميل *" value={form.customer_id} onChange={e => setForm(f => ({ ...f, customer_id: e.target.value }))}>
              <option value="">— اختر عميلاً —</option>
              {customers?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
            <Input label="تاريخ العقد *" type="date" value={form.date} onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
            <Input label="صالح حتى" type="date" value={form.valid_until} onChange={e => setForm(f => ({ ...f, valid_until: e.target.value }))} />
            <Input label="فترة الضمان (شهر)" type="number" value={form.warranty_months} onChange={e => setForm(f => ({ ...f, warranty_months: e.target.value }))} />
            <div style={{ gridColumn: '1 / -1' }}>
              <Textarea label="ما يشمله الضمان" value={form.warranty_description || ''} onChange={e => setForm(f => ({ ...f, warranty_description: e.target.value }))} placeholder="وصف تفصيلي لما يغطيه الضمان..." />
            </div>
            <Input label="ضريبة %" type="number" value={form.tax_pct} onChange={e => setForm(f => ({ ...f, tax_pct: e.target.value }))} />
            <Input label="خصم (ج.م)" type="number" value={form.discount_amt} onChange={e => setForm(f => ({ ...f, discount_amt: e.target.value }))} />
            <div style={{ gridColumn: '1 / -1' }}>
              <Textarea label="شروط الدفع" value={form.payment_terms} onChange={e => setForm(f => ({ ...f, payment_terms: e.target.value }))} />
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <Textarea label="شروط التسليم" value={form.delivery_terms} onChange={e => setForm(f => ({ ...f, delivery_terms: e.target.value }))} />
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <Textarea label="نطاق الأعمال / المواصفات" value={form.scope_of_work} onChange={e => setForm(f => ({ ...f, scope_of_work: e.target.value }))} />
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <Textarea label="شروط خاصة" value={form.special_terms} onChange={e => setForm(f => ({ ...f, special_terms: e.target.value }))} />
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <Textarea label="ملاحظات" value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
            </div>
          </FormGrid>

          {/* Lines */}
          <div style={{ marginTop:16, padding:12, background:'var(--bg)', borderRadius:8, border:'1px solid var(--border)' }}>
            <h4 style={{ fontWeight:600, marginBottom:10 }}>البنود</h4>
            <div style={{ display:'grid', gridTemplateColumns:'2fr 1fr 1fr 1fr 1fr 1fr auto', gap:8, alignItems:'end' }}>
              <Input label="الوصف *" value={newLine.description} onChange={e => setNewLine(n => ({ ...n, description: e.target.value }))} />
              <Input label="الموديل"  value={newLine.model_no}    onChange={e => setNewLine(n => ({ ...n, model_no: e.target.value }))} />
              <Input label="الكمية"   type="number" value={newLine.qty}        onChange={e => setNewLine(n => ({ ...n, qty: e.target.value }))} />
              <Input label="الوحدة"   value={newLine.unit}        onChange={e => setNewLine(n => ({ ...n, unit: e.target.value }))} />
              <Input label="السعر *"  type="number" value={newLine.unit_price} onChange={e => setNewLine(n => ({ ...n, unit_price: e.target.value }))} />
              <Select label="مادة (مخزون)" value={newLine.material_id} onChange={e => setNewLine(n => ({ ...n, material_id: e.target.value }))}
                options={[{ value:'', label:'— اختياري —' }, ...(materials||[]).map(m => ({ value: String(m.id), label: m.name_ar || m.name || m.code }))]} />
              <button className="btn-teal" style={{ padding:'8px 14px' }} onClick={addLine}>+</button>
            </div>
            {form.lines.length > 0 && (
              <table className="erp-table text-sm" style={{ marginTop:10 }}>
                <thead><tr><th>الوصف</th><th>الموديل</th><th>الكمية</th><th>السعر</th><th>الإجمالي</th><th></th></tr></thead>
                <tbody>
                  {form.lines.map((l,i) => (
                    <tr key={i}>
                      <td>{l.description}</td>
                      <td style={{ color:'var(--text-secondary)' }}>{l.model_no || '—'}</td>
                      <td>{l.qty} {l.unit}</td>
                      <td>{Number(l.unit_price).toLocaleString()}</td>
                      <td style={{ fontWeight:600 }}>{(Number(l.qty)*Number(l.unit_price)).toLocaleString()}</td>
                      <td><button className="btn-sm" style={{ color:'var(--red)' }} onClick={() => removeLine(i)}>✕</button></td>
                    </tr>
                  ))}
                  <tr style={{ background:'var(--surface)', fontWeight:700 }}>
                    <td colSpan={4} style={{ textAlign:'left', padding:'8px 12px' }}>الإجمالي المتوقع</td>
                    <td colSpan={2} style={{ padding:'8px 12px', color:'var(--teal)' }}>
                      {calcTotal(form.lines, form.tax_pct, form.discount_amt)} ج.م
                    </td>
                  </tr>
                </tbody>
              </table>
            )}
          </div>

          <ModalFooter>
            <button className="btn-primary"
              disabled={!form.customer_id || !form.date || acting}
              onClick={handleCreate}>
              {acting ? '…' : 'إنشاء العقد'}
            </button>
            <button className="btn-secondary" onClick={() => { setShowCreate(false); setForm(EMPTY_FORM) }}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      {/* ═══ Modal: خطة أقساط العقد ═══ */}
      {scheduleModal && (
        <Modal title="📅 خطة أقساط العقد" onClose={() => setScheduleModal(null)} size="md">
          <div style={{ marginBottom:12, fontSize:12, color:'var(--text-secondary)',
            background:'var(--surface)', borderRadius:8, padding:'8px 12px',
            border:'1px solid var(--border)' }}>
            حدد نسبة كل قسط من إجمالي العقد وعدد الأيام من تاريخ التسليم.
            مجموع النسب يجب أن يساوي 100%.
          </div>
          <table style={{ width:'100%', borderCollapse:'collapse', fontSize:13, marginBottom:12 }}>
            <thead>
              <tr style={{ background:'var(--surface)', borderBottom:'2px solid var(--border)' }}>
                <th style={{ padding:'8px 10px', textAlign:'right', fontWeight:700 }}>التسمية</th>
                <th style={{ padding:'8px 10px', textAlign:'center', fontWeight:700 }}>النسبة %</th>
                <th style={{ padding:'8px 10px', textAlign:'center', fontWeight:700 }}>أيام من التسليم</th>
                <th style={{ width:36 }}></th>
              </tr>
            </thead>
            <tbody>
              {scheduleRows.map((row, i) => (
                <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                  <td style={{ padding:'6px 8px' }}>
                    <input
                      className="input-field" style={{ width:'100%', fontSize:12 }}
                      placeholder="مثال: دفعة مقدمة"
                      value={row.label}
                      onChange={e => setScheduleRows(rs => rs.map((r,j) => j===i ? {...r, label: e.target.value} : r))}
                    />
                  </td>
                  <td style={{ padding:'6px 8px' }}>
                    <input
                      className="input-field" style={{ width:80, fontSize:12, textAlign:'center' }}
                      type="number" min="0" max="100" step="0.5"
                      placeholder="0"
                      value={row.pct}
                      onChange={e => setScheduleRows(rs => rs.map((r,j) => j===i ? {...r, pct: e.target.value} : r))}
                    />
                  </td>
                  <td style={{ padding:'6px 8px' }}>
                    <input
                      className="input-field" style={{ width:90, fontSize:12, textAlign:'center' }}
                      type="number" min="0" step="1"
                      placeholder="0"
                      value={row.days_offset}
                      onChange={e => setScheduleRows(rs => rs.map((r,j) => j===i ? {...r, days_offset: e.target.value} : r))}
                    />
                  </td>
                  <td style={{ padding:'6px 4px', textAlign:'center' }}>
                    {scheduleRows.length > 1 && (
                      <button style={{ background:'none', border:'none', cursor:'pointer',
                        color:'var(--red)', fontSize:16, padding:'2px 4px' }}
                        onClick={() => setScheduleRows(rs => rs.filter((_,j) => j!==i))}>
                        ×
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:4 }}>
            <button className="btn btn-sm" onClick={() =>
              setScheduleRows(rs => [...rs, { label:'', pct:'', days_offset:'0' }])}>
              + إضافة قسط
            </button>
            <div style={{ fontSize:12, fontWeight:700,
              color: Math.abs(scheduleRows.reduce((s,r) => s + Number(r.pct||0),0) - 100) < 0.01
                ? 'var(--teal)' : 'var(--red)' }}>
              المجموع: {scheduleRows.reduce((s,r) => s + Number(r.pct||0),0).toFixed(1)}%
            </div>
          </div>
          <ModalFooter>
            <button className="btn btn-primary" onClick={handleSaveSchedule} disabled={acting}>
              {acting ? '...' : '💾 حفظ الخطة'}
            </button>
            <button className="btn" onClick={() => setScheduleModal(null)}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      {/* ═══ Modal: تأكيد التسليم والاستلام ═══ */}
      {deliverModal && (
        <Modal title="🚛 تأكيد التسليم والاستلام" onClose={() => setDeliverModal(null)} size="md">
          <div style={{ marginBottom:12, fontSize:12, color:'var(--text-secondary)',
            background:'var(--surface)', borderRadius:8, padding:'8px 12px',
            border:'1px solid var(--border)' }}>
            سيتم إنشاء فاتورة مبيعات تلقائياً بإجمالي العقد، وتحديث المخزون للبنود المرتبطة بمواد،
            وتطبيق خطة الأقساط لو موجودة.
          </div>
          <FormGrid cols={2}>
            <Input label="تاريخ التسليم الفعلي *" type="date"
              value={deliverForm.delivery_date}
              onChange={e => setDeliverForm(f => ({ ...f, delivery_date: e.target.value }))} />
            <Input label="اسم المستلم" value={deliverForm.received_by}
              onChange={e => setDeliverForm(f => ({ ...f, received_by: e.target.value }))} />
            <Input label="وظيفة / صفة المستلم" value={deliverForm.receiver_title}
              onChange={e => setDeliverForm(f => ({ ...f, receiver_title: e.target.value }))} />
            <Input label="رقم لوحة السيارة" value={deliverForm.vehicle_plate}
              onChange={e => setDeliverForm(f => ({ ...f, vehicle_plate: e.target.value }))} />
            <Input label="نوع السيارة" value={deliverForm.vehicle_type}
              onChange={e => setDeliverForm(f => ({ ...f, vehicle_type: e.target.value }))} />
            <Input label="اسم السائق" value={deliverForm.driver_name}
              onChange={e => setDeliverForm(f => ({ ...f, driver_name: e.target.value }))} />
            <Input label="رقم رخصة القيادة" value={deliverForm.driver_license}
              onChange={e => setDeliverForm(f => ({ ...f, driver_license: e.target.value }))} />
            <Input label="موبايل السائق" value={deliverForm.driver_phone}
              onChange={e => setDeliverForm(f => ({ ...f, driver_phone: e.target.value }))} />
            <Textarea label="ملاحظات الاستلام" value={deliverForm.delivery_notes} span={2}
              onChange={e => setDeliverForm(f => ({ ...f, delivery_notes: e.target.value }))} />
          </FormGrid>
          <ModalFooter>
            <button className="btn-primary" onClick={handleConfirmDelivery} disabled={acting || !deliverForm.delivery_date}>
              {acting ? '...' : '✅ تأكيد التسليم'}
            </button>
            <button className="btn" onClick={() => setDeliverModal(null)}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      <ConfirmDialog
        open={!!confirmDel}
        message="هل تريد حذف هذا العقد نهائياً؟"
        onConfirm={() => { handleDelete(confirmDel); setConfirmDel(null) }}
        onCancel={() => setConfirmDel(null)}
      />
    </div>
  )
}
