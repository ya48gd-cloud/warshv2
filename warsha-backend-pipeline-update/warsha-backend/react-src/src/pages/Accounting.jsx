import { useState } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useToast } from '../store/toast'
import api from '../api/client'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'
import ConfirmDialog from '../components/ui/ConfirmDialog'

// بنبعت بس التواريخ المحددة فعلاً — "from_date=" فاضي كان بيروح للباك كـ
// قيمة تاريخ غير صحيحة (422) لما المستخدم يحدد تاريخ واحد بس من الاتنين
const dateParams = (from, to) => {
  const p = {}
  if (from) p.from_date = from
  if (to) p.to_date = to
  return Object.keys(p).length ? p : undefined
}

// تقريب لخانتين بالقروش — الباك بيقرّب كل سطر لخانتين وبيطلب تساوي تام
const toCents = (v) => Math.round(Number(v || 0) * 100)
// تاريخ اليوم بالتوقيت المحلي (toISOString بيرجع UTC فيطلع يوم امبارح بعد نص الليل بتوقيت مصر)
const todayStr = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}` }

const ACCOUNT_TYPES = { asset:'أصول', liability:'خصوم', equity:'حقوق ملكية', revenue:'إيرادات', expense:'مصروفات' }
const EMPTY_ACCOUNT = { code:'', name_ar:'', name_en:'', account_type:'asset', normal_balance:'debit', parent_id:'', notes:'' }

const TABS = [
  { id:'trial', label:'ميزان المراجعة' },
  { id:'balance_sheet', label:'الميزانية العمومية' },
  { id:'income', label:'قائمة الدخل' },
  { id:'vat', label:'ضريبة القيمة المضافة' },
  { id:'taxes', label:'إدارة الضرائب' },
  { id:'entries', label:'دفتر القيود' },
  { id:'chart', label:'دليل الحسابات' },
  { id:'fixed_assets', label:'الأصول الثابتة' },
]

const ASSET_TYPES = {
  equipment: 'معدة', vehicle: 'سيارة', building: 'مبنى', furniture: 'أثاث', other: 'أخرى',
}

const EMPTY_ASSET = {
  name:'', asset_type:'equipment', purchase_date: new Date().toISOString().slice(0,10),
  cost:'', salvage_value:'0', useful_life_years:'5', location:'', notes:'', payment_account_code:'1110',
}

export default function Accounting() {
  const [tab, setTab] = useState('trial')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [showEntry, setShowEntry] = useState(false)
  const toast = useToast()
  const [acting, setActing] = useState(false)
  const act = async (fn) => {
    if (acting) return
    setActing(true)
    try { await fn() } catch (e) { toast(e.message || 'حدث خطأ', 'error') } finally { setActing(false) }
  }

  // فترة معكوسة (من > إلى) الباك بيرفضها بـ 400 — منبعتهاش ونوضح السبب بدل سيل أخطاء
  const rangeInvalid = !!(fromDate && toDate && fromDate > toDate)
  const qFrom = rangeInvalid ? '' : fromDate
  const qTo   = rangeInvalid ? '' : toDate

  const { data: trialBal, loading: tbLoading, reload: reloadTB } = useApi(
    () => api.accounts.trialBalance(dateParams(qFrom, qTo)),
    [tab === 'trial', qFrom, qTo]
  )
  const { data: balSheet, loading: bsLoading, reload: reloadBS } = useApi(
    () => api.accounts.balanceSheet(qTo ? { as_of_date: qTo } : undefined),
    [tab === 'balance_sheet', qTo]
  )
  const { data: income, loading: incLoading, reload: reloadIncome } = useApi(
    () => api.accounts.incomeStatement(dateParams(qFrom, qTo)),
    [tab === 'income', qFrom, qTo]
  )
  const { data: vat, loading: vatLoading } = useApi(
    () => api.accounts.vatReturn(dateParams(qFrom, qTo)),
    [tab === 'vat', qFrom, qTo]
  )
  const { data: entries, loading: entLoading, reload: reloadEntries } = useApi(
    () => api.accounts.entries(dateParams(qFrom, qTo)),
    [tab === 'entries', qFrom, qTo]
  )
  const { data: chart, loading: chartLoading, reload: reloadChart } = useApi(
    () => api.accounts.chart(), [tab === 'chart']
  )

  const [taxRate, setTaxRate] = useState('0.225')
  // الباك بيرفض أي نسبة خارج [0, 1] — منبعتش نسبة غلط مع كل ضغطة زرار
  const taxRateNum = Number(taxRate)
  const taxRateValid = taxRate !== '' && Number.isFinite(taxRateNum) && taxRateNum >= 0 && taxRateNum <= 1
  const effTaxRate = taxRateValid ? taxRate : '0.225'
  const { data: taxSummary, loading: taxLoading, reload: reloadTaxSummary } = useApi(
    () => api.accounts.taxSummary({ ...(dateParams(qFrom, qTo) || {}), income_tax_rate: effTaxRate }),
    [tab === 'taxes', qFrom, qTo, effTaxRate]
  )
  const [taxPayModal, setTaxPayModal] = useState(null) // { tax_type, label }
  const [taxPayForm, setTaxPayForm] = useState({ amount:'', payment_date: new Date().toISOString().slice(0,10), cash_account_code:'1110', notes:'' })
  const [showTaxProvision, setShowTaxProvision] = useState(false)
  const [taxProvisionForm, setTaxProvisionForm] = useState({ amount:'', provision_date: new Date().toISOString().slice(0,10), notes:'' })

  const { data: assets, loading: assetsLoading, reload: reloadAssets } = useApi(
    () => api.fixedAssets.list(), [tab === 'fixed_assets']
  )
  const [showAsset, setShowAsset] = useState(false)
  const [editingAsset, setEditingAsset] = useState(null)
  const [assetForm, setAssetForm] = useState(EMPTY_ASSET)
  const [depAsset, setDepAsset] = useState(null)
  const now = new Date()
  const [depPeriod, setDepPeriod] = useState({ period_year: now.getFullYear(), period_month: now.getMonth()+1 })

  // Entry form
  const [entryForm, setEntryForm] = useState({ entry_date: new Date().toISOString().slice(0,10), description:'', reference:'', lines:[] })
  const [newEntLine, setNewEntLine] = useState({ account_id:'', side:'debit', amount:'' })
  const [postTarget, setPostTarget] = useState(null)      // قيد مطلوب ترحيله (تأكيد)
  const [expandedEntry, setExpandedEntry] = useState(null) // عرض سطور قيد

  const addEntLine = () => {
    if (!newEntLine.account_id) { toast('اختر الحساب', 'error'); return }
    const amt = Number(newEntLine.amount)
    if (!newEntLine.amount || !Number.isFinite(amt) || toCents(amt) <= 0) { toast('أدخل مبلغاً أكبر من صفر', 'error'); return }
    setEntryForm(f => ({ ...f, lines: [...f.lines, { ...newEntLine, amount: (toCents(amt) / 100).toFixed(2) }] }))
    setNewEntLine({ account_id:'', side:'debit', amount:'' })
  }
  const removeEntLine = (idx) => setEntryForm(f => ({ ...f, lines: f.lines.filter((_, i) => i !== idx) }))

  const debitCents   = entryForm.lines.filter(l => l.side==='debit').reduce((s,l) => s+toCents(l.amount),0)
  const creditCents  = entryForm.lines.filter(l => l.side==='credit').reduce((s,l) => s+toCents(l.amount),0)
  const totalDebits  = debitCents / 100
  const totalCredits = creditCents / 100
  const hasBothSides = debitCents > 0 && creditCents > 0
  const isBalanced   = debitCents === creditCents

  const handleSaveEntry = async () => {
    if (!entryForm.entry_date) { toast('أدخل تاريخ القيد', 'error'); return }
    if (!entryForm.description.trim()) { toast('أدخل وصف القيد', 'error'); return }
    if (entryForm.description.trim().length > 400) { toast('وصف القيد لا يزيد عن 400 حرف', 'error'); return }
    if ((entryForm.reference || '').length > 100) { toast('المرجع لا يزيد عن 100 حرف', 'error'); return }
    if (entryForm.lines.length < 2) { toast('أضف سطرين على الأقل', 'error'); return }
    if (!hasBothSides) { toast('القيد يجب أن يحتوي على طرف مدين وطرف دائن', 'error'); return }
    if (!isBalanced) { toast('القيد غير متوازن — المدين لازم يساوي الدائن', 'error'); return }
    await act(async () => {
      await api.accounts.createEntry({
        entry_date: entryForm.entry_date,
        description: entryForm.description.trim(),
        reference: entryForm.reference?.trim() || null,
        lines: entryForm.lines.map(l => ({ account_id: Number(l.account_id), side: l.side, amount: String(l.amount) }))
      })
      toast('تم حفظ القيد — القيد غير مرحّل ولن يظهر في التقارير إلا بعد ترحيله')
      setShowEntry(false)
      setEntryForm({ entry_date: new Date().toISOString().slice(0,10), description:'', reference:'', lines:[] })
      reloadEntries()
    })
  }

  // ترحيل القيد — الـ API موجود لكن ماكانش فيه زرار؛ القيود اليدوية كانت بتفضل
  // غير مرحّلة للأبد ومش بتظهر في ميزان المراجعة/الميزانية/قائمة الدخل
  const handlePostEntry = async (e) => {
    await act(async () => {
      await api.accounts.postEntry(e.id)
      toast('تم ترحيل القيد')
      reloadEntries()
      reloadTB(); reloadBS(); reloadIncome()
    })
  }

  // ── حساب جديد في دليل الحسابات ──
  const [showAccount, setShowAccount] = useState(false)
  const [accountForm, setAccountForm] = useState(EMPTY_ACCOUNT)
  const handleSaveAccount = async () => {
    const code = accountForm.code.trim(), name = accountForm.name_ar.trim()
    if (!code) { toast('أدخل كود الحساب', 'error'); return }
    if (code.length > 20) { toast('كود الحساب لا يزيد عن 20 حرفاً', 'error'); return }
    if (!name) { toast('أدخل اسم الحساب', 'error'); return }
    if (name.length > 200) { toast('اسم الحساب لا يزيد عن 200 حرف', 'error'); return }
    if ((accountForm.name_en || '').length > 200) { toast('الاسم الإنجليزي لا يزيد عن 200 حرف', 'error'); return }
    if (chart?.some(a => a.code === code)) { toast(`كود الحساب ${code} موجود بالفعل`, 'error'); return }
    await act(async () => {
      await api.accounts.createAccount({
        code, name_ar: name, name_en: accountForm.name_en?.trim() || null,
        account_type: accountForm.account_type, normal_balance: accountForm.normal_balance,
        parent_id: accountForm.parent_id ? Number(accountForm.parent_id) : null,
        notes: accountForm.notes?.trim() || null,
      })
      toast('تم إضافة الحساب')
      setShowAccount(false)
      setAccountForm(EMPTY_ACCOUNT)
      reloadChart()
    })
  }

  const openTaxPayment = (tax_type, label, amount) => {
    setTaxPayForm({ amount: amount > 0 ? String(amount) : '', payment_date: new Date().toISOString().slice(0,10), cash_account_code:'1110', notes:'' })
    setTaxPayModal({ tax_type, label })
  }

  const handleTaxPayment = async () => {
    if (!taxPayForm.amount || Number(taxPayForm.amount) <= 0) { toast('أدخل مبلغاً صحيحاً', 'error'); return }
    await act(async () => {
      await api.accounts.recordTaxPayment({
        tax_type: taxPayModal.tax_type,
        amount: Number(taxPayForm.amount),
        payment_date: taxPayForm.payment_date || null,
        cash_account_code: taxPayForm.cash_account_code,
        notes: taxPayForm.notes || null,
      })
      toast('تم تسجيل السداد')
      setTaxPayModal(null)
      reloadTaxSummary(); reloadEntries(); reloadTB(); reloadBS()
    })
  }

  const handleTaxProvision = async () => {
    if (!taxProvisionForm.amount || Number(taxProvisionForm.amount) <= 0) { toast('أدخل مبلغاً صحيحاً', 'error'); return }
    await act(async () => {
      await api.accounts.recordTaxProvision({
        amount: Number(taxProvisionForm.amount),
        provision_date: taxProvisionForm.provision_date || null,
        notes: taxProvisionForm.notes || null,
      })
      toast('تم تسجيل المخصص')
      setShowTaxProvision(false)
      reloadTaxSummary(); reloadEntries(); reloadTB(); reloadBS(); reloadIncome()
    })
  }

  const handleSeedChart = async () => {
    await act(async () => {
      const r = await api.accounts.seed()
      toast(r.message || 'تم زرع دليل الحسابات')
      reloadChart()
    })
  }

  const openNewAsset = () => { setEditingAsset(null); setAssetForm({ ...EMPTY_ASSET, purchase_date: todayStr() }); setShowAsset(true) }
  const openEditAsset = (a) => {
    setEditingAsset(a)
    setAssetForm({
      name: a.name, asset_type: a.asset_type, purchase_date: a.purchase_date,
      cost: String(a.cost), salvage_value: String(a.salvage_value),
      useful_life_years: String(a.useful_life_years), location: a.location || '', notes: a.notes || '',
      payment_account_code: '1110',
    })
    setShowAsset(true)
  }

  const handleSaveAsset = async () => {
    if (!assetForm.name.trim()) { toast('أدخل اسم الأصل', 'error'); return }
    if (assetForm.name.trim().length > 200) { toast('اسم الأصل لا يزيد عن 200 حرف', 'error'); return }
    if (!assetForm.purchase_date) { toast('أدخل تاريخ الشراء', 'error'); return }
    if (assetForm.purchase_date > todayStr()) { toast('تاريخ الشراء لا يمكن أن يكون في المستقبل', 'error'); return }
    if (!assetForm.cost || !Number.isFinite(Number(assetForm.cost)) || Number(assetForm.cost) <= 0) { toast('أدخل تكلفة صحيحة', 'error'); return }
    if (assetForm.salvage_value !== '' && (!Number.isFinite(Number(assetForm.salvage_value)) || Number(assetForm.salvage_value) < 0)) { toast('قيمة الخردة لا يمكن أن تكون سالبة', 'error'); return }
    if (Number(assetForm.salvage_value || 0) >= Number(assetForm.cost)) { toast('قيمة الخردة يجب أن تكون أقل من التكلفة', 'error'); return }
    const life = Number(assetForm.useful_life_years)
    if (!assetForm.useful_life_years || !Number.isInteger(life) || life <= 0 || life > 100) { toast('العمر الإنتاجي لازم يكون رقم صحيح بين 1 و 100 سنة', 'error'); return }
    if ((assetForm.location || '').length > 200) { toast('الموقع لا يزيد عن 200 حرف', 'error'); return }
    if (editingAsset && Number(editingAsset.accumulated_depreciation) > 0
        && Number(assetForm.cost) - Number(assetForm.salvage_value || 0) < Number(editingAsset.accumulated_depreciation)) {
      toast('الإهلاك المُرحَّل أكبر من القيمة القابلة للإهلاك بعد التعديل', 'error'); return
    }
    await act(async () => {
      const payload = {
        name: assetForm.name.trim(), asset_type: assetForm.asset_type, purchase_date: assetForm.purchase_date,
        cost: assetForm.cost, salvage_value: assetForm.salvage_value === '' ? '0' : assetForm.salvage_value,
        useful_life_years: Number(assetForm.useful_life_years), location: assetForm.location || null, notes: assetForm.notes || null,
      }
      if (editingAsset) {
        // في التعديل مش بنبعت payment_account_code — الباك بيستخدم حساب الدفع الأصلي للشراء
        await api.fixedAssets.update(editingAsset.id, payload)
        toast('تم تعديل الأصل')
      } else {
        await api.fixedAssets.create({ ...payload, payment_account_code: assetForm.payment_account_code || '1110' })
        toast('تم إضافة الأصل')
      }
      setShowAsset(false)
      reloadAssets(); reloadEntries(); reloadTB(); reloadBS()
    })
  }

  const [deleteAssetTarget, setDeleteAssetTarget] = useState(null)
  const handleDeleteAsset = (a) => setDeleteAssetTarget(a)
  const doDeleteAsset = async (a) => {
    await act(async () => {
      await api.fixedAssets.delete(a.id)
      toast('تم الحذف')
      reloadAssets(); reloadEntries(); reloadTB(); reloadBS()
    })
  }

  // استبعاد/بيع — الباك بيقبل عائد البيع وحساب الاستلام وتاريخ الاستبعاد وبيرحّل
  // قيد الربح/الخسارة؛ الواجهة كانت بتبعت الطلب فاضي فكل استبعاد كان "إعدام بدون عائد"
  const [disposeAsset, setDisposeAsset] = useState(null)
  const [disposeForm, setDisposeForm] = useState({ sale_amount:'0', cash_account_code:'1110', disposal_date: todayStr() })
  const handleDisposeAsset = (a) => {
    setDisposeForm({ sale_amount:'0', cash_account_code:'1110', disposal_date: todayStr() })
    setDisposeAsset(a)
  }
  const doDisposeAsset = async () => {
    const a = disposeAsset
    const sale = disposeForm.sale_amount === '' ? 0 : Number(disposeForm.sale_amount)
    if (!Number.isFinite(sale) || sale < 0) { toast('عائد البيع لا يمكن أن يكون سالباً', 'error'); return }
    if (!disposeForm.disposal_date) { toast('أدخل تاريخ الاستبعاد', 'error'); return }
    if (disposeForm.disposal_date > todayStr()) { toast('تاريخ الاستبعاد لا يمكن أن يكون في المستقبل', 'error'); return }
    if (a.purchase_date && disposeForm.disposal_date < a.purchase_date) { toast('تاريخ الاستبعاد لا يمكن أن يكون قبل تاريخ الشراء', 'error'); return }
    await act(async () => {
      await api.fixedAssets.dispose(a.id, {
        sale_amount: String(sale), cash_account_code: disposeForm.cash_account_code, disposal_date: disposeForm.disposal_date,
      })
      toast('تم استبعاد الأصل وترحيل القيد')
      setDisposeAsset(null)
      reloadAssets(); reloadEntries(); reloadTB(); reloadBS(); reloadIncome()
    })
  }

  const handleRecordDepreciation = async () => {
    const y = Number(depPeriod.period_year), m = Number(depPeriod.period_month)
    if (!Number.isInteger(y) || y < 2000 || y > 2100) { toast('السنة خارج النطاق المسموح', 'error'); return }
    if (!Number.isInteger(m) || m < 1 || m > 12) { toast('الشهر لازم يكون بين 1 و 12', 'error'); return }
    const cur = new Date()
    if (y * 12 + m > cur.getFullYear() * 12 + cur.getMonth() + 1) { toast('لا يمكن تسجيل إهلاك لفترة مستقبلية', 'error'); return }
    if (depAsset.purchase_date) {
      const [py, pm] = depAsset.purchase_date.split('-').map(Number)
      if (y * 12 + m < py * 12 + pm) { toast('لا يمكن تسجيل إهلاك لفترة قبل تاريخ شراء الأصل', 'error'); return }
    }
    if (Number(depAsset.book_value) - Number(depAsset.salvage_value || 0) <= 0.004) { toast('الأصل مُهلك بالكامل — لا يوجد إهلاك متبقٍ', 'error'); return }
    await act(async () => {
      await api.fixedAssets.depreciate(depAsset.id, {
        period_year: Number(depPeriod.period_year), period_month: Number(depPeriod.period_month),
      })
      toast('تم تسجيل الإهلاك وترحيل القيد')
      setDepAsset(null)
      reloadAssets(); reloadEntries(); reloadTB(); reloadBS(); reloadIncome()
    })
  }

  // numberingSystem: 'latn' لضمان أرقام غربية (زي باقي الشاشات) بدل الأرقام الهندية
  // العربية اللي ممكن تظهر حسب لغة المتصفح، و maximumFractionDigits ثابت على 2
  // عشان ما نعرضش كسور غير مقصودة (مثلاً 12.345 بدل 12.35)
  const egp = (n) => Number(n || 0).toLocaleString('ar-EG', { minimumFractionDigits:2, maximumFractionDigits:2, numberingSystem:'latn' })

  return (
    <div className="p-4 max-w-6xl mx-auto" dir="rtl">
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16 }}>
        <h2 style={{ fontSize:20, fontWeight:700 }}>المحاسبة ودليل الحسابات</h2>
      </div>

      {/* Date filter */}
      <div style={{ display:'flex', flexWrap:'wrap', gap:12, marginBottom:16, alignItems:'center', background:'var(--surface)', padding:'10px 14px', borderRadius:8, border:'1px solid var(--border)' }}>
        <span style={{ fontWeight:600, fontSize:13 }}>الفترة:</span>
        <input type="date" value={fromDate} max={toDate || undefined} onChange={e => setFromDate(e.target.value)} style={{ padding:'4px 8px', border:'1px solid var(--border)', borderRadius:6 }} />
        <span>—</span>
        <input type="date" value={toDate} min={fromDate || undefined} onChange={e => setToDate(e.target.value)} style={{ padding:'4px 8px', border:'1px solid var(--border)', borderRadius:6 }} />
        <button className="btn-sm btn-secondary" onClick={() => { setFromDate(''); setToDate('') }}>مسح</button>
        {rangeInvalid && <span style={{ color:'var(--red)', fontSize:12, fontWeight:600 }}>تاريخ البداية بعد تاريخ النهاية — تم تجاهل الفلتر</span>}
        {tab === 'balance_sheet' && !rangeInvalid && <span style={{ color:'var(--text-secondary)', fontSize:12 }}>الميزانية تُحسب حتى تاريخ النهاية فقط</span>}
      </div>

      {/* Tabs */}
      <div style={{ display:'flex', flexWrap:'wrap', gap:4, marginBottom:16, borderBottom:'2px solid var(--border)' }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            style={{ padding:'8px 16px', border:'none', borderRadius:'6px 6px 0 0', cursor:'pointer', fontWeight: tab===t.id?700:400,
              background: tab===t.id ? 'var(--teal)' : 'var(--surface)',
              color: tab===t.id ? '#fff' : 'var(--text-secondary)', fontSize:13 }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Trial Balance ── */}
      {tab === 'trial' && (
        <div>
          {tbLoading ? <PageSpinner /> : trialBal ? (
            <>
              <div style={{ display:'flex', gap:16, marginBottom:16, flexWrap:'wrap' }}>
                <div style={{ background:'var(--teal)', color:'#fff', padding:'12px 20px', borderRadius:8 }}>
                  <div style={{ fontSize:11 }}>إجمالي المدين</div>
                  <div style={{ fontSize:22, fontWeight:700 }}>{egp(trialBal.totals?.debit)} ج</div>
                </div>
                <div style={{ background: trialBal.totals?.balanced ? 'var(--teal)' : 'var(--red)', color:'#fff', padding:'12px 20px', borderRadius:8 }}>
                  <div style={{ fontSize:11 }}>إجمالي الدائن</div>
                  <div style={{ fontSize:22, fontWeight:700 }}>{egp(trialBal.totals?.credit)} ج</div>
                </div>
                <div style={{ background: trialBal.totals?.balanced ? '#15803d' : 'var(--red)', color:'#fff', padding:'12px 20px', borderRadius:8 }}>
                  <div style={{ fontSize:13, fontWeight:700 }}>{trialBal.totals?.balanced ? '✓ متوازن' : '✗ غير متوازن'}</div>
                </div>
              </div>
              <div style={{ overflowX:'auto' }}>
              <table className="erp-table text-sm">
                <thead><tr><th>الكود</th><th>الحساب</th><th>النوع</th><th>مدين</th><th>دائن</th><th>الرصيد</th></tr></thead>
                <tbody>
                  {!trialBal.accounts?.length && <tr><td colSpan={6} style={{ textAlign:'center', padding:24, color:'var(--text-secondary)' }}>لا توجد حركات مرحّلة في هذه الفترة</td></tr>}
                  {trialBal.accounts?.map((a, i) => (
                    <tr key={a.account_id ?? i}>
                      <td style={{ fontWeight:600 }}>{a.code}</td>
                      <td>{a.name_ar}</td>
                      <td style={{ color:'var(--text-secondary)', fontSize:11 }}>{ACCOUNT_TYPES[a.account_type] || a.account_type}</td>
                      <td style={{ color: a.total_debit > 0 ? 'var(--teal)' : 'inherit' }}>{a.total_debit > 0 ? egp(a.total_debit) : '—'}</td>
                      <td style={{ color: a.total_credit > 0 ? '#b45309' : 'inherit' }}>{a.total_credit > 0 ? egp(a.total_credit) : '—'}</td>
                      <td style={{ fontWeight:600, color: a.is_abnormal ? 'var(--red)' : 'var(--teal)' }}>
                        {egp(a.raw_balance ?? a.balance)}
                        {a.is_abnormal && <span title="رصيد شاذ — خرج عن طبيعة الحساب (مدين/دائن)" style={{ marginRight:6, fontSize:11 }}>⚠️</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </>
          ) : <div style={{ color:'var(--text-secondary)', padding:32, textAlign:'center' }}>لا توجد بيانات — تأكد من زرع دليل الحسابات وتسجيل قيود</div>}
        </div>
      )}

      {/* ── Balance Sheet ── */}
      {tab === 'balance_sheet' && (
        <div>
          {bsLoading ? <PageSpinner /> : balSheet ? (
            <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(280px, 1fr))', gap:20 }}>
              {[['الأصول','assets','var(--teal)'], ['الخصوم وحقوق الملكية','liabilities','#b45309']].map(([title, key, color]) => (
                <div key={key} style={{ background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:16 }}>
                  <h3 style={{ fontWeight:700, color, marginBottom:12, borderBottom:'2px solid '+color, paddingBottom:6 }}>{title}</h3>
                  {Object.entries(balSheet[key] || {}).map(([acc, val]) => (
                    <div key={acc} style={{ display:'flex', justifyContent:'space-between', padding:'4px 0', borderBottom:'1px solid var(--border)', fontSize:13 }}>
                      <span>{acc}</span><span style={{ fontWeight:600 }}>{egp(val)}</span>
                    </div>
                  ))}
                  {key === 'liabilities' && Object.entries(balSheet.equity || {}).map(([acc, val]) => (
                    <div key={acc} style={{ display:'flex', justifyContent:'space-between', padding:'4px 0', borderBottom:'1px solid var(--border)', fontSize:13, color:'#6d28d9' }}>
                      <span>{acc}</span><span style={{ fontWeight:600 }}>{egp(val)}</span>
                    </div>
                  ))}
                </div>
              ))}
              <div style={{ gridColumn:'1/-1', background: balSheet.totals?.balanced ? '#f0fdf4' : '#fef2f2', border:'1px solid var(--border)', borderRadius:8, padding:16 }}>
                <div style={{ display:'flex', flexWrap:'wrap', gap:32, justifyContent:'center' }}>
                  <span><b>إجمالي الأصول:</b> {egp(balSheet.totals?.total_assets)} ج</span>
                  <span><b>الخصوم + حقوق الملكية:</b> {egp((balSheet.totals?.total_liabilities||0) + (balSheet.totals?.total_equity||0))} ج</span>
                  <span style={{ fontWeight:700, color: balSheet.totals?.balanced ? '#15803d' : 'var(--red)' }}>
                    {balSheet.totals?.balanced ? '✓ الميزانية متوازنة' : '✗ غير متوازنة'}
                  </span>
                </div>
                {Array.isArray(balSheet.abnormal_accounts) && balSheet.abnormal_accounts.length > 0 && (
                  <div style={{ marginTop:10, fontSize:12, color:'var(--red)', textAlign:'center' }}>
                    ⚠️ حسابات برصيد شاذ (عكس طبيعتها): {balSheet.abnormal_accounts.map(x => typeof x === 'object' && x ? `${x.code ?? ''} ${x.name_ar ?? x.name ?? ''}`.trim() : String(x)).join('، ')}
                  </div>
                )}
              </div>
            </div>
          ) : <div style={{ padding:32, textAlign:'center', color:'var(--text-secondary)' }}>لا توجد بيانات</div>}
        </div>
      )}

      {/* ── Income Statement ── */}
      {tab === 'income' && (
        <div>
          {incLoading ? <PageSpinner /> : income ? (
            <div>
              <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(200px, 1fr))', gap:16, marginBottom:20 }}>
                {[['الإيرادات', income.revenue, 'var(--teal)'], ['المصروفات', income.expenses, 'var(--red)'], ['صافي الربح', income.net_income, income.net_income >= 0 ? '#15803d' : 'var(--red)']].map(([lbl,val,color]) => (
                  <div key={lbl} style={{ background:'var(--surface)', border:`2px solid ${color}`, borderRadius:8, padding:'14px 16px', textAlign:'center' }}>
                    <div style={{ fontSize:12, color:'var(--text-secondary)' }}>{lbl}</div>
                    <div style={{ fontSize:24, fontWeight:700, color }}>{egp(val)} ج</div>
                  </div>
                ))}
              </div>
              <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(280px, 1fr))', gap:20 }}>
                {[['الإيرادات', income.revenue_detail, 'var(--teal)'], ['المصروفات', income.expense_detail, 'var(--red)']].map(([title, items, color]) => (
                  <div key={title} style={{ background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:14 }}>
                    <h4 style={{ fontWeight:700, color, marginBottom:8 }}>{title}</h4>
                    {!Object.keys(items || {}).length && <div style={{ fontSize:12, color:'var(--text-secondary)' }}>لا توجد بنود</div>}
                    {Object.entries(items || {}).map(([acc, val]) => (
                      <div key={acc} style={{ display:'flex', justifyContent:'space-between', fontSize:12, padding:'3px 0', borderBottom:'1px dashed var(--border)' }}>
                        <span>{acc}</span><span style={{ fontWeight:600 }}>{egp(val)}</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          ) : <div style={{ padding:32, textAlign:'center', color:'var(--text-secondary)' }}>لا توجد بيانات</div>}
        </div>
      )}

      {/* ── VAT Return ── */}
      {tab === 'vat' && (
        <div>
          {vatLoading ? <PageSpinner /> : vat ? (
            <div>
              <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(220px, 1fr))', gap:16, marginBottom:20 }}>
                <div style={{ background:'var(--surface)', border:'1px solid var(--teal)', borderRadius:8, padding:16 }}>
                  <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:4 }}>{vat.output_vat?.description}</div>
                  <div style={{ fontSize:22, fontWeight:700, color:'var(--teal)' }}>{egp(vat.output_vat?.vat_amount)} ج</div>
                  <div style={{ fontSize:11, color:'var(--text-secondary)' }}>على مبيعات: {egp(vat.output_vat?.taxable_amount)} ج — {vat.output_vat?.invoice_count} فاتورة</div>
                </div>
                <div style={{ background:'var(--surface)', border:'1px solid #b45309', borderRadius:8, padding:16 }}>
                  <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:4 }}>{vat.input_vat?.description}</div>
                  <div style={{ fontSize:22, fontWeight:700, color:'#b45309' }}>{egp(vat.input_vat?.vat_amount)} ج</div>
                  <div style={{ fontSize:11, color:'var(--text-secondary)' }}>على مشتريات: {egp(vat.input_vat?.taxable_amount)} ج — {vat.input_vat?.invoice_count} فاتورة</div>
                </div>
                <div style={{ background: vat.net_vat_due > 0 ? '#fef2f2' : '#f0fdf4', border:`1px solid ${vat.net_vat_due > 0 ? 'var(--red)' : '#15803d'}`, borderRadius:8, padding:16 }}>
                  <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:4 }}>صافي الضريبة المستحقة</div>
                  <div style={{ fontSize:26, fontWeight:700, color: vat.net_vat_due > 0 ? 'var(--red)' : '#15803d' }}>{egp(vat.net_vat_due)} ج</div>
                  <div style={{ fontSize:13, fontWeight:600, color: vat.net_vat_due > 0 ? 'var(--red)' : '#15803d' }}>{vat.status_ar}</div>
                </div>
              </div>
            </div>
          ) : <div style={{ padding:32, textAlign:'center', color:'var(--text-secondary)' }}>لا توجد بيانات</div>}
        </div>
      )}

      {/* ── Tax Management ── */}
      {tab === 'taxes' && (
        <div>
          {taxLoading ? <PageSpinner /> : taxSummary ? (
            <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(240px, 1fr))', gap:16 }}>
              <div style={{ background:'var(--surface)', border:`2px solid ${taxSummary.vat?.net_vat_due > 0 ? 'var(--red)' : '#15803d'}`, borderRadius:8, padding:16 }}>
                <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:4 }}>ضريبة القيمة المضافة المستحقة (صافي)</div>
                <div style={{ fontSize:24, fontWeight:700, color: taxSummary.vat?.net_vat_due > 0 ? 'var(--red)' : '#15803d' }}>{egp(taxSummary.vat?.net_vat_due)} ج</div>
                <div style={{ fontSize:12, fontWeight:600, marginBottom:10 }}>{taxSummary.vat?.status_ar}</div>
                <button className="btn-secondary" disabled={!(taxSummary.vat?.net_vat_due > 0)}
                  onClick={() => openTaxPayment('vat', 'ضريبة القيمة المضافة', taxSummary.vat?.net_vat_due)}>تسجيل السداد</button>
              </div>

              <div style={{ background:'var(--surface)', border:`2px solid ${taxSummary.wht_payable?.balance > 0 ? 'var(--red)' : '#15803d'}`, borderRadius:8, padding:16 }}>
                <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:4 }}>ضريبة الخصم تحت الحساب المستحقة (WHT)</div>
                <div style={{ fontSize:24, fontWeight:700, color: taxSummary.wht_payable?.balance > 0 ? 'var(--red)' : '#15803d' }}>{egp(taxSummary.wht_payable?.balance)} ج</div>
                <div style={{ marginBottom:10 }}>&nbsp;</div>
                <button className="btn-secondary" disabled={!(taxSummary.wht_payable?.balance > 0)}
                  onClick={() => openTaxPayment('wht', 'ضريبة الخصم تحت الحساب', taxSummary.wht_payable?.balance)}>تسجيل السداد</button>
              </div>

              <div style={{ background:'var(--surface)', border:'2px solid var(--teal)', borderRadius:8, padding:16 }}>
                <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:4 }}>تقدير ضريبة الدخل</div>
                <div style={{ fontSize:24, fontWeight:700, color:'var(--teal)' }}>{egp(taxSummary.income_tax?.estimate)} ج</div>
                <div style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, marginBottom:10 }}>
                  <span>صافي الربح: {egp(taxSummary.income_tax?.net_income)} ج — النسبة:</span>
                  <input type="number" step="0.001" min="0" max="1" value={taxRate} onChange={e => setTaxRate(e.target.value)} style={{ width:60, padding:'2px 4px', borderColor: taxRateValid ? undefined : 'var(--red)' }} />
                </div>
                {!taxRateValid && <div style={{ color:'var(--red)', fontSize:11, marginBottom:8 }}>النسبة لازم تكون بين 0 و 1 (مثلاً 0.225 = 22.5%) — يتم الحساب بالنسبة الافتراضية</div>}
                <div style={{ display:'flex', gap:8 }}>
                  <button className="btn-secondary" disabled={!(taxSummary.income_tax?.estimate > 0)}
                    onClick={() => { setTaxProvisionForm({ amount: String(taxSummary.income_tax?.estimate), provision_date: new Date().toISOString().slice(0,10), notes:'' }); setShowTaxProvision(true) }}>تسجيل المخصص</button>
                  <button className="btn-secondary"
                    onClick={() => openTaxPayment('income_tax', 'ضريبة الدخل', 0)}>تسجيل السداد</button>
                </div>
              </div>

              <div style={{ gridColumn:'1 / -1', background:'var(--teal-lt)', borderRadius:8, padding:16, textAlign:'center' }}>
                <div style={{ fontSize:13, color:'var(--text-secondary)' }}>إجمالي الضرائب المستحقة</div>
                <div style={{ fontSize:28, fontWeight:700, color:'var(--teal)' }}>{egp(taxSummary.totals?.total_due)} ج</div>
              </div>
            </div>
          ) : <div style={{ padding:32, textAlign:'center', color:'var(--text-secondary)' }}>لا توجد بيانات</div>}
        </div>
      )}

      {/* ── Journal Entries ── */}
      {tab === 'entries' && (
        <div>
          <div style={{ marginBottom:12, display:'flex', justifyContent:'flex-end' }}>
            <button className="btn-primary" onClick={() => setShowEntry(true)}>+ قيد جديد</button>
          </div>
          {entLoading ? <PageSpinner /> : (
            <div style={{ overflowX:'auto' }}>
            <table className="erp-table text-sm">
              <thead><tr><th>التاريخ</th><th>الوصف</th><th>المصدر</th><th>مدين</th><th>دائن</th><th>مرحّل</th></tr></thead>
              <tbody>
                {!entries?.length && <tr><td colSpan={6} style={{ textAlign:'center', padding:24, color:'var(--text-secondary)' }}>لا توجد قيود</td></tr>}
                {entries?.map(e => (
                  <FragmentRow key={e.id}>
                  <tr>
                    <td>{e.entry_date}</td>
                    <td>
                      <button type="button" onClick={() => setExpandedEntry(x => x === e.id ? null : e.id)}
                        style={{ background:'none', border:'none', padding:0, cursor:'pointer', color:'inherit', textAlign:'right' }}
                        title="عرض بنود القيد">
                        {expandedEntry === e.id ? '▾ ' : '▸ '}{e.description}
                      </button>
                      {e.reference ? <div style={{ fontSize:11, color:'var(--text-secondary)' }}>مرجع: {e.reference}</div> : null}
                    </td>
                    <td style={{ fontSize:11, color:'var(--text-secondary)' }}>{e.source}</td>
                    <td style={{ color:'var(--teal)', fontWeight:600 }}>{egp(e.total_debit)}</td>
                    <td style={{ color:'#b45309', fontWeight:600 }}>{egp(e.total_credit)}</td>
                    <td>{e.posted ? <span style={{ color:'#15803d', fontWeight:700 }}>✓</span> : (
                      <span style={{ display:'inline-flex', gap:6, alignItems:'center' }}>
                        <span style={{ color:'var(--text-secondary)' }}>—</span>
                        <button className="btn-sm btn-secondary" disabled={acting} onClick={() => setPostTarget(e)}>ترحيل</button>
                      </span>
                    )}</td>
                  </tr>
                  {expandedEntry === e.id && (
                    <tr>
                      <td colSpan={6} style={{ background:'var(--bg)' }}>
                        {!e.lines?.length ? <span style={{ color:'var(--text-secondary)', fontSize:12 }}>لا توجد بنود</span> : (
                          <table className="erp-table text-sm">
                            <thead><tr><th>الحساب</th><th>مدين</th><th>دائن</th><th>ملاحظات</th></tr></thead>
                            <tbody>
                              {e.lines.map(l => {
                                const acc = chart?.find(a => a.id === l.account_id)
                                return (
                                  <tr key={l.id}>
                                    <td>{acc ? `${acc.code} — ${acc.name_ar}` : `#${l.account_id}`}</td>
                                    <td style={{ color:'var(--teal)' }}>{l.side === 'debit' ? egp(l.amount) : '—'}</td>
                                    <td style={{ color:'#b45309' }}>{l.side === 'credit' ? egp(l.amount) : '—'}</td>
                                    <td style={{ fontSize:11, color:'var(--text-secondary)' }}>{l.notes || ''}</td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                        )}
                      </td>
                    </tr>
                  )}
                  </FragmentRow>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>
      )}

      {/* ── Chart of Accounts ── */}
      {tab === 'chart' && (
        <div>
          <div style={{ marginBottom:12, display:'flex', gap:8, justifyContent:'flex-end' }}>
            <button className="btn-primary" onClick={() => { setAccountForm(EMPTY_ACCOUNT); setShowAccount(true) }}>+ حساب جديد</button>
            <button className="btn-secondary" onClick={handleSeedChart} disabled={acting}>{acting ? '…' : 'زرع الحسابات الافتراضية'}</button>
          </div>
          {chartLoading ? <PageSpinner /> : (
            <table className="erp-table text-sm">
              <thead><tr><th>الكود</th><th>اسم الحساب</th><th>النوع</th><th>الرصيد الطبيعي</th></tr></thead>
              <tbody>
                {!chart?.length && <tr><td colSpan={4} style={{ textAlign:'center', padding:24, color:'var(--text-secondary)' }}>لا توجد حسابات — اضغط "زرع الحسابات الافتراضية"</td></tr>}
                {chart?.map(a => (
                  <tr key={a.id}>
                    <td style={{ fontWeight:600, color:'var(--teal)', fontFamily:'monospace' }}>{a.code}</td>
                    <td style={{ paddingRight: a.parent_id ? 24 : 8 }}>{a.parent_id ? '└ ' : ''}{a.name_ar}</td>
                    <td><span style={{ fontSize:11, padding:'2px 6px', borderRadius:4, background:'var(--teal-lt)', color:'var(--teal)' }}>{ACCOUNT_TYPES[a.account_type] || a.account_type}</span></td>
                    <td>{a.normal_balance === 'debit' ? 'مدين' : 'دائن'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ── Fixed Assets ── */}
      {tab === 'fixed_assets' && (
        <div>
          <div style={{ marginBottom:12, display:'flex', justifyContent:'flex-end' }}>
            <button className="btn-primary" onClick={openNewAsset}>+ أصل جديد</button>
          </div>
          {assetsLoading ? <PageSpinner /> : (
            <div style={{ overflowX:'auto' }}>
            <table className="erp-table text-sm">
              <thead>
                <tr>
                  <th>الكود</th><th>الاسم</th><th>النوع</th><th>تاريخ الشراء</th><th>التكلفة</th>
                  <th>مجمع الإهلاك</th><th>القيمة الدفترية</th><th>إهلاك شهري</th><th>الحالة</th><th>إجراءات</th>
                </tr>
              </thead>
              <tbody>
                {!assets?.length && <tr><td colSpan={10} style={{ textAlign:'center', padding:24, color:'var(--text-secondary)' }}>لا توجد أصول ثابتة مسجلة</td></tr>}
                {assets?.map(a => (
                  <tr key={a.id}>
                    <td style={{ fontWeight:600, fontFamily:'monospace' }}>{a.code}</td>
                    <td>{a.name}{a.location ? <div style={{ fontSize:11, color:'var(--text-secondary)' }}>{a.location}</div> : null}</td>
                    <td>{ASSET_TYPES[a.asset_type] || a.asset_type}</td>
                    <td>{a.purchase_date}</td>
                    <td>{egp(a.cost)}</td>
                    <td style={{ color:'#b45309', fontWeight:600 }}>{egp(a.accumulated_depreciation)}</td>
                    <td style={{ fontWeight:600, color:'var(--teal)' }}>{egp(a.book_value)}</td>
                    <td>{egp(a.monthly_depreciation)}</td>
                    <td>
                      {a.status === 'active'
                        ? <span style={{ fontSize:11, padding:'2px 6px', borderRadius:4, background:'#f0fdf4', color:'#15803d' }}>نشط</span>
                        : <span style={{ fontSize:11, padding:'2px 6px', borderRadius:4, background:'#fef2f2', color:'var(--red)' }}>مستبعد</span>}
                    </td>
                    <td>
                      <div style={{ display:'flex', gap:4, flexWrap:'wrap' }}>
                        {a.status === 'active' && (
                          <button className="btn-sm btn-secondary" onClick={() => { setDepAsset(a); setDepPeriod({ period_year: now.getFullYear(), period_month: now.getMonth()+1 }) }}>تسجيل إهلاك</button>
                        )}
                        <button className="btn-sm btn-secondary" onClick={() => openEditAsset(a)}>تعديل</button>
                        {a.status === 'active' && <button className="btn-sm btn-secondary" onClick={() => handleDisposeAsset(a)}>استبعاد</button>}
                        <button className="btn-sm btn-secondary" style={{ color:'var(--red)' }} disabled={acting} onClick={() => handleDeleteAsset(a)}>حذف</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>
      )}

      {/* Add/Edit Asset Modal */}
      {showAsset && (
        <Modal title={editingAsset ? 'تعديل أصل ثابت' : 'أصل ثابت جديد'} onClose={() => setShowAsset(false)} size="lg">
          <FormGrid>
            <Input label="اسم الأصل" value={assetForm.name} onChange={e => setAssetForm(f => ({ ...f, name: e.target.value }))} span={2} />
            <Select label="نوع الأصل" value={assetForm.asset_type} onChange={e => setAssetForm(f => ({ ...f, asset_type: e.target.value }))}>
              {Object.entries(ASSET_TYPES).map(([k,v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
            <Input label="تاريخ الشراء" type="date" max={todayStr()} value={assetForm.purchase_date} onChange={e => setAssetForm(f => ({ ...f, purchase_date: e.target.value }))} />
            <Input label="التكلفة" type="number" min="0" step="0.01" value={assetForm.cost} onChange={e => setAssetForm(f => ({ ...f, cost: e.target.value }))} />
            <Input label="قيمة الخردة" type="number" min="0" step="0.01" value={assetForm.salvage_value} onChange={e => setAssetForm(f => ({ ...f, salvage_value: e.target.value }))} />
            <Input label="العمر الإنتاجي (سنوات)" type="number" min="1" max="100" step="1" value={assetForm.useful_life_years} onChange={e => setAssetForm(f => ({ ...f, useful_life_years: e.target.value }))} />
            <Input label="الموقع" value={assetForm.location} onChange={e => setAssetForm(f => ({ ...f, location: e.target.value }))} />
            {!editingAsset && (
              <Select label="تم الدفع من حساب" value={assetForm.payment_account_code} onChange={e => setAssetForm(f => ({ ...f, payment_account_code: e.target.value }))}>
                <option value="1110">الصندوق (1110)</option>
                <option value="1120">البنك (1120)</option>
              </Select>
            )}
            <Textarea label="ملاحظات" value={assetForm.notes} onChange={e => setAssetForm(f => ({ ...f, notes: e.target.value }))} span={2} />
          </FormGrid>
          <ModalFooter>
            <button className="btn-primary" onClick={handleSaveAsset} disabled={acting}>{acting ? '…' : 'حفظ'}</button>
            <button className="btn-secondary" onClick={() => setShowAsset(false)}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      {/* Record Depreciation Modal */}
      {depAsset && (
        <Modal title={`تسجيل إهلاك — ${depAsset.name}`} onClose={() => setDepAsset(null)}>
          <div style={{ marginBottom:12, fontSize:13, color:'var(--text-secondary)' }}>
            الإهلاك الشهري المحتسب: <b style={{ color:'var(--teal)' }}>{egp(depAsset.monthly_depreciation)} ج</b>
          </div>
          <FormGrid>
            <Input label="السنة" type="number" min="2000" max="2100" value={depPeriod.period_year} onChange={e => setDepPeriod(p => ({ ...p, period_year: e.target.value }))} />
            <Input label="الشهر" type="number" min="1" max="12" value={depPeriod.period_month} onChange={e => setDepPeriod(p => ({ ...p, period_month: e.target.value }))} />
          </FormGrid>
          <ModalFooter>
            <button className="btn-primary" onClick={handleRecordDepreciation} disabled={acting}>{acting ? '…' : 'تسجيل وترحيل القيد'}</button>
            <button className="btn-secondary" onClick={() => setDepAsset(null)}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      {/* Tax Payment Modal */}
      {taxPayModal && (
        <Modal title={`تسجيل سداد — ${taxPayModal.label}`} onClose={() => setTaxPayModal(null)}>
          <FormGrid>
            <Input label="المبلغ" type="number" min="0" step="0.01" value={taxPayForm.amount} onChange={e => setTaxPayForm(f => ({ ...f, amount: e.target.value }))} />
            <Input label="تاريخ السداد" type="date" value={taxPayForm.payment_date} onChange={e => setTaxPayForm(f => ({ ...f, payment_date: e.target.value }))} />
            <Select label="من حساب" value={taxPayForm.cash_account_code} onChange={e => setTaxPayForm(f => ({ ...f, cash_account_code: e.target.value }))}>
              <option value="1110">الصندوق (1110)</option>
              <option value="1120">البنك (1120)</option>
            </Select>
            <Textarea label="ملاحظات" value={taxPayForm.notes} onChange={e => setTaxPayForm(f => ({ ...f, notes: e.target.value }))} span={2} />
          </FormGrid>
          <ModalFooter>
            <button className="btn-primary" onClick={handleTaxPayment} disabled={acting}>{acting ? '…' : 'تسجيل السداد'}</button>
            <button className="btn-secondary" onClick={() => setTaxPayModal(null)}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      {/* Tax Provision Modal */}
      {showTaxProvision && (
        <Modal title="تسجيل مخصص ضريبة الدخل" onClose={() => setShowTaxProvision(false)}>
          <FormGrid>
            <Input label="المبلغ" type="number" min="0" step="0.01" value={taxProvisionForm.amount} onChange={e => setTaxProvisionForm(f => ({ ...f, amount: e.target.value }))} />
            <Input label="التاريخ" type="date" value={taxProvisionForm.provision_date} onChange={e => setTaxProvisionForm(f => ({ ...f, provision_date: e.target.value }))} />
            <Textarea label="ملاحظات" value={taxProvisionForm.notes} onChange={e => setTaxProvisionForm(f => ({ ...f, notes: e.target.value }))} span={2} />
          </FormGrid>
          <ModalFooter>
            <button className="btn-primary" onClick={handleTaxProvision} disabled={acting}>{acting ? '…' : 'تسجيل المخصص'}</button>
            <button className="btn-secondary" onClick={() => setShowTaxProvision(false)}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      {/* New Entry Modal */}
      {showEntry && (
        <Modal title="قيد محاسبي جديد" onClose={() => setShowEntry(false)} size="lg">
          <FormGrid>
            <Input label="التاريخ" type="date" value={entryForm.entry_date} onChange={e => setEntryForm(f => ({ ...f, entry_date: e.target.value }))} />
            <Input label="الوصف" value={entryForm.description} maxLength={400} onChange={e => setEntryForm(f => ({ ...f, description: e.target.value }))} span={2} />
            <Input label="المرجع (اختياري)" value={entryForm.reference} maxLength={100} onChange={e => setEntryForm(f => ({ ...f, reference: e.target.value }))} />
          </FormGrid>
          <div style={{ marginTop:12, padding:12, background:'var(--bg)', borderRadius:8 }}>
            <h4 style={{ marginBottom:8, fontWeight:600 }}>بنود القيد:</h4>
            {!chart?.length && <div style={{ color:'var(--red)', fontSize:12, marginBottom:8 }}>لا توجد حسابات — ازرع دليل الحسابات أولاً من تبويب "دليل الحسابات"</div>}
            <div style={{ display:'grid', gridTemplateColumns:'3fr 1fr 2fr auto', gap:8, alignItems:'end' }}>
              <Select label="الحساب" value={newEntLine.account_id} onChange={e => setNewEntLine(n => ({ ...n, account_id: e.target.value }))}>
                <option value="">— اختر حساباً —</option>
                {chart?.map(a => <option key={a.id} value={a.id}>{a.code} — {a.name_ar}</option>)}
              </Select>
              <Select label="الجانب" value={newEntLine.side} onChange={e => setNewEntLine(n => ({ ...n, side: e.target.value }))}>
                <option value="debit">مدين</option>
                <option value="credit">دائن</option>
              </Select>
              <Input label="المبلغ" type="number" min="0" step="0.01" value={newEntLine.amount} onChange={e => setNewEntLine(n => ({ ...n, amount: e.target.value }))} />
              <button className="btn-teal" style={{ padding:'8px 14px' }} onClick={addEntLine}>+</button>
            </div>
            {entryForm.lines.length > 0 && (
              <table className="erp-table text-sm" style={{ marginTop:8 }}>
                <thead><tr><th>الحساب</th><th>مدين</th><th>دائن</th><th></th></tr></thead>
                <tbody>
                  {entryForm.lines.map((l,i) => {
                    const acc = chart?.find(a => String(a.id) === String(l.account_id))
                    return (
                      <tr key={i}>
                        <td>{acc?.code} — {acc?.name_ar}</td>
                        <td style={{ color:'var(--teal)', fontWeight:600 }}>{l.side==='debit' ? Number(l.amount).toFixed(2) : '—'}</td>
                        <td style={{ color:'#b45309', fontWeight:600 }}>{l.side==='credit' ? Number(l.amount).toFixed(2) : '—'}</td>
                        <td><button type="button" className="btn-sm btn-secondary" style={{ color:'var(--red)' }} title="حذف البند" onClick={() => removeEntLine(i)}>✕</button></td>
                      </tr>
                    )
                  })}
                  <tr style={{ background:'var(--teal-lt)', fontWeight:700 }}>
                    <td>الإجمالي</td>
                    <td style={{ color:'var(--teal)' }}>{totalDebits.toFixed(2)}</td>
                    <td style={{ color:'#b45309' }}>{totalCredits.toFixed(2)}</td>
                    <td></td>
                  </tr>
                </tbody>
              </table>
            )}
            {isBalanced && !hasBothSides && entryForm.lines.length >= 2 && (
              <div style={{ color:'var(--red)', fontWeight:600, marginTop:8, fontSize:13 }}>
                ⚠️ القيد يجب أن يحتوي على طرف مدين وطرف دائن
              </div>
            )}
            {!isBalanced && entryForm.lines.length >= 2 && (
              <div style={{ color:'var(--red)', fontWeight:600, marginTop:8, fontSize:13 }}>
                ⚠️ القيد غير متوازن — الفرق: {Math.abs(totalDebits - totalCredits).toFixed(2)} ج
              </div>
            )}
          </div>
          <ModalFooter>
            <button className="btn-primary" onClick={handleSaveEntry} disabled={!isBalanced || !hasBothSides || entryForm.lines.length < 2 || acting}>{acting ? '…' : 'حفظ القيد'}</button>
            <button className="btn-secondary" onClick={() => setShowEntry(false)}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      {/* New Account Modal */}
      {showAccount && (
        <Modal title="حساب جديد في دليل الحسابات" onClose={() => setShowAccount(false)} size="lg">
          <FormGrid>
            <Input label="كود الحساب" value={accountForm.code} maxLength={20} onChange={e => setAccountForm(f => ({ ...f, code: e.target.value }))} />
            <Input label="اسم الحساب (عربي)" value={accountForm.name_ar} maxLength={200} onChange={e => setAccountForm(f => ({ ...f, name_ar: e.target.value }))} />
            <Input label="الاسم الإنجليزي (اختياري)" value={accountForm.name_en} maxLength={200} onChange={e => setAccountForm(f => ({ ...f, name_en: e.target.value }))} />
            <Select label="نوع الحساب" value={accountForm.account_type}
              onChange={e => { const t = e.target.value; setAccountForm(f => ({ ...f, account_type: t, normal_balance: (t === 'asset' || t === 'expense') ? 'debit' : 'credit' })) }}>
              {Object.entries(ACCOUNT_TYPES).map(([k,v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
            <Select label="الرصيد الطبيعي" value={accountForm.normal_balance} onChange={e => setAccountForm(f => ({ ...f, normal_balance: e.target.value }))}>
              <option value="debit">مدين</option>
              <option value="credit">دائن</option>
            </Select>
            <Select label="الحساب الأب (اختياري)" value={accountForm.parent_id} onChange={e => setAccountForm(f => ({ ...f, parent_id: e.target.value }))}>
              <option value="">— بدون —</option>
              {chart?.map(a => <option key={a.id} value={a.id}>{a.code} — {a.name_ar}</option>)}
            </Select>
            <Textarea label="ملاحظات" value={accountForm.notes} onChange={e => setAccountForm(f => ({ ...f, notes: e.target.value }))} span={2} />
          </FormGrid>
          <ModalFooter>
            <button className="btn-primary" onClick={handleSaveAccount} disabled={acting}>{acting ? '…' : 'حفظ'}</button>
            <button className="btn-secondary" onClick={() => setShowAccount(false)}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      {/* Dispose Asset Modal */}
      {disposeAsset && (
        <Modal title={`استبعاد / بيع أصل — ${disposeAsset.name}`} onClose={() => setDisposeAsset(null)}>
          <div style={{ marginBottom:12, fontSize:13, color:'var(--text-secondary)' }}>
            القيمة الدفترية الحالية: <b style={{ color:'var(--teal)' }}>{egp(disposeAsset.book_value)} ج</b>
            {' — '}سيتم ترحيل قيد الاستبعاد وإثبات الربح/الخسارة تلقائياً. هذه العملية لا يمكن التراجع عنها.
          </div>
          <FormGrid>
            <Input label="عائد البيع (0 = إعدام بدون عائد)" type="number" min="0" step="0.01" value={disposeForm.sale_amount} onChange={e => setDisposeForm(f => ({ ...f, sale_amount: e.target.value }))} />
            <Input label="تاريخ الاستبعاد" type="date" min={disposeAsset.purchase_date || undefined} max={todayStr()} value={disposeForm.disposal_date} onChange={e => setDisposeForm(f => ({ ...f, disposal_date: e.target.value }))} />
            {Number(disposeForm.sale_amount) > 0 && (
              <Select label="استلام العائد في" value={disposeForm.cash_account_code} onChange={e => setDisposeForm(f => ({ ...f, cash_account_code: e.target.value }))}>
                <option value="1110">الصندوق (1110)</option>
                <option value="1120">البنك (1120)</option>
              </Select>
            )}
          </FormGrid>
          {Number(disposeForm.sale_amount) >= 0 && disposeForm.sale_amount !== '' && (
            <div style={{ marginTop:10, fontSize:12, fontWeight:600, color: Number(disposeForm.sale_amount) - Number(disposeAsset.book_value) >= 0 ? '#15803d' : 'var(--red)' }}>
              {Number(disposeForm.sale_amount) - Number(disposeAsset.book_value) >= 0 ? 'ربح استبعاد' : 'خسارة استبعاد'}: {egp(Math.abs(Number(disposeForm.sale_amount) - Number(disposeAsset.book_value)))} ج
            </div>
          )}
          <ModalFooter>
            <button className="btn-primary" onClick={doDisposeAsset} disabled={acting}>{acting ? '…' : 'تأكيد الاستبعاد'}</button>
            <button className="btn-secondary" onClick={() => setDisposeAsset(null)}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}

      <ConfirmDialog
        open={!!deleteAssetTarget}
        onClose={() => setDeleteAssetTarget(null)}
        onConfirm={() => deleteAssetTarget && doDeleteAsset(deleteAssetTarget)}
        title="حذف أصل ثابت"
        message={deleteAssetTarget ? `حذف الأصل "${deleteAssetTarget.name}"؟ سيتم عكس قيد الشراء المرتبط به.` : ''}
        confirmLabel="حذف"
        danger
      />

      <ConfirmDialog
        open={!!postTarget}
        onClose={() => setPostTarget(null)}
        onConfirm={() => postTarget && handlePostEntry(postTarget)}
        title="ترحيل القيد"
        message={postTarget ? `ترحيل القيد "${postTarget.description}"؟ بعد الترحيل لا يمكن التعديل وسيظهر في ميزان المراجعة والتقارير.` : ''}
        confirmLabel="ترحيل"
      />
    </div>
  )
}

// صف مجمّع بدون عنصر DOM إضافي (عشان key على مجموعة صفوف جدول)
function FragmentRow({ children }) { return <>{children}</> }
