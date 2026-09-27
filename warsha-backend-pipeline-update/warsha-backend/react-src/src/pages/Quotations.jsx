/**
 * Quotations — عروض الأسعار
 *
 * تحسينات مطبّقة:
 * 1. Line items ديناميكي (وصف + كمية + وحدة + سعر) مع حساب تلقائي
 * 2. خصم % وضريبة % مع ملخص مفصّل: subtotal → خصم → taxable → ضريبة → الإجمالي
 * 3. زر "تحويل لفاتورة" مباشر بدون الحاجة لتغيير الحالة يدوياً
 * 4. زر طباعة يفتح /sales/quotations/{id}/print
 * 5. توليد تلقائي لرقم العرض
 * 6. حقل Terms الشروط والأحكام
 * 7. عرض تفاصيل البنود في expand row
 */

import { useState, useMemo, useCallback, useEffect, useRef } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api, { getToken, bgErr } from '../api/client'
import { egp, fmtDate, today, QUOTE_STATUS } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import SearchInput from '../components/ui/SearchInput'
import { PageSpinner } from '../components/ui/Spinner'
import { fetchWorkshopProfile } from './WorkshopProfile'

// ── Print helper (same as Reports.jsx) ───────────────────────────────────────

const NAVY = '#071B34'
const ORANGE = '#FF7A00'
const ORANGE_LIGHT = '#ffcb9a'
const BORDER = '#E5E7EB'

const DOC_TITLE_EN = {
  'عرض سعر': 'QUOTATION',
  'أمر توريد': 'SUPPLY ORDER',
  'عقد اتفاق': 'CONTRACT',
}

// بدون خطوط خارجية (Google Fonts) — تحميل مورد عبر الشبكة في نافذة الطباعة قد يمنع اكتمال
// الرندر قبل استدعاء print()‎ فيخرج PDF صفحة بيضاء. نستخدم خط النظام العربي بديلاً عن Cairo.
const FONT_LINK = ''
const FONT_STACK = `'Cairo','Segoe UI','Tahoma',Arial,sans-serif`

// شريط الهيدر الموحّد لكل المطبوعات: اسم الورشة وشعارها على اليمين، بيانات التواصل على الشمال
function pageHeaderHTML(ws) {
  const logo = ws.logo_data ? `<img src="${ws.logo_data}" class="header-logo">` : ''
  return `
<div class="header">
  <div class="logo-section">
    ${logo}
    <div>
      <h1>${ws.name || ''}</h1>
      ${ws.address ? `<p>${ws.address}</p>` : ''}
    </div>
  </div>
  ${ws.phone ? `<div class="contact">${ws.phone}</div>` : ''}
</div>`
}

// عنوان المستند (عربي كبير + إنجليزي بفاصل حروف) — يُعرض أسفل الهيدر مباشرة
function pageTitleHTML(titleAr) {
  const titleEn = DOC_TITLE_EN[titleAr] || ''
  return `
<div class="title">
  <h2>${titleAr}</h2>
  ${titleEn ? `<span>${titleEn}</span>` : ''}
</div>`
}

// شريط تذييل موحّد ببيانات التواصل
function pageFooterHTML(ws) {
  const items = []
  if (ws.phone) items.push(`📞 ${ws.phone}`)
  if (ws.email) items.push(`✉️ ${ws.email}`)
  if (ws.website) items.push(`🌐 ${ws.website}`)
  if (!items.length && ws.report_footer) items.push(ws.report_footer)
  if (!items.length) return ''
  return `<div class="footer">${items.map(i => `<span>${i}</span>`).join('')}</div>`
}

// CSS مشترك (هيدر/عنوان/بطاقات معلومات/جدول/تذييل) — ثيم واحد ثابت navy/orange بخط Cairo
function sharedStyles() {
  return `
  @page { size: A4 landscape; margin: 10mm }
  * { box-sizing:border-box; margin:0; padding:0; font-family:${FONT_STACK} }
  body { background:#fff; color:#1F2937; width:277mm; font-size:12px }
  .sheet { padding:6mm 8mm; min-height:190mm }
  .header { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:14px 20px; background:${NAVY}; color:#fff; border-radius:12px }
  .logo-section { display:flex; align-items:center; gap:12px }
  .header-logo { width:46px; height:46px; object-fit:contain; background:#fff; border-radius:8px; padding:4px; flex-shrink:0 }
  .logo-section h1 { font-size:17px; font-weight:800 }
  .logo-section p { color:${ORANGE_LIGHT}; font-size:10px; margin-top:3px }
  .contact { font-size:12px; font-weight:700; direction:ltr }
  .title { text-align:center; padding:16px 0 12px }
  .title h2 { font-size:28px; color:${NAVY}; font-weight:800 }
  .title span { display:block; color:${ORANGE}; letter-spacing:4px; font-weight:700; font-size:10px; margin-top:4px }
  .info { display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:10px; margin-bottom:14px }
  .card { background:#fff; border:1px solid ${BORDER}; padding:10px 12px; border-radius:10px; font-size:11px; line-height:1.9 }
  .card-title { font-size:12px; font-weight:700; margin-bottom:6px; color:${NAVY} }
  table { width:100%; border-collapse:collapse; border-radius:10px; overflow:hidden }
  thead { background:${NAVY}; color:#fff }
  th { padding:8px; font-size:11px; font-weight:700; text-align:center }
  td { padding:7px; border-bottom:1px solid #eee; font-size:11px; text-align:center; vertical-align:middle }
  td.spec { text-align:right; font-size:10px; line-height:1.7; white-space:pre-wrap; word-wrap:break-word; overflow-wrap:anywhere }
  .price { font-weight:800; color:${ORANGE}; font-size:13px }
  .bottom { display:grid; grid-template-columns:2fr 1fr; gap:12px; margin-top:14px }
  .conditions { border:1px solid ${BORDER}; border-radius:10px; padding:12px }
  .conditions h3 { color:${ORANGE}; margin-bottom:8px; font-size:12px }
  .conditions ul { padding-right:16px; line-height:2; font-size:11px }
  .summary { border-radius:10px; overflow:hidden; border:1px solid ${BORDER}; align-self:start }
  .summary-row { display:flex; justify-content:space-between; padding:8px 12px; font-size:11px; border-bottom:1px solid ${BORDER} }
  .summary-row:last-child { border-bottom:none }
  .total { background:${ORANGE}; color:#fff; font-size:16px; font-weight:800 }
  .footer { background:${NAVY}; color:#fff; padding:10px 16px; display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px; font-size:10px; border-radius:10px; margin-top:14px; direction:ltr }
  .section { margin-bottom:16px }
  .section-title { font-size:12px; font-weight:700; color:#6b7280; letter-spacing:.5px; margin-bottom:8px; padding-bottom:4px; border-bottom:1px solid ${BORDER} }
  .sign-row { display:flex; justify-content:space-between; margin-top:48px }
  .sign-box { width:45%; text-align:center; font-size:12px }
  .sign-line { border-top:1px solid #1a1a18; margin-top:48px; padding-top:6px }
  .clause { margin:8px 0; font-size:12px; line-height:1.9 }
  .line-img { width:60px; height:50px; object-fit:cover; border-radius:6px; border:1px solid ${BORDER} }
  .items-grid { display:flex; flex-direction:column; gap:10px; margin-bottom:14px }
  .item-card { border:1px solid ${BORDER}; border-radius:10px; overflow:hidden; break-inside:avoid; page-break-inside:avoid }
  .item-card-head { display:flex; justify-content:space-between; align-items:center; gap:8px; background:${NAVY}; color:#fff; padding:8px 12px }
  .item-card-title { font-size:13px; font-weight:800 }
  .item-card-meta { font-size:10px; color:${ORANGE_LIGHT}; text-align:left; white-space:nowrap }
  .item-card-body { display:flex; gap:10px; padding:10px 12px }
  .item-card-img { width:90px; height:78px; object-fit:cover; border-radius:6px; border:1px solid ${BORDER}; flex-shrink:0 }
  .item-card-specs { font-size:10px; line-height:1.8; white-space:pre-wrap; word-wrap:break-word; overflow-wrap:anywhere; flex:1; color:#374151 }
  .item-card-foot { display:flex; justify-content:space-between; align-items:center; padding:7px 12px; border-top:1px solid ${BORDER}; font-size:11px; background:#fafafa }
  .item-card-foot .price { font-weight:800; color:${ORANGE}; font-size:13px }
  `
}

