// ─────────────────────────────────────────────────────────────────────────────
// This file is a barrel — each page is exported as a named component.
// Split into individual files if you prefer; they're separated by clear markers.
// ─────────────────────────────────────────────────────────────────────────────
import { useState, useMemo, useEffect, useRef } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api from '../api/client'
import { egp, fmtDate, today, WO_STATUS, QUOTE_STATUS } from '../utils/fmt'
import { StatusBadge } from '../components/ui/Badge'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import SearchInput from '../components/ui/SearchInput'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

// ══════════════════════════════════════════════════════════════
// QUOTATIONS — عروض الأسعار
// نفس structure الفواتير الإلكترونية بالظبط:
//   • QuotationForm مطابق لـ InvoiceForm
//   • KPI cards: إجمالي العروض / المقبولة / القيمة الإجمالية
//   • list view + create/edit view منفصلة
//   • تحويل لفاتورة مباشر + طباعة + قبول/رفض
// ══════════════════════════════════════════════════════════════

const QUOTE_UNITS = ['قطعة', 'كجم', 'طن', 'متر', 'م²', 'م³', 'لتر', 'ساعة', 'يوم', 'رحلة', 'مجموعة']

const QUOTE_STATUS_MAP = {
  draft:    { label: 'مسودة',   cls: 'badge-gray'  },
  sent:     { label: 'مرسل',    cls: 'badge-blue'  },
  accepted: { label: 'مقبول',   cls: 'badge-green' },
  rejected: { label: 'مرفوض',   cls: 'badge-red'   },
}

const genQuoteCode = () => {
  const d = new Date()
  const yy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const rnd = String(d.getSeconds()).padStart(2, '0') + String(d.getMilliseconds()).slice(0, 2)
  return `QT-${yy}${mm}-${rnd}`
}

// ── ImageCell — رفع صورة لكل بند في عرض السعر ───────────────
function ImageCell({ value, onChange }) {
  const fileRef = useRef(null)
  const toast = useToast()

  const handleFile = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    // max 500KB
    if (file.size > 500 * 1024) {
      toast('الصورة كبيرة — الحد الأقصى 500 كيلوبايت', 'error')
      return
    }
    const reader = new FileReader()
    reader.onload = (ev) => onChange(ev.target.result)
    reader.readAsDataURL(file)
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:4 }}>
      {value ? (
        <div style={{ position:'relative' }}>
          <img src={value} alt="صورة المنتج"
            style={{ width:76, height:60, objectFit:'contain', borderRadius:6, border:'1px solid var(--border)', background:'#fff', cursor:'pointer' }}
            onClick={() => fileRef.current?.click()} title="اضغط لتغيير الصورة" />
          <button onClick={() => onChange('')}
            style={{ position:'absolute', top:-6, left:-6, background:'#EF4444', color:'#fff', border:'none', borderRadius:'50%', width:18, height:18, fontSize:10, cursor:'pointer', lineHeight:'18px', textAlign:'center' }}>✕</button>
        </div>
      ) : (
        <div onClick={() => fileRef.current?.click()}
          style={{ width:76, height:60, border:'2px dashed var(--border)', borderRadius:6, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', cursor:'pointer', color:'var(--text-secondary)', fontSize:10, gap:2 }}
          title="اضغط لرفع صورة">
          <span style={{ fontSize:18 }}>📷</span>
          <span>إضافة صورة</span>
        </div>
      )}
      <input ref={fileRef} type="file" accept="image/*" style={{ display:'none' }} onChange={handleFile} />
    </div>
  )
}

