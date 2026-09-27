import { useState } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useToast } from '../store/toast'
import api from '../api/client'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'

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
  cost:'', salvage_value:'0', useful_life_years:'5', location:'', notes:'',
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

  const { data: trialBal, loading: tbLoading, reload: reloadTB } = useApi(
    () => api.accounts.trialBalance(fromDate || toDate ? { from_date: fromDate, to_date: toDate } : {}),
    [tab === 'trial', fromDate, toDate]
  )
  const { data: balSheet, loading: bsLoading } = useApi(
    () => api.accounts.balanceSheet(toDate ? { as_of_date: toDate } : {}),
    [tab === 'balance_sheet', toDate]
  )
  const { data: income, loading: incLoading } = useApi(
    () => api.accounts.incomeStatement(fromDate || toDate ? { from_date: fromDate, to_date: toDate } : {}),
    [tab === 'income', fromDate, toDate]
  )
  const { data: vat, loading: vatLoading } = useApi(
    () => api.accounts.vatReturn(fromDate || toDate ? { from_date: fromDate, to_date: toDate } : {}),
    [tab === 'vat', fromDate, toDate]
  )
  const { data: entries, loading: entLoading, reload: reloadEntries } = useApi(
    () => api.accounts.entries(fromDate || toDate ? { from_date: fromDate, to_date: toDate } : {}),
    [tab === 'entries', fromDate, toDate]
  )
  const { data: chart, loading: chartLoading, reload: reloadChart } = useApi(
    () => api.accounts.chart(), [tab === 'chart']
  )

  const [taxRate, setTaxRate] = useState('0.225')
  const { data: taxSummary, loading: taxLoading, reload: reloadTaxSummary } = useApi(
    () => api.accounts.taxSummary({ ...(fromDate || toDate ? { from_date: fromDate, to_date: toDate } : {}), income_tax_rate: taxRate }),
    [tab === 'taxes', fromDate, toDate, taxRate]
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
  const [entryForm, setEntryForm] = useState({ entry_date: new Date().toISOString().slice(0,10), description:'', lines:[] })
  const [newEntLine, setNewEntLine] = useState({ account_id:'', side:'debit', amount:'' })

  const addEntLine = () => {
    if (!newEntLine.account_id || !newEntLine.amount) return
    setEntryForm(f => ({ ...f, lines: [...f.lines, { ...newEntLine }] }))
    setNewEntLine({ account_id:'', side:'debit', amount:'' })
  }

  const totalDebits  = entryForm.lines.filter(l => l.side==='debit').reduce((s,l) => s+Number(l.amount),0)
  const totalCredits = entryForm.lines.filter(l => l.side==='credit').reduce((s,l) => s+Number(l.amount),0)
  const isBalanced   = Math.abs(totalDebits - totalCredits) < 0.01

  const handleSaveEntry = async () => {
    if (!entryForm.description) { toast('أدخل وصف القيد', 'error'); return }
    if (entryForm.lines.length < 2) { toast('أضف سطرين على الأقل', 'error'); return }
    if (!isBalanced) { toast('القيد غير متوازن — المدين لازم يساوي الدائن', 'error'); return }
    await act(async () => {
      await api.accounts.createEntry({
        ...entryForm,
        lines: entryForm.lines.map(l => ({ ...l, account_id: Number(l.account_id), amount: String(l.amount) }))
      })
      toast('تم حفظ القيد')
      setShowEntry(false)
      setEntryForm({ entry_date: new Date().toISOString().slice(0,10), description:'', lines:[] })
      reloadEntries()
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
        payment_date: taxPayForm.payment_date,
        cash_account_code: taxPayForm.cash_account_code,
        notes: taxPayForm.notes || null,
      })
      toast('تم تسجيل السداد')
      setTaxPayModal(null)
      reloadTaxSummary()
    })
  }

  const handleTaxProvision = async () => {
    if (!taxProvisionForm.amount || Number(taxProvisionForm.amount) <= 0) { toast('أدخل مبلغاً صحيحاً', 'error'); return }
    await act(async () => {
      await api.accounts.recordTaxProvision({
        amount: Number(taxProvisionForm.amount),
        provision_date: taxProvisionForm.provision_date,
        notes: taxProvisionForm.notes || null,
      })
      toast('تم تسجيل المخصص')
      setShowTaxProvision(false)
      reloadTaxSummary()
    })
  }

  const handleSeedChart = async () => {
    await act(async () => {
      const r = await api.accounts.seed()
      toast(r.message || 'تم زرع دليل الحسابات')
      reloadChart()
    })
  }

  const openNewAsset = () => { setEditingAsset(null); setAssetForm(EMPTY_ASSET); setShowAsset(true) }
  const openEditAsset = (a) => {
    setEditingAsset(a)
    setAssetForm({
      name: a.name, asset_type: a.asset_type, purchase_date: a.purchase_date,
      cost: String(a.cost), salvage_value: String(a.salvage_value),
      useful_life_years: String(a.useful_life_years), location: a.location || '', notes: a.notes || '',
    })
    setShowAsset(true)
  }

  const handleSaveAsset = async () => {
    if (!assetForm.name) { toast('أدخل اسم الأصل', 'error'); return }
    if (!assetForm.cost || Number(assetForm.cost) <= 0) { toast('أدخل تكلفة صحيحة', 'error'); return }
    if (Number(assetForm.salvage_value) >= Number(assetForm.cost)) { toast('قيمة الخردة يجب أن تكون أقل من التكلفة', 'error'); return }
    if (!assetForm.useful_life_years || Number(assetForm.useful_life_years) <= 0) { toast('أدخل عمراً إنتاجياً صحيحاً', 'error'); return }
    await act(async () => {
      const payload = {
        name: assetForm.name, asset_type: assetForm.asset_type, purchase_date: assetForm.purchase_date,
        cost: assetForm.cost, salvage_value: assetForm.salvage_value,
        useful_life_years: Number(assetForm.useful_life_years), location: assetForm.location || null, notes: assetForm.notes || null,
      }
      if (editingAsset) {
        await api.fixedAssets.update(editingAsset.id, payload)
        toast('تم تعديل الأصل')
      } else {
        await api.fixedAssets.create(payload)
        toast('تم إضافة الأصل')
      }
      setShowAsset(false)
      reloadAssets()
    })
  }

  const handleDeleteAsset = async (a) => {
    if (!confirm(`حذف الأصل "${a.name}"؟`)) return
    await act(async () => {
      await api.fixedAssets.delete(a.id)
      toast('تم الحذف')
      reloadAssets()
    })
  }

  const handleDisposeAsset = async (a) => {
    if (!confirm(`استبعاد الأصل "${a.name}"؟`)) return
    await act(async () => {
      await api.fixedAssets.dispose(a.id)
      toast('تم استبعاد الأصل')
      reloadAssets()
    })
  }

  const handleRecordDepreciation = async () => {
    await act(async () => {
      await api.fixedAssets.depreciate(depAsset.id, {
        period_year: Number(depPeriod.period_year), period_month: Number(depPeriod.period_month),
      })
      toast('تم تسجيل الإهلاك وترحيل القيد')
      setDepAsset(null)
      reloadAssets()
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
      <div style={{ display:'flex', gap:12, marginBottom:16, alignItems:'center', background:'var(--surface)', padding:'10px 14px', borderRadius:8, border:'1px solid var(--border)' }}>
        <span style={{ fontWeight:600, fontSize:13 }}>الفترة:</span>
        <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)} style={{ padding:'4px 8px', border:'1px solid var(--border)', borderRadius:6 }} />
        <span>—</span>
        <input type="date" value={toDate} onChange={e => setToDate(e.target.value)} style={{ padding:'4px 8px', border:'1px solid var(--border)', borderRadius:6 }} />
        <button className="btn-sm btn-secondary" onClick={() => { setFromDate(''); setToDate('') }}>مسح</button>
      </div>

      {/* Tabs */}
      <div style={{ display:'flex', gap:4, marginBottom:16, borderBottom:'2px solid var(--border)' }}>
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
              <table className="erp-table text-sm">
                <thead><tr><th>الكود</th><th>الحساب</th><th>النوع</th><th>مدين</th><th>دائن</th><th>الرصيد</th></tr></thead>
                <tbody>
                  {trialBal.accounts?.map((a, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight:600 }}>{a.code}</td>
                      <td>{a.name_ar}</td>
                      <td style={{ color:'var(--text-secondary)', fontSize:11 }}>{a.account_type}</td>
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
            </>
          ) : <div style={{ color:'var(--text-secondary)', padding:32, textAlign:'center' }}>لا توجد بيانات — تأكد من زرع دليل الحسابات وتسجيل قيود</div>}
        </div>
      )}

      {/* ── Balance Sheet ── */}
      {tab === 'balance_sheet' && (
        <div>
          {bsLoading ? <PageSpinner /> : balSheet ? (
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:20 }}>
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
                <div style={{ display:'flex', gap:32, justifyContent:'center' }}>
                  <span><b>إجمالي الأصول:</b> {egp(balSheet.totals?.total_assets)} ج</span>
                  <span><b>الخصوم + حقوق الملكية:</b> {egp((balSheet.totals?.total_liabilities||0) + (balSheet.totals?.total_equity||0))} ج</span>
                  <span style={{ fontWeight:700, color: balSheet.totals?.balanced ? '#15803d' : 'var(--red)' }}>
                    {balSheet.totals?.balanced ? '✓ الميزانية متوازنة' : '✗ غير متوازنة'}
                  </span>
                </div>
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
              <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:16, marginBottom:20 }}>
                {[['الإيرادات', income.revenue, 'var(--teal)'], ['المصروفات', income.expenses, 'var(--red)'], ['صافي الربح', income.net_income, income.net_income >= 0 ? '#15803d' : 'var(--red)']].map(([lbl,val,color]) => (
                  <div key={lbl} style={{ background:'var(--surface)', border:`2px solid ${color}`, borderRadius:8, padding:'14px 16px', textAlign:'center' }}>
                    <div style={{ fontSize:12, color:'var(--text-secondary)' }}>{lbl}</div>
                    <div style={{ fontSize:24, fontWeight:700, color }}>{egp(val)} ج</div>
                  </div>
                ))}
              </div>
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:20 }}>
                {[['الإيرادات', income.revenue_detail, 'var(--teal)'], ['المصروفات', income.expense_detail, 'var(--red)']].map(([title, items, color]) => (
                  <div key={title} style={{ background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:14 }}>
                    <h4 style={{ fontWeight:700, color, marginBottom:8 }}>{title}</h4>
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
              <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:16, marginBottom:20 }}>
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
            <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:16 }}>
              <div style={{ background:'var(--surface)', border:`2px solid ${taxSummary.vat?.net_vat_due > 0 ? 'var(--red)' : '#15803d'}`, borderRadius:8, padding:16 }}>
                <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:4 }}>ضريبة القيمة المضافة المستحقة (صافي)</div>
                <div style={{ fontSize:24, fontWeight:700, color: taxSummary.vat?.net_vat_due > 0 ? 'var(--red)' : '#15803d' }}>{egp(taxSummary.vat?.net_vat_due)} ج</div>
                <div style={{ fontSize:12, fontWeight:600, marginBottom:10 }}>{taxSummary.vat?.status_ar}</div>
                <button className="btn-secondary" disabled={taxSummary.vat?.net_vat_due <= 0}
                  onClick={() => openTaxPayment('vat', 'ضريبة القيمة المضافة', taxSummary.vat?.net_vat_due)}>تسجيل السداد</button>
              </div>

              <div style={{ background:'var(--surface)', border:`2px solid ${taxSummary.wht_payable?.balance > 0 ? 'var(--red)' : '#15803d'}`, borderRadius:8, padding:16 }}>
                <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:4 }}>ضريبة الخصم تحت الحساب المستحقة (WHT)</div>
                <div style={{ fontSize:24, fontWeight:700, color: taxSummary.wht_payable?.balance > 0 ? 'var(--red)' : '#15803d' }}>{egp(taxSummary.wht_payable?.balance)} ج</div>
                <div style={{ marginBottom:10 }}>&nbsp;</div>
                <button className="btn-secondary" disabled={taxSummary.wht_payable?.balance <= 0}
                  onClick={() => openTaxPayment('wht', 'ضريبة الخصم تحت الحساب', taxSummary.wht_payable?.balance)}>تسجيل السداد</button>
              </div>

              <div style={{ background:'var(--surface)', border:'2px solid var(--teal)', borderRadius:8, padding:16 }}>
                <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:4 }}>تقدير ضريبة الدخل</div>
                <div style={{ fontSize:24, fontWeight:700, color:'var(--teal)' }}>{egp(taxSummary.income_tax?.estimate)} ج</div>
                <div style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, marginBottom:10 }}>
                  <span>صافي الربح: {egp(taxSummary.income_tax?.net_income)} ج — النسبة:</span>
                  <input type="number" step="0.001" value={taxRate} onChange={e => setTaxRate(e.target.value)} style={{ width:60, padding:'2px 4px' }} />
                </div>
                <div style={{ display:'flex', gap:8 }}>
                  <button className="btn-secondary" disabled={taxSummary.income_tax?.estimate <= 0}
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
            <table className="erp-table text-sm">
              <thead><tr><th>التاريخ</th><th>الوصف</th><th>المصدر</th><th>مدين</th><th>دائن</th><th>مرحّل</th></tr></thead>
              <tbody>
                {!entries?.length && <tr><td colSpan={6} style={{ textAlign:'center', padding:24, color:'var(--text-secondary)' }}>لا توجد قيود</td></tr>}
                {entries?.map(e => (
                  <tr key={e.id}>
                    <td>{e.entry_date}</td>
                    <td>{e.description}</td>
                    <td style={{ fontSize:11, color:'var(--text-secondary)' }}>{e.source}</td>
                    <td style={{ color:'var(--teal)', fontWeight:600 }}>{egp(e.total_debit)}</td>
                    <td style={{ color:'#b45309', fontWeight:600 }}>{egp(e.total_credit)}</td>
                    <td>{e.posted ? <span style={{ color:'#15803d', fontWeight:700 }}>✓</span> : <span style={{ color:'var(--text-secondary)' }}>—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ── Chart of Accounts ── */}
      {tab === 'chart' && (
        <div>
          <div style={{ marginBottom:12, display:'flex', gap:8, justifyContent:'flex-end' }}>
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
                    <td><span style={{ fontSize:11, padding:'2px 6px', borderRadius:4, background:'var(--teal-lt)', color:'var(--teal)' }}>{a.account_type}</span></td>
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
                        <button className="btn-sm btn-secondary" style={{ color:'var(--red)' }} onClick={() => handleDeleteAsset(a)}>حذف</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
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
            <Input label="تاريخ الشراء" type="date" value={assetForm.purchase_date} onChange={e => setAssetForm(f => ({ ...f, purchase_date: e.target.value }))} />
            <Input label="التكلفة" type="number" value={assetForm.cost} onChange={e => setAssetForm(f => ({ ...f, cost: e.target.value }))} />
            <Input label="قيمة الخردة" type="number" value={assetForm.salvage_value} onChange={e => setAssetForm(f => ({ ...f, salvage_value: e.target.value }))} />
            <Input label="العمر الإنتاجي (سنوات)" type="number" value={assetForm.useful_life_years} onChange={e => setAssetForm(f => ({ ...f, useful_life_years: e.target.value }))} />
            <Input label="الموقع" value={assetForm.location} onChange={e => setAssetForm(f => ({ ...f, location: e.target.value }))} />
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
            <Input label="السنة" type="number" value={depPeriod.period_year} onChange={e => setDepPeriod(p => ({ ...p, period_year: e.target.value }))} />
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
            <Input label="المبلغ" type="number" value={taxPayForm.amount} onChange={e => setTaxPayForm(f => ({ ...f, amount: e.target.value }))} />
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
            <Input label="المبلغ" type="number" value={taxProvisionForm.amount} onChange={e => setTaxProvisionForm(f => ({ ...f, amount: e.target.value }))} />
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
            <Input label="الوصف" value={entryForm.description} onChange={e => setEntryForm(f => ({ ...f, description: e.target.value }))} span={2} />
          </FormGrid>
          <div style={{ marginTop:12, padding:12, background:'var(--bg)', borderRadius:8 }}>
            <h4 style={{ marginBottom:8, fontWeight:600 }}>بنود القيد:</h4>
            <div style={{ display:'grid', gridTemplateColumns:'3fr 1fr 2fr auto', gap:8, alignItems:'end' }}>
              <Select label="الحساب" value={newEntLine.account_id} onChange={e => setNewEntLine(n => ({ ...n, account_id: e.target.value }))}>
                <option value="">— اختر حساباً —</option>
                {chart?.map(a => <option key={a.id} value={a.id}>{a.code} — {a.name_ar}</option>)}
              </Select>
              <Select label="الجانب" value={newEntLine.side} onChange={e => setNewEntLine(n => ({ ...n, side: e.target.value }))}>
                <option value="debit">مدين</option>
                <option value="credit">دائن</option>
              </Select>
              <Input label="المبلغ" type="number" value={newEntLine.amount} onChange={e => setNewEntLine(n => ({ ...n, amount: e.target.value }))} />
              <button className="btn-teal" style={{ padding:'8px 14px' }} onClick={addEntLine}>+</button>
            </div>
            {entryForm.lines.length > 0 && (
              <table className="erp-table text-sm" style={{ marginTop:8 }}>
                <thead><tr><th>الحساب</th><th>مدين</th><th>دائن</th></tr></thead>
                <tbody>
                  {entryForm.lines.map((l,i) => {
                    const acc = chart?.find(a => String(a.id) === String(l.account_id))
                    return (
                      <tr key={i}>
                        <td>{acc?.code} — {acc?.name_ar}</td>
                        <td style={{ color:'var(--teal)', fontWeight:600 }}>{l.side==='debit' ? Number(l.amount).toFixed(2) : '—'}</td>
                        <td style={{ color:'#b45309', fontWeight:600 }}>{l.side==='credit' ? Number(l.amount).toFixed(2) : '—'}</td>
                      </tr>
                    )
                  })}
                  <tr style={{ background:'var(--teal-lt)', fontWeight:700 }}>
                    <td>الإجمالي</td>
                    <td style={{ color:'var(--teal)' }}>{totalDebits.toFixed(2)}</td>
                    <td style={{ color:'#b45309' }}>{totalCredits.toFixed(2)}</td>
                  </tr>
                </tbody>
              </table>
            )}
            {!isBalanced && entryForm.lines.length >= 2 && (
              <div style={{ color:'var(--red)', fontWeight:600, marginTop:8, fontSize:13 }}>
                ⚠️ القيد غير متوازن — الفرق: {Math.abs(totalDebits - totalCredits).toFixed(2)} ج
              </div>
            )}
          </div>
          <ModalFooter>
            <button className="btn-primary" onClick={handleSaveEntry} disabled={!isBalanced || entryForm.lines.length < 2 || acting}>{acting ? '…' : 'حفظ القيد'}</button>
            <button className="btn-secondary" onClick={() => setShowEntry(false)}>إلغاء</button>
          </ModalFooter>
        </Modal>
      )}
    </div>
  )
}
