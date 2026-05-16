/**
 * التقارير — Reports with PDF generation
 * Invoice PDF, Quotation PDF, BOM PDF, Work Order Cost PDF
 */
import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import api from '../api/client'
import { egp, fmtDate } from '../utils/fmt'
import { getWorkshopProfile } from './WorkshopProfile'
import { PageSpinner } from '../components/ui/Spinner'

// ── PDF generation via print ────────────────────────────────────
function buildInvoiceHTML(inv, lines, ws) {
  const logo = ws.logo_url ? `<img src="${ws.logo_url}" style="max-height:70px;max-width:160px;object-fit:contain">` : ''
  const subtotal = lines.reduce((s,l) => s + Number(l.qty||1)*Number(l.unit_price||0), 0)
  const tax      = subtotal * Number(inv.tax_pct||0) / 100
  const disc     = subtotal * Number(inv.discount_pct||0) / 100
  const total    = subtotal + tax - disc

  return `<!DOCTYPE html><html dir="rtl" lang="ar">
<head><meta charset="UTF-8">
<style>
  * { box-sizing:border-box; margin:0; padding:0 }
  body { font-family:'Tajawal',Arial,sans-serif; font-size:13px; color:#1a1a18; background:#fff; padding:32px }
  .header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:24px; padding-bottom:16px; border-bottom:2px solid #0F6E56 }
  .ws-name { font-size:20px; font-weight:700; color:#0F6E56 }
  .ws-info { font-size:11px; color:#6b7280; margin-top:4px; line-height:1.6 }
  .inv-title { font-size:22px; font-weight:700; color:#0F6E56; text-align:left }
  .inv-meta  { font-size:12px; color:#6b7280; text-align:left; margin-top:4px; line-height:1.8 }
  .section { margin-bottom:20px }
  .section-title { font-size:12px; font-weight:700; color:#6b7280; text-transform:uppercase; letter-spacing:.5px; margin-bottom:8px; padding-bottom:4px; border-bottom:1px solid #e2e0d8 }
  table { width:100%; border-collapse:collapse }
  th { background:#f5f4f0; padding:8px 10px; text-align:right; font-size:11px; font-weight:600; color:#6b7280; border-bottom:2px solid #e2e0d8 }
  td { padding:8px 10px; border-bottom:1px solid #f3f4f6; font-size:12px }
  tr:last-child td { border-bottom:none }
  .total-row td { font-weight:700; background:#E1F5EE; color:#0F6E56; font-size:14px }
  .footer { margin-top:32px; padding-top:16px; border-top:1px solid #e2e0d8; font-size:11px; color:#6b7280; text-align:center }
  .watermark { position:fixed; top:50%; left:50%; transform:translate(-50%,-50%) rotate(-30deg); opacity:0.04; font-size:80px; font-weight:900; color:#0F6E56; pointer-events:none; z-index:-1 }
  @media print { body { padding:16px } }
</style>
</head><body>
<div class="watermark">${ws.name}</div>
<div class="header">
  <div>
    ${logo}
    <div class="ws-name">${ws.name}</div>
    <div class="ws-info">
      ${ws.address ? ws.address + '<br>' : ''}
      ${ws.phone ? 'ت: ' + ws.phone : ''} ${ws.mobile ? '| م: ' + ws.mobile : ''}<br>
      ${ws.tax_id ? 'بطاقة ضريبية: ' + ws.tax_id : ''} ${ws.commercial_reg ? '| سجل تجاري: ' + ws.commercial_reg : ''}
    </div>
  </div>
  <div>
    <div class="inv-title">فاتورة ضريبية</div>
    <div class="inv-meta">
      رقم: <strong>${inv.code}</strong><br>
      التاريخ: ${fmtDate(inv.issue_date || inv.date)}<br>
      الاستحقاق: ${inv.due_date ? fmtDate(inv.due_date) : '—'}
    </div>
  </div>
</div>

<div class="section">
  <div class="section-title">بيانات العميل</div>
  <strong>${inv.customer_name || inv.customer?.name || '—'}</strong>
</div>

<div class="section">
  <div class="section-title">البنود</div>
  <table>
    <thead><tr><th>#</th><th>الوصف</th><th>الكمية</th><th>الوحدة</th><th>سعر الوحدة</th><th>الإجمالي</th></tr></thead>
    <tbody>
      ${lines.map((l,i) => `
        <tr>
          <td>${i+1}</td>
          <td>${l.description}</td>
          <td>${Number(l.qty||1).toFixed(2)}</td>
          <td>${l.unit||'pcs'}</td>
          <td>${egp(l.unit_price)}</td>
          <td>${egp(Number(l.qty||1)*Number(l.unit_price||0))}</td>
        </tr>`).join('')}
    </tbody>
  </table>
</div>

<div style="display:flex;justify-content:flex-start;margin-top:16px">
  <table style="width:280px">
    <tbody>
      <tr><td>الإجمالي قبل الضريبة</td><td style="text-align:left">${egp(subtotal)}</td></tr>
      ${inv.tax_pct > 0 ? `<tr><td>ضريبة ${inv.tax_pct}%</td><td style="text-align:left">${egp(tax)}</td></tr>` : ''}
      ${inv.discount_pct > 0 ? `<tr><td>خصم ${inv.discount_pct}%</td><td style="text-align:left">- ${egp(disc)}</td></tr>` : ''}
      <tr class="total-row"><td>الإجمالي النهائي</td><td style="text-align:left">${egp(total)}</td></tr>
    </tbody>
  </table>
</div>

${inv.terms || inv.payment_terms ? `<div style="margin-top:16px;font-size:11px;color:#6b7280">شروط الدفع: ${inv.terms || inv.payment_terms}</div>` : ''}

<div class="footer">${ws.report_footer}</div>
</body></html>`
}