// ── CatalogPickerModal ────────────────────────────────────────
function CatalogPickerModal({ open, onClose, onSelect }) {
  const [q, setQ]             = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter]   = useState('all')
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

  useEffect(() => { if (open) { setQ(''); setResults([]); setFilter('all') } }, [open])
  if (!open) return null

  const shown = filter === 'all' ? results : results.filter(r => r.source === filter)
  const srcColor = { catalog: '#E87722', equipment: '#3B82F6', material: '#22C55E' }
  const srcIcon  = { catalog: '💰', equipment: '⚙️', material: '🧱' }
  const srcLabel = { catalog: 'الكتالوج', equipment: 'المعدات', material: 'المواد' }

  return (
    <div style={{ position:'fixed', inset:0, background:'#00000088', zIndex:3000, display:'flex', alignItems:'center', justifyContent:'center', padding:16 }}>
      <div style={{ background:'var(--bg)', borderRadius:16, width:'100%', maxWidth:740, maxHeight:'88vh', display:'flex', flexDirection:'column', direction:'rtl' }}>
        <div style={{ padding:'16px 22px', borderBottom:'1px solid var(--border)', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <div>
            <div style={{ fontWeight:700, fontSize:16 }}>🔍 اختر من الكتالوج / المعدات / المواد</div>
            <div style={{ fontSize:12, color:'var(--text-secondary)', marginTop:2 }}>السعر بيتملى تلقائياً = تكلفة + هامش ربح</div>
          </div>
          <button onClick={onClose} style={{ background:'none', border:'none', fontSize:20, cursor:'pointer', color:'var(--text-secondary)' }}>✕</button>
        </div>
        <div style={{ padding:'10px 22px', borderBottom:'1px solid var(--border)', display:'flex', gap:8, flexWrap:'wrap' }}>
          <input autoFocus placeholder="ابحث باسم أو كود..." value={q} onChange={e => setQ(e.target.value)}
            style={{ flex:1, padding:'7px 12px', borderRadius:8, border:'1px solid var(--border)', background:'var(--surface)', color:'var(--text-primary)', fontSize:13, minWidth:180 }} />
          {['all','catalog','equipment','material'].map(s => (
            <button key={s} onClick={() => setFilter(s)} style={{
              padding:'5px 12px', borderRadius:20, fontSize:12, fontWeight:600, cursor:'pointer', border:'1px solid',
              background: filter===s ? (s==='all'?'#E87722':srcColor[s]) : 'transparent',
              color: filter===s ? '#fff' : 'var(--text-secondary)',
              borderColor: s==='all' ? '#E87722' : srcColor[s],
            }}>
              {s==='all' ? 'الكل' : `${srcIcon[s]} ${srcLabel[s]}`}
            </button>
          ))}
        </div>
        <div style={{ flex:1, overflowY:'auto', padding:'8px 14px' }}>
          {loading && <div style={{ textAlign:'center', padding:28, color:'var(--text-secondary)' }}>جاري البحث...</div>}
          {!loading && !shown.length && <div style={{ textAlign:'center', padding:40, color:'var(--text-secondary)' }}>{q?'لا نتائج':'ابدأ بالكتابة للبحث'}</div>}
          {!loading && shown.map((item, i) => (
            <div key={`${item.source}-${item.id}-${i}`} onClick={() => { onSelect(item); onClose() }}
              style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'10px 14px', borderRadius:10, marginBottom:4, cursor:'pointer', border:'1px solid var(--border)', background:'var(--surface)' }}
              onMouseEnter={e=>e.currentTarget.style.background='var(--surface-hover)'}
              onMouseLeave={e=>e.currentTarget.style.background='var(--surface)'}
            >
              <div style={{ flex:1 }}>
                <div style={{ display:'flex', gap:6, marginBottom:3 }}>
                  <span style={{ fontSize:11, fontWeight:700, padding:'2px 7px', borderRadius:10, background:srcColor[item.source]+'22', color:srcColor[item.source] }}>
                    {srcIcon[item.source]} {srcLabel[item.source]}
                  </span>
                  {item.model_no && <span style={{ fontSize:11, color:'var(--text-secondary)' }}>{item.model_no}</span>}
                  {item.item_type && <span style={{ fontSize:11, color:'var(--text-secondary)' }}>• {item.item_type}</span>}
                </div>
                <div style={{ fontWeight:600, fontSize:13 }}>{item.name}</div>
                {item.description && item.description !== item.name &&
                  <div style={{ fontSize:11, color:'var(--text-secondary)', marginTop:1, maxWidth:380, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{item.description}</div>}
              </div>
              <div style={{ textAlign:'left', marginRight:8 }}>
                <div style={{ fontSize:15, fontWeight:700, color:'#E87722' }}>{Number(item.unit_price).toLocaleString('ar-EG',{minimumFractionDigits:0})}</div>
                <div style={{ fontSize:11, color:'var(--text-secondary)' }}>تكلفة: {Number(item.cost_price).toLocaleString()} | {item.margin_pct}%</div>
                <div style={{ fontSize:11, color:'var(--text-secondary)' }}>{item.unit}</div>
              </div>
            </div>
          ))}
        </div>
        <div style={{ padding:'10px 22px', borderTop:'1px solid var(--border)', fontSize:12, color:'var(--text-secondary)' }}>
          اضغط على أي صنف لإضافته تلقائياً في جدول البنود
        </div>
      </div>
    </div>
  )
}

// ── QuotationForm — تصميم مطابق للـ PDF ──────────────────────
function QuotationForm({ customers, onSave, onCancel, initial }) {
  const toast = useToast()

  const [form, setForm] = useState({
    code:          initial?.code || initial?.number || genQuoteCode(),
    customer_id:   initial?.customer_id || '',
    date:          initial?.date || today(),
    valid_until:   initial?.valid_until || '',
    tax_pct:       initial?.tax_pct ?? 0,
    discount_pct:  initial?.discount_pct ?? 0,
    status:        initial?.status || 'draft',
    title:         initial?.notes?.startsWith('عنوان:') ? initial.notes.replace('عنوان:','').trim() : '',
    payment_terms: initial?.payment_terms || '50% دفعة تعاقد — 50% عند الشحن',
    notes:         initial?.notes || '',
    terms:         initial?.terms || 'العرض ساري لمدة 7 أيام\nالأسعار غير شاملة الضرائب\nمدة التوريد 120 يوم عمل من الدفعة المقدمة\nالضمان 12 شهر ضد عيوب الصناعة',
  })

  const emptyLine = () => ({ id: Date.now() + Math.random(), item_type:'', model_no:'', description:'', qty:1, unit:'طقم', unit_price:0, notes:'', image_data:'' })

  const [lines, setLines] = useState(
    initial?.lines?.length
      ? initial.lines.map(l => {
          // استخراج الصورة من الـ notes لو مخزنة
          let image_data = '', notes = l.notes || ''
          if (notes.startsWith('IMG:')) {
            const sep = notes.indexOf('||')
            image_data = sep > 0 ? notes.slice(4, sep) : notes.slice(4)
            notes = sep > 0 ? notes.slice(sep + 2) : ''
          }
          return { ...l, id: l.id ?? Date.now() + Math.random(), qty: l.qty ?? l.quantity ?? 1, image_data, notes }
        })
      : []
  )
  const [saving, setSaving]       = useState(false)
  const [catalogOpen, setCatalog] = useState(false)

  const addLine    = () => setLines(ls => [...ls, emptyLine()])
  const removeLine = (id) => setLines(ls => ls.filter(l => l.id !== id))
  const updateLine = (id, field, value) =>
    setLines(ls => ls.map(l => l.id === id ? { ...l, [field]: value } : l))

  const handleCatalogSelect = (item) => {
    setLines(ls => [...ls, {
      id:          Date.now() + Math.random(),
      item_type:   item.item_type || '',
      model_no:    item.model_no  || '',
      description: item.description || item.name,
      qty:         1,
      unit:        item.unit || 'طقم',
      unit_price:  item.unit_price,
      notes:       `تكلفة: ${item.cost_price} | هامش: ${item.margin_pct}%`,
    }])
  }

  const subtotal = lines.reduce((s, l) => s + Number(l.qty || 0) * Number(l.unit_price || 0), 0)
  const taxAmt   = subtotal * Number(form.tax_pct  || 0) / 100
  const discAmt  = subtotal * Number(form.discount_pct || 0) / 100
  const total    = subtotal + taxAmt - discAmt

  const handleSave = async () => {
    if (!form.customer_id) { toast('اختر العميل', 'error'); return }
    if (!form.code?.trim()) { toast('رقم العرض مطلوب', 'error'); return }
    if (!lines.length) { toast('أضف بنداً واحداً على الأقل', 'error'); return }
    if (lines.some(l => !l.description?.trim())) { toast('أدخل وصفاً لكل البنود', 'error'); return }
    setSaving(true)
    try {
      await onSave({
        code:         form.code,
        customer_id:  Number(form.customer_id),
        status:       form.status,
        date:         form.date || today(),
        valid_until:  form.valid_until || null,
        tax_pct:      Number(form.tax_pct    || 0),
        discount_pct: Number(form.discount_pct || 0),
        notes:        form.title ? `عنوان:${form.title}` : (form.notes || null),
        terms:        form.terms || null,
        subtotal,
        tax_amount:   taxAmt,
        discount_amt: discAmt,
        total,
        lines: lines.map((l, i) => ({
          item_type:   l.item_type  || '',
          model_no:    l.model_no   || '',
          description: l.description,
          qty:         Number(l.qty || 1),
          unit:        l.unit || 'طقم',
          unit_price:  Number(l.unit_price || 0),
          total_price: Number(l.qty || 1) * Number(l.unit_price || 0),
          notes:       l.image_data
            ? `IMG:${l.image_data}||${l.notes || ''}`
            : (l.notes || null),
          sort_order:  i + 1,
        })),
      })
    } finally { setSaving(false) }
  }

  return (
    <div className="flex flex-col gap-4" style={{ direction:'rtl' }}>

      {/* ══ بيانات العرض ══ */}
      <div className="card">
        <div className="text-sm font-bold mb-4" style={{ color:'#E87722' }}>📋 بيانات العرض</div>
        <div className="grid gap-3" style={{ gridTemplateColumns:'1fr 1fr' }}>
          <Input label="رقم العرض *" value={form.code}
            onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="QT-202501-001" />
          <Select label="العميل *" value={form.customer_id}
            onChange={e => setForm(f => ({ ...f, customer_id: e.target.value }))}>
            <option value="">— اختر عميل —</option>
            {customers?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
          <Input label="تاريخ العرض *" type="date" value={form.date}
            onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
          <Input label="صالح حتى" type="date" value={form.valid_until}
            onChange={e => setForm(f => ({ ...f, valid_until: e.target.value }))} />
          <Input label="نسبة الضريبة (%)" type="number" step="0.5" min="0" max="100" value={form.tax_pct}
            onChange={e => setForm(f => ({ ...f, tax_pct: e.target.value }))} />
          <Input label="نسبة الخصم (%)" type="number" step="0.5" min="0" max="100" value={form.discount_pct}
            onChange={e => setForm(f => ({ ...f, discount_pct: e.target.value }))} />
          <Select label="الحالة" value={form.status}
            onChange={e => setForm(f => ({ ...f, status: e.target.value }))}>
            {Object.entries(QUOTE_STATUS_MAP).map(([v, { label }]) =>
              <option key={v} value={v}>{label}</option>)}
          </Select>
        </div>
        {/* عنوان العرض — مثل "عرض أسعار خط انتاج 10 طن/ساعة" */}
        <div className="mt-3">
          <Input label="عنوان العرض (يظهر في الطباعة)" value={form.title}
            onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
            placeholder="مثال: عرض أسعار خط إنتاج أعلاف 10 طن / ساعة" />
        </div>
      </div>

      {/* ══ جدول البنود — النوع | الموديل | المواصفات | الكمية | سعر الوحدة | الإجمالي ══ */}
      <div className="card" style={{ padding:0, overflow:'hidden' }}>
        {/* Header */}
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'14px 18px', borderBottom:'1px solid var(--border)', background:'#E877220D' }}>
          <div className="text-sm font-bold" style={{ color:'#E87722' }}>📦 بنود عرض السعر</div>
          <div style={{ display:'flex', gap:8 }}>
            <button className="btn btn-sm" style={{ background:'#E877220D', color:'#E87722', border:'1px solid #E8772244', fontSize:12, fontWeight:600 }}
              onClick={() => setCatalog(true)}>
              🔍 اختر من الكتالوج / المعدات
            </button>
            <button className="btn btn-primary btn-sm" onClick={addLine}>+ إضافة يدوي</button>
          </div>
        </div>

        {/* Column headers */}
        <div style={{ display:'grid', gridTemplateColumns:'100px 110px 90px 1fr 70px 90px 110px 110px 32px', gap:6, padding:'8px 14px', background:'var(--surface)', fontSize:11, fontWeight:700, color:'var(--text-secondary)', borderBottom:'1px solid var(--border)' }}>
          <div>النوع</div>
          <div>الموديل</div>
          <div style={{ textAlign:'center' }}>الصورة</div>
          <div>المواصفات *</div>
          <div style={{ textAlign:'center' }}>الكمية</div>
          <div style={{ textAlign:'center' }}>الوحدة</div>
          <div style={{ textAlign:'center' }}>سعر الوحدة</div>
          <div style={{ textAlign:'center' }}>الإجمالي</div>
          <div />
        </div>

        {/* Empty state */}
        {!lines.length && (
          <div style={{ textAlign:'center', padding:'32px 20px', color:'var(--text-secondary)', fontSize:13, borderBottom:'1px solid var(--border)' }}>
            لا توجد بنود — اضغط "اختر من الكتالوج" أو "إضافة يدوي"
          </div>
        )}

        {/* Lines */}
        <div style={{ padding:'8px 14px', display:'flex', flexDirection:'column', gap:8 }}>
          {lines.map((l, i) => {
            const lineTotal = Number(l.qty || 0) * Number(l.unit_price || 0)
            return (
              <div key={l.id} style={{ display:'grid', gridTemplateColumns:'100px 110px 90px 1fr 70px 90px 110px 110px 32px', gap:6, alignItems:'start', background:'var(--surface)', borderRadius:8, padding:'8px 10px', border:'1px solid var(--border)' }}>
                {/* النوع */}
                <div>
                  <div style={{ fontSize:10, color:'var(--text-secondary)', marginBottom:2 }}>النوع</div>
                  <input className="field-input" style={{ fontSize:12, padding:'5px 8px' }}
                    placeholder="مجرشة..." value={l.item_type || ''}
                    onChange={e => updateLine(l.id, 'item_type', e.target.value)} />
                </div>
                {/* الموديل */}
                <div>
                  <div style={{ fontSize:10, color:'var(--text-secondary)', marginBottom:2 }}>الموديل</div>
                  <input className="field-input" style={{ fontSize:12, padding:'5px 8px' }}
                    placeholder="SFSP66X60" value={l.model_no || ''}
                    onChange={e => updateLine(l.id, 'model_no', e.target.value)} />
                </div>
                {/* الصورة */}
                <div>
                  <div style={{ fontSize:10, color:'var(--text-secondary)', marginBottom:2 }}>الصورة</div>
                  <ImageCell
                    value={l.image_data || ''}
                    onChange={v => updateLine(l.id, 'image_data', v)}
                  />
                </div>
                {/* المواصفات */}
                <div>
                  <div style={{ fontSize:10, color:'var(--text-secondary)', marginBottom:2 }}>المواصفات *</div>
                  <textarea className="field-input" style={{ fontSize:12, padding:'5px 8px', minHeight:52, resize:'vertical', width:'100%', boxSizing:'border-box' }}
                    placeholder={`البند ${i+1} — اكتب المواصفات التفصيلية`}
                    value={l.description}
                    onChange={e => updateLine(l.id, 'description', e.target.value)} />
                  {l.notes && <div style={{ fontSize:10, color:'#E87722', marginTop:2 }}>{l.notes}</div>}
                </div>
                {/* الكمية */}
                <div>
                  <div style={{ fontSize:10, color:'var(--text-secondary)', marginBottom:2 }}>الكمية</div>
                  <input className="field-input" style={{ fontSize:12, padding:'5px 8px', textAlign:'center' }}
                    type="number" min="0.001" step="1" value={l.qty}
                    onChange={e => updateLine(l.id, 'qty', e.target.value)} />
                </div>
                {/* الوحدة */}
                <div>
                  <div style={{ fontSize:10, color:'var(--text-secondary)', marginBottom:2 }}>الوحدة</div>
                  <select className="field-select" style={{ fontSize:12, padding:'5px 8px' }}
                    value={l.unit} onChange={e => updateLine(l.id, 'unit', e.target.value)}>
                    {QUOTE_UNITS.map(u => <option key={u}>{u}</option>)}
                  </select>
                </div>
                {/* سعر الوحدة */}
                <div>
                  <div style={{ fontSize:10, color:'var(--text-secondary)', marginBottom:2 }}>سعر الوحدة</div>
                  <input className="field-input" style={{ fontSize:12, padding:'5px 8px', textAlign:'center' }}
                    type="number" min="0" step="0.01" placeholder="0"
                    value={l.unit_price}
                    onChange={e => updateLine(l.id, 'unit_price', e.target.value)} />
                </div>
                {/* الإجمالي */}
                <div>
                  <div style={{ fontSize:10, color:'var(--text-secondary)', marginBottom:2 }}>الإجمالي</div>
                  <div className="field-input" style={{ fontSize:13, fontWeight:700, textAlign:'center', color:'#E87722', background:'#E877220A', padding:'5px 8px' }}>
                    {lineTotal.toLocaleString('ar-EG', {minimumFractionDigits:0})}
                  </div>
                </div>
                {/* حذف */}
                <div style={{ paddingTop:16 }}>
                  <button style={{ background:'none', border:'none', color:'var(--red)', cursor:'pointer', fontSize:16 }}
                    onClick={() => removeLine(l.id)}>✕</button>
                </div>
              </div>
            )
          })}
        </div>

        {/* Totals */}
        {lines.length > 0 && (
          <div style={{ borderTop:'1px solid var(--border)', padding:'14px 18px', display:'flex', justifyContent:'flex-end' }}>
            <div style={{ minWidth:260, display:'flex', flexDirection:'column', gap:6, fontSize:13 }}>
              <div style={{ display:'flex', justifyContent:'space-between' }}>
                <span style={{ color:'var(--text-secondary)' }}>الإجمالي قبل الخصم</span>
                <span style={{ fontWeight:600 }}>{subtotal.toLocaleString('ar-EG',{minimumFractionDigits:0})}</span>
              </div>
              {Number(form.discount_pct) > 0 && (
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text-secondary)' }}>خصم {form.discount_pct}%</span>
                  <span style={{ color:'#e74c3c' }}>({discAmt.toLocaleString('ar-EG',{minimumFractionDigits:0})})</span>
                </div>
              )}
              {Number(form.tax_pct) > 0 && (
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text-secondary)' }}>ضريبة {form.tax_pct}%</span>
                  <span>{taxAmt.toLocaleString('ar-EG',{minimumFractionDigits:0})}</span>
                </div>
              )}
              <div style={{ borderTop:'2px solid #E87722', paddingTop:8, display:'flex', justifyContent:'space-between' }}>
                <span style={{ fontWeight:800, fontSize:15, color:'#E87722' }}>إجمالي السعر</span>
                <span style={{ fontWeight:800, fontSize:17, color:'#E87722' }}>{total.toLocaleString('ar-EG',{minimumFractionDigits:0})}</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ══ الشروط ══ */}
      <div className="card">
        <div className="text-sm font-bold mb-3" style={{ color:'#E87722' }}>📄 شروط العرض والسداد</div>
        <div className="grid gap-3" style={{ gridTemplateColumns:'1fr 1fr' }}>
          <Textarea label="شروط الدفع" rows={2} value={form.payment_terms}
            onChange={e => setForm(f => ({ ...f, payment_terms: e.target.value }))} />
          <Textarea label="شروط وأحكام إضافية" rows={2} value={form.terms}
            onChange={e => setForm(f => ({ ...f, terms: e.target.value }))} />
        </div>
      </div>

      {/* ══ Actions ══ */}
      <div style={{ display:'flex', gap:10, alignItems:'center' }}>
        <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
          💾 {saving ? 'جاري الحفظ...' : (initial ? 'حفظ التعديلات' : 'حفظ العرض')}
        </button>
        <button className="btn" onClick={onCancel}>إلغاء</button>
      </div>

      {/* ══ Catalog Picker ══ */}
      <CatalogPickerModal open={catalogOpen} onClose={() => setCatalog(false)} onSelect={handleCatalogSelect} />

    </div>
  )
}

