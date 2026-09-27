/**
 * أوامر التوريد — Delivery Orders
 * فورم رقمي كامل يشمل بيانات السائق والمركبة
 */
import { useState, useEffect, useCallback } from 'react'
import { useApi } from '../hooks/useApi'
import { useToast } from '../store/toast'
import api from '../api/client'
import { fetchWorkshopProfile } from './WorkshopProfile'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

const today = () => new Date().toISOString().slice(0, 10)

const STATUS_AR    = { draft: 'مسودة', confirmed: 'مؤكد', delivered: 'مُسلَّم', cancelled: 'ملغي' }
const STATUS_COLOR = { draft: 'var(--text-secondary)', confirmed: 'var(--teal)', delivered: '#15803d', cancelled: 'var(--red)' }

const EMPTY_FORM = {
  order_date:       today(),
  customer_id:      '',
  reference:        '',
  notes:            '',
  delivery_date:    '',
  delivery_address: '',
  received_by:      '',
  receiver_title:   '',
  receiver_phone:   '',
  delivery_notes:   '',
  vehicle_plate:    '',
  vehicle_type:     '',
  driver_name:      '',
  driver_license:   '',
  driver_phone:     '',
  lines: [],
}

const EMPTY_LINE = { description: '', model_no: '', qty: '1', unit: 'قطعة', notes: '', equipment_id: '', work_order_id: '' }

const VEHICLE_TYPES = ['', 'نقل', 'وانيت', 'تريلا', 'بيك أب', 'فورد', 'سيارة ملاكي', 'أخرى']