function buildBOMHTML(eq, lines, totalCost, ws) {
  const logo = ws.logo_url ? `<img src="${ws.logo_url}" style="max-height:60px;object-fit:contain">` : ''
  const LEVEL_COLORS = { 0:'#1D9E75',1:'#185FA5',2:'#854F0B',3:'#A32D2D',4:'#6b7280' }
  return `<!DOCTYPE html><html dir="rtl" lang="ar">
<head><meta charset="UTF-8">
<style>
  * { box-sizing:border-box; margin:0; padding:0 }
  body { font-family:'Tajawal',Arial,sans-serif; font-size:12px; color:#1a1a18; padding:24px }
  .header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:20px; padding-bottom:12px; border-bottom:2px solid #0F6E56 }
  .ws-name { font-size:18px; font-weight:700; color:#0F6E56 }
  h2 { font-size:16px; color:#0F6E56 }
  table { width:100%; border-collapse:collapse; margin-top:12px }
  th { background:#f5f4f0; padding:7px 8px; text-align:right; font-size:10px; font-weight:600; color:#6b7280; border-bottom:2px solid #e2e0d8 }
  td { padding:7px 8px; border-bottom:1px solid #f3f4f6; font-size:11px; vertical-align:middle }
  .lv-badge { display:inline-block; padding:1px 6px; border-radius:4px; font-size:10px; font-weight:700; color:#fff }
  .total-row td { background:#E1F5EE; font-weight:700; color:#0F6E56 }
  .footer { margin-top:24px; font-size:10px; color:#6b7280; text-align:center; border-top:1px solid #e2e0d8; padding-top:12px }
  .watermark { position:fixed;top:50%;left:50%;transform:translate(-50%,-50%) rotate(-30deg);opacity:0.04;font-size:72px;font-weight:900;color:#0F6E56;pointer-events:none;z-index:-1 }
  @media print { body { padding:12px } }
</style>
</head><body>
<div class="watermark">${ws.name}</div>
<div class="header">
  <div>${logo}<div class="ws-name">${ws.name}</div><div style="font-size:10px;color:#6b7280">${ws.address||''}</div></div>
  <div><h2>تفكيك BOM</h2><div style="font-size:11px;color:#6b7280;margin-top:4px">${eq.code} — ${eq.name_ar}</div></div>
</div>
<table>
  <thead><tr><th>المستوى</th><th>الكود</th><th>الاسم</th><th>الوزن</th><th>الكمية</th><th>سعر الوحدة</th><th>تكلفة BOM</th></tr></thead>
  <tbody>
    ${lines.map(l => {
      const lv = l.equipment?.level ?? 0
      const col = LEVEL_COLORS[lv] || '#6b7280'
      return `<tr>
        <td><span class="lv-badge" style="background:${col}">L${lv}</span></td>
        <td style="font-family:monospace">${l.material?.code||'—'}</td>
        <td>${l.material?.name_ar||'—'}</td>
        <td style="color:#6b7280">${l.material?.weight_kg ? l.material.weight_kg+' kg' : '—'}</td>
        <td>${Number(l.qty).toFixed(3)} ${l.material?.unit||''}</td>
        <td>${egp(l.unit_cost)}</td>
        <td style="color:#0F6E56;font-weight:600">${Number(l.total_cost)>0 ? egp(l.total_cost) : '—'}</td>
      </tr>`
    }).join('')}
    <tr class="total-row">
      <td colspan="6" style="text-align:right">تكلفة المواد الإجمالية</td>
      <td>${egp(totalCost)}</td>
    </tr>
  </tbody>
</table>
<div class="footer">${ws.report_footer} — تاريخ الطباعة: ${fmtDate(new Date().toISOString())}</div>
</body></html>`
}