// ── Quotations list — مطابق لـ Invoices list ─────────────────
export function Quotations() {
  const { canWrite } = useAuth()
  const write = canWrite('sales')
  const toast  = useToast()

  const { data: quotes, loading, reload } = useApi(() => api.sales.quotations())
  const { data: customers } = useApi(() => api.customers.list())

  const [q,           setQ]           = useState('')
  const [view,        setView]        = useState('list')   // 'list' | 'create' | 'edit'
  const [editTarget,  setEditTarget]  = useState(null)
  const [converting,  setConverting]  = useState(null)
  const [statusModal, setStatusModal] = useState(null)     // { qt, newStatus }

  const filtered = useMemo(() => {
    if (!quotes) return []
    const lq = q.toLowerCase()
    return !lq ? quotes : quotes.filter(x =>
      x.code?.toLowerCase().includes(lq)          ||
      x.number?.toLowerCase().includes(lq)        ||
      x.customer_name?.toLowerCase().includes(lq)
    )
  }, [quotes, q])

  // ── KPI — مطابق للفواتير ────────────────────────────────────
  const totals = useMemo(() => ({
    all:      (quotes || []).length,
    accepted: (quotes || []).filter(x => x.status === 'accepted').length,
    value:    (quotes || []).reduce((s, x) => s + Number(x.total || 0), 0),
  }), [quotes])

  // ── Create ──────────────────────────────────────────────────
  const handleCreate = async (data) => {
    await api.sales.createQuote(data)
    toast('✅ تم حفظ عرض السعر')
    setView('list')
    reload()
  }

  // ── Edit ────────────────────────────────────────────────────
  const handleEdit = async (data) => {
    await api.sales.updateQuote(editTarget.id, data)
    toast('✅ تم تحديث العرض')
    setView('list')
    setEditTarget(null)
    reload()
  }

  const openEdit = (qt) => { setEditTarget(qt); setView('edit') }

  // ── تحويل لفاتورة — زي زر "دفع" في الفواتير ───────────────
  const handleConvert = async (qt) => {
    if (!window.confirm(
      `تحويل العرض "${qt.code || qt.number}" للعميل "${qt.customer_name}" إلى فاتورة مبيعات؟\n\nسيتم نسخ كل البنود والأسعار تلقائياً.`
    )) return
    setConverting(qt.id)
    try {
      const code = `INV-${(qt.code || qt.id)}-${Date.now().toString().slice(-4)}`
      await api.sales.toInvoice(qt.id, code)
      toast(`✅ تم إنشاء فاتورة ${code} من العرض`)
      reload()
    } catch {
      toast('حدث خطأ أثناء التحويل', 'error')
    } finally {
      setConverting(null)
    }
  }

  // ── طباعة ────────────────────────────────────────────────────
  const handlePrint = (qt) =>
    api.print(`/sales/quotations/${qt.id}/print`)

  if (loading) return <PageSpinner />

  // ── Create view ──────────────────────────────────────────────
  if (view === 'create') {
    return (
      <QuotationForm
        customers={customers}
        onSave={handleCreate}
        onCancel={() => setView('list')}
      />
    )
  }

  // ── Edit view ────────────────────────────────────────────────
  if (view === 'edit' && editTarget) {
    return (
      <QuotationForm
        customers={customers}
        initial={editTarget}
        onSave={handleEdit}
        onCancel={() => { setView('list'); setEditTarget(null) }}
      />
    )
  }

  // ── List view — مطابق لـ Invoices list ──────────────────────
  return (
    <div className="flex flex-col gap-4">

      {/* KPI row — نفس الـ 3 cards بتاعة الفواتير */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'إجمالي العروض',   val: `${totals.all} عرض`,    color: 'var(--blue)'  },
          { label: 'العروض المقبولة', val: `${totals.accepted} عرض`, color: 'var(--green)' },
          { label: 'القيمة الإجمالية', val: egp(totals.value),       color: 'var(--teal)'  },
        ].map(k => (
          <div key={k.label} className="card text-center py-3">
            <div className="text-xs text-erp-muted mb-1">{k.label}</div>
            <div className="text-lg font-bold" style={{ color: k.color }}>{k.val}</div>
          </div>
        ))}
      </div>

      {/* Toolbar — نفس شريط الفواتير */}
      <div className="card flex items-center gap-3 py-3">
        <div className="flex-1">
          <SearchInput value={q} onChange={setQ} placeholder="بحث برقم العرض أو العميل…" />
        </div>
        {write && (
          <button className="btn btn-primary btn-sm" onClick={() => setView('create')}>
            + عرض جديد
          </button>
        )}
      </div>

      {/* Table — نفس columns الفواتير */}
      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr>
              <th>الرقم</th>
              <th>العميل</th>
              <th>التاريخ</th>
              <th>صالح حتى</th>
              <th>الإجمالي</th>
              <th>الحالة</th>
              {write && <th style={{ width: 120 }} />}
            </tr>
          </thead>
          <tbody>
            {!filtered.length && (
              <tr>
                <td colSpan={7} className="text-center text-erp-muted py-10">
                  لا توجد عروض أسعار
                </td>
              </tr>
            )}
            {filtered.map(qt => {
              const st = QUOTE_STATUS_MAP[qt.status] || { label: qt.status, cls: 'badge-gray' }
              return (
                <tr key={qt.id}>
                  <td className="font-mono text-xs font-medium">{qt.code || qt.number}</td>
                  <td className="font-medium">{qt.customer_name}</td>
                  <td className="text-xs text-erp-muted">{fmtDate(qt.date)}</td>
                  <td className="text-xs">{fmtDate(qt.valid_until) || '—'}</td>
                  <td className="font-semibold">{egp(qt.total)}</td>
                  <td><span className={`badge ${st.cls}`}>{st.label}</span></td>
                  {write && (
                    <td>
                      <div className="flex gap-1 flex-wrap">
                        {/* Edit */}
                        <button className="btn btn-sm text-xs" onClick={() => openEdit(qt)}
                          title="تعديل">✏️</button>

                        {/* Print */}
                        <button className="btn btn-sm text-xs" onClick={() => handlePrint(qt)}
                          title="طباعة">🖨️</button>

                        {/* عقد اتفاق */}
                        <button
                          className="btn btn-sm text-xs"
                          style={{ color: 'var(--teal)', borderColor: 'var(--teal)' }}
                          onClick={() => api.print(`/sales/quotations/${qt.id}/agreement`)}
                          title="عقد الاتفاق"
                        >📋 عقد</button>

                        {/* Convert — زي زر "دفع" في الفواتير بالظبط */}
                        {qt.status !== 'rejected' && (
                          <button
                            className="btn btn-sm text-xs"
                            style={{ color: 'var(--green)', borderColor: 'var(--green-lt)' }}
                            disabled={converting === qt.id}
                            onClick={() => handleConvert(qt)}
                            title="تحويل لفاتورة"
                          >
                            {converting === qt.id ? '…' : '🧾 فاتورة'}
                          </button>
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
  )
}

// COSTING — التكاليف والقيود
// إصلاحات:
//   1. qty + unit_cost منفصلين بدل مبلغ واحد
//   2. dropdown للمواد لما النوع = material
//   3. overhead كـ type صريح
//   4. KPI cards في الـ idle state
//   5. breakdown مواد/عمالة/غير مباشر
//   6. entry_type + description في جدول القيود
// ══════════════════════════════════════════════════════════════

const COST_TYPES = [
  { value: 'material', label: 'مواد',              badge: 'badge-blue'  },
  { value: 'labor',    label: 'عمالة',             badge: 'badge-amber' },
  { value: 'overhead', label: 'مصاريف غير مباشرة', badge: 'badge-gray'  },
]

const costLabel  = (t) => COST_TYPES.find(x => x.value === t)?.label  || t
const costBadge  = (t) => COST_TYPES.find(x => x.value === t)?.badge  || 'badge-gray'

export function Payroll() {
  const { canWrite } = useAuth()
  const write = canWrite('workers')
  const toast = useToast()
  const { data: runs, loading, reload } = useApi(() => api.workers.payrollRuns())
  const [modal, setModal] = useState(false)

  // حساب آخر سبت
  const lastSaturday = () => {
    const d = new Date()
    const day = d.getDay() // 0=Sun,6=Sat
    const diff = day === 6 ? 0 : (day + 1)
    d.setDate(d.getDate() - diff)
    // تاريخ محلي (toISOString كان بيرجّع يوم الجمعة بعد منتصف الليل بتوقيت مصر)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }

  const [form, setForm] = useState({ week_start: lastSaturday() })
  const [detail, setDetail] = useState(null)
  const { data: detailData, reload: reloadDetail } = useApi(() => detail ? api.workers.payrollDetail(detail) : Promise.resolve(null), [detail])

  const [save, saving] = useAction(async () => {
    const week_start = form.week_start || lastSaturday()
    const endDate = new Date(week_start)
    endDate.setDate(endDate.getDate() + 6)
    const week_end = endDate.toISOString().slice(0, 10)
    // تحذير: تأكيد اكتمال الحضور قبل الإنشاء
    const confirmed = window.confirm(
      `سيتم إنشاء كشف رواتب للفترة:\n${week_start} — ${week_end}\n\n` +
      `تأكد من اكتمال سجلات الحضور لجميع العمال قبل المتابعة.\n\n` +
      `هل تريد المتابعة؟`
    )
    if (!confirmed) return
    // 1) إنشاء الكشف
    const created = await api.workers.createPayroll({ week_start, week_end })
    // 2) توليد بنود الرواتب من الحضور تلقائياً
    try {
      await api.post(`/workers/payroll/${created.id}/generate`)
    } catch (err) {
      // الكشف اتعمل فعلاً — لازم القائمة تتحدث ويتقال للمستخدم إن البنود ماتولدتش
      setModal(false); reload()
      throw Object.assign(new Error(`تم إنشاء الكشف لكن تعذّر حساب البنود: ${err?.message || ''}`), { status: err?.status })
    }
    toast('تم إنشاء كشف الرواتب وحساب البنود'); setModal(false); reload()
  })

  const [pay, paying] = useAction(async () => {
    if (!detailData) return
    const confirmed = window.confirm(
      `سيتم صرف صافي الرواتب (${egp(detailData.total_net)}) من الخزنة وتسجيل القيد المحاسبي.\n\n` +
      `هذا الإجراء لا يمكن التراجع عنه. هل تريد المتابعة؟`
    )
    if (!confirmed) return
    await api.workers.payPayroll(detail)
    toast('تم صرف الرواتب وتحديث الخزنة')
    reloadDetail(); reload()
  })

  if (loading) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        {write && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ كشف رواتب جديد</button>}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-0 overflow-hidden lg:col-span-1">
          <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">كشوف الرواتب</div>
          {!runs?.length ? <div className="p-6 text-center text-erp-muted text-sm">لا توجد كشوف</div> :
            runs.map(r => (
              <div key={r.id} className={`px-4 py-3 border-b border-erp-border cursor-pointer hover:bg-erp transition-colors ${detail === r.id ? 'bg-teal-lt' : ''}`}
                   onClick={() => setDetail(r.id)}>
                <div className="flex items-center justify-between">
                  <div className="font-medium text-sm">أسبوع: {fmtDate(r.week_start)} — {fmtDate(r.week_end)}</div>
                  {write && (
                    <button className="btn btn-sm text-xs" style={{ color: 'var(--red)' }}
                      onClick={async (e) => {
                        e.stopPropagation()
                        if (!window.confirm(`حذف كشف رواتب أسبوع ${fmtDate(r.week_start)}؟`)) return
                        try {
                          await api.workers.deletePayroll(r.id)
                          toast('تم حذف الكشف')
                          if (detail === r.id) setDetail(null)
                          reload()
                        } catch (err) { toast(err?.message || 'تعذّر الحذف', 'error') }
                      }}>🗑</button>
                  )}
                </div>
                <div className="flex items-center justify-between mt-1">
                  <span className="text-xs text-erp-muted">{r.worker_count} عامل</span>
                  <span className="font-semibold text-sm">{egp(r.total)}</span>
                </div>
              </div>
            ))
          }
        </div>
        <div className="card lg:col-span-2">
          {!detail ? (
            <div className="text-center text-erp-muted py-10 text-sm">
              <div className="text-3xl mb-3">💵</div>اختر كشف رواتب
            </div>
          ) : !detailData ? <PageSpinner /> : (
            <div>
              <div className="flex items-center justify-between mb-3">
                <div className="text-sm font-semibold">
                  تفاصيل الكشف{' '}
                  {detailData.status === 'paid' ? (
                    <span className="badge badge-green">مصروف{detailData.paid_date ? ` — ${fmtDate(detailData.paid_date)}` : ''}</span>
                  ) : (
                    <span className="badge badge-amber">لم يُصرف بعد</span>
                  )}
                </div>
                {write && detailData.status !== 'paid' && (
                  <button className="btn btn-primary btn-sm" onClick={pay} disabled={paying}>
                    {paying ? '…' : '💰 صرف الرواتب'}
                  </button>
                )}
              </div>
              <table className="erp-table">
                <thead><tr><th>العامل</th><th>أيام العمل</th><th>الأجر</th><th>السلف</th><th>الصافي</th></tr></thead>
                <tbody>
                  {detailData.lines?.map(l => (
                    <tr key={l.id}>
                      <td className="font-medium">{l.worker_name}</td>
                      <td>{l.days_worked}</td>
                      <td>{egp(l.gross_amount)}</td>
                      <td className="text-xs" style={{ color: 'var(--red)' }}>{egp(l.deductions)}</td>
                      <td className="font-semibold">{egp(l.net_amount)}</td>
                    </tr>
                  ))}
                  <tr style={{ background: 'var(--teal-lt)' }}>
                    <td colSpan={4} className="font-semibold">الإجمالي</td>
                    <td className="font-bold">{egp(detailData.total_net)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      <Modal open={modal} onClose={() => setModal(false)} title="كشف رواتب جديد" size="sm">
        <Input label="بداية الأسبوع (السبت)" type="date" value={form.week_start || lastSaturday()} onChange={e => setForm(f => ({ ...f, week_start: e.target.value }))} />
        {form.week_start && (
          <div className="text-xs text-erp-muted mt-1">
            نهاية الأسبوع: {(() => { const d = new Date(form.week_start); d.setDate(d.getDate()+6); return d.toLocaleDateString('ar-EG') })()}
          </div>
        )}
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إنشاء'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// ATTENDANCE
// ══════════════════════════════════════════════════════════════

export function Attendance() {
  const { canWrite } = useAuth()
  const write = canWrite('workers')
  const toast = useToast()

  // Default to last Saturday
  const lastSat = () => {
    const d = new Date()
    const day = d.getDay() // 0=Sun,6=Sat
    const diff = day === 6 ? 0 : (day + 1)
    d.setDate(d.getDate() - diff)
    // تاريخ محلي (toISOString كان بيرجّع يوم الجمعة بعد منتصف الليل بتوقيت مصر)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }
  const [date, setDate] = useState(lastSat())
  const { data: recs, loading, reload } = useApi(() => api.attendance.list(date), [date])
  const { data: workers } = useApi(() => api.workers.list())
  const [bulk, setBulk] = useState({})
  const [bulkMode, setBulkMode] = useState(false)

  const initBulk = () => {
    const init = {}
    workers?.forEach(w => { init[w.id] = recs?.find(r => r.worker_id === w.id)?.status || 'present' })
    setBulk(init); setBulkMode(true)
  }

  const [saveBulk, saving] = useAction(async () => {
    const records = Object.entries(bulk).map(([worker_id, status]) => ({ worker_id: +worker_id, status }))
    if (!records.length) { toast('لا يوجد عمال لتسجيل حضورهم', 'error'); return }
    await api.attendance.bulk({ work_date: date, date, records })
    // تصفير bulk: كان بيفضل محفوظ ويتعرض كحالة الحضور لأي تاريخ تاني تختاره بعد الحفظ
    toast('تم حفظ الحضور'); setBulkMode(false); setBulk({}); reload()
  })

  const STATUS = {
    present: { label:'حاضر',     color:'var(--green)'    },
    absent:  { label:'غائب',     color:'var(--red)'      },
    half:    { label:'نصف يوم',  color:'var(--amber-md)' },
    leave:   { label:'إجازة',    color:'var(--blue)'     },
  }

  // Status summary
  const summary = useMemo(() => {
    const src = bulkMode ? Object.values(bulk) : (recs || []).map(r => r.status)
    return Object.fromEntries(Object.keys(STATUS).map(k => [k, src.filter(s => s===k).length]))
  }, [recs, bulk, bulkMode])

  if (loading && !workers) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div>
          <label className="field-label">تاريخ الحضور</label>
          <input className="field-input" type="date" value={date} onChange={e => { setDate(e.target.value); setBulkMode(false); setBulk({}) }} />
        </div>
        {write && !bulkMode && (
          <button className="btn btn-primary btn-sm mt-4" onClick={initBulk}>📝 تسجيل حضور اليوم</button>
        )}
        {write && bulkMode && (
          <>
            <button className="btn btn-sm mt-4" onClick={() => {
              const all = {}; workers?.forEach(w => { all[w.id]='present' }); setBulk(all)
            }}>✓ الكل حاضر</button>
            <button className="btn btn-primary btn-sm mt-4" onClick={saveBulk} disabled={saving}>
              {saving ? '…' : '💾 حفظ'}
            </button>
            <button className="btn btn-sm mt-4" onClick={() => { setBulkMode(false); setBulk({}) }}>إلغاء</button>
          </>
        )}
      </div>

      {/* Summary badges */}
      <div className="flex gap-2 flex-wrap">
        {Object.entries(STATUS).map(([k,v]) => (
          <div key={k} className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold"
               style={{ background: v.color+'22', color: v.color }}>
            {v.label}: {summary[k] || 0}
          </div>
        ))}
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead>
              <tr><th>#</th><th>العامل</th><th>الوظيفة</th><th>الحالة</th></tr>
            </thead>
            <tbody>
              {!workers?.length && (
                <tr><td colSpan={4} className="text-center text-erp-muted py-10">لا يوجد عمال</td></tr>
              )}
              {workers?.map((w, idx) => {
                const rec = recs?.find(r => r.worker_id === w.id)
                const cur = (bulkMode ? bulk[w.id] : undefined) || rec?.status
                const st  = STATUS[cur]
                return (
                  <tr key={w.id}>
                    <td className="text-erp-muted text-xs w-8">{idx+1}</td>
                    <td className="font-medium">{w.name}</td>
                    <td className="text-erp-muted text-xs">{w.job_title}</td>
                    <td>
                      {bulkMode ? (
                        <div className="flex gap-1">
                          {Object.entries(STATUS).map(([k,v]) => (
                            <button key={k}
                              className="px-2 py-0.5 rounded text-xs font-medium border transition-all"
                              style={{
                                background: bulk[w.id]===k ? v.color : 'transparent',
                                color:      bulk[w.id]===k ? '#fff'   : v.color,
                                borderColor: v.color,
                              }}
                              onClick={() => setBulk(b => ({...b, [w.id]:k}))}>
                              {v.label}
                            </button>
                          ))}
                        </div>
                      ) : cur ? (
                        <span className="badge text-xs font-semibold"
                          style={{ background: (st?.color||'var(--muted)')+'22', color: st?.color||'var(--muted)' }}>
                          {st?.label || cur}
                        </span>
                      ) : (
                        <span className="text-erp-muted text-xs">—</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// COSTING
// ══════════════════════════════════════════════════════════════

export function Costing() {
  const { data: wos }       = useApi(() => api.accounting.workOrders())
  const { data: dashboard } = useApi(() => api.accounting.dashboard())
  const { data: materials } = useApi(() => api.inventory.materials())

  const [woId, setWoId] = useState('')

  const { data: lines, loading, reload } = useApi(
    () => woId ? api.accounting.costLines(woId) : Promise.resolve([]),
    [woId]
  )
  const { data: journals } = useApi(
    () => woId ? api.accounting.journals(woId) : Promise.resolve([]),
    [woId]
  )

  const { canWrite } = useAuth()
  const write = canWrite('production')
  const toast  = useToast()

  const [modal, setModal] = useState(false)
  const [form,  setForm]  = useState({
    cost_type:   'material',
    description: '',
    qty:         1,
    unit_cost:   '',
    material_id: '',
  })

  // ── حساب الإجمالي من qty × unit_cost ─────────────────────
  const lineTotal = (Number(form.qty) || 0) * (Number(form.unit_cost) || 0)

  // ── breakdown per type ────────────────────────────────────
  const breakdown = useMemo(() => {
    const b = { material: 0, labor: 0, overhead: 0 }
    ;(lines || []).forEach(l => {
      const key = l.cost_type || l.type || 'overhead'
      b[key] = (b[key] || 0) + Number(l.total_cost || l.amount || 0)
    })
    return b
  }, [lines])

  const totalCost = breakdown.material + breakdown.labor + breakdown.overhead

  // ── إضافة سطر تكلفة ──────────────────────────────────────
  const [save, saving] = useAction(async () => {
    if (!woId)               { toast('اختر أمر الإنتاج', 'error'); return }
    if (!form.unit_cost || Number(form.unit_cost) <= 0) { toast('أدخل سعر وحدة أكبر من صفر',  'error'); return }
    if (!form.description?.trim() && !form.material_id) {
      toast('أدخل وصفاً للبند', 'error'); return
    }

    const payload = {
      work_order_id: +woId,
      cost_type:     form.cost_type,
      description:   form.description || materials?.find(m => m.id === +form.material_id)?.name || '',
      qty:           Number(form.qty)       || 1,
      unit_cost:     Number(form.unit_cost) || 0,
      material_id:   form.material_id ? +form.material_id : null,
    }

    await api.accounting.createCL(payload)
    toast('✅ تمت الإضافة')
    setModal(false)
    setForm({ cost_type: 'material', description: '', qty: 1, unit_cost: '', material_id: '' })
    reload()
  })

  const [doDelete] = useAction(async (id) => {
    await api.accounting.deleteCL(id)
    toast('تم الحذف')
    reload()
  })

  // ── لما يختار مادة من الـ dropdown ─────────────────────
  const handleMaterialChange = (matId) => {
    const mat = materials?.find(m => m.id === +matId)
    setForm(f => ({
      ...f,
      material_id: matId,
      description: mat?.name || f.description,
      unit_cost:   mat?.unit_cost ?? mat?.price ?? f.unit_cost,
    }))
  }

  const selectedWo = wos?.find(w => w.id === +woId)

  if (loading && woId) return <PageSpinner />

  return (
    <div className="flex flex-col gap-4">

      {/* ── Toolbar ── */}
      <div className="card flex items-center gap-3 py-3 flex-wrap">
        <div className="flex-1 min-w-48">
          <Select value={woId} onChange={e => setWoId(e.target.value)} label="">
            <option value="">— اختر أمر إنتاج —</option>
            {wos?.map(w => (
              <option key={w.id} value={w.id}>
                {w.code}{w.equipment_name_ar ? ' — ' + w.equipment_name_ar : ''}
              </option>
            ))}
          </Select>
        </div>
        {write && woId && (
          <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>
            + سطر تكلفة
          </button>
        )}
        {woId && (
          <div className="text-sm font-semibold" style={{ color: 'var(--teal)' }}>
            الإجمالي: {egp(totalCost)}
          </div>
        )}
      </div>

      {/* ── Idle state: KPI dashboard ── */}
      {!woId && (
        <>
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: 'تكاليف المواد (الشهر)', val: egp(dashboard?.mtd_material ?? 0),  color: 'var(--blue)'  },
              { label: 'تكاليف العمالة (الشهر)', val: egp(dashboard?.mtd_labor   ?? 0),  color: 'var(--amber)' },
              { label: 'إجمالي التكاليف (الشهر)', val: egp((dashboard?.mtd_material ?? 0) + (dashboard?.mtd_labor ?? 0)), color: 'var(--teal)' },
            ].map(k => (
              <div key={k.label} className="card text-center py-3">
                <div className="text-xs text-erp-muted mb-1">{k.label}</div>
                <div className="text-lg font-bold" style={{ color: k.color }}>{k.val}</div>
              </div>
            ))}
          </div>

          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">
              آخر أوامر الإنتاج
            </div>
            <table className="erp-table">
              <thead>
                <tr>
                  <th>الكود</th>
                  <th>المعدة</th>
                  <th>الحالة</th>
                  <th>التكلفة الفعلية</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {!wos?.length && (
                  <tr><td colSpan={5} className="text-center text-erp-muted py-8">لا توجد أوامر إنتاج</td></tr>
                )}
                {wos?.slice(0, 8).map(w => (
                  <tr key={w.id}>
                    <td><span className="font-mono text-xs font-medium">{w.code}</span></td>
                    <td className="font-medium">{w.equipment_name_ar || '—'}</td>
                    <td>
                      <span className={`badge ${
                        w.status === 'done'        ? 'badge-green' :
                        w.status === 'in_progress' ? 'badge-amber' :
                        w.status === 'cancelled'   ? 'badge-red'   : 'badge-gray'
                      }`}>
                        {w.status === 'done' ? 'منتهي' : w.status === 'in_progress' ? 'جاري' : w.status === 'cancelled' ? 'ملغي' : 'مسودة'}
                      </span>
                    </td>
                    <td className="font-semibold">{egp(w.actual_cost ?? 0)}</td>
                    <td>
                      <button
                        className="btn btn-sm text-xs"
                        style={{ color: 'var(--teal)', borderColor: 'var(--teal-lt)' }}
                        onClick={() => setWoId(String(w.id))}
                      >
                        عرض التكاليف
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* ── Selected WO view ── */}
      {woId && (
        <>
          {/* Breakdown cards */}
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: 'مواد',              val: breakdown.material, color: 'var(--blue)'  },
              { label: 'عمالة',             val: breakdown.labor,    color: 'var(--amber)' },
              { label: 'غير مباشر',         val: breakdown.overhead, color: 'var(--muted)' },
            ].map(k => (
              <div key={k.label} className="card text-center py-3">
                <div className="text-xs text-erp-muted mb-1">{k.label}</div>
                <div className="text-base font-bold" style={{ color: k.color }}>{egp(k.val)}</div>
              </div>
            ))}
          </div>

          {/* Cost lines + Journals */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

            {/* سطور التكلفة */}
            <div className="card p-0 overflow-hidden">
              <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">
                سطور التكلفة
              </div>
              <table className="erp-table">
                <thead>
                  <tr>
                    <th>النوع</th>
                    <th>الوصف</th>
                    <th>الكمية</th>
                    <th>سعر الوحدة</th>
                    <th>الإجمالي</th>
                    {write && <th />}
                  </tr>
                </thead>
                <tbody>
                  {!lines?.length && (
                    <tr><td colSpan={6} className="text-center text-erp-muted py-6">لا توجد تكاليف</td></tr>
                  )}
                  {lines?.map(l => (
                    <tr key={l.id}>
                      <td>
                        <span className={`badge ${costBadge(l.cost_type || l.type)}`}>
                          {costLabel(l.cost_type || l.type)}
                        </span>
                      </td>
                      <td className="text-erp-muted text-xs">{l.description || '—'}</td>
                      <td className="text-xs text-center">{l.qty ?? 1}</td>
                      <td className="text-xs text-center">{egp(l.unit_cost ?? l.amount ?? 0)}</td>
                      <td className="font-semibold" style={{ color: 'var(--teal)' }}>
                        {egp(l.total_cost || l.amount || 0)}
                      </td>
                      {write && (
                        <td>
                          <button
                            className="btn btn-sm btn-danger text-xs"
                            onClick={() => doDelete(l.id)}
                          >🗑</button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* القيود المحاسبية */}
            <div className="card p-0 overflow-hidden">
              <div className="px-4 py-3 border-b border-erp-border text-sm font-semibold">
                القيود المحاسبية
              </div>
              <table className="erp-table">
                <thead>
                  <tr>
                    <th>التاريخ</th>
                    <th>النوع</th>
                    <th>الوصف</th>
                    <th>مدين</th>
                    <th>دائن</th>
                  </tr>
                </thead>
                <tbody>
                  {!journals?.length && (
                    <tr><td colSpan={5} className="text-center text-erp-muted py-6">لا توجد قيود</td></tr>
                  )}
                  {journals?.map((j, i) => (
                    <tr key={i}>
                      <td className="text-xs text-erp-muted">{fmtDate(j.entry_date || j.date)}</td>
                      <td>
                        <span className={`badge ${costBadge(j.entry_type?.replace('_cost',''))}`}>
                          {costLabel(j.entry_type?.replace('_cost',''))}
                        </span>
                      </td>
                      <td className="text-xs text-erp-muted">{j.description || '—'}</td>
                      <td className="font-medium text-sm" style={{ color: 'var(--blue)' }}>
                        {j.debit_account || j.debit ? `${j.debit_account || ''} ${j.debit ? egp(j.amount || j.debit) : ''}`.trim() : '—'}
                      </td>
                      <td className="font-medium text-sm" style={{ color: 'var(--green)' }}>
                        {j.credit_account || j.credit ? `${j.credit_account || ''} ${j.credit ? egp(j.amount || j.credit) : ''}`.trim() : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* ── Modal: سطر تكلفة جديد ── */}
      <Modal open={modal} onClose={() => setModal(false)} title="سطر تكلفة جديد" size="sm">
        <div className="flex flex-col gap-3">

          {/* النوع */}
          <Select
            label="النوع *"
            value={form.cost_type}
            onChange={e => setForm(f => ({ ...f, cost_type: e.target.value, material_id: '', description: '', unit_cost: '' }))}
            options={COST_TYPES.map(t => ({ value: t.value, label: t.label }))}
          />

          {/* لو نوع مواد: dropdown المواد */}
          {form.cost_type === 'material' && materials?.length > 0 && (
            <Select
              label="المادة (اختياري)"
              value={form.material_id || ''}
              onChange={e => handleMaterialChange(e.target.value)}
            >
              <option value="">— اختر مادة من المخزون —</option>
              {materials.map(m => (
                <option key={m.id} value={m.id}>
                  {m.name}{m.unit ? ` (${m.unit})` : ''}{m.unit_cost ? ` — ${egp(m.unit_cost)}/وحدة` : ''}
                </option>
              ))}
            </Select>
          )}

          {/* الوصف */}
          <Input
            label="الوصف *"
            value={form.description || ''}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            placeholder={form.cost_type === 'labor' ? 'مثال: أجور نجارين 3 أيام' : 'وصف البند'}
          />

          {/* الكمية × سعر الوحدة */}
          <FormGrid cols={2}>
            <Input
              label="الكمية *"
              type="number"
              min="0.001"
              step="0.001"
              value={form.qty}
              onChange={e => setForm(f => ({ ...f, qty: e.target.value }))}
            />
            <Input
              label="سعر الوحدة (ج) *"
              type="number"
              min="0"
              step="0.01"
              value={form.unit_cost}
              onChange={e => setForm(f => ({ ...f, unit_cost: e.target.value }))}
            />
          </FormGrid>

          {/* الإجمالي */}
          {lineTotal > 0 && (
            <div
              className="flex items-center justify-between px-3 py-2 rounded-erp text-sm"
              style={{ background: 'var(--teal-lt)' }}
            >
              <span className="text-erp-muted">الإجمالي</span>
              <span className="font-bold" style={{ color: 'var(--teal)' }}>{egp(lineTotal)}</span>
            </div>
          )}
        </div>

        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? '…' : 'إضافة'}
          </button>
        </ModalFooter>
      </Modal>

    </div>
  )
}
// ORDERS
// ══════════════════════════════════════════════════════════════
export function Orders() {
  const { canWrite } = useAuth()
  const write = canWrite('sales')
  const toast = useToast()
  const { data: customers } = useApi(() => api.customers.list())
  const [custId, setCustId] = useState('')
  const { data: orders, loading, reload } = useApi(
    () => custId ? api.customers.orders(custId) : Promise.resolve([]),
    [custId]
  )
  const [modal, setModal] = useState(false)
  const [form, setForm] = useState({ date: today(), quantity: 1, unit_price: '' })

  const calcTotal = () => (Number(form.quantity || 1) * Number(form.unit_price || 0))

  const [save, saving] = useAction(async () => {
    if (!custId) { toast('اختر العميل', 'error'); return }
    if (!form.code) { toast('أدخل كود الطلب', 'error'); return }
    await api.customers.createOrder({ ...form, quantity: Number(form.quantity || 1), unit_price: Number(form.unit_price || 0), customer_id: +custId })
    toast('تم إنشاء الطلب'); setModal(false); reload()
  })

  if (loading && custId) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="card flex items-center gap-3 py-3">
        <Select value={custId} onChange={e => setCustId(e.target.value)} label="">
          <option value="">— اختر عميل —</option>
          {customers?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>
        {write && custId && <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ طلب جديد</button>}
      </div>
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead><tr><th>الكود</th><th>التاريخ</th><th>الوصف</th><th>الإجمالي</th><th>الحالة</th></tr></thead>
            <tbody>
              {!orders?.length && <tr><td colSpan={5} className="text-center text-erp-muted py-10">{custId ? 'لا توجد طلبات' : 'اختر عميلاً'}</td></tr>}
              {orders?.map(o => (
                <tr key={o.id}>
                  <td><span className="font-mono text-xs">{o.code}</span></td>
                  <td className="text-erp-muted text-xs">{fmtDate(o.date)}</td>
                  <td className="text-sm">{o.description || '—'}</td>
                  <td className="font-semibold">{egp(o.total)}</td>
                  <td><span className={`badge ${o.status === 'delivered' ? 'badge-green' : o.status === 'in_progress' ? 'badge-amber' : o.status === 'cancelled' ? 'badge-red' : 'badge-blue'}`}>
                    {o.status === 'delivered' ? 'مُسلَّم' : o.status === 'in_progress' ? 'جاري' : o.status === 'cancelled' ? 'ملغي' : 'معلق'}
                  </span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <Modal open={modal} onClose={() => setModal(false)} title="طلب جديد" size="sm">
        <div className="flex flex-col gap-3">
          <Input label="كود الطلب *" value={form.code || ''} onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="ORD-2025-001" />
          <Input label="التاريخ" type="date" value={form.date} onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
          <Input label="الكمية" type="number" value={form.quantity} onChange={e => setForm(f => ({ ...f, quantity: e.target.value }))} min={1} />
          <Input label="سعر الوحدة (ج.م)" type="number" value={form.unit_price} onChange={e => setForm(f => ({ ...f, unit_price: e.target.value }))} min={0} step="0.01" />
          <Input label="الإجمالي (محسوب تلقائياً)" value={egp(calcTotal())} readOnly style={{ background: 'var(--bg)', color: 'var(--text-secondary)', cursor: 'default' }} />
          <Textarea label="الوصف" value={form.description || ''} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} rows={2} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'إنشاء'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// PRODUCTION
// ══════════════════════════════════════════════════════════════
// ══════════════════════════════════════════════════════════════
// MRP
// ══════════════════════════════════════════════════════════════
export function MRP() {
  const { canWrite } = useAuth()
  const write = canWrite('production')
  const toast = useToast()
  const { data: suggestions, loading, reload } = useApi(() => api.production.mrp())

  const [runMrp, running] = useAction(async () => {
    await api.production.runMrp()
    toast('تم تشغيل MRP'); reload()
  })

  if (loading) return <PageSpinner />
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-erp-muted">اقتراحات الشراء المحتسبة تلقائياً من خلال MRP</p>
        {write && (
          <button className="btn btn-primary btn-sm" onClick={runMrp} disabled={running}>
            {running ? '⏳ جاري…' : '🔄 تشغيل MRP'}
          </button>
        )}
      </div>
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table">
            <thead><tr><th>المادة</th><th>الكمية الحالية</th><th>النقص</th><th>الكمية المقترحة</th><th>التاريخ المتوقع</th><th>الأولوية</th></tr></thead>
            <tbody>
              {!suggestions?.length && <tr><td colSpan={6} className="text-center text-erp-muted py-10">لا توجد اقتراحات — المخزون كافٍ أو شغّل MRP أولاً</td></tr>}
              {(Array.isArray(suggestions) ? suggestions : []).map((s0, i) => {
                // الباك (run_mrp) مش بيرجّع priority — نشتقها: رصيد صفر/سالب = عاجل، غير كده متوسط
                const s = s0.priority ? s0 : { ...s0, priority: Number(s0.current_stock ?? s0.stock_qty ?? 0) <= 0 ? 'high' : 'medium' }
                return (
                <tr key={s.material_id ?? i}>
                  <td className="font-medium">{s.material_name}</td>
                  <td className="text-sm">{Number(s.current_stock ?? s.stock_qty ?? 0).toFixed(1)}</td>
                  <td className="font-semibold" style={{ color: 'var(--red)' }}>{Number(s.shortage ?? s.required_qty ?? 0).toFixed(1)}</td>
                  <td className="font-bold" style={{ color: 'var(--teal)' }}>{Number(s.suggested_qty ?? s.qty ?? 0).toFixed(1)}</td>
                  <td className="text-erp-muted text-xs">{fmtDate(s.expected_by)}</td>
                  <td><span className={`badge ${s.priority === 'high' ? 'badge-red' : s.priority === 'medium' ? 'badge-amber' : 'badge-blue'}`}>
                    {s.priority === 'high' ? 'عاجل' : s.priority === 'medium' ? 'متوسط' : 'منخفض'}
                  </span></td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// STOCK COUNT
// ══════════════════════════════════════════════════════════════
// جرد المخزون — اتنقل لصفحة مستقلة بجرد لكل مخزن (pages/StockCount.jsx)
export { default as StockCount } from './StockCount'


// ══════════════════════════════════════════════════════════════
// PAYMENT HISTORY — سجل المدفوعات الموحد
// يجمع دفعات العملاء (وارد) + دفعات الموردين (صادر) في جدول واحد
// ══════════════════════════════════════════════════════════════
export function PaymentHistory() {
  const [filters, setFilters] = useState({ payment_type: 'all', date_from: '', date_to: '' })
  const [applied, setApplied] = useState({ payment_type: 'all' })

  const { data, loading, reload } = useApi(
    () => {
      const p = {}
      if (applied.payment_type !== 'all') p.payment_type = applied.payment_type
      if (applied.date_from) p.date_from = applied.date_from
      if (applied.date_to)   p.date_to   = applied.date_to
      return api.reports.paymentHistory(p)
    },
    [applied]
  )

  const applyFilters = () => setApplied({ ...filters })

  const METHOD_AR = { cash: 'نقدي', transfer: 'تحويل', cheque: 'شيك', bill: 'كمبيالة' }

  return (
    <div className="flex flex-col gap-4">
      {/* KPI cards */}
      {data && (
        <div className="grid grid-cols-3 gap-3">
          <div className="card flex flex-col gap-1 py-3">
            <div className="text-xs text-erp-muted">إجمالي الوارد</div>
            <div className="text-lg font-bold" style={{ color: 'var(--green)' }}>{egp(data.total_in)}</div>
          </div>
          <div className="card flex flex-col gap-1 py-3">
            <div className="text-xs text-erp-muted">إجمالي الصادر</div>
            <div className="text-lg font-bold" style={{ color: 'var(--red)' }}>{egp(data.total_out)}</div>
          </div>
          <div className="card flex flex-col gap-1 py-3">
            <div className="text-xs text-erp-muted">الصافي</div>
            <div className={`text-lg font-bold ${data.net >= 0 ? '' : ''}`}
                 style={{ color: data.net >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {egp(data.net)}
            </div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="card flex flex-wrap items-end gap-3 py-3">
        <Select label="نوع الدفعة" value={filters.payment_type}
          onChange={e => setFilters(f => ({ ...f, payment_type: e.target.value }))}>
          <option value="all">الكل (عملاء + موردون)</option>
          <option value="customer">دفعات العملاء فقط</option>
          <option value="supplier">دفعات الموردين فقط</option>
        </Select>
        <Input label="من تاريخ" type="date" value={filters.date_from}
          onChange={e => setFilters(f => ({ ...f, date_from: e.target.value }))} />
        <Input label="إلى تاريخ" type="date" value={filters.date_to}
          onChange={e => setFilters(f => ({ ...f, date_to: e.target.value }))} />
        <button className="btn btn-primary btn-sm" onClick={applyFilters}>بحث</button>
        <button className="btn btn-sm" onClick={() => { setFilters({ payment_type: 'all', date_from: '', date_to: '' }); setApplied({ payment_type: 'all' }) }}>إعادة ضبط</button>
      </div>

      {/* Table */}
      {loading ? <PageSpinner /> : (
        <div className="card p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="erp-table">
              <thead>
                <tr>
                  <th>التاريخ</th>
                  <th>النوع</th>
                  <th>الجهة</th>
                  <th>طريقة الدفع</th>
                  <th>المرجع</th>
                  <th>الملاحظات</th>
                  <th>سجّله</th>
                  <th>المبلغ</th>
                  <th>الاتجاه</th>
                </tr>
              </thead>
              <tbody>
                {!data?.records?.length && (
                  <tr><td colSpan={8} className="text-center text-erp-muted py-10">لا توجد مدفوعات</td></tr>
                )}
                {data?.records?.map((r, i) => (
                  <tr key={`${r.type}-${r.id}-${i}`}>
                    <td className="text-xs text-erp-muted">{fmtDate(r.payment_date)}</td>
                    <td>
                      <span className={`badge ${r.type === 'customer' ? 'badge-green' : 'badge-amber'}`}>
                        {r.type_ar}
                      </span>
                    </td>
                    <td className="font-medium">{r.party_name}</td>
                    <td className="text-xs">{METHOD_AR[r.payment_method] || r.payment_method || 'نقدي'}</td>
                    <td className="text-xs font-mono">{r.reference || '—'}</td>
                    <td className="text-xs text-erp-muted">{r.notes || '—'}</td>
                    <td className="text-xs text-erp-muted">{r.created_by_name || '—'}</td>
                    <td className="font-semibold">{egp(r.amount)}</td>
                    <td>
                      <span className={`badge ${r.direction === 'in' ? 'badge-green' : 'badge-red'}`}>
                        {r.direction === 'in' ? '▲ وارد' : '▼ صادر'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data?.count > 0 && (
            <div className="px-4 py-2 text-xs text-erp-muted border-t border-erp-border">
              {data.count} سجل — {egp(data.total_in)} وارد / {egp(data.total_out)} صادر
            </div>
          )}
        </div>
      )}
    </div>
  )
}
