/**
 * التقارير — Reports with PDF generation
 * Invoice PDF, Quotation PDF, BOM PDF, Work Order Cost PDF
 */
import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import api, { bgErr } from '../api/client'
import { egp, fmtDate } from '../utils/fmt'
import { fetchWorkshopProfile } from './WorkshopProfile'
import { PageSpinner } from '../components/ui/Spinner'

// ── PDF generation via print ────────────────────────────────────
function buildInvoiceHTML(inv, lines, ws) {
  const logo = (ws.logo_data || ws.logo_url) ? `<img src="${ws.logo_data || ws.logo_url}" style="max-height:70px;max-width:160px;object-fit:contain">` : ''
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
          <td>${l.unit||'قطعة'}</td>
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

function buildBOMHTML(eq, lines, totalCost, ws, allEqs = []) {
  const logo = (ws.logo_data || ws.logo_url) ? `<img src="${ws.logo_data || ws.logo_url}" style="max-height:60px;object-fit:contain">` : ''
  const LEVEL_COLORS = { 0:'#1D9E75',1:'#185FA5',2:'#854F0B',3:'#A32D2D',4:'#6b7280' }
  const LEVEL_BG     = { 0:'#E8F8F2',1:'#EBF3FC',2:'#FDF5E8',3:'#FDEEEE',4:'#F5F5F5' }

  // Build equipment map and group lines by equipment
  const eqMap = {}
  allEqs.forEach(e => { eqMap[e.id] = e })
  const byEq = {}
  lines.forEach(l => { if (!byEq[l.equipment_id]) byEq[l.equipment_id] = []; byEq[l.equipment_id].push(l) })
  const orderedEqIds = allEqs.filter(e => byEq[e.id]).map(e => e.id)
  Object.keys(byEq).map(Number).forEach(id => { if (!orderedEqIds.includes(id)) orderedEqIds.push(id) })

  const rows = orderedEqIds.map(eid => {
    const eqItem   = eqMap[eid] || {}
    const lv       = eqItem.level ?? 0
    const col      = LEVEL_COLORS[lv] || '#6b7280'
    const bg       = LEVEL_BG[lv]    || '#F5F5F5'
    const indent   = lv * 12
    const eqLines  = byEq[eid] || []
    const eqCost   = eqLines.reduce((s,l) => s + Number(l.total_cost||0), 0)
    return `
      <tr style="background:${bg}">
        <td colspan="7" style="padding-right:${8+indent}px;font-weight:600;font-size:11px">
          <span class="lv-badge" style="background:${col}">L${lv}</span>
          &nbsp;${eqItem.name_ar||'معدة #'+eid}
          <span style="font-family:monospace;font-size:10px;color:#6b7280;margin-right:6px">${eqItem.code||''}</span>
          ${eqCost>0 ? `<span style="float:left;color:#0F6E56;font-weight:700">${egp(eqCost)}</span>` : ''}
        </td>
      </tr>
      ${eqLines.map(l => `<tr>
        <td style="padding-right:${20+indent}px;font-size:11px">${l.material?.name_ar||'—'}</td>
        <td style="font-family:monospace;font-size:10px">${l.material?.code||'—'}</td>
        <td style="color:#6b7280;font-size:10px">—</td>
        <td>${Number(l.qty).toFixed(3)} ${l.material?.unit||''}</td>
        <td style="color:#6b7280">${
          l.dim_length_cm && l.dim_width_cm
            ? `${l.dim_count||1}×${l.dim_length_cm}×${l.dim_width_cm} سم`
            : l.bar_length_cm ? `${l.dim_count||1}×${l.bar_length_cm} سم` : '—'
        }</td>
        <td>${egp(l.unit_cost)}</td>
        <td style="color:#0F6E56;font-weight:600">${Number(l.total_cost)>0 ? egp(l.total_cost) : '—'}</td>
      </tr>`).join('')}
    `
  }).join('')

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
  td { padding:6px 8px; border-bottom:1px solid #f3f4f6; font-size:11px; vertical-align:middle }
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
  <div><h2>تفكيك BOM — ${eq.name_ar}</h2><div style="font-size:11px;color:#6b7280;margin-top:4px">${eq.code}</div></div>
</div>
<table>
  <thead><tr><th>الاسم</th><th>الكود</th><th>الوزن</th><th>الكمية</th><th>الأبعاد</th><th>سعر الوحدة</th><th>تكلفة BOM</th></tr></thead>
  <tbody>
    ${rows}
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
      const ws    = await fetchWorkshopProfile()
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
      const ws    = await fetchWorkshopProfile()
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
  const { data: allEqs } = useApi(() => api.equipment.listAll())
  const [eqId, setEqId] = useState('')
  const [loading, setLoading] = useState(false)

  // Only show root equipment (L0) in selector — the API pulls all descendants
  const roots = allEqs?.filter(e => !e.parent_id) || []

  const print = async () => {
    if (!eqId) return
    setLoading(true)
    try {
      const bom  = await api.get(`/equipment/${eqId}/bom/cost`)
        .catch(() => api.equipment.bom(eqId).then(lines => ({ lines, total_cost: lines.reduce((s,l)=>s+Number(l.total_cost||0),0) })))
      const eq   = allEqs?.find(e => e.id == eqId) || { code:'—', name_ar:'—' }
      const ws   = await fetchWorkshopProfile()
      printHTML(buildBOMHTML(eq, bom.lines || [], bom.total_cost || 0, ws, allEqs || []))
    } finally { setLoading(false) }
  }

  return (
    <div className="flex items-end gap-3">
      <div className="flex-1">
        <label className="field-label">اختر معدة (تفكيك كامل الشجرة)</label>
        <select className="field-select w-full" value={eqId} onChange={e => setEqId(e.target.value)}>
          <option value="">— اختر —</option>
          {roots.map(eq => (
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
      const lines = await api.get(`/accounting/work-orders/${woId}/cost-lines`).catch(bgErr('بنود تكلفة أمر الإنتاج', []))
      const wo    = wos.find(w => w.id == woId) || {}
      const ws    = await fetchWorkshopProfile()
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
    ${(ws.logo_data || ws.logo_url) ? `<img src="${ws.logo_data || ws.logo_url}" style="max-height:60px">` : ''}
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

// ── Date range helper ────────────────────────────────────────────
function DateRange({ from, to, onFrom, onTo }) {
  return (
    <div className="flex gap-2 items-end flex-wrap mb-3">
      <div>
        <label className="field-label">من</label>
        <input type="date" className="field-input" value={from} onChange={e => onFrom(e.target.value)} />
      </div>
      <div>
        <label className="field-label">إلى</label>
        <input type="date" className="field-input" value={to} onChange={e => onTo(e.target.value)} />
      </div>
    </div>
  )
}

// ── Inventory Summary Report ─────────────────────────────────────
function InventorySummaryReport() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await api.reports.inventorySummary()) } finally { setLoading(false) }
  }

  const printReport = async () => {
    if (!data) return
    const ws = await fetchWorkshopProfile()
    const rows = data.top_value_materials.map((m, i) => `
      <tr>
        <td>${i + 1}</td>
        <td>${m.code}</td>
        <td>${m.name_ar}</td>
        <td>${m.category || '—'}</td>
        <td>${Number(m.stock_qty).toFixed(2)} ${m.unit}</td>
        <td>${egp(m.unit_cost)}</td>
        <td style="font-weight:600;color:#0F6E56">${egp(m.stock_value)}</td>
      </tr>`).join('')
    printHTML(`<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8">
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Tajawal',Arial,sans-serif;font-size:12px;padding:24px}
.header{display:flex;justify-content:space-between;margin-bottom:20px;padding-bottom:12px;border-bottom:2px solid #0F6E56}
.ws-name{font-size:18px;font-weight:700;color:#0F6E56}
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
.kpi{background:#f5f4f0;border-radius:8px;padding:12px;text-align:center}
.kpi-val{font-size:18px;font-weight:700;color:#0F6E56}.kpi-lbl{font-size:10px;color:#6b7280;margin-top:4px}
table{width:100%;border-collapse:collapse}
th{background:#f5f4f0;padding:7px 8px;text-align:right;font-size:10px;font-weight:600;color:#6b7280;border-bottom:2px solid #e2e0d8}
td{padding:6px 8px;border-bottom:1px solid #f3f4f6;font-size:11px}
.footer{margin-top:24px;font-size:10px;color:#6b7280;text-align:center;border-top:1px solid #e2e0d8;padding-top:12px}
</style></head><body>
<div class="header"><div><div class="ws-name">${ws.name}</div><div style="font-size:10px;color:#6b7280">${ws.address || ''}</div></div>
<div><div style="font-size:16px;font-weight:700;color:#0F6E56">تقرير ملخص المخزون</div>
<div style="font-size:11px;color:#6b7280">تاريخ الطباعة: ${fmtDate(new Date().toISOString())}</div></div></div>
<div class="kpi-grid">
  <div class="kpi"><div class="kpi-val">${data.total_items}</div><div class="kpi-lbl">إجمالي المواد</div></div>
  <div class="kpi"><div class="kpi-val" style="color:var(--red,#c0392b)">${data.low_stock}</div><div class="kpi-lbl">تحت حد إعادة الطلب</div></div>
  <div class="kpi"><div class="kpi-val">${egp(data.total_value)}</div><div class="kpi-lbl">قيمة المخزون الإجمالية</div></div>
  <div class="kpi"><div class="kpi-val">${egp(data.mtd_in_value)}</div><div class="kpi-lbl">وارد الشهر الحالي</div></div>
</div>
<div style="font-size:12px;font-weight:600;margin-bottom:8px">أعلى 10 مواد بالقيمة</div>
<table><thead><tr><th>#</th><th>الكود</th><th>المادة</th><th>الفئة</th><th>الكمية</th><th>سعر الوحدة</th><th>القيمة</th></tr></thead>
<tbody>${rows}</tbody></table>
<div class="footer">${ws.report_footer}</div></body></html>`)
  }

  return (
    <div className="flex flex-col gap-3">
      {data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
          {[
            { label: 'إجمالي المواد', val: data.total_items, color: 'var(--teal)' },
            { label: 'تحت الحد الأدنى', val: data.low_stock, color: 'var(--red)' },
            { label: 'خارج المخزون', val: data.out_of_stock, color: 'var(--red)' },
            { label: 'القيمة الإجمالية', val: egp(data.total_value), color: 'var(--green)' },
          ].map(k => (
            <div key={k.label} className="card py-2 text-center">
              <div className="text-base font-bold" style={{ color: k.color }}>{k.val}</div>
              <div className="text-[10px] text-erp-muted">{k.label}</div>
            </div>
          ))}
        </div>
      )}
      {data?.top_value_materials?.length > 0 && (
        <table className="erp-table text-xs">
          <thead><tr><th>الكود</th><th>المادة</th><th>الكمية</th><th>القيمة</th></tr></thead>
          <tbody>
            {data.top_value_materials.map(m => (
              <tr key={m.id}>
                <td><span className="font-mono text-xs badge badge-blue">{m.code}</span></td>
                <td>{m.name_ar}</td>
                <td>{Number(m.stock_qty).toFixed(2)} {m.unit}</td>
                <td className="font-semibold" style={{ color: 'var(--green)' }}>{egp(m.stock_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
        {data && <button className="btn btn-sm" onClick={printReport}>🖨 طباعة / PDF</button>}
      </div>
    </div>
  )
}

// ── Inventory Movement Report ────────────────────────────────────
function InventoryMovementReport() {
  const today = new Date().toISOString().slice(0, 10)
  const firstOfMonth = today.slice(0, 8) + '01'
  const [from, setFrom] = useState(firstOfMonth)
  const [to, setTo] = useState(today)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await api.reports.inventoryMovement({ date_from: from, date_to: to })) } finally { setLoading(false) }
  }

  const printReport = async () => {
    if (!data) return
    const ws = await fetchWorkshopProfile()
    const rows = data.movements.map((m, i) => `
      <tr>
        <td>${m.date}</td>
        <td><span style="padding:2px 6px;border-radius:4px;font-size:10px;background:${m.type==='in'?'#E8F8F2':'#FDEEEE'};color:${m.type==='in'?'#0F6E56':'#c0392b'}">${m.type_ar}</span></td>
        <td>${m.material_code}</td>
        <td>${m.material_name}</td>
        <td>${Number(m.qty).toFixed(2)}</td>
        <td>${egp(m.unit_cost)}</td>
        <td style="font-weight:600">${egp(m.total_cost)}</td>
        <td style="font-size:10px;color:#6b7280">${m.reference || '—'}</td>
      </tr>`).join('')
    printHTML(`<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8">
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Tajawal',Arial,sans-serif;font-size:12px;padding:24px}
.header{display:flex;justify-content:space-between;margin-bottom:20px;padding-bottom:12px;border-bottom:2px solid #0F6E56}
.ws-name{font-size:18px;font-weight:700;color:#0F6E56}
.summary{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px}
.sum-box{background:#f5f4f0;border-radius:6px;padding:10px;text-align:center}
table{width:100%;border-collapse:collapse}
th{background:#f5f4f0;padding:7px 8px;text-align:right;font-size:10px;font-weight:600;color:#6b7280;border-bottom:2px solid #e2e0d8}
td{padding:6px 8px;border-bottom:1px solid #f3f4f6;font-size:11px}
.footer{margin-top:24px;font-size:10px;color:#6b7280;text-align:center;border-top:1px solid #e2e0d8;padding-top:12px}
</style></head><body>
<div class="header"><div><div class="ws-name">${ws.name}</div></div>
<div><div style="font-size:16px;font-weight:700;color:#0F6E56">تقرير حركة المخزون</div>
<div style="font-size:11px;color:#6b7280">من ${from} إلى ${to}</div></div></div>
<div class="summary">
  <div class="sum-box"><div style="font-size:16px;font-weight:700;color:#0F6E56">${egp(data.total_in)}</div><div style="font-size:10px;color:#6b7280">إجمالي الوارد</div></div>
  <div class="sum-box"><div style="font-size:16px;font-weight:700;color:#c0392b">${egp(data.total_out)}</div><div style="font-size:10px;color:#6b7280">إجمالي الصادر</div></div>
  <div class="sum-box"><div style="font-size:16px;font-weight:700">${egp(data.net)}</div><div style="font-size:10px;color:#6b7280">الصافي</div></div>
</div>
<table><thead><tr><th>التاريخ</th><th>النوع</th><th>الكود</th><th>المادة</th><th>الكمية</th><th>سعر الوحدة</th><th>الإجمالي</th><th>المرجع</th></tr></thead>
<tbody>${rows}</tbody></table>
<div class="footer">${ws.report_footer}</div></body></html>`)
  }

  return (
    <div className="flex flex-col gap-3">
      <DateRange from={from} to={to} onFrom={setFrom} onTo={setTo} />
      {data && (
        <div className="grid grid-cols-3 gap-2 mb-2">
          <div className="card py-2 text-center"><div className="font-bold text-sm" style={{ color: 'var(--green)' }}>{egp(data.total_in)}</div><div className="text-[10px] text-erp-muted">وارد</div></div>
          <div className="card py-2 text-center"><div className="font-bold text-sm" style={{ color: 'var(--red)' }}>{egp(data.total_out)}</div><div className="text-[10px] text-erp-muted">صادر</div></div>
          <div className="card py-2 text-center"><div className="font-bold text-sm">{egp(data.net)}</div><div className="text-[10px] text-erp-muted">صافي</div></div>
        </div>
      )}
      {data?.movements?.length > 0 && (
        <div className="text-xs text-erp-muted">{data.movements.length} حركة في الفترة المحددة</div>
      )}
      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
        {data && <button className="btn btn-sm" onClick={printReport}>🖨 طباعة / PDF</button>}
      </div>
    </div>
  )
}

// ── Slow Moving Report ───────────────────────────────────────────
function SlowMovingReport() {
  const [days, setDays] = useState(90)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await api.reports.inventorySlowMoving(days)) } finally { setLoading(false) }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2 items-end">
        <div>
          <label className="field-label">عدد الأيام</label>
          <select className="field-select" value={days} onChange={e => setDays(+e.target.value)}>
            <option value={30}>30 يوم</option>
            <option value={60}>60 يوم</option>
            <option value={90}>90 يوم</option>
            <option value={180}>6 أشهر</option>
            <option value={365}>سنة</option>
          </select>
        </div>
        <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
      </div>
      {data && (
        <div>
          <div className="text-sm mb-2">
            <span className="font-semibold" style={{ color: 'var(--amber)' }}>{data.slow_moving_count}</span> مادة راكدة بقيمة {egp(data.slow_moving_value)}
          </div>
          <table className="erp-table text-xs">
            <thead><tr><th>الكود</th><th>المادة</th><th>الفئة</th><th>الكمية</th><th>القيمة</th></tr></thead>
            <tbody>
              {data.items.slice(0, 10).map(m => (
                <tr key={m.id}>
                  <td><span className="font-mono text-xs badge badge-amber">{m.code}</span></td>
                  <td>{m.name_ar}</td>
                  <td className="text-erp-muted">{m.category || '—'}</td>
                  <td>{Number(m.stock_qty).toFixed(2)} {m.unit}</td>
                  <td className="font-semibold" style={{ color: 'var(--amber)' }}>{egp(m.stock_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Receivables Aging Report ─────────────────────────────────────
function AgingReport() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await api.reports.salesAging()) } finally { setLoading(false) }
  }

  const BUCKETS = [
    { key: 'current', label: 'حالي (لم يستحق)', color: 'var(--green)' },
    { key: '1-30', label: '1-30 يوم', color: 'var(--amber)' },
    { key: '31-60', label: '31-60 يوم', color: 'var(--orange, #e67e22)' },
    { key: '61-90', label: '61-90 يوم', color: 'var(--red)' },
    { key: '90+', label: '90+ يوم', color: '#6b0000' },
  ]

  const printReport = async () => {
    if (!data) return
    const ws = await fetchWorkshopProfile()
    const bucketRows = BUCKETS.map(b => {
      const entries = data.buckets[b.key] || []
      if (!entries.length) return ''
      const rows = entries.map(e => `
        <tr>
          <td>${e.code}</td>
          <td>${e.customer_name}</td>
          <td>${e.date}</td>
          <td>${e.due_date}</td>
          <td>${e.days_overdue > 0 ? e.days_overdue + ' يوم' : 'لم يستحق'}</td>
          <td style="font-weight:600;color:${b.color}">${egp(e.remaining)}</td>
        </tr>`).join('')
      return `<tr style="background:#f5f4f0"><td colspan="6" style="font-weight:700;padding:6px 8px;color:${b.color}">${b.label} — ${egp(data.totals_by_bucket[b.key] || 0)}</td></tr>${rows}`
    }).join('')
    printHTML(`<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8">
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Tajawal',Arial,sans-serif;font-size:12px;padding:24px}
.header{display:flex;justify-content:space-between;margin-bottom:20px;padding-bottom:12px;border-bottom:2px solid #0F6E56}
.ws-name{font-size:18px;font-weight:700;color:#0F6E56}
table{width:100%;border-collapse:collapse}
th{background:#f5f4f0;padding:7px 8px;text-align:right;font-size:10px;font-weight:600;color:#6b7280;border-bottom:2px solid #e2e0d8}
td{padding:6px 8px;border-bottom:1px solid #f3f4f6;font-size:11px}
.footer{margin-top:24px;font-size:10px;color:#6b7280;text-align:center;border-top:1px solid #e2e0d8;padding-top:12px}
</style></head><body>
<div class="header"><div><div class="ws-name">${ws.name}</div></div>
<div><div style="font-size:16px;font-weight:700;color:#0F6E56">تقرير Aging التحصيل</div>
<div style="font-size:11px;color:#6b7280">إجمالي المديونيات: ${egp(data.grand_total)}</div></div></div>
<table><thead><tr><th>رقم الفاتورة</th><th>العميل</th><th>تاريخ الفاتورة</th><th>تاريخ الاستحقاق</th><th>التأخر</th><th>المتبقي</th></tr></thead>
<tbody>${bucketRows}</tbody></table>
<div class="footer">${ws.report_footer}</div></body></html>`)
  }

  return (
    <div className="flex flex-col gap-3">
      {data && (
        <>
          <div className="text-sm font-semibold mb-1">إجمالي المديونيات: <span style={{ color: 'var(--red)' }}>{egp(data.grand_total)}</span></div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
            {BUCKETS.map(b => (
              <div key={b.key} className="card py-2 text-center">
                <div className="font-bold text-sm" style={{ color: b.color }}>{egp(data.totals_by_bucket[b.key] || 0)}</div>
                <div className="text-[10px] text-erp-muted">{b.label}</div>
                <div className="text-[10px]" style={{ color: b.color }}>{(data.buckets[b.key] || []).length} فاتورة</div>
              </div>
            ))}
          </div>
        </>
      )}
      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
        {data && <button className="btn btn-sm" onClick={printReport}>🖨 طباعة / PDF</button>}
      </div>
      {data && BUCKETS.map(b => {
        const entries = data.buckets[b.key] || []
        if (!entries.length) return null
        return (
          <div key={b.key} style={{ marginBottom: 8 }}>
            <div style={{ fontWeight: 700, fontSize: 12, color: b.color, marginBottom: 4, padding: '4px 8px', background: '#f8f8f8', borderRadius: 6, borderRight: `3px solid ${b.color}` }}>
              {b.label} — {entries.length} فاتورة — {egp(data.totals_by_bucket[b.key] || 0)}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <tbody>
                  {entries.map(e => (
                    <tr key={e.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 8px', fontWeight: 700, color: b.color }}>{e.code}</td>
                      <td style={{ padding: '6px 8px' }}>{e.customer_name}</td>
                      <td style={{ padding: '6px 8px', color: '#6B7280', fontSize: 11 }}>{e.due_date}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                        {e.has_schedule && (
                          <span style={{ background: '#1D4ED8', color: '#fff', borderRadius: 4, padding: '1px 6px', fontSize: 10 }}>أقساط</span>
                        )}
                      </td>
                      <td style={{ padding: '6px 8px', color: e.days_overdue > 0 ? b.color : '#6B7280', fontSize: 11 }}>
                        {e.days_overdue > 0 ? `متأخر ${e.days_overdue} يوم` : 'لم يستحق'}
                      </td>
                      <td style={{ padding: '6px 8px', fontWeight: 700, color: b.color, textAlign: 'left' }}>{egp(e.remaining)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Sales by Customer Report ─────────────────────────────────────
function SalesByCustomerReport() {
  const today = new Date().toISOString().slice(0, 10)
  const firstOfMonth = today.slice(0, 8) + '01'
  const [from, setFrom] = useState(firstOfMonth)
  const [to, setTo] = useState(today)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await api.reports.salesByCustomer({ date_from: from, date_to: to })) } finally { setLoading(false) }
  }

  const printReport = async () => {
    if (!data) return
    const ws = await fetchWorkshopProfile()
    const total = data.reduce((s, r) => s + r.total, 0)
    const rows = data.map((r, i) => `
      <tr>
        <td>${i + 1}</td>
        <td>${r.customer_name}</td>
        <td style="text-align:center">${r.invoice_count}</td>
        <td style="font-weight:600">${egp(r.total)}</td>
        <td style="color:#0F6E56">${egp(r.paid)}</td>
        <td style="color:#c0392b">${egp(r.unpaid)}</td>
        <td style="font-size:10px;color:#6b7280">${total > 0 ? (r.total / total * 100).toFixed(1) + '%' : '—'}</td>
      </tr>`).join('')
    printHTML(`<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8">
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Tajawal',Arial,sans-serif;font-size:12px;padding:24px}
.header{display:flex;justify-content:space-between;margin-bottom:20px;padding-bottom:12px;border-bottom:2px solid #0F6E56}
.ws-name{font-size:18px;font-weight:700;color:#0F6E56}
table{width:100%;border-collapse:collapse}
th{background:#f5f4f0;padding:7px 8px;text-align:right;font-size:10px;font-weight:600;color:#6b7280;border-bottom:2px solid #e2e0d8}
td{padding:6px 8px;border-bottom:1px solid #f3f4f6;font-size:11px}
.total-row td{background:#E1F5EE;font-weight:700;color:#0F6E56}
.footer{margin-top:24px;font-size:10px;color:#6b7280;text-align:center;border-top:1px solid #e2e0d8;padding-top:12px}
</style></head><body>
<div class="header"><div><div class="ws-name">${ws.name}</div></div>
<div><div style="font-size:16px;font-weight:700;color:#0F6E56">مبيعات حسب العميل</div>
<div style="font-size:11px;color:#6b7280">من ${from} إلى ${to}</div></div></div>
<table><thead><tr><th>#</th><th>العميل</th><th>عدد الفواتير</th><th>الإجمالي</th><th>المحصّل</th><th>المتبقي</th><th>نسبة المبيعات</th></tr></thead>
<tbody>${rows}
<tr class="total-row"><td colspan="3" style="text-align:right">الإجمالي</td><td>${egp(total)}</td><td>${egp(data.reduce((s,r)=>s+r.paid,0))}</td><td>${egp(data.reduce((s,r)=>s+r.unpaid,0))}</td><td>100%</td></tr>
</tbody></table>
<div class="footer">${ws.report_footer}</div></body></html>`)
  }

  return (
    <div className="flex flex-col gap-3">
      <DateRange from={from} to={to} onFrom={setFrom} onTo={setTo} />
      {data !== null && data.length === 0 && (
        <div className="text-center text-erp-muted py-4 text-sm">لا توجد مبيعات في هذه الفترة</div>
      )}
      {data?.length > 0 && (
        <table className="erp-table text-xs">
          <thead><tr><th>العميل</th><th>فواتير</th><th>الإجمالي</th><th>المحصّل</th><th>المتبقي</th></tr></thead>
          <tbody>
            {data.map(r => (
              <tr key={r.customer_id}>
                <td className="font-medium">{r.customer_name}</td>
                <td className="text-center">{r.invoice_count}</td>
                <td className="font-semibold">{egp(r.total)}</td>
                <td style={{ color: 'var(--green)' }}>{egp(r.paid)}</td>
                <td style={{ color: r.unpaid > 0 ? 'var(--red)' : 'var(--green)' }}>{egp(r.unpaid)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
        {data && <button className="btn btn-sm" onClick={printReport}>🖨 طباعة / PDF</button>}
      </div>
    </div>
  )
}

// ── Purchases by Supplier Report ─────────────────────────────────
function PurchasesBySupplierReport() {
  const today = new Date().toISOString().slice(0, 10)
  const firstOfMonth = today.slice(0, 8) + '01'
  const [from, setFrom] = useState(firstOfMonth)
  const [to, setTo] = useState(today)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await api.reports.purchasesBySupplier({ date_from: from, date_to: to })) } finally { setLoading(false) }
  }

  const printReport = async () => {
    if (!data) return
    const ws = await fetchWorkshopProfile()
    const total = data.reduce((s, r) => s + r.total, 0)
    const rows = data.map((r, i) => `
      <tr>
        <td>${i + 1}</td><td>${r.supplier_name}</td>
        <td style="text-align:center">${r.invoice_count}</td>
        <td style="font-weight:600">${egp(r.total)}</td>
        <td style="color:#0F6E56">${egp(r.paid)}</td>
        <td style="color:#c0392b">${egp(r.unpaid)}</td>
      </tr>`).join('')
    printHTML(`<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8">
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Tajawal',Arial,sans-serif;font-size:12px;padding:24px}
.header{display:flex;justify-content:space-between;margin-bottom:20px;padding-bottom:12px;border-bottom:2px solid #0F6E56}
.ws-name{font-size:18px;font-weight:700;color:#0F6E56}
table{width:100%;border-collapse:collapse}
th{background:#f5f4f0;padding:7px 8px;text-align:right;font-size:10px;font-weight:600;color:#6b7280;border-bottom:2px solid #e2e0d8}
td{padding:6px 8px;border-bottom:1px solid #f3f4f6;font-size:11px}
.total-row td{background:#E1F5EE;font-weight:700;color:#0F6E56}
.footer{margin-top:24px;font-size:10px;color:#6b7280;text-align:center;border-top:1px solid #e2e0d8;padding-top:12px}
</style></head><body>
<div class="header"><div><div class="ws-name">${ws.name}</div></div>
<div><div style="font-size:16px;font-weight:700;color:#0F6E56">مشتريات حسب المورد</div>
<div style="font-size:11px;color:#6b7280">من ${from} إلى ${to}</div></div></div>
<table><thead><tr><th>#</th><th>المورد</th><th>عدد الفواتير</th><th>الإجمالي</th><th>المدفوع</th><th>المتبقي</th></tr></thead>
<tbody>${rows}
<tr class="total-row"><td colspan="3" style="text-align:right">الإجمالي</td><td>${egp(total)}</td><td>${egp(data.reduce((s,r)=>s+r.paid,0))}</td><td>${egp(data.reduce((s,r)=>s+r.unpaid,0))}</td></tr>
</tbody></table>
<div class="footer">${ws.report_footer}</div></body></html>`)
  }

  return (
    <div className="flex flex-col gap-3">
      <DateRange from={from} to={to} onFrom={setFrom} onTo={setTo} />
      {data !== null && data.length === 0 && (
        <div className="text-center text-erp-muted py-4 text-sm">لا توجد مشتريات في هذه الفترة</div>
      )}
      {data?.length > 0 && (
        <table className="erp-table text-xs">
          <thead><tr><th>المورد</th><th>فواتير</th><th>الإجمالي</th><th>المدفوع</th><th>المتبقي</th></tr></thead>
          <tbody>
            {data.map((r, i) => (
              <tr key={i}>
                <td className="font-medium">{r.supplier_name}</td>
                <td className="text-center">{r.invoice_count}</td>
                <td className="font-semibold">{egp(r.total)}</td>
                <td style={{ color: 'var(--green)' }}>{egp(r.paid)}</td>
                <td style={{ color: r.unpaid > 0 ? 'var(--red)' : 'var(--green)' }}>{egp(r.unpaid)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
        {data && <button className="btn btn-sm" onClick={printReport}>🖨 طباعة / PDF</button>}
      </div>
    </div>
  )
}

// ── Production Summary Report ────────────────────────────────────
function ProductionSummaryReport() {
  const today = new Date().toISOString().slice(0, 10)
  const firstOfMonth = today.slice(0, 8) + '01'
  const [from, setFrom] = useState(firstOfMonth)
  const [to, setTo] = useState(today)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await api.reports.productionSummary({ date_from: from, date_to: to })) } finally { setLoading(false) }
  }

  const STATUS_AR = { draft: 'مسودة', in_progress: 'جاري', done: 'منجز', cancelled: 'ملغي' }

  return (
    <div className="flex flex-col gap-3">
      <DateRange from={from} to={to} onFrom={setFrom} onTo={setTo} />
      {data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
          {[
            { label: 'إجمالي الأوامر', val: data.total_orders, color: 'var(--teal)' },
            { label: 'التكلفة المخططة', val: egp(data.total_planned_cost), color: 'var(--blue)' },
            { label: 'التكلفة الفعلية', val: egp(data.total_actual_cost), color: 'var(--amber)' },
            { label: 'انحراف التكلفة', val: `${data.variance_pct > 0 ? '+' : ''}${data.variance_pct}%`, color: data.variance_pct > 10 ? 'var(--red)' : 'var(--green)' },
          ].map(k => (
            <div key={k.label} className="card py-2 text-center">
              <div className="font-bold text-sm" style={{ color: k.color }}>{k.val}</div>
              <div className="text-[10px] text-erp-muted">{k.label}</div>
            </div>
          ))}
        </div>
      )}
      {data?.by_status && (
        <div className="flex gap-2 flex-wrap">
          {Object.entries(data.by_status).map(([s, c]) => (
            <span key={s} className="badge badge-blue">{STATUS_AR[s] || s}: {c}</span>
          ))}
        </div>
      )}
      <button className="btn btn-primary btn-sm w-fit" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
    </div>
  )
}

function ProfitabilityReport() {
  const today = new Date().toISOString().slice(0, 10)
  const firstOfMonth = today.slice(0, 8) + '01'
  const [from, setFrom] = useState(firstOfMonth)
  const [to, setTo] = useState(today)
  const [groupBy, setGroupBy] = useState('work_order')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await api.reports.profitability({ date_from: from, date_to: to, group_by: groupBy })) } finally { setLoading(false) }
  }

  const exportCsv = () => api.export.profitability({ date_from: from, date_to: to })

  const STATUS_AR = { draft: 'مسودة', in_progress: 'جاري', done: 'منجز', cancelled: 'ملغي' }

  const printReport = async () => {
    if (!data) return
    const ws = await fetchWorkshopProfile()
    const rows = (data.rows || []).map(r => `
      <tr>
        <td>${r.code}</td>
        <td>${r.customer || '—'}</td>
        <td>${r.equipment || '—'}</td>
        <td>${egp(r.revenue)}</td>
        <td>${egp(r.cost)}</td>
        <td style="font-weight:600;color:${r.profit >= 0 ? '#0F6E56' : '#c0392b'}">${egp(r.profit)}</td>
        <td>${r.margin_pct}%</td>
      </tr>`).join('')
    printHTML(`<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8">
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Tajawal',Arial,sans-serif;font-size:12px;padding:24px}
.header{display:flex;justify-content:space-between;margin-bottom:20px;padding-bottom:12px;border-bottom:2px solid #0F6E56}
.ws-name{font-size:18px;font-weight:700;color:#0F6E56}
table{width:100%;border-collapse:collapse}
th{background:#f5f4f0;padding:7px 8px;text-align:right;font-size:10px;font-weight:600;color:#6b7280;border-bottom:2px solid #e2e0d8}
td{padding:6px 8px;border-bottom:1px solid #f3f4f6;font-size:11px}
.total-row td{background:#E1F5EE;font-weight:700;color:#0F6E56}
.footer{margin-top:24px;font-size:10px;color:#6b7280;text-align:center;border-top:1px solid #e2e0d8;padding-top:12px}
</style></head><body>
<div class="header"><div><div class="ws-name">${ws.name}</div></div>
<div><div style="font-size:16px;font-weight:700;color:#0F6E56">تقرير الربحية التفصيلي</div>
<div style="font-size:11px;color:#6b7280">من ${from} إلى ${to}</div></div></div>
<table><thead><tr><th>الكود</th><th>العميل</th><th>المعدة</th><th>الإيراد</th><th>التكلفة</th><th>الربح</th><th>الهامش %</th></tr></thead>
<tbody>${rows}
<tr class="total-row"><td colspan="3" style="text-align:right">الإجمالي</td><td>${egp(data.total_revenue)}</td><td>${egp(data.total_cost)}</td><td>${egp(data.total_profit)}</td><td>${data.total_margin_pct}%</td></tr>
</tbody></table>
<div class="footer">${ws.report_footer}</div></body></html>`)
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2 items-end flex-wrap mb-1">
        <DateRange from={from} to={to} onFrom={setFrom} onTo={setTo} />
        <div>
          <label className="field-label">التجميع حسب</label>
          <select className="field-input" value={groupBy} onChange={e => setGroupBy(e.target.value)}>
            <option value="work_order">أمر الإنتاج</option>
            <option value="customer">العميل</option>
            <option value="equipment">المعدة</option>
          </select>
        </div>
      </div>

      {data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
          {[
            { label: 'إجمالي الإيراد', val: egp(data.total_revenue), color: 'var(--teal)' },
            { label: 'إجمالي التكلفة', val: egp(data.total_cost), color: 'var(--amber)' },
            { label: 'إجمالي الربح', val: egp(data.total_profit), color: data.total_profit >= 0 ? 'var(--green)' : 'var(--red)' },
            { label: 'هامش الربح', val: `${data.total_margin_pct}%`, color: data.total_margin_pct >= 0 ? 'var(--green)' : 'var(--red)' },
          ].map(k => (
            <div key={k.label} className="card py-2 text-center">
              <div className="font-bold text-sm" style={{ color: k.color }}>{k.val}</div>
              <div className="text-[10px] text-erp-muted">{k.label}</div>
            </div>
          ))}
        </div>
      )}

      {data?.rows?.length > 0 && groupBy === 'work_order' && (
        <table className="erp-table text-xs">
          <thead><tr><th>الكود</th><th>الحالة</th><th>العميل</th><th>المعدة</th><th>الإيراد</th><th>التكلفة</th><th>الربح</th><th>الهامش %</th></tr></thead>
          <tbody>
            {data.rows.map(r => (
              <tr key={r.work_order_id}>
                <td className="font-medium">{r.code}</td>
                <td><span className="badge badge-blue">{STATUS_AR[r.status] || r.status}</span></td>
                <td>{r.customer || '—'}</td>
                <td>{r.equipment || '—'}</td>
                <td>{egp(r.revenue)}</td>
                <td>{egp(r.cost)}</td>
                <td style={{ color: r.profit >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>{egp(r.profit)}</td>
                <td>{r.margin_pct}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {data?.grouped?.length > 0 && groupBy !== 'work_order' && (
        <table className="erp-table text-xs">
          <thead><tr><th>{groupBy === 'customer' ? 'العميل' : 'المعدة'}</th><th>عدد الأوامر</th><th>الإيراد</th><th>التكلفة</th><th>الربح</th><th>الهامش %</th></tr></thead>
          <tbody>
            {data.grouped.map((g, i) => (
              <tr key={i}>
                <td className="font-medium">{g.name}</td>
                <td className="text-center">{g.count}</td>
                <td>{egp(g.revenue)}</td>
                <td>{egp(g.cost)}</td>
                <td style={{ color: g.profit >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>{egp(g.profit)}</td>
                <td>{g.margin_pct}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {data && (data.rows || []).length === 0 && (
        <div className="text-center text-erp-muted py-4 text-sm">لا توجد بيانات في هذه الفترة</div>
      )}

      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
        {data?.rows?.length > 0 && <button className="btn btn-sm" onClick={printReport}>🖨 طباعة / PDF</button>}
        {data?.rows?.length > 0 && <button className="btn btn-sm" onClick={exportCsv}>⬇️ تصدير CSV</button>}
      </div>
    </div>
  )
}

function CashflowReport() {
  const today = new Date().toISOString().slice(0, 10)
  const firstOfMonth = today.slice(0, 8) + '01'
  const [from, setFrom] = useState(firstOfMonth)
  const [to, setTo] = useState(today)
  const [groupBy, setGroupBy] = useState('day')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await api.reports.cashflow({ date_from: from, date_to: to, group_by: groupBy })) } finally { setLoading(false) }
  }

  const exportCsv = () => api.export.cashflow({ date_from: from, date_to: to, group_by: groupBy })

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2 items-end flex-wrap mb-1">
        <DateRange from={from} to={to} onFrom={setFrom} onTo={setTo} />
        <div>
          <label className="field-label">التجميع حسب</label>
          <select className="field-input" value={groupBy} onChange={e => setGroupBy(e.target.value)}>
            <option value="day">يومي</option>
            <option value="week">أسبوعي</option>
            <option value="month">شهري</option>
          </select>
        </div>
      </div>

      {data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
          {[
            { label: 'الرصيد الافتتاحي', val: egp(data.opening_balance), color: 'var(--blue)' },
            { label: 'إجمالي الداخل', val: egp(data.total_in), color: 'var(--green)' },
            { label: 'إجمالي الخارج', val: egp(data.total_out), color: 'var(--red)' },
            { label: 'الرصيد الختامي', val: egp(data.closing_balance), color: data.closing_balance >= 0 ? 'var(--teal)' : 'var(--red)' },
          ].map(k => (
            <div key={k.label} className="card py-2 text-center">
              <div className="font-bold text-sm" style={{ color: k.color }}>{k.val}</div>
              <div className="text-[10px] text-erp-muted">{k.label}</div>
            </div>
          ))}
        </div>
      )}

      {data?.treasuries?.length > 0 && (
        <div className="flex gap-2 flex-wrap">
          {data.treasuries.map(t => (
            <span key={t.id} className="badge badge-blue">{t.name}: {egp(t.balance)}</span>
          ))}
        </div>
      )}

      {data?.periods?.length > 0 && (
        <table className="erp-table text-xs">
          <thead><tr><th>الفترة</th><th>داخل</th><th>خارج</th><th>صافي</th><th>الرصيد</th></tr></thead>
          <tbody>
            {data.periods.map(p => (
              <tr key={p.period}>
                <td className="font-medium">{p.period}</td>
                <td style={{ color: 'var(--green)' }}>{egp(p.in)}</td>
                <td style={{ color: 'var(--red)' }}>{egp(p.out)}</td>
                <td style={{ color: p.net >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>{egp(p.net)}</td>
                <td>{egp(p.balance)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {data?.by_category?.length > 0 && (
        <>
          <div className="text-xs font-semibold text-erp-muted mt-2">التوزيع حسب الفئة</div>
          <table className="erp-table text-xs">
            <thead><tr><th>الفئة</th><th>النوع</th><th>الإجمالي</th><th>عدد الحركات</th></tr></thead>
            <tbody>
              {data.by_category.map((c, i) => (
                <tr key={i}>
                  <td className="font-medium">{c.name}</td>
                  <td><span className={c.flow === 'in' ? 'badge badge-green' : 'badge badge-red'}>{c.flow === 'in' ? 'داخل' : 'خارج'}</span></td>
                  <td>{egp(c.total)}</td>
                  <td className="text-center">{c.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {data && (data.periods || []).length === 0 && (
        <div className="text-center text-erp-muted py-4 text-sm">لا توجد حركات في هذه الفترة</div>
      )}

      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
        {data?.periods?.length > 0 && <button className="btn btn-sm" onClick={exportCsv}>⬇️ تصدير CSV</button>}
      </div>
    </div>
  )
}

function LaborPerformanceReport() {
  const today = new Date().toISOString().slice(0, 10)
  const firstOfMonth = today.slice(0, 8) + '01'
  const [from, setFrom] = useState(firstOfMonth)
  const [to, setTo] = useState(today)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await api.reports.laborPerformance({ date_from: from, date_to: to })) } finally { setLoading(false) }
  }

  const exportCsv = () => api.export.laborPerformance({ date_from: from, date_to: to })

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2 items-end flex-wrap mb-1">
        <DateRange from={from} to={to} onFrom={setFrom} onTo={setTo} />
      </div>

      {data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
          {[
            { label: 'عدد العمال', val: data.total_workers },
            { label: 'ساعات مخططة', val: data.total_planned_hours, color: 'var(--blue)' },
            { label: 'ساعات فعلية', val: data.total_actual_hours, color: 'var(--teal)' },
            { label: 'الكفاءة العامة', val: data.overall_efficiency_pct != null ? `${data.overall_efficiency_pct}%` : '—', color: 'var(--green)' },
            { label: 'تكلفة العمالة', val: egp(data.total_labor_cost), color: 'var(--red)' },
            { label: 'صافي الرواتب', val: egp(data.total_payroll_net), color: 'var(--red)' },
            { label: 'أيام حضور', val: data.total_present_days, color: 'var(--green)' },
            { label: 'أيام غياب', val: data.total_absent_days, color: 'var(--red)' },
          ].map(k => (
            <div key={k.label} className="card py-2 text-center">
              <div className="font-bold text-sm" style={{ color: k.color }}>{k.val}</div>
              <div className="text-[10px] text-erp-muted">{k.label}</div>
            </div>
          ))}
        </div>
      )}

      {data?.rows?.length > 0 && (
        <div className="overflow-x-auto">
          <table className="erp-table text-xs">
            <thead>
              <tr>
                <th>الكود</th><th>الاسم</th><th>الوظيفة</th>
                <th>حضور</th><th>غياب</th><th>نسبة الحضور</th>
                <th>أوامر العمل</th><th>ساعات مخططة</th><th>ساعات فعلية</th><th>الكفاءة</th>
                <th>تكلفة العمالة</th><th>صافي الراتب</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map(r => (
                <tr key={r.worker_id}>
                  <td className="font-medium">{r.code}</td>
                  <td>{r.name}</td>
                  <td className="text-erp-muted">{r.job_title || '—'}</td>
                  <td style={{ color: 'var(--green)' }}>{r.present_days}</td>
                  <td style={{ color: 'var(--red)' }}>{r.absent_days}</td>
                  <td><span className={r.attendance_rate >= 90 ? 'badge badge-green' : r.attendance_rate >= 70 ? 'badge badge-blue' : 'badge badge-red'}>{r.attendance_rate}%</span></td>
                  <td className="text-center">{r.work_orders}</td>
                  <td>{r.planned_hours}</td>
                  <td>{r.actual_hours}</td>
                  <td>{r.efficiency_pct != null ? `${r.efficiency_pct}%` : '—'}</td>
                  <td>{egp(r.labor_cost)}</td>
                  <td>{egp(r.payroll_net)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && (data.rows || []).length === 0 && (
        <div className="text-center text-erp-muted py-4 text-sm">لا يوجد عمالة نشطة</div>
      )}

      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
        {data?.rows?.length > 0 && <button className="btn btn-sm" onClick={exportCsv}>⬇️ تصدير CSV</button>}
      </div>
    </div>
  )
}

function PeriodComparisonReport() {
  const today = new Date().toISOString().slice(0, 10)
  const firstOfMonth = today.slice(0, 8) + '01'
  const [from, setFrom] = useState(firstOfMonth)
  const [to, setTo] = useState(today)
  const [compareFrom, setCompareFrom] = useState('')
  const [compareTo, setCompareTo] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const params = { date_from: from, date_to: to }
      if (compareFrom) params.compare_from = compareFrom
      if (compareTo) params.compare_to = compareTo
      setData(await api.reports.periodComparison(params))
    } finally { setLoading(false) }
  }

  const exportCsv = () => {
    const params = { date_from: from, date_to: to }
    if (compareFrom) params.compare_from = compareFrom
    if (compareTo) params.compare_to = compareTo
    api.export.periodComparison(params)
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2 items-end flex-wrap mb-1">
        <DateRange from={from} to={to} onFrom={setFrom} onTo={setTo} />
      </div>
      <div className="flex gap-2 items-end flex-wrap mb-1">
        <div className="text-xs text-erp-muted">فترة المقارنة (اختياري — تُحسب تلقائيًا الفترة السابقة بنفس الطول إن لم تُحدَّد)</div>
      </div>
      <div className="flex gap-2 items-end flex-wrap mb-1">
        <DateRange from={compareFrom} to={compareTo} onFrom={setCompareFrom} onTo={setCompareTo} />
      </div>

      {data && (
        <>
          <div className="text-xs text-erp-muted">
            الفترة الحالية: <strong>{data.current.date_from} → {data.current.date_to}</strong>
            {'  |  '}الفترة السابقة: <strong>{data.previous.date_from} → {data.previous.date_to}</strong>
          </div>
          <table className="erp-table text-xs">
            <thead>
              <tr>
                <th>المؤشر</th><th>الفترة الحالية</th><th>الفترة السابقة</th><th>الفرق</th><th>التغيير %</th>
              </tr>
            </thead>
            <tbody>
              {data.comparison.map(c => (
                <tr key={c.key}>
                  <td className="font-medium">{c.label}</td>
                  <td>{c.current}</td>
                  <td className="text-erp-muted">{c.previous}</td>
                  <td style={{ color: c.change >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>{c.change >= 0 ? '+' : ''}{c.change}</td>
                  <td>
                    {c.change_pct == null ? '—' : (
                      <span className={c.change_pct >= 0 ? 'badge badge-green' : 'badge badge-red'}>
                        {c.change_pct >= 0 ? '▲' : '▼'} {Math.abs(c.change_pct)}%
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
        {data && <button className="btn btn-sm" onClick={exportCsv}>⬇️ تصدير CSV</button>}
      </div>
    </div>
  )
}

// ── محطة 6: متابعة تأخر الأقساط + تنبيه بالأقساط القادمة ──────────
function InstallmentAlertsReport() {
  const [upcoming, setUpcoming] = useState(null)
  const [overdue, setOverdue] = useState(null)
  const [days, setDays] = useState(7)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [u, o] = await Promise.all([
        api.reports.installmentsUpcoming(days),
        api.reports.installmentsOverdue(),
      ])
      setUpcoming(u); setOverdue(o)
    } finally { setLoading(false) }
  }

  const Row = ({ x, kind }) => (
    <tr>
      <td><span className="font-mono text-xs badge badge-gray">{x.invoice_code}</span></td>
      <td>{x.customer_name || '—'}</td>
      <td>{x.label || `قسط ${x.installment_no}`}</td>
      <td>{x.due_date}</td>
      <td style={{ color: kind === 'overdue' ? 'var(--red)' : 'var(--amber)' }}>
        {kind === 'overdue' ? `متأخر ${x.days_overdue} يوم` : `خلال ${-x.days_overdue} يوم`}
      </td>
      <td className="font-semibold">{egp(x.remaining_amount)}</td>
    </tr>
  )

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <label className="text-xs text-erp-muted">نافذة التنبيه (أيام)</label>
        <input type="number" min="1" className="input input-sm" style={{ width: 70 }}
          value={days} onChange={e => setDays(Number(e.target.value) || 7)} />
        <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '…' : '📊 تحميل'}</button>
      </div>

      {overdue && (
        <div>
          <div className="text-sm font-semibold mb-1" style={{ color: 'var(--red)' }}>
            🔴 أقساط متأخرة ({overdue.count}) — إجمالي {egp(overdue.total_overdue)}
          </div>
          {overdue.items.length === 0 ? (
            <div className="text-xs text-erp-muted">لا يوجد أقساط متأخرة 🎉</div>
          ) : (
            <table className="erp-table text-xs">
              <thead><tr><th>الفاتورة</th><th>العميل</th><th>القسط</th><th>الاستحقاق</th><th>الحالة</th><th>المتبقي</th></tr></thead>
              <tbody>{overdue.items.map(x => <Row key={x.installment_id} x={x} kind="overdue" />)}</tbody>
            </table>
          )}
        </div>
      )}

      {upcoming && (
        <div>
          <div className="text-sm font-semibold mb-1" style={{ color: 'var(--amber)' }}>
            🟡 أقساط قادمة خلال {upcoming.days} يوم ({upcoming.count}) — إجمالي {egp(upcoming.total_due)}
          </div>
          {upcoming.items.length === 0 ? (
            <div className="text-xs text-erp-muted">لا يوجد أقساط مستحقة قريبًا</div>
          ) : (
            <table className="erp-table text-xs">
              <thead><tr><th>الفاتورة</th><th>العميل</th><th>القسط</th><th>الاستحقاق</th><th>الحالة</th><th>المتبقي</th></tr></thead>
              <tbody>{upcoming.items.map(x => <Row key={x.installment_id} x={x} kind="upcoming" />)}</tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main Reports page ────────────────────────────────────────────
export default function Reports() {
  return (
    <div className="flex flex-col gap-3">
      {/* Section: PDF Documents */}
      <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider px-1 mt-1">مستندات PDF</div>

      <ReportCard icon="🧾" title="فاتورة مبيعات" desc="طباعة / تصدير PDF لأي فاتورة">
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

      {/* Section: Inventory */}
      <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider px-1 mt-2">تقارير المخزون</div>

      <ReportCard icon="📦" title="ملخص المخزون" desc="القيمة الإجمالية + أعلى المواد + تحذيرات النقص">
        <InventorySummaryReport />
      </ReportCard>

      <ReportCard icon="🔄" title="حركة المخزون" desc="سجل الوارد والصادر في فترة زمنية">
        <InventoryMovementReport />
      </ReportCard>

      <ReportCard icon="🧊" title="المواد الراكدة" desc="مواد لم تتحرك منذ فترة — فرصة لتحسين المخزون">
        <SlowMovingReport />
      </ReportCard>

      {/* Section: Sales */}
      <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider px-1 mt-2">تقارير المبيعات والتحصيل</div>

      <ReportCard icon="⏳" title="Aging التحصيل" desc="فواتير متأخرة مرتبة بعمر الدين — أولويات التحصيل">
        <AgingReport />
      </ReportCard>

      <ReportCard icon="📅" title="متابعة أقساط الفواتير" desc="الأقساط المتأخرة + القادمة خلال فترة تحددها">
        <InstallmentAlertsReport />
      </ReportCard>

      <ReportCard icon="👥" title="مبيعات حسب العميل" desc="من هو أكبر عملائك؟ وما نسبة تحصيله؟">
        <SalesByCustomerReport />
      </ReportCard>

      {/* Section: Purchases */}
      <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider px-1 mt-2">تقارير المشتريات</div>

      <ReportCard icon="🏭" title="مشتريات حسب المورد" desc="تحليل الموردين والمبالغ المدفوعة والمتبقية">
        <PurchasesBySupplierReport />
      </ReportCard>

      {/* Section: Production */}
      <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider px-1 mt-2">تقارير الإنتاج</div>

      <ReportCard icon="🏭" title="ملخص الإنتاج" desc="أداء أوامر الإنتاج ومقارنة التكاليف المخططة بالفعلية">
        <ProductionSummaryReport />
      </ReportCard>

      {/* Section: Profitability */}
      <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider px-1 mt-2">تقارير الربحية</div>

      <ReportCard icon="💰" title="تقرير الربحية التفصيلي" desc="الإيراد والتكلفة والربح لكل أمر إنتاج/عميل/معدة">
        <ProfitabilityReport />
      </ReportCard>

      {/* Section: Treasury */}
      <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider px-1 mt-2">تقارير الخزينة</div>

      <ReportCard icon="🏦" title="التدفقات النقدية" desc="حركة الداخل والخارج بالخزينة وتوزيعها حسب الفئة">
        <CashflowReport />
      </ReportCard>

      {/* Section: Labor */}
      <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider px-1 mt-2">تقارير العمالة</div>

      <ReportCard icon="👷" title="أداء العمالة" desc="الحضور وساعات العمل والكفاءة وتكلفة العمالة لكل عامل">
        <LaborPerformanceReport />
      </ReportCard>

      {/* Section: Comparison */}
      <div className="text-xs font-semibold text-erp-muted uppercase tracking-wider px-1 mt-2">مقارنة الفترات</div>

      <ReportCard icon="📈" title="مقارنة فترتين" desc="مقارنة المبيعات والتحصيل والتكاليف والتدفق النقدي بين فترتين">
        <PeriodComparisonReport />
      </ReportCard>
    </div>
  )
}