function printHTML(html) {
  const w = window.open('', '_blank')
  w.document.write(html)
  w.document.close()
  setTimeout(() => { w.print() }, 600)
}

// ── Report card ─────────────────────────────────────────────────
function ReportCard({ icon, title, desc, children }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="card">
      <div className="flex items-center justify-between cursor-pointer" onClick={() => setOpen(o => !o)}>
        <div className="flex items-center gap-2.5">
          <span className="text-2xl">{icon}</span>
          <div>
            <div className="font-semibold text-sm">{title}</div>
            <div className="text-xs text-erp-muted">{desc}</div>
          </div>
        </div>
        <span className="text-erp-muted">{open ? '▲' : '▼'}</span>
      </div>
      {open && <div className="mt-4 pt-4 border-t border-erp-border">{children}</div>}
    </div>
  )
}

// ── Invoice report ───────────────────────────────────────────────
function InvoiceReport() {
  const { data: invoices } = useApi(() => api.sales.invoices())
  const [invId, setInvId] = useState('')
  const [loading, setLoading] = useState(false)

  const print = async () => {
    if (!invId) return
    setLoading(true)
    try {
      const inv   = await api.get(`/sales/invoices/${invId}`)
      const lines = inv.lines || []
      const ws    = getWorkshopProfile()
      printHTML(buildInvoiceHTML(inv, lines, ws))
    } finally { setLoading(false) }
  }

  return (
    <div className="flex items-end gap-3">
      <div className="flex-1">
        <label className="field-label">اختر فاتورة</label>
        <select className="field-select w-full" value={invId} onChange={e => setInvId(e.target.value)}>
          <option value="">— اختر —</option>
          {invoices?.map(i => (
            <option key={i.id} value={i.id}>{i.code} — {i.customer_name || i.customer?.name}</option>
          ))}
        </select>
      </div>
      <button className="btn btn-primary" onClick={print} disabled={!invId || loading}>
        {loading ? '…' : '🖨 طباعة / PDF'}
      </button>
    </div>
  )
}