export default function DeliveryNotes() {
  const { data: customers } = useApi(() => api.customers.list(), [])
  // محطة 16 (واجهة): أوامر الإنتاج الجاهزة للربط — عشان التسليم يخصم فعليًا من FG
  const { data: pipeline } = useApi(() => api.production.pipeline(), [])
  const [orders,     setOrders]     = useState([])
  const [loadingOrders, setLoadingOrders] = useState(true)
  const toast = useToast()

  const [showCreate, setShowCreate] = useState(false)
  const [detail,     setDetail]     = useState(null)
  const [form,       setForm]       = useState(EMPTY_FORM)
  const [newLine,    setNewLine]     = useState(EMPTY_LINE)
  const [confirmDel, setConfirmDel] = useState(null)
  const [confirmCancel, setConfirmCancel] = useState(null)
  const [acting,     setActing]     = useState(false)
  const [activeTab,  setActiveTab]  = useState('basic')

  const loadOrders = useCallback(async () => {
    try {
      const data = await api.deliveryOrders.list()
      setOrders(data)
    } catch { toast('تعذّر تحميل أوامر التوريد', 'error') }
    finally { setLoadingOrders(false) }
  }, [])

  useEffect(() => { loadOrders() }, [loadOrders])

  const act = async (fn) => {
    if (acting) return
    setActing(true)
    try { await fn() }
    catch (e) { toast(e.message || 'حدث خطأ', 'error') }
    finally { setActing(false) }
  }

  const f = (field) => (e) => setForm(prev => ({ ...prev, [field]: e.target.value }))

  const addLine = () => {
    if (!newLine.description.trim()) { toast('أدخل وصف البند', 'error'); return }
    setForm(prev => ({ ...prev, lines: [...prev.lines, { ...newLine, id: Date.now() }] }))
    setNewLine(EMPTY_LINE)
  }

  const removeLine = (id) => setForm(prev => ({ ...prev, lines: prev.lines.filter(l => l.id !== id) }))

  const handleCreate = () => act(async () => {
    if (!form.customer_id)          { toast('اختر العميل', 'error'); return }
    if (!form.order_date)           { toast('التاريخ مطلوب', 'error'); return }
    if (!form.lines.length)         { toast('أضف بنداً واحداً على الأقل', 'error'); return }
    if (!form.driver_name.trim())   { toast('اسم السائق مطلوب (تبويب السيارة والسائق)', 'error'); return }
    if (!form.vehicle_plate.trim()) { toast('رقم اللوحة مطلوب (تبويب السيارة والسائق)', 'error'); return }

    const created = await api.deliveryOrders.create({
      ...form,
      customer_id: Number(form.customer_id),
      lines: form.lines.map(({ id: _id, equipment_id, work_order_id, ...l }) => ({
        ...l,
        equipment_id:  equipment_id  ? Number(equipment_id)  : null,
        work_order_id: work_order_id ? Number(work_order_id) : null,
      })),
    })
    setOrders(prev => [created, ...prev])
    toast('تم إنشاء أمر التوريد ✅')
    setShowCreate(false)
    setForm(EMPTY_FORM)
    setActiveTab('basic')
  })

  const handleConfirm = (o) => act(async () => {
    const updated = await api.deliveryOrders.setStatus(o.id, 'confirmed')
    setOrders(prev => prev.map(x => x.id === o.id ? updated : x))
    setDetail(updated)
    toast('تم تأكيد الأمر')
  })

  const handleDeliver = (o) => act(async () => {
    // محطة 16: لو أي بند مربوط بأمر إنتاج معندوش رصيد FG كافي، الباك اند
    // بيرفض العملية بالكامل (400) — و act() بيعرض رسالة الخطأ تلقائيًا.
    const updated = await api.deliveryOrders.setStatus(o.id, 'delivered')
    setOrders(prev => prev.map(x => x.id === o.id ? updated : x))
    setDetail(updated)
    toast('تم تسجيل التسليم ✅')
  })

  const handleDelete = (id) => act(async () => {
    await api.deliveryOrders.delete(id)
    setOrders(prev => prev.filter(o => o.id !== id))
    setDetail(null)
    toast('تم الحذف')
  })

  // الباك اند بيسمح بإلغاء الأمر من draft أو confirmed (قبل التسليم الفعلي)
  const handleCancel = (o) => act(async () => {
    const updated = await api.deliveryOrders.setStatus(o.id, 'cancelled')
    setOrders(prev => prev.map(x => x.id === o.id ? updated : x))
    setDetail(updated)
    toast('تم إلغاء الأمر')
  })

  const handlePrint = async (o) => {
    const ws = await fetchWorkshopProfile()
    const w = window.open('', '_blank', 'width=900,height=700')
    if (!w) { toast('فعّل النوافذ المنبثقة (popup) في المتصفح عشان تقدر تطبع', 'error'); return }

    const statusAr    = STATUS_AR[o.status] || o.status
    const statusColor = o.status === 'delivered' ? '#15803d' : '#0C2340'

    // logo_data = base64 data:URI مخزّنة في DB — تعمل في popup بدون مصادقة
    const logoSrc = ws.logo_data || null
    const logoHtml = logoSrc
      ? `<img src="${logoSrc}" style="height:64px;object-fit:contain;"/>`
      : `<div style="width:64px;height:64px;background:#C8A550;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:900;color:#fff;">${(ws.name || 'W')[0]}</div>`

    const rows = (o.lines || []).map((l, i) => `
      <tr style="background:${i % 2 === 0 ? '#fafafa' : '#fff'}">
        <td style="padding:9px 10px;text-align:center;color:#999;font-size:12px">${i + 1}</td>
        <td style="padding:9px 10px"><b>${l.description}</b>${l.model_no ? `<br/><small style="color:#888;font-size:10px">${l.model_no}</small>` : ''}</td>
        <td style="padding:9px 10px;text-align:center">${l.qty} ${l.unit}</td>
        <td style="padding:9px 10px;font-size:12px;color:#555">${l.notes || '—'}</td>
      </tr>`).join('')

    w.document.write(`<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head><meta charset="UTF-8"/><title>أمر توريد — ${o.code}</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Cairo',sans-serif;font-size:13px;color:#1a1a2e;background:#eee;direction:rtl;line-height:1.6;}
@page{size:A4;margin:10mm 8mm;}
@media print{body{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact;}.no-print{display:none!important;}.page{box-shadow:none;border-radius:0;margin:0;max-width:100%;}}
.page{max-width:820px;margin:14px auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 4px 40px rgba(0,0,0,.13);}
.header{background:linear-gradient(135deg,#081828,#0C2340);color:#fff;padding:20px 26px;display:flex;justify-content:space-between;align-items:center;}
.doc-type{font-size:22px;font-weight:900;color:#C8A550;line-height:1;}
.doc-num{font-size:12px;color:rgba(255,255,255,.6);margin-top:4px;}
.gold-stripe{height:4px;background:linear-gradient(90deg,#C8A550,rgba(200,165,80,.3),#C8A550);}
.body{padding:20px 26px;}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px;}
.info-box{background:#F8F7F4;border:1px solid #E8E6E0;border-radius:10px;padding:12px 16px;border-top:3px solid #C8A550;}
.info-box h3{font-size:10px;font-weight:800;color:#7A7870;letter-spacing:.1em;margin-bottom:8px;border-bottom:1px solid #E8E6E0;padding-bottom:4px;}
.info-row{display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px;}
.info-row span:first-child{color:#666;}
.info-row span:last-child{font-weight:600;}
.vehicle-box{background:#EFF6FF;border:2px solid #93C5FD;border-radius:10px;padding:12px 16px;margin-bottom:18px;}
.vehicle-box h3{font-size:11px;font-weight:800;color:#1d4ed8;letter-spacing:.05em;margin-bottom:10px;border-bottom:1px solid #BFDBFE;padding-bottom:5px;}
.vehicle-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;}
.v-item{background:#fff;border:1px solid #BFDBFE;border-radius:7px;padding:8px 10px;}
.v-item .label{font-size:10px;color:#3b82f6;margin-bottom:2px;}
.v-item .value{font-size:13px;font-weight:700;color:#1e3a8a;}
table{width:100%;border-collapse:collapse;margin-bottom:18px;border-radius:10px;overflow:hidden;border:1px solid #E8E6E0;}
thead tr{background:#0C2340;}
thead th{padding:9px 10px;font-size:11px;color:rgba(255,255,255,.75);font-weight:700;text-align:right;}
tbody td{border-bottom:1px solid #E8E6E0;font-size:12.5px;}
tbody tr:last-child td{border-bottom:none;}
.sig-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:18px;margin-top:24px;padding-top:20px;border-top:1px solid #E8E6E0;}
.sig-box{border:1px solid #E8E6E0;border-radius:10px;padding:14px;text-align:center;}
.sig-box .title{font-size:12px;font-weight:800;margin-bottom:3px;}
.sig-box .sub{font-size:10px;color:#888;margin-bottom:44px;}
.sig-box .line{border-top:1px solid #ccc;padding-top:5px;font-size:10px;color:#aaa;margin:0 8px;}
.no-print{text-align:center;margin:20px;}
.btn-p{padding:10px 28px;background:#0C2340;color:#fff;border:none;border-radius:8px;font-size:14px;cursor:pointer;font-family:inherit;}
</style></head>
<body onload="setTimeout(()=>window.print(),500)">
<button class="no-print btn-p" onclick="window.print()">🖨 طباعة</button>
<div class="page">
  <div class="header">
    <div style="display:flex;align-items:center;gap:14px;">
      ${logoHtml}
      <div>
        <div style="font-size:18px;font-weight:900;color:#fff;">${ws.name || 'الورشة'}</div>
        ${ws.address ? `<div style="font-size:11px;color:rgba(255,255,255,.65);margin-top:2px;">${ws.address}</div>` : ''}
        ${ws.phone   ? `<div style="font-size:11px;color:rgba(255,255,255,.65);">📞 ${ws.phone}</div>` : ''}
        ${ws.tax_id  ? `<div style="font-size:10px;color:rgba(255,255,255,.5);">الرقم الضريبي: ${ws.tax_id}</div>` : ''}
      </div>
    </div>
    <div style="text-align:left;">
      <div style="font-size:20px;font-weight:900;color:#C8A550;">أمر توريد</div>
      <div style="font-size:12px;color:rgba(255,255,255,.6);margin-top:2px;">${o.code}</div>
      <div style="font-size:11px;color:rgba(255,255,255,.65);margin-top:6px;">تاريخ الأمر: <b>${o.order_date}</b></div>
      ${o.delivery_date ? `<div style="font-size:11px;color:rgba(255,255,255,.65);">تاريخ التسليم: <b>${o.delivery_date}</b></div>` : ''}
      <div style="margin-top:4px;font-weight:800;color:${o.status === 'delivered' ? '#86efac' : '#C8A550'}">${statusAr}</div>
    </div>
  </div>
  <div class="gold-stripe"></div>
  <div class="body">
    <div class="grid2">
      <div class="info-box">
        <h3>بيانات العميل / الجهة المستلِمة</h3>
        <div class="info-row"><span>الاسم</span><span>${o.customer_name}</span></div>
        ${o.customer_phone ? `<div class="info-row"><span>الهاتف</span><span>${o.customer_phone}</span></div>` : ''}
        ${o.customer_address ? `<div class="info-row"><span>العنوان</span><span>${o.customer_address}</span></div>` : ''}
        ${o.delivery_address ? `<div class="info-row"><span>عنوان التسليم</span><span>${o.delivery_address}</span></div>` : ''}
        ${o.received_by ? `<div class="info-row"><span>المستلم</span><span>${o.received_by}</span></div>` : ''}
        ${o.receiver_title ? `<div class="info-row"><span>الصفة</span><span>${o.receiver_title}</span></div>` : ''}
        ${o.receiver_phone ? `<div class="info-row"><span>هاتف المستلم</span><span>${o.receiver_phone}</span></div>` : ''}
      </div>
      <div class="info-box">
        <h3>بيانات الأمر</h3>
        <div class="info-row"><span>رقم الأمر</span><span>${o.code}</span></div>
        <div class="info-row"><span>تاريخ الإنشاء</span><span>${o.order_date}</span></div>
        ${o.reference ? `<div class="info-row"><span>مرجع</span><span>${o.reference}</span></div>` : ''}
        <div class="info-row"><span>الحالة</span><span style="font-weight:800;color:${statusColor}">${statusAr}</span></div>
      </div>
    </div>

    <div class="vehicle-box">
      <h3>🚛 بيانات المركبة والسائق</h3>
      <div class="vehicle-grid">
        <div class="v-item"><div class="label">رقم اللوحة</div><div class="value">${o.vehicle_plate || '—'}</div></div>
        <div class="v-item"><div class="label">نوع المركبة</div><div class="value">${o.vehicle_type || '—'}</div></div>
        <div class="v-item"><div class="label">اسم السائق</div><div class="value">${o.driver_name || '—'}</div></div>
        <div class="v-item"><div class="label">رقم الرخصة</div><div class="value">${o.driver_license || '—'}</div></div>
        <div class="v-item"><div class="label">موبايل السائق</div><div class="value">${o.driver_phone || '—'}</div></div>
      </div>
    </div>

    <table>
      <thead><tr>
        <th style="width:36px;text-align:center">#</th>
        <th>الصنف / البند</th>
        <th style="width:110px;text-align:center">الكمية</th>
        <th>ملاحظات</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>

    ${o.delivery_notes ? `<div style="background:#fffbf0;border:1px solid #fde68a;border-radius:8px;padding:12px 14px;margin-bottom:18px;font-size:12.5px;"><b>ملاحظات التسليم:</b> ${o.delivery_notes}</div>` : ''}
    ${o.notes ? `<div style="background:#F8F7F4;border:1px solid #E8E6E0;border-radius:8px;padding:12px 14px;margin-bottom:18px;font-size:12.5px;"><b>ملاحظات:</b> ${o.notes}</div>` : ''}

    <div class="sig-row">
      <div class="sig-box">
        <div class="title">المورِّد / الطرف الأول</div>
        <div class="sub">الاسم والتوقيع والختم</div>
        <div class="line">التوقيع</div>
      </div>
      <div class="sig-box">
        <div class="title">المستلِم / الطرف الثاني</div>
        <div class="sub">${o.received_by || o.customer_name}</div>
        <div class="line">التوقيع</div>
      </div>
      <div class="sig-box">
        <div class="title">السائق</div>
        <div class="sub">${o.driver_name || '—'}</div>
        <div class="line">التوقيع</div>
      </div>
    </div>
  </div>
</div>
</body></html>`)
    w.document.close()
  }

  const TabBtn = ({ id, label }) => (
    <button onClick={() => setActiveTab(id)} style={{
      padding: '8px 18px', border: '1px solid',
      borderColor: activeTab === id ? 'var(--teal)' : 'var(--border)',
      borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: 13,
      background: activeTab === id ? 'var(--teal)' : 'var(--surface)',
      color: activeTab === id ? '#fff' : 'var(--text-secondary)',
      transition: 'all .15s',
    }}>{label}</button>
  )

  const summary = {
    total:     orders.length,
    confirmed: orders.filter(o => o.status === 'confirmed').length,
    delivered: orders.filter(o => o.status === 'delivered').length,
    draft:     orders.filter(o => o.status === 'draft').length,
  }

  if (loadingOrders) return <PageSpinner />

  return (
    <div className="p-4 max-w-6xl mx-auto" dir="rtl">

      {/* KPI */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12, marginBottom: 20 }}>
        {[
          ['إجمالي الأوامر', summary.total,     'var(--teal)'],
          ['مؤكدة',          summary.confirmed,  '#2563eb'],
          ['مُسلَّمة',         summary.delivered, '#15803d'],
          ['مسودة',          summary.draft,      '#b45309'],
        ].map(([label, val, color]) => (
          <div key={label} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', borderTop: `3px solid ${color}` }}>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 22, fontWeight: 700, color }}>{val}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ fontSize: 20, fontWeight: 700 }}>🚛 أوامر التوريد</h2>
        <button className="btn-primary" onClick={() => { setForm(EMPTY_FORM); setActiveTab('basic'); setShowCreate(true) }}>
          + أمر توريد جديد
        </button>
      </div>

      {/* Table */}
      <div style={{ background: 'var(--surface)', borderRadius: 10, border: '1px solid var(--border)', overflow: 'hidden' }}>
        <table className="erp-table">
          <thead><tr>
            <th>الكود</th><th>العميل</th><th>التاريخ</th>
            <th>السائق</th><th>اللوحة</th><th>الحالة</th><th>البنود</th><th></th>
          </tr></thead>
          <tbody>
            {!orders.length && (
              <tr><td colSpan={8} style={{ textAlign: 'center', padding: 40, color: 'var(--text-secondary)' }}>
                لا توجد أوامر توريد — أنشئ أمراً جديداً
              </td></tr>
            )}
            {orders.map(o => (
              <tr key={o.id} style={{ cursor: 'pointer' }} onClick={() => setDetail(o)}>
                <td style={{ fontWeight: 600, color: 'var(--teal)' }}>{o.code}</td>
                <td>{o.customer_name}</td>
                <td>{o.order_date}</td>
                <td>{o.driver_name || '—'}</td>
                <td style={{ fontFamily: 'monospace' }}>{o.vehicle_plate || '—'}</td>
                <td><span style={{ color: STATUS_COLOR[o.status], fontWeight: 600 }}>{STATUS_AR[o.status]}</span></td>
                <td style={{ color: 'var(--text-secondary)' }}>{o.lines.length} بند</td>
                <td onClick={e => e.stopPropagation()} style={{ display: 'flex', gap: 4 }}>
                  <button className="btn-sm" onClick={() => handlePrint(o)}>🖨️</button>
                  {(o.status === 'draft' || o.status === 'cancelled') && (
                    <button className="btn-sm btn-danger" onClick={() => setConfirmDel(o.id)}>🗑️</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Detail Modal */}
      {detail && (
        <Modal title={`أمر التوريد: ${detail.code}`} onClose={() => setDetail(null)} size="lg">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginBottom: 14 }}>
            {[
              ['العميل',        detail.customer_name],
              ['تاريخ الأمر',   detail.order_date],
              ['الحالة',        <span style={{ color: STATUS_COLOR[detail.status], fontWeight: 700 }}>{STATUS_AR[detail.status]}</span>],
              ['عنوان التسليم', detail.delivery_address || '—'],
              ['المستلم',       detail.received_by  || '—'],
              ['صفة المستلم',   detail.receiver_title || '—'],
            ].map(([k, v]) => (
              <div key={k} style={{ background: 'var(--surface)', borderRadius: 8, padding: '8px 12px', border: '1px solid var(--border)' }}>
                <div style={{ fontSize: 10, color: 'var(--text-secondary)', fontWeight: 700, marginBottom: 3 }}>{k}</div>
                <div style={{ fontWeight: 600, fontSize: 13 }}>{v}</div>
              </div>
            ))}
          </div>

          {/* بيانات المركبة */}
          <div style={{ background: '#EFF6FF', border: '1.5px solid #93C5FD', borderRadius: 10, padding: '12px 16px', marginBottom: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 800, color: '#1d4ed8', marginBottom: 10 }}>🚛 بيانات المركبة والسائق</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 8 }}>
              {[
                ['اللوحة',        detail.vehicle_plate  || '—'],
                ['نوع المركبة',   detail.vehicle_type   || '—'],
                ['السائق',        detail.driver_name    || '—'],
                ['رقم الرخصة',   detail.driver_license || '—'],
                ['موبايل السائق', detail.driver_phone   || '—'],
              ].map(([k, v]) => (
                <div key={k} style={{ background: '#fff', border: '1px solid #BFDBFE', borderRadius: 7, padding: '7px 10px' }}>
                  <div style={{ fontSize: 10, color: '#3b82f6', marginBottom: 2 }}>{k}</div>
                  <div style={{ fontWeight: 700, fontSize: 12, color: '#1e3a8a' }}>{v}</div>
                </div>
              ))}
            </div>
          </div>

          {/* البنود */}
          <table className="erp-table text-sm" style={{ marginBottom: 14 }}>
            <thead><tr><th>#</th><th>الوصف</th><th>الموديل</th><th>الكمية</th><th>ملاحظات</th></tr></thead>
            <tbody>
              {detail.lines.map((l, i) => (
                <tr key={i}>
                  <td style={{ color: 'var(--text-secondary)' }}>{i + 1}</td>
                  <td style={{ fontWeight: 600 }}>{l.description}</td>
                  <td style={{ color: 'var(--text-secondary)', fontSize: 11 }}>{l.model_no || '—'}</td>
                  <td>{l.qty} {l.unit}</td>
                  <td style={{ color: 'var(--text-secondary)', fontSize: 11 }}>{l.notes || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {detail.delivery_notes && (
            <div style={{ background: '#fffbf0', border: '1px solid #fde68a', borderRadius: 8, padding: '10px 14px', marginBottom: 14, fontSize: 12.5 }}>
              <b>ملاحظات التسليم:</b> {detail.delivery_notes}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className="btn-secondary" onClick={() => handlePrint(detail)}>🖨️ طباعة</button>
            {detail.status === 'draft' && (
              <button className="btn-teal" onClick={() => handleConfirm(detail)} disabled={acting}>✓ تأكيد الأمر</button>
            )}
            {detail.status === 'confirmed' && (
              <button className="btn-teal" onClick={() => handleDeliver(detail)} disabled={acting}>🚛 تسجيل التسليم</button>
            )}
            {(detail.status === 'draft' || detail.status === 'confirmed') && (
              <button className="btn-danger" onClick={() => setConfirmCancel(detail)} disabled={acting}>
                ❌ إلغاء الأمر
              </button>
            )}
            {(detail.status === 'draft' || detail.status === 'cancelled') && (
              <button className="btn-danger" onClick={() => { setConfirmDel(detail.id); setDetail(null) }} disabled={acting}>
                🗑️ حذف
              </button>
            )}
          </div>
        </Modal>
      )}

      {/* Create Modal */}
      {showCreate && (
        <Modal title="إنشاء أمر توريد جديد" onClose={() => { setShowCreate(false); setForm(EMPTY_FORM) }} size="lg">

          {/* Tabs */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
            <TabBtn id="basic"    label="📋 البيانات الأساسية" />
            <TabBtn id="delivery" label="📦 التسليم والاستلام" />
            <TabBtn id="vehicle"  label="🚛 السيارة والسائق" />
            <TabBtn id="lines"    label="📝 البنود" />
          </div>

          {activeTab === 'basic' && (
            <FormGrid>
              <Select label="العميل *" value={form.customer_id} onChange={f('customer_id')}>
                <option value="">— اختر عميلاً —</option>
                {customers?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
              <Input label="تاريخ الأمر *" type="date" value={form.order_date} onChange={f('order_date')} />
              <Input label="مرجع / رقم عقد (اختياري)" value={form.reference} onChange={f('reference')}
                placeholder="SC-0001 أو رقم عرض السعر" />
              <Textarea label="ملاحظات عامة" value={form.notes} onChange={f('notes')} span={2} />
            </FormGrid>
          )}

          {activeTab === 'delivery' && (
            <FormGrid>
              <Input label="تاريخ التسليم المتوقع" type="date" value={form.delivery_date} onChange={f('delivery_date')} />
              <Input label="عنوان موقع التسليم" value={form.delivery_address} onChange={f('delivery_address')}
                placeholder="الموقع / الفرع / العنوان الكامل" />
              <Input label="اسم المستلم" value={form.received_by} onChange={f('received_by')} />
              <Input label="صفة / وظيفة المستلم" value={form.receiver_title} onChange={f('receiver_title')} />
              <Input label="هاتف المستلم" value={form.receiver_phone} onChange={f('receiver_phone')} />
              <Textarea label="ملاحظات التسليم" value={form.delivery_notes} onChange={f('delivery_notes')} span={2} />
            </FormGrid>
          )}

          {activeTab === 'vehicle' && (
            <div>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 14, color: 'var(--teal)', padding: '8px 0', borderBottom: '2px solid var(--border)' }}>
                🚛 بيانات المركبة
              </div>
              <FormGrid>
                <Input label="رقم اللوحة *" value={form.vehicle_plate} onChange={f('vehicle_plate')} placeholder="أ ب ج 1234" />
                <Select label="نوع المركبة" value={form.vehicle_type} onChange={f('vehicle_type')}>
                  {VEHICLE_TYPES.map(t => <option key={t} value={t}>{t || '— اختر النوع —'}</option>)}
                </Select>
              </FormGrid>

              <div style={{ fontWeight: 700, fontSize: 14, margin: '20px 0 14px', color: 'var(--teal)', padding: '8px 0', borderBottom: '2px solid var(--border)' }}>
                👤 بيانات السائق
              </div>
              <FormGrid>
                <Input label="اسم السائق *" value={form.driver_name} onChange={f('driver_name')} />
                <Input label="رقم رخصة القيادة" value={form.driver_license} onChange={f('driver_license')} />
                <Input label="موبايل السائق" value={form.driver_phone} onChange={f('driver_phone')} />
              </FormGrid>
            </div>
          )}

          {activeTab === 'lines' && (
            <div>
              <div style={{ background: 'var(--bg)', borderRadius: 10, padding: 14, border: '1px solid var(--border)', marginBottom: 14 }}>
                <div style={{ fontWeight: 700, marginBottom: 12, color: 'var(--teal)' }}>إضافة بند جديد</div>

                <div style={{ marginBottom: 10 }}>
                  <Select label="ربط بأمر إنتاج جاهز (اختياري — يخصم فعليًا من رصيد المخزون عند التسليم)"
                    value={newLine.work_order_id}
                    onChange={e => {
                      const woId = e.target.value
                      const row = pipeline?.find(p => String(p.work_order_id) === woId)
                      setNewLine(n => ({
                        ...n,
                        work_order_id: woId,
                        equipment_id:  row?.equipment_id || '',
                        description:   n.description || (row ? `${row.equipment_name} (${row.work_order_code})` : n.description),
                        qty:           row ? String(row.fg_qty || 1) : n.qty,
                      }))
                    }}>
                    <option value="">— بدون ربط (بند نصي حر) —</option>
                    {(pipeline || []).map(p => (
                      <option key={p.work_order_id} value={p.work_order_id}>
                        {p.equipment_name || p.work_order_code} — {p.work_order_code}
                        {p.customer_name ? ` — ${p.customer_name}` : ''} — {p.pipeline_status} (FG متاح: {p.fg_qty})
                      </option>
                    ))}
                  </Select>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 80px 90px auto', gap: 8, alignItems: 'end' }}>
                  <Input label="الوصف *" value={newLine.description}
                    onChange={e => setNewLine(n => ({ ...n, description: e.target.value }))} />
                  <Input label="الموديل" value={newLine.model_no}
                    onChange={e => setNewLine(n => ({ ...n, model_no: e.target.value }))} />
                  <Input label="الكمية" type="number" value={newLine.qty}
                    onChange={e => setNewLine(n => ({ ...n, qty: e.target.value }))} />
                  <Input label="الوحدة" value={newLine.unit}
                    onChange={e => setNewLine(n => ({ ...n, unit: e.target.value }))} />
                  <button className="btn-teal" style={{ padding: '8px 18px', alignSelf: 'flex-end' }} onClick={addLine}>+ إضافة</button>
                </div>
                <div style={{ marginTop: 10 }}>
                  <Input label="ملاحظة البند (اختياري)" value={newLine.notes}
                    onChange={e => setNewLine(n => ({ ...n, notes: e.target.value }))} />
                </div>
              </div>

              {form.lines.length > 0 ? (
                <table className="erp-table text-sm">
                  <thead><tr><th>#</th><th>الوصف</th><th>الموديل</th><th>الكمية</th><th>الوحدة</th><th></th></tr></thead>
                  <tbody>
                    {form.lines.map((l, i) => (
                      <tr key={l.id}>
                        <td style={{ color: 'var(--text-secondary)' }}>{i + 1}</td>
                        <td style={{ fontWeight: 600 }}>
                          {l.description}
                          {l.work_order_id && (
                            <span className="badge badge-teal" style={{ marginInlineStart: 6 }}>🔗 مرتبط بأمر إنتاج</span>
                          )}
                        </td>
                        <td style={{ color: 'var(--text-secondary)', fontSize: 11 }}>{l.model_no || '—'}</td>
                        <td>{l.qty}</td>
                        <td>{l.unit}</td>
                        <td><button style={{ background: 'none', border: 'none', color: 'var(--red)', cursor: 'pointer', fontSize: 15 }} onClick={() => removeLine(l.id)}>✕</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div style={{ textAlign: 'center', padding: 36, color: 'var(--text-secondary)', fontSize: 13 }}>
                  لا توجد بنود — أضف بنداً من الحقل أعلاه
                </div>
              )}
            </div>
          )}

          <ModalFooter>
            <button className="btn-primary" onClick={handleCreate} disabled={acting}>
              {acting ? '…' : '🚛 إنشاء أمر التوريد'}
            </button>
            <div style={{ display: 'flex', gap: 8 }}>
              {activeTab !== 'basic' && (
                <button className="btn-secondary" onClick={() => setActiveTab(
                  activeTab === 'delivery' ? 'basic' :
                  activeTab === 'vehicle'  ? 'delivery' : 'vehicle'
                )}>→ السابق</button>
              )}
              {activeTab !== 'lines' && (
                <button className="btn-secondary" onClick={() => setActiveTab(
                  activeTab === 'basic'    ? 'delivery' :
                  activeTab === 'delivery' ? 'vehicle' : 'lines'
                )}>← التالي</button>
              )}
            </div>
            <button className="btn-secondary" onClick={() => { setShowCreate(false); setForm(EMPTY_FORM) }}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      <ConfirmDialog
        open={!!confirmDel}
        message="هل تريد حذف أمر التوريد هذا نهائياً؟"
        onConfirm={() => { handleDelete(confirmDel); setConfirmDel(null) }}
        onCancel={() => setConfirmDel(null)}
      />

      <ConfirmDialog
        open={!!confirmCancel}
        message={`هل تريد إلغاء أمر التوريد "${confirmCancel?.code}"؟`}
        onConfirm={() => { handleCancel(confirmCancel); setConfirmCancel(null) }}
        onCancel={() => setConfirmCancel(null)}
      />
    </div>
  )
}