function buildQuoteHTML(qt, lines, ws) {
  const subtotal = lines.reduce((s, l) => s + Number(l.qty || 1) * Number(l.unit_price || 0), 0)
  const tax      = subtotal * Number(qt.tax_pct || 0) / 100
  const disc     = subtotal * Number(qt.discount_pct || 0) / 100
  const total    = subtotal + tax - disc
  return `<!DOCTYPE html><html dir="rtl" lang="ar">
<head><meta charset="UTF-8">${FONT_LINK}
<style>
  ${sharedStyles()}
</style>
</head><body>
<div class="sheet">
${pageHeaderHTML(ws)}
${pageTitleHTML('عرض سعر')}
<div class="info">
  <div class="card"><div class="card-title">بيانات العميل</div>${qt.customer_name || '—'}</div>
  <div class="card"><div class="card-title">بيانات العرض</div>رقم العرض: ${qt.number || qt.code}<br>التاريخ: ${fmtDate(qt.date)}</div>
  <div class="card"><div class="card-title">صلاحية العرض</div>${qt.valid_until ? `حتى ${fmtDate(qt.valid_until)}` : '—'}</div>
</div>
<div class="items-grid">
  ${lines.map((l, i) => `
    <div class="item-card">
      <div class="item-card-head">
        <div class="item-card-title">${i + 1}. ${l.item_type || 'بند'}</div>
        <div class="item-card-meta">${l.model_no ? `موديل: ${l.model_no}<br>` : ''}الكمية: ${Number(l.qty || 1).toFixed(2)} ${l.unit || 'قطعة'}</div>
      </div>
      <div class="item-card-body">
        ${l.image_data ? `<img src="${l.image_data}" class="item-card-img">` : ''}
        <div class="item-card-specs">${(l.description || '').split('\n').filter(Boolean).map(t => `<div>${t}</div>`).join('') || '—'}</div>
      </div>
      <div class="item-card-foot">
        <span>سعر الوحدة: ${egp(l.unit_price)}</span>
        <span class="price">الإجمالي: ${egp(Number(l.qty || 1) * Number(l.unit_price || 0))}</span>
      </div>
    </div>`).join('')}
</div>
<div class="bottom">
  <div class="conditions">
    <h3>الشروط والملاحظات</h3>
    <ul>
      ${qt.terms ? qt.terms.split('\n').filter(Boolean).map(t => `<li>${t}</li>`).join('') : ''}
      ${qt.notes ? qt.notes.split('\n').filter(Boolean).map(t => `<li>${t}</li>`).join('') : ''}
      ${!qt.terms && !qt.notes ? '<li>هذا العرض غير ملزم إلا بعد التوقيع عليه من الطرفين.</li>' : ''}
    </ul>
  </div>
  <div class="summary">
    <div class="summary-row"><span>الإجمالي قبل الضريبة</span><span>${egp(subtotal)}</span></div>
    ${qt.tax_pct > 0 ? `<div class="summary-row"><span>ضريبة ${qt.tax_pct}%</span><span>${egp(tax)}</span></div>` : ''}
    ${qt.discount_pct > 0 ? `<div class="summary-row"><span>خصم ${qt.discount_pct}%</span><span>- ${egp(disc)}</span></div>` : ''}
    <div class="summary-row total"><span>الإجمالي النهائي</span><span>${egp(total)}</span></div>
  </div>
</div>
${pageFooterHTML(ws)}
</div>
</body></html>`
}

function docShell(ws, titleAr, infoCardsHtml, bodyHtml) {
  return `<!DOCTYPE html><html dir="rtl" lang="ar">
<head><meta charset="UTF-8">${FONT_LINK}
<style>${sharedStyles()}</style>
</head><body>
<div class="sheet">
${pageHeaderHTML(ws)}
${pageTitleHTML(titleAr)}
${infoCardsHtml ? `<div class="info">${infoCardsHtml}</div>` : ''}
${bodyHtml}
${pageFooterHTML(ws)}
</div>
</body></html>`
}

function lineSpecCell(l) {
  const spec = (l.description || '').split('\n').filter(Boolean).map(t => `<div>${t}</div>`).join('')
  return `<strong>${l.item_type || ''}${l.model_no ? ` (${l.model_no})` : ''}</strong>${spec ? spec : ''}`
}

function linesTable(lines) {
  return `<table>
    <thead><tr><th>#</th><th>البند / المواصفات الفنية</th><th>الكمية</th><th>الوحدة</th><th>سعر الوحدة</th><th>الإجمالي</th></tr></thead>
    <tbody>${lines.map((l,i)=>`<tr><td>${i+1}</td><td class="spec">${lineSpecCell(l)}</td><td>${Number(l.qty||1).toFixed(2)}</td><td>${l.unit||'قطعة'}</td><td>${egp(l.unit_price)}</td><td class="price">${egp(Number(l.qty||1)*Number(l.unit_price||0))}</td></tr>`).join('')}</tbody>
  </table>`
}

function linesTableNoPrice(lines) {
  return `<table>
    <thead><tr><th>#</th><th>البند / المواصفات الفنية</th><th>الكمية</th><th>الوحدة</th></tr></thead>
    <tbody>${lines.map((l,i)=>`<tr><td>${i+1}</td><td class="spec">${lineSpecCell(l)}</td><td>${Number(l.qty||1).toFixed(2)}</td><td>${l.unit||'قطعة'}</td></tr>`).join('')}</tbody>
  </table>`
}

function buildSupplyOrderHTML(qt, lines, ws) {
  const info = `
    <div class="card"><div class="card-title">المورّد له / الجهة</div>${qt.customer_name || '—'}</div>
    <div class="card"><div class="card-title">بيانات الأمر</div>رقم الأمر: ${qt.number || qt.code}<br>التاريخ: ${fmtDate(qt.date)}</div>
    <div class="card"><div class="card-title">مرجع عرض السعر</div>${qt.number || qt.code}</div>`
  const body = `
    <div class="section"><div class="section-title">الأصناف المطلوب توريدها</div>${linesTableNoPrice(lines)}</div>
    <div class="clause" style="margin-top:20px">يُرجى توريد الأصناف المذكورة أعلاه حسب المواصفات والكميات المتفق عليها${qt.valid_until?` في موعد أقصاه ${fmtDate(qt.valid_until)}`:''}.</div>
    ${qt.terms?`<div class="clause">الشروط: ${qt.terms}</div>`:''}
    <div class="sign-row">
      <div class="sign-box"><div class="sign-line">توقيع الجهة الطالبة</div></div>
      <div class="sign-box"><div class="sign-line">توقيع المورّد</div></div>
    </div>`
  return docShell(ws, 'أمر توريد', info, body)
}

function buildContractHTML(qt, lines, ws) {
  const subtotal = lines.reduce((s,l)=>s+Number(l.qty||1)*Number(l.unit_price||0),0)
  const tax  = subtotal * Number(qt.tax_pct||0)/100
  const disc = subtotal * Number(qt.discount_pct||0)/100
  const total = subtotal + tax - disc
  const info = `
    <div class="card"><div class="card-title">بيانات العقد</div>رقم العقد: ${qt.number || qt.code}<br>التاريخ: ${fmtDate(qt.date)}</div>
    <div class="card"><div class="card-title">الطرف الأول</div>${ws.name || ''}${ws.tax_id ? `<br>بطاقة ضريبية: ${ws.tax_id}` : ''}</div>
    <div class="card"><div class="card-title">الطرف الثاني</div>${qt.customer_name || '—'}</div>`
  const body = `
    <div class="clause">إنه في يوم ${fmtDate(qt.date)} تم الاتفاق بين كل من الطرف الأول والطرف الثاني على ما يلي:</div>
    <div class="section" style="margin-top:16px"><div class="section-title">البند الأول: محل العقد</div>
      <div class="clause">اتفق الطرفان على قيام الطرف الأول بتنفيذ/توريد الأصناف التالية للطرف الثاني:</div>
      ${linesTable(lines)}
    </div>
    <div class="section"><div class="section-title">البند الثاني: قيمة العقد</div>
      <div class="clause">القيمة الإجمالية للعقد <strong>${egp(total)}</strong> (شاملة الضريبة والخصم إن وُجد).</div>
    </div>
    <div class="section"><div class="section-title">البند الثالث: الشروط</div>
      <div class="clause">${qt.terms || 'تسري الشروط والأحكام العامة المتفق عليها بين الطرفين.'}</div>
      ${qt.valid_until?`<div class="clause">مدة التنفيذ حتى تاريخ ${fmtDate(qt.valid_until)}.</div>`:''}
    </div>
    <div class="sign-row">
      <div class="sign-box"><div class="sign-line">الطرف الأول</div></div>
      <div class="sign-box"><div class="sign-line">الطرف الثاني</div></div>
    </div>`
  return docShell(ws, 'عقد اتفاق', info, body)
}