// ── Quotation report ─────────────────────────────────────────────
function QuotationReport() {
  const { data: quotes } = useApi(() => api.sales.quotations())
  const [qtId, setQtId] = useState('')
  const [loading, setLoading] = useState(false)

  const print = async () => {
    if (!qtId) return
    setLoading(true)
    try {
      const qt    = await api.get(`/sales/quotations/${qtId}`)
      const lines = qt.lines || []
      const ws    = getWorkshopProfile()
      // Reuse invoice template with quotation title
      const html  = buildInvoiceHTML({...qt, issue_date: qt.date}, lines, ws)
        .replace('>فاتورة ضريبية<', '>عرض سعر<')
        .replace('رقم:', 'رقم عرض السعر:')
      printHTML(html)
    } finally { setLoading(false) }
  }

  return (
    <div className="flex items-end gap-3">
      <div className="flex-1">
        <label className="field-label">اختر عرض سعر</label>
        <select className="field-select w-full" value={qtId} onChange={e => setQtId(e.target.value)}>
          <option value="">— اختر —</option>
          {quotes?.map(q => (
            <option key={q.id} value={q.id}>{q.code} — {q.customer_name || q.customer?.name}</option>
          ))}
        </select>
      </div>
      <button className="btn btn-primary" onClick={print} disabled={!qtId || loading}>
        {loading ? '…' : '🖨 طباعة / PDF'}
      </button>
    </div>
  )
}

// ── BOM report ───────────────────────────────────────────────────
function BOMReport() {
  const { data: eqs } = useApi(() => api.equipment.list())
  const [eqId, setEqId] = useState('')
  const [loading, setLoading] = useState(false)

  const print = async () => {
    if (!eqId) return
    setLoading(true)
    try {
      const bom  = await api.get(`/equipment/${eqId}/bom/cost`)
        .catch(() => api.equipment.bom(eqId).then(lines => ({ lines, total_cost: lines.reduce((s,l)=>s+Number(l.total_cost||0),0) })))
      const eq   = eqs.find(e => e.id == eqId) || { code:'—', name_ar:'—' }
      const ws   = getWorkshopProfile()
      printHTML(buildBOMHTML(eq, bom.lines || [], bom.total_cost || 0, ws))
    } finally { setLoading(false) }
  }

  return (
    <div className="flex items-end gap-3">
      <div className="flex-1">
        <label className="field-label">اختر معدة</label>
        <select className="field-select w-full" value={eqId} onChange={e => setEqId(e.target.value)}>
          <option value="">— اختر —</option>
          {eqs?.map(eq => (
            <option key={eq.id} value={eq.id}>{eq.code} — {eq.name_ar}</option>
          ))}
        </select>
      </div>
      <button className="btn btn-primary" onClick={print} disabled={!eqId || loading}>
        {loading ? '…' : '🖨 طباعة / PDF'}
      </button>
    </div>
  )
}

// ── Work Order Cost report ───────────────────────────────────────
function WorkOrderReport() {
  const { data: wos } = useApi(() => api.accounting.workOrders())
  const [woId, setWoId] = useState('')
  const [loading, setLoading] = useState(false)

  const print = async () => {
    if (!woId) return
    setLoading(true)
    try {
      const lines = await api.get(`/accounting/work-orders/${woId}/cost-lines`).catch(() => [])
      const wo    = wos.find(w => w.id == woId) || {}
      const ws    = getWorkshopProfile()
      const total = lines.reduce((s,l) => s+Number(l.total_cost||0), 0)
      const html  = `<!DOCTYPE html><html dir="rtl" lang="ar">
<head><meta charset="UTF-8">
<style>
  body{font-family:'Tajawal',Arial,sans-serif;font-size:12px;padding:24px}
  .header{display:flex;justify-content:space-between;margin-bottom:20px;padding-bottom:12px;border-bottom:2px solid #0F6E56}
  .ws-name{font-size:18px;font-weight:700;color:#0F6E56}
  table{width:100%;border-collapse:collapse;margin-top:12px}
  th{background:#f5f4f0;padding:7px 8px;text-align:right;font-size:11px;font-weight:600;color:#6b7280;border-bottom:2px solid #e2e0d8}
  td{padding:7px 8px;border-bottom:1px solid #f3f4f6;font-size:11px}
  .total-row td{background:#E1F5EE;font-weight:700;color:#0F6E56}
  .footer{margin-top:24px;font-size:10px;color:#6b7280;text-align:center;border-top:1px solid #e2e0d8;padding-top:12px}
</style></head><body>
<div class="header">
  <div>
    ${ws.logo_url ? `<img src="${ws.logo_url}" style="max-height:60px">` : ''}
    <div class="ws-name">${ws.name}</div>
  </div>
  <div>
    <div style="font-size:16px;font-weight:700;color:#0F6E56">تقرير تكاليف أمر إنتاج</div>
    <div style="font-size:11px;color:#6b7280;margin-top:4px">كود: ${wo.code || '—'}</div>
  </div>
</div>
<table>
  <thead><tr><th>النوع</th><th>الوصف</th><th>الكمية</th><th>سعر الوحدة</th><th>الإجمالي</th></tr></thead>
  <tbody>
    ${lines.map(l => `<tr>
      <td><span style="padding:2px 8px;border-radius:4px;font-size:10px;background:${l.cost_type==='material'?'#E6F1FB':l.cost_type==='labor'?'#FAEEDA':'#f3f4f6'};color:${l.cost_type==='material'?'#185FA5':l.cost_type==='labor'?'#854F0B':'#6b7280'}">${l.cost_type==='material'?'مواد':l.cost_type==='labor'?'عمالة':'أخرى'}</span></td>
      <td>${l.description||'—'}</td>
      <td>${Number(l.qty||0).toFixed(2)}</td>
      <td>${egp(l.unit_cost)}</td>
      <td>${egp(l.total_cost)}</td>
    </tr>`).join('')}
    <tr class="total-row"><td colspan="4" style="text-align:right">الإجمالي</td><td>${egp(total)}</td></tr>
  </tbody>
</table>
<div class="footer">${ws.report_footer}</div>
</body></html>`
      printHTML(html)
    } finally { setLoading(false) }
  }

  return (
    <div className="flex items-end gap-3">
      <div className="flex-1">
        <label className="field-label">اختر أمر إنتاج</label>
        <select className="field-select w-full" value={woId} onChange={e => setWoId(e.target.value)}>
          <option value="">— اختر —</option>
          {wos?.map(w => (
            <option key={w.id} value={w.id}>{w.code}</option>
          ))}
        </select>
      </div>
      <button className="btn btn-primary" onClick={print} disabled={!woId || loading}>
        {loading ? '…' : '🖨 طباعة / PDF'}
      </button>
    </div>
  )
}

// ── Main Reports page ────────────────────────────────────────────
export default function Reports() {
  return (
    <div className="flex flex-col gap-3">
      <div className="text-xs text-erp-muted mb-2">
        اختر التقرير المطلوب — سيفتح في نافذة جديدة جاهزة للطباعة أو حفظ كـ PDF
      </div>

      <ReportCard icon="🧾" title="فاتورة" desc="طباعة / تصدير PDF لأي فاتورة">
        <InvoiceReport />
      </ReportCard>

      <ReportCard icon="📋" title="عرض سعر" desc="طباعة / تصدير PDF لعروض الأسعار">
        <QuotationReport />
      </ReportCard>

      <ReportCard icon="📐" title="تفكيك BOM" desc="تقرير قائمة مواد المعدة مع التكاليف">
        <BOMReport />
      </ReportCard>

      <ReportCard icon="🏭" title="تكاليف أمر إنتاج" desc="تقرير تفصيلي بتكاليف أمر الإنتاج">
        <WorkOrderReport />
      </ReportCard>
    </div>
  )
}