// يجلب صفحة الطباعة الجاهزة من السيرفر (HTML كامل بأنماطه، مُختبَر أنه يطبع
// بالاستايل الصحيح). تتطلب توكن المصادقة في الهيدر، فلا يمكن فتحها مباشرة عبر
// window.open — نجلبها بـ fetch ثم نكتبها في النافذة المفتوحة مسبقًا.
async function fetchPrintHTML(path) {
  const res = await fetch(`/api/v1${path}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  })
  if (!res.ok) throw new Error('فشل تحميل صفحة الطباعة')
  return await res.text()
}

// نطبع عبر نافذة جديدة مستقلة (مستند كامل بأنماطه الخاصة، بلا أي تعارض مع CSS
// التطبيق). النافذة تُفتح بشكل متزامن في handlePrint/handleWhatsApp قبل أي
// await — لأن المتصفح يحجب window.open المُستدعى بعد عمليات async تعتبر خارج
// تفاعل المستخدم. إن حُجبت (win=null) نرجع لطباعة داخل نفس الصفحة كخطة بديلة.
function printHTML(html, win) {
  if (win && !win.closed) {
    win.document.open()
    win.document.write(html)
    win.document.close()
    const doPrint = () => {
      try { win.focus(); win.print() }
      catch (e) {
        window.dispatchEvent(new CustomEvent('erp:toast', {
          detail: { msg: 'تعذّر فتح مربع الطباعة تلقائيًا — استخدم Ctrl+P من نافذة المعاينة', type: 'warn' },
        }))
      }
    }
    if (win.document.readyState === 'complete') setTimeout(doPrint, 300)
    else win.addEventListener('load', () => setTimeout(doPrint, 300))
    return true
  }
  return printInPage(html)
}

// خطة بديلة: نحقن محتوى المستند المُولَّد كـ div مخفي ونطاق كل قواعد الـ CSS
// الخاصة به تحت @media print، ثم نستدعي window.print() على نفس النافذة —
// تُستخدم فقط لو حجب المتصفح فتح نافذة جديدة.
function printInPage(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html')

  // أضف روابط الخطوط من رأس المستند المُولَّد إن لم تكن محمّلة بالفعل
  doc.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
    if (![...document.head.querySelectorAll('link')].some(l => l.href === link.href)) {
      document.head.appendChild(link.cloneNode())
    }
  })

  // فصل @page (تُطبَّق عالميًا أثناء الطباعة) عن باقي القواعد، ثم نطاق باقي
  // القواعد تحت #__print_area لتفادي تعارضها مع تنسيقات التطبيق نفسه
  let css = [...doc.querySelectorAll('style')].map(s => s.textContent).join('\n')
  const pageRules = []
  css = css.replace(/@page[^{]*\{[^}]*\}/g, m => { pageRules.push(m); return '' })
  const scoped = css.replace(/([^{}]+)\{([^{}]*)\}/g, (m, sel, decl) =>
    `${sel.split(',').map(s => `#__print_area ${s.trim()}`).join(', ')} { ${decl} }`)

  document.getElementById('__print_area')?.remove()
  document.getElementById('__print_style')?.remove()

  const style = document.createElement('style')
  style.id = '__print_style'
  style.textContent = `
    ${pageRules.join('\n')}
    @media screen { #__print_area { display:none } }
    @media print {
      body > *:not(#__print_area) { display:none !important }
      #__print_area { display:block !important }
      ${scoped}
    }
  `
  document.head.appendChild(style)

  const container = document.createElement('div')
  container.id = '__print_area'
  container.innerHTML = doc.body.innerHTML
  document.body.appendChild(container)

  window.print()

  setTimeout(() => { container.remove(); style.remove() }, 1000)
  return true
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const UNITS = ['قطعة', 'م²', 'م³', 'طن', 'كجم', 'لتر', 'ساعة', 'يوم', 'متر']

/** توليد رقم عرض تلقائي */
const genQuoteNumber = () => {
  const d = new Date()
  const yy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0') + String(d.getMilliseconds()).slice(0, 2)
  return `QT-${yy}${mm}-${ss}`
}

/** حساب ملخص الأسعار من الـ lines */
const calcSummary = (lines, taxPct, discountPct) => {
  const subtotal = lines.reduce((s, l) => s + (Number(l.qty) || 0) * (Number(l.unit_price) || 0), 0)
  const discountAmt = subtotal * (Number(discountPct) || 0) / 100
  const taxable = subtotal - discountAmt
  const taxAmount = taxable * (Number(taxPct) || 0) / 100
  const total = taxable + taxAmount
  return { subtotal, discountAmt, taxable, taxAmount, total }
}

const emptyLine = () => ({ _id: Date.now() + Math.random(), item_type: '', model_no: '', description: '', qty: 1, unit: 'قطعة', unit_price: '', notes: '', image_data: '' })

// تحويل ملف صورة مرفوع إلى base64 data:URI لتخزينه مباشرة مع البند
function readImageAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

// ─── StatusBadge ──────────────────────────────────────────────────────────────

function StatusBadge({ value }) {
  const s = QUOTE_STATUS[value] || { label: value, badge: 'badge-gray' }
  return <span className={`badge ${s.badge}`}>{s.label}</span>
}

// ─── SummaryBox ───────────────────────────────────────────────────────────────

function SummaryBox({ lines, taxPct, discountPct }) {
  const { subtotal, discountAmt, taxable, taxAmount, total } = calcSummary(lines, taxPct, discountPct)
  const hasDiscount = (Number(discountPct) || 0) > 0
  const hasTax = (Number(taxPct) || 0) > 0

  return (
    <div className="rounded-lg border border-erp-border overflow-hidden text-sm" style={{ background: 'var(--bg)' }}>
      <div className="flex justify-between px-4 py-2 border-b border-erp-border">
        <span className="text-erp-muted">الإجمالي الفرعي</span>
        <span className="font-medium">{egp(subtotal)}</span>
      </div>
      {hasDiscount && (
        <div className="flex justify-between px-4 py-2 border-b border-erp-border" style={{ color: 'var(--red)' }}>
          <span>خصم ({discountPct}%)</span>
          <span>− {egp(discountAmt)}</span>
        </div>
      )}
      {hasTax && (
        <div className="flex justify-between px-4 py-2 border-b border-erp-border text-erp-muted">
          <span>ضريبة ({taxPct}%)</span>
          <span>+ {egp(taxAmount)}</span>
        </div>
      )}
      <div className="flex justify-between px-4 py-3 font-semibold text-base" style={{ background: 'var(--teal-lt)', color: 'var(--teal)' }}>
        <span>الإجمالي</span>
        <span>{egp(total)}</span>
      </div>
    </div>
  )
}

// ─── LineItemsTable ───────────────────────────────────────────────────────────

function LineItemsTable({ lines, onChange, onOpenCatalog }) {
  const update = (id, field, value) =>
    onChange(lines.map(l => l._id === id ? { ...l, [field]: value } : l))

  const remove = (id) => onChange(lines.filter(l => l._id !== id))

  const add = () => onChange([...lines, emptyLine()])

  return (
    <div className="flex flex-col gap-2">
      {/* Header */}
      <div
        className="grid text-xs font-medium text-erp-muted px-2"
        style={{ gridTemplateColumns: '64px 90px 100px 2fr 70px 80px 110px 70px 32px', gap: '6px' }}
      >
        <span>الصورة</span>
        <span>اسم البند *</span>
        <span>الموديل</span>
        <span>المواصفات الفنية</span>
        <span>الكمية</span>
        <span>الوحدة</span>
        <span>سعر الوحدة</span>
        <span>الإجمالي</span>
        <span />
      </div>

      {/* Rows */}
      {lines.map((l, i) => {
        const lineTotal = (Number(l.qty) || 0) * (Number(l.unit_price) || 0)
        return (
          <div
            key={l._id}
            className="grid items-center rounded-lg border border-erp-border px-2 py-2"
            style={{ gridTemplateColumns: '64px 90px 100px 2fr 70px 80px 110px 70px 32px', gap: '6px', background: 'var(--bg-card)' }}
          >
            {/* Image */}
            <div style={{ position: 'relative', width: 48, height: 48 }}>
              <input
                type="file"
                accept="image/*"
                id={`line-img-${l._id}`}
                style={{ display: 'none' }}
                onChange={async e => {
                  const file = e.target.files?.[0]
                  if (!file) return
                  const dataUrl = await readImageAsDataURL(file)
                  update(l._id, 'image_data', dataUrl)
                  e.target.value = ''
                }}
              />
              <label
                htmlFor={`line-img-${l._id}`}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  width: 48, height: 48, borderRadius: 6, cursor: 'pointer',
                  border: '1px dashed var(--erp-border, #ccc)', overflow: 'hidden',
                  background: l.image_data ? `url(${l.image_data}) center/cover no-repeat` : 'var(--bg)',
                }}
                title="رفع صورة البند"
              >
                {!l.image_data && <span style={{ fontSize: 18 }}>📷</span>}
              </label>
              {l.image_data && (
                <button
                  type="button"
                  onClick={() => update(l._id, 'image_data', '')}
                  title="حذف الصورة"
                  style={{
                    position: 'absolute', top: -6, right: -6, width: 18, height: 18,
                    borderRadius: '50%', background: 'var(--red)', color: '#fff',
                    border: 'none', fontSize: 11, lineHeight: '18px', cursor: 'pointer', padding: 0,
                  }}
                >×</button>
              )}
            </div>
            {/* item_type */}
            <input
              className="field-input text-sm"
              placeholder={`اسم البند ${i + 1} *`}
              value={l.item_type || ''}
              onChange={e => update(l._id, 'item_type', e.target.value)}
            />
            {/* model_no */}
            <input
              className="field-input text-sm"
              placeholder="الموديل"
              value={l.model_no || ''}
              onChange={e => update(l._id, 'model_no', e.target.value)}
            />
            {/* Description / Technical specs */}
            <textarea
              className="field-input text-sm"
              placeholder="المواصفات الفنية (اختياري)"
              rows={1}
              style={{ resize: 'vertical', minHeight: 34 }}
              value={l.description || ''}
              onChange={e => update(l._id, 'description', e.target.value)}
            />
            {/* Qty */}
            <input
              className="field-input text-sm text-center"
              type="number"
              min="0.01"
              step="0.01"
              value={l.qty}
              onChange={e => update(l._id, 'qty', e.target.value)}
            />
            {/* Unit */}
            <select
              className="field-select text-sm"
              value={l.unit}
              onChange={e => update(l._id, 'unit', e.target.value)}
            >
              {UNITS.map(u => <option key={u} value={u}>{u}</option>)}
            </select>
            {/* Unit price */}
            <input
              className="field-input text-sm"
              type="number"
              min="0"
              step="0.01"
              placeholder="0.00"
              value={l.unit_price}
              onChange={e => update(l._id, 'unit_price', e.target.value)}
            />
            {/* Line total */}
            <div className="text-sm font-semibold text-right" style={{ color: 'var(--teal)' }}>
              {egp(lineTotal)}
            </div>
            {/* Remove */}
            <button
              type="button"
              className="btn btn-sm text-xs"
              style={{ color: 'var(--red)', borderColor: 'transparent', padding: '0 4px' }}
              onClick={() => remove(l._id)}
              title="حذف البند"
              disabled={lines.length === 1}
            >×</button>
          </div>
        )
      })}

      {/* Add row buttons */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          type="button"
          className="btn btn-sm text-xs"
          style={{ color: 'var(--teal)', borderColor: 'var(--teal-md)', background: 'var(--teal-lt)' }}
          onClick={add}
        >
          + إضافة بند يدوي
        </button>
        <button
          type="button"
          className="btn btn-sm text-xs"
          style={{ color: '#FF8A00', borderColor: '#FF8A0044', background: '#FF8A0011' }}
          onClick={onOpenCatalog}
        >
          🔍 اختر من الكتالوج / المعدات
        </button>
      </div>
    </div>
  )
}

// ─── CatalogPickerModal ──────────────────────────────────────────────────────
function CatalogPickerModal({ open, onClose, onSelect }) {
  const [q, setQ]           = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter]  = useState('all')
  const [childrenMap, setChildrenMap] = useState({}) // { [parentId]: items[] | 'loading' }
  const timer = useRef(null)

  useEffect(() => {
    if (!open) return
    clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      setLoading(true)
      try {
        const data = await api.get(`/price-catalog/search/unified?q=${encodeURIComponent(q)}`)
        setResults(data)
      } catch { setResults([]) }
      finally { setLoading(false) }
    }, 300)
    return () => clearTimeout(timer.current)
  }, [q, open])

  useEffect(() => { if (open) { setQ(''); setResults([]); setFilter('all'); setChildrenMap({}) } }, [open])

  if (!open) return null

  const toggleChildren = async (item) => {
    if (childrenMap[item.id] && childrenMap[item.id] !== 'loading') {
      setChildrenMap(m => { const n = { ...m }; delete n[item.id]; return n })
      return
    }
    setChildrenMap(m => ({ ...m, [item.id]: 'loading' }))
    try {
      const kids = await api.get(`/price-catalog/equipment-children/${item.id}`)
      setChildrenMap(m => ({ ...m, [item.id]: kids }))
    } catch { setChildrenMap(m => { const n = { ...m }; delete n[item.id]; return n }) }
  }

  const shownBase = filter === 'all' ? results : results.filter(r => r.source === filter)
  const shown = shownBase.flatMap(item => {
    const kids = childrenMap[item.id]
    if (!kids || kids === 'loading') return [item]
    return [item, ...kids.map(k => ({ ...k, _isChild: true }))]
  })
  const sourceColor = { catalog: '#FF8A00', equipment: '#3B82F6', material: '#22C55E' }
  const sourceIcon  = { catalog: '💰', equipment: '⚙️', material: '🧱' }

  return (
    <div style={{ position:'fixed', inset:0, background:'#00000088', zIndex:2000, display:'flex', alignItems:'center', justifyContent:'center', padding:16 }}>
      <div style={{ background:'var(--bg)', borderRadius:16, width:'100%', maxWidth:720, maxHeight:'85vh', display:'flex', flexDirection:'column', direction:'rtl' }}>
        {/* Header */}
        <div style={{ padding:'18px 24px', borderBottom:'1px solid var(--border)', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <div>
            <div style={{ fontWeight:700, fontSize:16 }}>🔍 اختر من الكتالوج / المعدات / المواد</div>
            <div style={{ fontSize:12, color:'var(--text-secondary)', marginTop:2 }}>السعر بيتملى تلقائياً بناءً على التكلفة + هامش الربح</div>
          </div>
          <button onClick={onClose} style={{ background:'none', border:'none', fontSize:20, cursor:'pointer', color:'var(--text-secondary)' }}>✕</button>
        </div>

        {/* Search + filter */}
        <div style={{ padding:'12px 24px', borderBottom:'1px solid var(--border)', display:'flex', gap:10, flexWrap:'wrap' }}>
          <input
            autoFocus
            placeholder="ابحث باسم أو كود..."
            value={q}
            onChange={e => setQ(e.target.value)}
            style={{ flex:1, padding:'8px 14px', borderRadius:8, border:'1px solid var(--border)', background:'var(--surface)', color:'var(--text-primary)', fontSize:14, minWidth:200 }}
          />
          {['all','catalog','equipment','material'].map(s => (
            <button key={s} onClick={() => setFilter(s)} style={{
              padding:'6px 14px', borderRadius:20, fontSize:12, fontWeight:600, cursor:'pointer', border:'1px solid',
              background: filter===s ? (s==='all' ? '#FF8A00' : sourceColor[s] || '#FF8A00') : 'transparent',
              color: filter===s ? '#fff' : 'var(--text-secondary)',
              borderColor: s==='all' ? '#FF8A00' : (sourceColor[s] || '#FF8A00'),
            }}>
              {s==='all' ? 'الكل' : s==='catalog' ? '💰 الكتالوج' : s==='equipment' ? '⚙️ المعدات' : '🧱 المواد'}
            </button>
          ))}
        </div>

        {/* Results */}
        <div style={{ flex:1, overflowY:'auto', padding:'8px 16px' }}>
          {loading && <div style={{ textAlign:'center', padding:30, color:'var(--text-secondary)' }}>جاري البحث...</div>}
          {!loading && shown.length === 0 && (
            <div style={{ textAlign:'center', padding:40, color:'var(--text-secondary)' }}>
              {q ? 'لا توجد نتائج' : 'ابدأ بكتابة اسم المنتج أو الكود'}
            </div>
          )}
          {!loading && shown.map((item, i) => (
            <div key={`${item.source}-${item.id}-${i}`}
              onClick={() => { onSelect(item); onClose() }}
              style={{
                display:'flex', justifyContent:'space-between', alignItems:'center',
                padding:'12px 16px', borderRadius:10, marginBottom:4, cursor:'pointer',
                border:'1px solid var(--border)', background:'var(--surface)',
                transition:'background 0.15s',
                marginRight: item._isChild ? 24 : 0,
              }}
              onMouseEnter={e => e.currentTarget.style.background='var(--surface-hover)'}
              onMouseLeave={e => e.currentTarget.style.background='var(--surface)'}
            >
              <div style={{ flex:1 }}>
                <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:4, flexWrap:'wrap' }}>
                  {item._isChild && <span style={{ fontSize:11, color:'var(--text-secondary)' }}>↳ فرعي</span>}
                  <span style={{ fontSize:11, fontWeight:700, padding:'2px 8px', borderRadius:12,
                    background: sourceColor[item.source]+'22', color: sourceColor[item.source] }}>
                    {sourceIcon[item.source]} {item.source_label}
                  </span>
                  {item.length_min != null && (
                    <span style={{ fontSize:11, fontWeight:700, padding:'2px 8px', borderRadius:12, background:'#e0f2fe', color:'#0369a1' }}>
                      📐 مترية {item.length_min}–{item.length_max}م
                    </span>
                  )}
                  {item.model_no && <span style={{ fontSize:11, color:'var(--text-secondary)' }}>{item.model_no}</span>}
                  {item.item_type && <span style={{ fontSize:11, color:'var(--text-secondary)' }}>• {item.item_type}</span>}
                  {item.source === 'equipment' && item.has_children && !item._isChild && (
                    <button
                      onClick={e => { e.stopPropagation(); toggleChildren(item) }}
                      style={{ fontSize:11, fontWeight:600, padding:'2px 8px', borderRadius:12, border:'1px solid var(--border)', background:'transparent', color:'var(--text-secondary)', cursor:'pointer' }}>
                      {childrenMap[item.id] === 'loading' ? '⏳' : childrenMap[item.id] ? '▾ إخفاء الفرعيات' : '▸ عرض الفرعيات'}
                    </button>
                  )}
                </div>
                <div style={{ fontWeight:600, fontSize:14 }}>{item.name}</div>
                {item.description && item.description !== item.name && (
                  <div style={{ fontSize:11, color:'var(--text-secondary)', marginTop:2, maxWidth:400, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                    {item.description}
                  </div>
                )}
              </div>
              <div style={{ textAlign:'left', marginRight:8 }}>
                <div style={{ fontSize:16, fontWeight:700, color:'#FF8A00' }}>
                  {Number(item.unit_price).toLocaleString('ar-EG', {minimumFractionDigits:0})}
                </div>
                <div style={{ fontSize:11, color:'var(--text-secondary)' }}>
                  تكلفة: {Number(item.cost_price).toLocaleString()} | هامش: {item.margin_pct}%
                </div>
                <div style={{ fontSize:11, color:'var(--text-secondary)' }}>{item.unit}</div>
              </div>
            </div>
          ))}
        </div>

        <div style={{ padding:'12px 24px', borderTop:'1px solid var(--border)', fontSize:12, color:'var(--text-secondary)' }}>
          اضغط على أي صنف لإضافته تلقائياً في بند عرض السعر
        </div>
      </div>
    </div>
  )
}

// ─── MetricLengthModal ───────────────────────────────────────────────────────
function MetricLengthModal({ item, onAdd, onClose }) {
  const [length, setLength]     = useState(item ? item.length_min : 0)
  const [margin, setMargin]     = useState(25)
  const [bomResult, setBomResult] = useState(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)
  const debounceRef = useRef(null)

  useEffect(() => {
    if (!item) return
    setLength(item.length_min)
    setBomResult(null)
    setError(null)
  }, [item])

  useEffect(() => {
    if (!item || length == null) return
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setLoading(true)
      setError(null)
      try {
        const res = await api.equipment.bomParametricLength(item.id, length)
        setBomResult(res)
      } catch (e) {
        setError(e?.message || 'خطأ في الحساب')
        setBomResult(null)
      } finally {
        setLoading(false)
      }
    }, 400)
    return () => clearTimeout(debounceRef.current)
  }, [item, length])

  if (!item) return null

  const cost = bomResult?.total_cost ?? 0
  const sellPrice = cost > 0 ? Math.round(cost * (1 + margin / 100)) : null

  return (
    <div style={{ position:'fixed', inset:0, background:'#00000088', zIndex:2100, display:'flex', alignItems:'center', justifyContent:'center', padding:16 }}>
      <div style={{ background:'var(--bg)', borderRadius:16, width:'100%', maxWidth:480, direction:'rtl', boxShadow:'0 20px 60px #0004' }}>
        {/* Header */}
        <div style={{ padding:'18px 24px', borderBottom:'1px solid var(--border)', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <div>
            <div style={{ fontWeight:700, fontSize:15 }}>📐 تسعير حسب الطول</div>
            <div style={{ fontSize:12, color:'var(--text-secondary)', marginTop:2 }}>{item.name}</div>
          </div>
          <button onClick={onClose} style={{ background:'none', border:'none', fontSize:20, cursor:'pointer', color:'var(--text-secondary)' }}>✕</button>
        </div>

        <div style={{ padding:'20px 24px', display:'flex', flexDirection:'column', gap:16 }}>
          {/* Length slider */}
          <div>
            <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
              <label style={{ fontSize:13, fontWeight:600 }}>الطول المطلوب</label>
              <span style={{ fontSize:15, fontWeight:700, color:'var(--teal)' }}>{length} م</span>
            </div>
            <input
              type="range"
              min={item.length_min}
              max={item.length_max}
              step={0.5}
              value={length}
              onChange={e => setLength(Number(e.target.value))}
              style={{ width:'100%' }}
            />
            <div style={{ display:'flex', justifyContent:'space-between', fontSize:11, color:'var(--text-secondary)', marginTop:2 }}>
              <span>{item.length_min} م</span>
              <span>{item.length_max} م</span>
            </div>
            <input
              type="number"
              min={item.length_min}
              max={item.length_max}
              step={0.1}
              value={length}
              onChange={e => {
                const v = Math.min(item.length_max, Math.max(item.length_min, Number(e.target.value)))
                setLength(v)
              }}
              style={{ marginTop:8, width:'100%', padding:'8px 12px', borderRadius:8, border:'1px solid var(--border)', background:'var(--surface)', color:'var(--text-primary)', fontSize:14, textAlign:'center' }}
            />
          </div>

          {/* Margin */}
          <div>
            <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
              <label style={{ fontSize:13, fontWeight:600 }}>هامش الربح %</label>
              <span style={{ fontSize:14, fontWeight:700, color:'#FF8A00' }}>{margin}%</span>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={margin}
              onChange={e => setMargin(Number(e.target.value))}
              style={{ width:'100%' }}
            />
          </div>

          {/* BOM Result */}
          <div style={{ background:'var(--surface)', borderRadius:12, padding:'14px 16px', border:'1px solid var(--border)' }}>
            {loading && <div style={{ textAlign:'center', color:'var(--text-secondary)', fontSize:13 }}>⏳ جاري الحساب...</div>}
            {error && <div style={{ color:'var(--red)', fontSize:13 }}>⚠ {error}</div>}
            {!loading && !error && bomResult && (
              <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
                <div style={{ display:'flex', justifyContent:'space-between', fontSize:13 }}>
                  <span style={{ color:'var(--text-secondary)' }}>تكلفة BOM ({length}م)</span>
                  <span style={{ fontWeight:600 }}>{egp(cost)}</span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between', fontSize:13 }}>
                  <span style={{ color:'var(--text-secondary)' }}>هامش {margin}%</span>
                  <span style={{ color:'#FF8A00' }}>+ {egp(cost * margin / 100)}</span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between', fontSize:16, fontWeight:700, borderTop:'1px solid var(--border)', paddingTop:8, color:'var(--teal)' }}>
                  <span>سعر البيع</span>
                  <span>{egp(sellPrice)}</span>
                </div>
                <div style={{ fontSize:11, color:'var(--text-secondary)', marginTop:4 }}>
                  {bomResult.lines?.length ?? 0} مادة | {bomResult.lines?.filter(l=>l.line_type==='variable').length ?? 0} متغيرة
                </div>
              </div>
            )}
            {!loading && !error && !bomResult && (
              <div style={{ textAlign:'center', color:'var(--text-secondary)', fontSize:13 }}>اختر الطول لحساب السعر</div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div style={{ padding:'14px 24px', borderTop:'1px solid var(--border)', display:'flex', gap:10, justifyContent:'flex-end' }}>
          <button className="btn" onClick={onClose}>إلغاء</button>
          <button
            className="btn btn-primary"
            disabled={!sellPrice || loading}
            onClick={() => onAdd({ ...item, _bom_cost: cost }, length, sellPrice)}
          >
            ✅ أضف للعرض — {sellPrice ? egp(sellPrice) : '—'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── ExpandedLines (قراءة فقط في الجدول) ────────────────────────────────────

function ExpandedLines({ lines }) {
  if (!lines?.length) return <div className="text-xs text-erp-muted px-4 py-2">لا توجد بنود</div>
  return (
    <div className="overflow-x-auto">
      <table className="erp-table text-xs">
        <thead>
          <tr>
            <th>الوصف</th>
            <th>الكمية</th>
            <th>الوحدة</th>
            <th>سعر الوحدة</th>
            <th>الإجمالي</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={l.id ?? i}>
              <td>{l.description}</td>
              <td>{l.qty}</td>
              <td>{l.unit}</td>
              <td>{egp(l.unit_price)}</td>
              <td className="font-semibold">{egp(l.total_price ?? (l.qty * l.unit_price))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ─── Main Quotations Component ────────────────────────────────────────────────

export default function Quotations() {
  const { canWrite } = useAuth()
  const write = canWrite('sales')
  const toast = useToast()

  const { data: quotes, loading, reload } = useApi(() => api.sales.quotations())
  const { data: customers } = useApi(() => api.customers.list())

  // Search & expand
  const [q, setQ] = useState('')
  const [expandedId, setExpandedId] = useState(null)

  // Modal state
  const [modal, setModal] = useState(null) // { mode: 'add' | 'edit', item? }

  // Form state
  const [form, setForm] = useState({})
  const [lines, setLines] = useState([emptyLine()])
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [metricItem, setMetricItem]   = useState(null)  // pending metric equipment

  // مواقع العميل المختار
  const [locations, setLocations] = useState([])
  useEffect(() => {
    if (!form.customer_id) { setLocations([]); return }
    api.customers.locations(form.customer_id).then(r => setLocations(Array.isArray(r) ? r : [])).catch(bgErr('عناوين العميل', () => setLocations([])))
  }, [form.customer_id])

  // اختيار من الكتالوج → إضافة بند جديد (معدة مترية → طلب الطول أولاً)
  const handleCatalogSelect = (item) => {
    if (item.source === 'equipment' && item.length_min != null && item.length_max != null) {
      setMetricItem(item)
      return
    }
    setLines(prev => [...prev, {
      _id:         Date.now() + Math.random(),
      item_type:   item.item_type || '',
      model_no:    item.model_no  || '',
      description: item.description || item.name,
      qty:         1,
      unit:        item.unit || 'قطعة',
      unit_price:  item.unit_price,
      notes:       `تكلفة: ${item.cost_price} | هامش: ${item.margin_pct}%`,
    }])
  }

  const handleMetricAdd = (item, length, unitPrice) => {
    setLines(prev => [...prev, {
      _id:         Date.now() + Math.random(),
      item_type:   item.item_type || 'معدة',
      model_no:    item.model_no  || '',
      description: [`${item.name} — ${length}م`, item.description && item.description !== item.name ? item.description : null].filter(Boolean).join('\n'),
      qty:         1,
      unit:        'متر',
      unit_price:  unitPrice,
      notes:       `طول: ${length}م | تكلفة BOM: ${item._bom_cost || ''} | هامش مُضاف`,
    }])
    setMetricItem(null)
  }

  // Convert to invoice confirm
  const [convertTarget, setConvertTarget] = useState(null)
  const [converting, setConverting] = useState(false)
  const [copyingId, setCopyingId] = useState(null)

  // Convert to customer order
  const [toOrderTarget, setToOrderTarget] = useState(null)
  const [toOrderForm, setToOrderForm]     = useState({})
  const [toOrdering, setToOrdering]       = useState(false)

  // محطة 7: قالب خطة الدفع
  const [templateModal, setTemplateModal]     = useState(null)   // عرض السعر المفتوح عليه
  const [templateExisting, setTemplateExisting] = useState(null) // null = بيتحمّل، [] = مفيش
  const [templateRows, setTemplateRows]       = useState([])
  const [templateMode, setTemplateMode]       = useState('view')
  const [savingTemplate, setSavingTemplate]   = useState(false)

  const openTemplateModal = async (qt) => {
    setTemplateModal(qt)
    setTemplateExisting(null)
    setTemplateMode('view')
    try {
      const rows = await api.sales.getPaymentTemplate(qt.id)
      setTemplateExisting(rows || [])
      if (!rows || rows.length === 0) {
        setTemplateMode('edit')
        setTemplateRows([{ label: 'دفعة مقدمة', pct: '100', days_offset: '0' }])
      }
    } catch (e) {
      toast(e.message || 'تعذّر تحميل القالب', 'error')
      setTemplateExisting([])
      setTemplateMode('edit')
      setTemplateRows([{ label: 'دفعة مقدمة', pct: '100', days_offset: '0' }])
    }
  }
  const startEditTemplate = () => {
    const base = (templateExisting && templateExisting.length)
      ? templateExisting.map(r => ({ label: r.label || '', pct: String(r.pct), days_offset: String(r.days_offset) }))
      : [{ label: 'دفعة مقدمة', pct: '100', days_offset: '0' }]
    setTemplateRows(base)
    setTemplateMode('edit')
  }
  const addTemplateRow = () => setTemplateRows(rs => [...rs, { label: `دفعة ${rs.length + 1}`, pct: '', days_offset: '0' }])
  const removeTemplateRow = (idx) => setTemplateRows(rs => rs.filter((_, i) => i !== idx))
  const updateTemplateRow = (idx, field, value) =>
    setTemplateRows(rs => rs.map((r, i) => i === idx ? { ...r, [field]: value } : r))
  const templatePctTotal = useMemo(
    () => templateRows.reduce((s, r) => s + (Number(r.pct) || 0), 0),
    [templateRows]
  )
  const saveTemplate = async () => {
    if (templateRows.length === 0) { toast('أضف قسط واحد على الأقل', 'error'); return }
    if (Math.abs(templatePctTotal - 100) > 0.5) {
      toast(`مجموع النسب (${templatePctTotal}%) لازم يساوي 100%`, 'error'); return
    }
    setSavingTemplate(true)
    try {
      const hasExisting = templateExisting && templateExisting.length > 0
      await api.sales.createPaymentTemplate(templateModal.id, {
        installments: templateRows.map(r => ({
          label: r.label || null,
          pct: Number(r.pct),
          days_offset: Number(r.days_offset) || 0,
        })),
      }, hasExisting)
      toast('تم حفظ قالب خطة الدفع')
      const rows = await api.sales.getPaymentTemplate(templateModal.id)
      setTemplateExisting(rows || [])
      setTemplateMode('view')
    } catch (e) {
      toast(e.message || 'تعذّر الحفظ', 'error')
    } finally {
      setSavingTemplate(false)
    }
  }

  // ── Filtered list ──────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    if (!quotes) return []
    const lq = q.toLowerCase()
    return !lq ? quotes : quotes.filter(x =>
      x.number?.toLowerCase().includes(lq) ||
      x.code?.toLowerCase().includes(lq) ||
      x.customer_name?.toLowerCase().includes(lq)
    )
  }, [quotes, q])

  // ── Open add ──────────────────────────────────────────────────────────────
  const openAdd = () => {
    setForm({
      number: genQuoteNumber(),
      status: 'draft',
      date: today(),
      valid_until: '',
      location_id: '',
      tax_pct: 0,
      discount_pct: 0,
      notes: '',
      terms: '',
    })
    setLines([emptyLine()])
    setModal({ mode: 'add' })
  }

  // ── Open edit ─────────────────────────────────────────────────────────────
  const openEdit = useCallback(async (qt) => {
    setForm({
      id: qt.id,
      number: qt.number || qt.code || '',
      customer_id: qt.customer_id,
      location_id: qt.location_id || '',
      status: qt.status,
      date: qt.date,
      valid_until: qt.valid_until || '',
      tax_pct: qt.tax_pct ?? 0,
      discount_pct: qt.discount_pct ?? 0,
      notes: qt.notes || '',
      terms: qt.terms || '',
    })
    // إذا في lines محفوظة نستخدمها، وإلا نحطّ line فارغة
    const existingLines = qt.lines?.length
      ? qt.lines.map(l => ({ ...l, _id: l.id ?? (Date.now() + Math.random()) }))
      : [emptyLine()]
    setLines(existingLines)
    setModal({ mode: 'edit', item: qt })
  }, [])

  // ── Save ──────────────────────────────────────────────────────────────────
  const [save, saving] = useAction(async () => {
    if (!form.customer_id) { toast('اختر العميل', 'error'); return }
    if (!form.number?.trim()) { toast('رقم العرض مطلوب', 'error'); return }
    if (!lines.length) { toast('أضف بنداً واحداً على الأقل', 'error'); return }
    if (form.date && form.valid_until && form.valid_until < form.date) {
      toast('تاريخ صلاحية العرض لازم يكون بعد تاريخ الإصدار', 'error'); return
    }
    if (lines.some(l => !l.item_type?.trim())) { toast('أدخل اسم لكل البنود', 'error'); return }
    if (lines.some(l => !Number(l.unit_price))) { toast('أدخل سعر لكل البنود', 'error'); return }

    const { total } = calcSummary(lines, form.tax_pct, form.discount_pct)

    const payload = {
      code:          form.number,
      customer_id:   Number(form.customer_id),
      location_id:   form.location_id ? Number(form.location_id) : null,
      date:          form.date || today(),
      valid_until:   form.valid_until || null,
      tax_pct:       Number(form.tax_pct) || 0,
      discount_pct:  Number(form.discount_pct) || 0,
      notes:         form.notes || null,
      terms:         form.terms || null,
      lines:         lines.map((l, i) => ({
        item_type:   l.item_type || null,
        model_no:    l.model_no  || null,
        description: l.description,
        qty:         Number(l.qty) || 1,
        unit:        l.unit || 'قطعة',
        unit_price:  Number(l.unit_price) || 0,
        notes:       l.notes || null,
        sort_order:  i,
        image_data:  l.image_data || null,
      })),
    }

    if (modal?.mode === 'edit') {
      await api.sales.updateQuote(form.id, payload)
      toast('✅ تم تحديث العرض')
    } else {
      await api.sales.createQuote(payload)
      toast('✅ تم إنشاء العرض')
    }
    setModal(null)
    reload()
  })

  // ── Convert to invoice ────────────────────────────────────────────────────
  const handleConvert = async () => {
    if (!convertTarget) return
    setConverting(true)
    try {
      const { code } = await api.sales.nextInvoiceCode()
      const inv = await api.sales.toInvoice(convertTarget.id, code)
      toast(`✅ تم إنشاء فاتورة ${inv?.code || ''}`)
      setConvertTarget(null)
      reload()
    } catch (e) {
      toast(e?.message || 'حدث خطأ أثناء التحويل', 'error')
    } finally {
      setConverting(false)
    }
  }

  // ── Convert quotation to customer order ──────────────────────────────────
  const handleToOrder = async () => {
    if (!toOrderTarget) return
    setToOrdering(true)
    try {
      const ord = await api.sales.toOrder(toOrderTarget.id, {
        delivery_date: toOrderForm.delivery_date || null,
        notes:         toOrderForm.notes         || null,
      })
      toast(`✅ تم إنشاء الطلب ${ord?.code || ''}`)
      setToOrderTarget(null)
      reload()
    } catch (e) {
      toast(e?.detail || e?.message || 'حدث خطأ أثناء التحويل', 'error')
    } finally {
      setToOrdering(false)
    }
  }

  // ── Copy quotation ───────────────────────────────────────────────────────
  const copyQuotation = async (qt) => {
    if (copyingId) return
    setCopyingId(qt.id)
    try {
      const srcLines = qt.lines || []
      await api.sales.createQuote({
        number:       genQuoteNumber(),
        customer_id:  qt.customer_id,
        status:       'draft',
        date:         today(),
        valid_until:  '',
        tax_pct:      qt.tax_pct || 0,
        discount_pct: qt.discount_pct || 0,
        notes:        `نسخة من: ${qt.number || qt.code}`,
        terms:        qt.terms || '',
        lines: srcLines.map((l, i) => ({
          description: l.description,
          qty:         l.qty,
          unit:        l.unit || 'قطعة',
          unit_price:  l.unit_price,
          item_type:   l.item_type  || '',
          model_no:    l.model_no   || '',
          notes:       l.notes      || '',
          sort_order:  i,
        })),
      })
      toast('✅ تم نسخ العرض — تحقق من القائمة')
      reload()
    } catch (e) { toast(e.message || 'فشل النسخ', 'error') }
    finally { setCopyingId(null) }
  }

  // ── Print ─────────────────────────────────────────────────────────────────
  const [printing, setPrinting] = useState(null) // id of quote being printed

  const handlePrint = async (qt, docType = 'quote') => {
    // نفتح النافذة فورًا (متزامنة مع نقرة المستخدم) قبل أي await، حتى لا يحجبها المتصفح
    const win = window.open('', '_blank')
    setPrinting(qt.id)
    try {
      // عرض السعر وعقد الاتفاق: نستخدم صفحة الطباعة الجاهزة من السيرفر — مستند
      // كامل قائم بذاته يطبع تلقائيًا (onload) ومُختبَر فعليًا أنه يعمل مع طباعة
      // ويندوز، بخلاف التوليد من جهة العميل. الصفحة محمية بتوكن المصادقة لذا
      // نجلبها بـ fetch ونكتبها في النافذة المفتوحة مسبقًا.
      if (docType === 'quote' || docType === 'contract') {
        if (!win) { toast('يرجى السماح بالنوافذ المنبثقة للطباعة', 'error'); return }
        const path = docType === 'quote'
          ? `/sales/quotations/${qt.id}/print`
          : `/sales/quotations/${qt.id}/agreement`
        const html = await fetchPrintHTML(path)
        printHTML(html, win)
        return
      }
      const full = await api.get(`/sales/quotations/${qt.id}`)
      const lines = full.lines || qt.lines || []
      const ws = await fetchWorkshopProfile()
      const data = { ...qt, ...full }
      const html = buildSupplyOrderHTML(data, lines, ws)
      printHTML(html, win)
    } catch (e) {
      win?.close()
      toast('تعذّرت الطباعة', 'error')
    } finally {
      setPrinting(null)
    }
  }

  // واتساب: يطبع/يحفظ المستند كـ PDF ثم يفتح واتساب لإرفاقه
  const handleWhatsApp = async (qt) => {
    // نفتح صفحة الطباعة الجاهزة من السيرفر — بها زر واتساب مدمج وتطبع تلقائيًا
    // ليحفظها المستخدم كـ PDF ثم يرفقها
    const win = window.open('', '_blank')
    setPrinting(qt.id)
    try {
      if (!win) { toast('يرجى السماح بالنوافذ المنبثقة للطباعة', 'error'); return }
      const html = await fetchPrintHTML(`/sales/quotations/${qt.id}/print`)
      printHTML(html, win)
      const full = await api.get(`/sales/quotations/${qt.id}`)
      const lines = full.lines || qt.lines || []
      const ws = await fetchWorkshopProfile()
      const total = lines.reduce((s, l) => s + Number(l.qty || 1) * Number(l.unit_price || 0), 0)
      const msg = `عرض سعر رقم ${qt.number || qt.code} من ${ws.name}\nالإجمالي التقريبي: ${egp(total)}\n(مرفق ملف PDF)`
      const phone = (qt.customer_phone || '').replace(/[^0-9]/g, '')
      const waUrl = phone
        ? `https://wa.me/${phone}?text=${encodeURIComponent(msg)}`
        : `https://wa.me/?text=${encodeURIComponent(msg)}`
      setTimeout(() => window.open(waUrl, '_blank'), 1200)
      toast('احفظ الملف كـ PDF ثم أرفقه في واتساب')
    } catch (e) {
      toast('تعذّر التحضير', 'error')
    } finally {
      setPrinting(null)
    }
  }

  // ── Summary for modal ─────────────────────────────────────────────────────
  const modalSummary = useMemo(() => calcSummary(lines, form.tax_pct, form.discount_pct), [lines, form.tax_pct, form.discount_pct])

  if (loading) return <PageSpinner />

  return (
    <div className="flex flex-col gap-4">
      {/* ── Toolbar ─────────────────────────────────────────────────────────── */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div className="flex-1">
          <SearchInput value={q} onChange={setQ} placeholder="بحث برقم العرض أو العميل…" />
        </div>
        <div className="text-xs text-erp-muted">العروض ({filtered.length})</div>
        <button className="btn btn-sm text-xs" title="تصدير Excel"
          onClick={() => api.export.quotations()}>📥 Excel</button>
        {write && (
          <button className="btn btn-primary btn-sm" onClick={openAdd}>+ عرض جديد</button>
        )}
      </div>

      {/* ── Table ───────────────────────────────────────────────────────────── */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr>
                <th />
                <th>الرقم</th>
                <th>العميل</th>
                <th>التاريخ</th>
                <th>صالح حتى</th>
                <th>الإجمالي</th>
                <th>الحالة</th>
                {write && <th>إجراءات</th>}
              </tr>
            </thead>
            <tbody>
              {!filtered.length && (
                <tr>
                  <td colSpan={8} className="text-center text-erp-muted py-10">
                    لا توجد عروض أسعار
                  </td>
                </tr>
              )}
              {filtered.map(qt => (
                <>
                  <tr key={qt.id}>
                    {/* Expand toggle */}
                    <td style={{ width: 32, textAlign: 'center', cursor: 'pointer', color: 'var(--muted)' }}
                      onClick={() => setExpandedId(expandedId === qt.id ? null : qt.id)}
                      title="عرض البنود"
                    >
                      {expandedId === qt.id ? '▲' : '▼'}
                    </td>
                    <td><span className="font-mono text-xs font-medium">{qt.number || qt.code}</span></td>
                    <td className="font-medium">{qt.customer_name}</td>
                    <td className="text-erp-muted text-xs">{fmtDate(qt.date)}</td>
                    <td className="text-xs">{fmtDate(qt.valid_until) || '—'}</td>
                    <td className="font-semibold">{egp(qt.total)}</td>
                    <td><StatusBadge value={qt.status} /></td>
                    {write && (
                      <td>
                        <div className="flex gap-1 flex-wrap">
                          {/* Edit */}
                          <button
                            className="btn btn-sm text-xs"
                            onClick={() => openEdit(qt)}
                            title="تعديل"
                          >✏️</button>

                          {/* Copy */}
                          {write && (
                            <button
                              className="btn btn-sm text-xs"
                              onClick={() => copyQuotation(qt)}
                              title="نسخ العرض"
                              disabled={copyingId === qt.id}
                            >{copyingId === qt.id ? '…' : '📋'}</button>
                          )}

                          {/* Print */}
                          <button
                            className="btn btn-sm text-xs"
                            onClick={() => handlePrint(qt)}
                            title="طباعة عرض السعر"
                            disabled={printing === qt.id}
                          >{printing === qt.id ? '…' : '🖨️'}</button>

                          {/* أمر توريد */}
                          <button
                            className="btn btn-sm text-xs"
                            onClick={() => handlePrint(qt, 'supply')}
                            title="أمر توريد"
                            disabled={printing === qt.id}
                          >📦</button>

                          {/* عقد اتفاق */}
                          <button
                            className="btn btn-sm text-xs"
                            onClick={() => handlePrint(qt, 'contract')}
                            title="عقد اتفاق"
                            disabled={printing === qt.id}
                          >📜</button>

                          {/* واتساب PDF */}
                          <button
                            className="btn btn-sm text-xs"
                            onClick={() => handleWhatsApp(qt)}
                            title="إرسال PDF عبر واتساب"
                            disabled={printing === qt.id}
                            style={{ color: '#25D366' }}
                          >💬</button>

                          {/* Convert to customer order */}
                          {qt.status !== 'rejected' && (
                            <button
                              className="btn btn-sm text-xs"
                              style={{ background: 'var(--blue-lt)', color: 'var(--blue)', borderColor: 'var(--blue-lt)' }}
                              onClick={() => { setToOrderTarget(qt); setToOrderForm({ delivery_date: '', notes: '' }) }}
                              title="تحويل لطلب عميل"
                            >📋 طلب</button>
                          )}

                          {/* محطة 7: قالب خطة الدفع */}
                          {qt.status !== 'rejected' && (
                            <button
                              className="btn btn-sm text-xs"
                              style={{ color: 'var(--teal)', borderColor: 'var(--teal-lt)' }}
                              onClick={() => openTemplateModal(qt)}
                              title="قالب خطة الدفع"
                            >📅 قالب</button>
                          )}

                          {/* Convert to invoice — لأي حالة غير مرفوض */}
                          {qt.status !== 'rejected' && (
                            <button
                              className="btn btn-sm text-xs"
                              style={{ background: 'var(--teal-lt)', color: 'var(--teal)', borderColor: 'var(--teal-md)' }}
                              onClick={() => setConvertTarget(qt)}
                              title="تحويل لفاتورة"
                            >🧾 فاتورة</button>
                          )}
                        </div>
                      </td>
                    )}
                  </tr>

                  {/* Expanded lines */}
                  {expandedId === qt.id && (
                    <tr key={`${qt.id}-exp`}>
                      <td colSpan={8} style={{ padding: 0, background: 'var(--bg)' }}>
                        <ExpandedLines lines={qt.lines} />
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Create / Edit Modal ─────────────────────────────────────────────── */}
      <Modal
        open={!!modal}
        onClose={() => setModal(null)}
        title={modal?.mode === 'edit' ? `تعديل عرض سعر — ${form.number}` : 'عرض سعر جديد'}
        size="xl"
      >
        <div className="flex flex-col gap-4">
          {/* Row 1: رقم + عميل + موقع + حالة (الحالة في التعديل فقط) */}
          <FormGrid cols={4}>
            <Input
              label={modal?.mode === 'add' ? 'رقم العرض (مولّد تلقائياً)' : 'رقم العرض *'}
              value={form.number || ''}
              readOnly={modal?.mode === 'add'}
              onChange={modal?.mode !== 'add' ? (e => setForm(f => ({ ...f, number: e.target.value }))) : undefined}
              style={modal?.mode === 'add' ? { background: 'var(--bg)', color: 'var(--text-secondary)', cursor: 'default' } : undefined}
              placeholder="QT-202501-001"
            />
            <Select
              label="العميل *"
              value={form.customer_id || ''}
              onChange={e => setForm(f => ({ ...f, customer_id: +e.target.value, location_id: '' }))}
            >
              <option value="">— اختر عميل —</option>
              {customers?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
            {locations.length > 0 && (
              <Select
                label="الموقع / الفرع"
                value={form.location_id || ''}
                onChange={e => setForm(f => ({ ...f, location_id: e.target.value }))}
              >
                <option value="">— بدون موقع محدد —</option>
                {locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
              </Select>
            )}
            {modal?.mode === 'edit' && (
              <Select
                label="الحالة"
                value={form.status || 'draft'}
                onChange={e => setForm(f => ({ ...f, status: e.target.value }))}
                options={Object.entries(QUOTE_STATUS).map(([v, { label }]) => ({ value: v, label }))}
              />
            )}
          </FormGrid>

          {/* Row 2: تاريخ + صالح حتى + خصم + ضريبة */}
          <FormGrid cols={4}>
            <Input
              label="تاريخ العرض"
              type="date"
              value={form.date || today()}
              onChange={e => setForm(f => ({ ...f, date: e.target.value }))}
            />
            <Input
              label="صالح حتى"
              type="date"
              value={form.valid_until || ''}
              onChange={e => setForm(f => ({ ...f, valid_until: e.target.value }))}
            />
            <Input
              label="خصم %"
              type="number"
              min="0"
              max="100"
              step="0.5"
              placeholder="0"
              value={form.discount_pct || ''}
              onChange={e => setForm(f => ({ ...f, discount_pct: e.target.value }))}
            />
            <Input
              label="ضريبة %"
              type="number"
              min="0"
              max="100"
              step="0.5"
              placeholder="0"
              value={form.tax_pct || ''}
              onChange={e => setForm(f => ({ ...f, tax_pct: e.target.value }))}
            />
          </FormGrid>

          {/* Line items */}
          <div>
            <label className="field-label mb-2 block">البنود *</label>
            <LineItemsTable lines={lines} onChange={setLines} onOpenCatalog={() => setCatalogOpen(true)} />
          </div>

          {/* Summary */}
          <SummaryBox lines={lines} taxPct={form.tax_pct} discountPct={form.discount_pct} />

          {/* Notes + Terms */}
          <FormGrid cols={2}>
            <Textarea
              label="ملاحظات"
              rows={2}
              value={form.notes || ''}
              onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
              placeholder="ملاحظات للعميل…"
            />
            <Textarea
              label="الشروط والأحكام"
              rows={2}
              value={form.terms || ''}
              onChange={e => setForm(f => ({ ...f, terms: e.target.value }))}
              placeholder="شروط الدفع، التسليم…"
            />
          </FormGrid>
        </div>

        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? '⏳ جاري الحفظ…' : modal?.mode === 'edit' ? 'حفظ التعديلات' : 'إنشاء العرض'}
          </button>
        </ModalFooter>
      </Modal>

      {/* ── Catalog Picker ──────────────────────────────────────────────────── */}
      <CatalogPickerModal
        open={catalogOpen}
        onClose={() => setCatalogOpen(false)}
        onSelect={handleCatalogSelect}
      />

      {/* ── Metric Equipment Length Picker ──────────────────────────────────── */}
      <MetricLengthModal
        item={metricItem}
        onAdd={handleMetricAdd}
        onClose={() => setMetricItem(null)}
      />

      {/* ── Convert to invoice Confirm ──────────────────────────────────────── */}
      <ConfirmDialog
        open={!!convertTarget}
        title="تحويل عرض السعر لفاتورة"
        message={`هل تريد تحويل العرض "${convertTarget?.number || convertTarget?.code}" للعميل "${convertTarget?.customer_name}" إلى فاتورة مبيعات؟ سيتم نسخ كل البنود والأسعار تلقائياً.`}
        onConfirm={handleConvert}
        onCancel={() => setConvertTarget(null)}
        loading={converting}
      />

      {/* ── Convert to Customer Order Modal ─────────────────────────────────── */}
      <Modal
        open={!!toOrderTarget}
        onClose={() => setToOrderTarget(null)}
        title={`📋 تحويل لطلب عميل — ${toOrderTarget?.number || toOrderTarget?.code || ''}`}
      >
        <div className="flex flex-col gap-3">
          <div className="px-3 py-2 rounded text-sm"
               style={{ background: 'var(--blue-lt)', color: 'var(--blue)' }}>
            العميل: <b>{toOrderTarget?.customer_name}</b>
          </div>
          <Input
            label="تاريخ التسليم المتوقع (اختياري)"
            type="date"
            value={toOrderForm.delivery_date || ''}
            onChange={e => setToOrderForm(f => ({ ...f, delivery_date: e.target.value }))}
          />
          <Textarea
            label="ملاحظات (اختياري)"
            rows={2}
            value={toOrderForm.notes || ''}
            onChange={e => setToOrderForm(f => ({ ...f, notes: e.target.value }))}
          />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setToOrderTarget(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={handleToOrder} disabled={toOrdering}>
            {toOrdering ? '⏳ جاري التحويل…' : '📋 تحويل لطلب عميل'}
          </button>
        </ModalFooter>
      </Modal>

      {/* ── محطة 7: قالب خطة الدفع ───────────────────────────────────────── */}
      <Modal open={!!templateModal} onClose={() => setTemplateModal(null)}
        title={`قالب خطة الدفع — ${templateModal?.number || templateModal?.code || ''}`}>
        <div className="text-xs text-erp-muted mb-2">
          القالب بيتحول تلقائيًا لخطة أقساط فعلية بمجرد ما العرض يتحول لفاتورة (النسب تتحول لمبالغ، والأيام لتواريخ حسب تاريخ الفاتورة الفعلي).
        </div>
        {templateExisting === null ? (
          <div className="text-sm text-erp-muted p-4 text-center">جارِ التحميل…</div>
        ) : templateMode === 'view' ? (
          <div className="flex flex-col gap-3">
            <table className="erp-table text-sm">
              <thead><tr><th>#</th><th>البيان</th><th>النسبة</th><th>بعد كام يوم من الفاتورة</th></tr></thead>
              <tbody>
                {templateExisting.map(row => (
                  <tr key={row.id}>
                    <td>{row.installment_no}</td>
                    <td>{row.label || '—'}</td>
                    <td>{row.pct}%</td>
                    <td>{row.days_offset === 0 ? 'يوم الفاتورة' : `+${row.days_offset} يوم`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="btn" onClick={startEditTemplate}>
              {templateExisting.length ? '✏️ تعديل / استبدال القالب' : '➕ إنشاء قالب'}
            </button>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {templateRows.map((row, idx) => (
              <div key={idx} className="flex gap-2 items-end" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
                <Input label="البيان" value={row.label}
                  onChange={e => updateTemplateRow(idx, 'label', e.target.value)} />
                <Input label="النسبة %" type="number" step="0.01" value={row.pct}
                  onChange={e => updateTemplateRow(idx, 'pct', e.target.value)} />
                <Input label="بعد كام يوم من الفاتورة" type="number" value={row.days_offset}
                  onChange={e => updateTemplateRow(idx, 'days_offset', e.target.value)} />
                <button className="btn btn-sm btn-danger" style={{ marginBottom: 4 }}
                  onClick={() => removeTemplateRow(idx)} disabled={templateRows.length === 1}>🗑️</button>
              </div>
            ))}
            <button className="btn btn-sm" onClick={addTemplateRow}>➕ إضافة قسط</button>
            <div className="flex items-center justify-between px-3 py-2 rounded-erp"
                 style={{ background: Math.abs(templatePctTotal - 100) > 0.5 ? 'var(--red-lt)' : 'var(--green-lt)' }}>
              <span className="text-sm">مجموع النسب</span>
              <span className="font-bold">{templatePctTotal}% / 100%</span>
            </div>
          </div>
        )}
        <ModalFooter>
          <button className="btn" onClick={() => setTemplateModal(null)}>إغلاق</button>
          {templateMode === 'edit' && (
            <button className="btn btn-primary" onClick={saveTemplate} disabled={savingTemplate}
              style={{ background: 'var(--teal)', borderColor: 'var(--teal)' }}>
              {savingTemplate ? '…' : '💾 حفظ القالب'}
            </button>
          )}
        </ModalFooter>
      </Modal>
    </div>
  )
}
