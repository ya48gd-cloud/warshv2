import { useState, useMemo } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'

export default function Treasury() {
  const toast = useToast()
  const { canWrite } = useAuth()
  const write = canWrite('accounting')

  const { data: summary, loading, reload } = useApi(() => api.treasury.summary())
  const { data: txns, reload: reloadTxns } = useApi(() => api.treasury.transactions().catch(bgErr('حركات الخزينة', [])))
  const { data: cats, reload: reloadCats } = useApi(() => api.treasury.categories().catch(bgErr('فئات الخزينة', [])))
  const { data: cheques, reload: reloadCheques } = useApi(() => api.treasury.cheques().catch(bgErr('الشيكات', [])))

  const [tab, setTab] = useState('txns')   // txns | cheques
  const [filterTreasury, setFilterTreasury] = useState('')
  const [filterDir, setFilterDir] = useState('')
  const [txnModal, setTxnModal] = useState(null)      // 'in' | 'out' | null
  const [transferModal, setTransferModal] = useState(false)
  const [treasuryModal, setTreasuryModal] = useState(false)
  const [catModal, setCatModal] = useState(false)
  const [deleteTxn, setDeleteTxn] = useState(null)
  const [confirmTxn, setConfirmTxn] = useState(false)
  const [editingTxnId, setEditingTxnId] = useState(null)
  // لقطة من الحركة الأصلية قبل التعديل — تُستخدم لحساب "الرصيد المتاح" الحقيقي
  // أثناء تعديل مصروف (out): رصيد الخزنة المعروض من الـ summary يشمل بالفعل أثر
  // الحركة القديمة، فلازم نضيفه مرة أخرى قبل مقارنته بالمبلغ الجديد.
  const [editingOriginal, setEditingOriginal] = useState(null)

  const [form, setForm] = useState({})
  const [tForm, setTForm] = useState({})
  const [trForm, setTrForm] = useState({})
  const [newCat, setNewCat] = useState({ name: '', flow: 'out' })
  const [confirmCatDel, setConfirmCatDel] = useState(null)

  const treasuries = summary?.treasuries || []

  const filtered = useMemo(() => {
    let list = txns || []
    if (filterTreasury) list = list.filter(t => Number(t.treasury_id) === Number(filterTreasury))
    if (filterDir) list = list.filter(t => t.direction === filterDir)
    return list
  }, [txns, filterTreasury, filterDir])

  // ── Actions ──
  const openEditTxn = (t) => {
    setForm({
      treasury_id: t.treasury_id,
      amount: t.amount,
      category_id: t.category_id || '',
      txn_date: t.txn_date,
      description: t.description || '',
      reference: t.reference || '',
      payment_method: t.payment_method || 'cash',
      // كانت هذي الحقول بتتفقد عند فتح تعديل حركة شيك — يضطر المستخدم
      // يعيد كتابتها من الصفر (والتحقق كان بيرفض الحفظ لحد ما يعملها)
      cheque_number: t.cheque_number || '',
      cheque_date: t.cheque_date || '',
      cheque_bank: t.cheque_bank || '',
    })
    setEditingTxnId(t.id)
    setEditingOriginal({ treasury_id: t.treasury_id, direction: t.direction, amount: t.amount })
    setTxnModal(t.direction)
  }

  const [saveTxn, savingTxn] = useAction(async () => {
    const payload = {
      treasury_id: Number(form.treasury_id),
      direction: txnModal,
      amount: Number(form.amount),
      category_id: form.category_id ? Number(form.category_id) : null,
      txn_date: form.txn_date || today(),
      description: form.description || null,
      reference: form.reference || null,
      payment_method: form.payment_method || 'cash',
      cheque_number: form.payment_method === 'cheque' ? (form.cheque_number || null) : null,
      cheque_date: form.payment_method === 'cheque' ? (form.cheque_date || null) : null,
      cheque_bank: form.payment_method === 'cheque' ? (form.cheque_bank || null) : null,
    }
    if (editingTxnId) {
      await api.treasury.updateTxn(editingTxnId, payload)
      toast('تم تعديل الحركة')
    } else {
      await api.treasury.addTxn(payload)
      toast(txnModal === 'in' ? 'تم تسجيل الإيراد' : 'تم تسجيل المصروف')
    }
    setConfirmTxn(false); setTxnModal(null); setForm({}); setEditingTxnId(null); setEditingOriginal(null); reload(); reloadTxns(); reloadCheques()
  })

  // التحقق قبل فتح التأكيد
  const requestSaveTxn = () => {
    if (!form.treasury_id) { toast('اختر الخزنة', 'error'); return }
    if (!form.amount || Number(form.amount) <= 0) { toast('أدخل مبلغ صحيح', 'error'); return }
    if (form.payment_method === 'cheque' && (!form.cheque_number || !form.cheque_date)) {
      toast('أدخل رقم الشيك وتاريخ الاستحقاق', 'error'); return
    }
    setConfirmTxn(true)
  }

  const [saveTreasury, savingT] = useAction(async () => {
    if (!tForm.name?.trim()) { toast('أدخل اسم الخزنة', 'error'); return }
    await api.treasury.createAccount({
      name: tForm.name.trim(),
      kind: tForm.kind || 'cash',
      opening_balance: Number(tForm.opening_balance || 0),
      notes: tForm.notes || null,
    })
    toast('تمت إضافة الخزنة'); setTreasuryModal(false); setTForm({}); reload()
  })

  const [doTransfer, transferring] = useAction(async () => {
    if (!trForm.from_treasury_id || !trForm.to_treasury_id) { toast('اختر الخزنتين', 'error'); return }
    if (trForm.from_treasury_id === trForm.to_treasury_id) { toast('لا يمكن التحويل لنفس الخزنة', 'error'); return }
    if (!trForm.amount || Number(trForm.amount) <= 0) { toast('أدخل مبلغ صحيح', 'error'); return }
    await api.treasury.transfer({
      from_treasury_id: Number(trForm.from_treasury_id),
      to_treasury_id: Number(trForm.to_treasury_id),
      amount: Number(trForm.amount),
      txn_date: trForm.txn_date || today(),
      description: trForm.description || null,
    })
    toast('تم التحويل'); setTransferModal(false); setTrForm({}); reload(); reloadTxns()
  })

  const [addCat, addingCat] = useAction(async () => {
    if (!newCat.name.trim()) { toast('أدخل اسم الفئة', 'error'); return }
    await api.treasury.createCategory({ name: newCat.name.trim(), flow: newCat.flow })
    toast('تمت إضافة الفئة'); setNewCat({ name: '', flow: 'out' }); reloadCats()
  })

  const [removeTxn] = useAction(async () => {
    await api.treasury.deleteTxn(deleteTxn.id)
    toast('تم حذف الحركة'); setDeleteTxn(null); reload(); reloadTxns(); reloadCheques()
  })

  const [setChequeStatus] = useAction(async (id, status) => {
    await api.treasury.updateChequeStatus(id, status)
    toast('تم تحديث حالة الشيك'); reload(); reloadCheques(); reloadTxns()
  })

  if (loading) return <PageSpinner />

  const kpis = [
    { label: 'إجمالي الرصيد', val: egp(summary?.total_balance || 0), color: 'var(--teal)' },
    { label: 'إجمالي الداخل', val: egp(summary?.total_in || 0),    color: 'var(--green)' },
    { label: 'إجمالي الخارج', val: egp(summary?.total_out || 0),   color: 'var(--red)' },
    { label: 'الصافي',        val: egp(summary?.net || 0),          color: (summary?.net || 0) >= 0 ? 'var(--green)' : 'var(--red)' },
  ]

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-base font-semibold">💰 الخزنة</h2>
        {write && (
          <div className="flex gap-2 flex-wrap">
            <button className="btn btn-sm" style={{ background: 'var(--green-lt)', color: 'var(--green)' }}
              onClick={() => { setForm({ txn_date: today() }); setTxnModal('in') }}>↓ إيراد (داخل)</button>
            <button className="btn btn-sm" style={{ background: 'var(--red-lt)', color: 'var(--red)' }}
              onClick={() => { setForm({ txn_date: today() }); setTxnModal('out') }}>↑ مصروف (خارج)</button>
            <button className="btn btn-sm" onClick={() => { setTrForm({ txn_date: today() }); setTransferModal(true) }}>⇄ تحويل</button>
            <button className="btn btn-sm" onClick={() => setTreasuryModal(true)}>+ خزنة</button>
            <button className="btn btn-sm" onClick={() => setCatModal(true)}>الفئات</button>
          </div>
        )}
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {kpis.map(k => (
          <div key={k.label} className="card">
            <div className="text-xs text-erp-muted mb-1">{k.label}</div>
            <div className="text-lg font-bold" style={{ color: k.color }}>{k.val}</div>
          </div>
        ))}
      </div>

      {/* الخزن */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {treasuries.map(t => (
          <div key={t.id} className="card">
            <div className="flex items-center justify-between">
              <div className="font-semibold text-sm">{t.kind === 'bank' ? '🏦' : '💵'} {t.name}</div>
              <span className="text-[11px] text-erp-muted">{t.kind === 'bank' ? 'بنك' : 'نقدي'}</span>
            </div>
            <div className="text-lg font-bold mt-2" style={{ color: 'var(--teal)' }}>{egp(t.balance)}</div>
          </div>
        ))}
        {!treasuries.length && (
          <div className="text-sm text-erp-muted">لا توجد خزن بعد. اضغط "+ خزنة" لإضافة واحدة.</div>
        )}
      </div>

      {/* تبويبات */}
      <div className="flex items-center gap-2 border-b" style={{ borderColor: 'var(--border)' }}>
        <button className={`btn btn-sm ${tab === 'txns' ? 'btn-primary' : ''}`} onClick={() => setTab('txns')}>الحركات</button>
        <button className={`btn btn-sm ${tab === 'cheques' ? 'btn-primary' : ''}`} onClick={() => setTab('cheques')}>
          🧾 الشيكات {cheques?.filter(c => c.cheque_status === 'under_collection').length ? `(${cheques.filter(c => c.cheque_status === 'under_collection').length})` : ''}
        </button>
      </div>

      {tab === 'txns' && (<>
      {/* فلاتر الحركات */}
      <div className="flex items-center gap-2 flex-wrap">
        <select className="field-select" style={{ maxWidth: 180, height: 32, padding: '0 8px', fontSize: 13 }}
          value={filterTreasury} onChange={e => setFilterTreasury(e.target.value)}>
          <option value="">كل الخزن</option>
          {treasuries.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <select className="field-select" style={{ maxWidth: 130, height: 32, padding: '0 8px', fontSize: 13 }}
          value={filterDir} onChange={e => setFilterDir(e.target.value)}>
          <option value="">داخل وخارج</option>
          <option value="in">داخل فقط</option>
          <option value="out">خارج فقط</option>
        </select>
        <div className="text-xs text-erp-muted">{filtered.length} حركة</div>
      </div>

      {/* جدول الحركات */}
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-erp-muted text-xs border-b" style={{ borderColor: 'var(--border)' }}>
              <th className="text-right py-2 px-2">رقم السند</th>
              <th className="text-right py-2 px-2">التاريخ</th>
              <th className="text-right py-2 px-2">الخزنة</th>
              <th className="text-right py-2 px-2">الفئة</th>
              <th className="text-right py-2 px-2">البيان</th>
              <th className="text-right py-2 px-2">طريقة الدفع</th>
              <th className="text-right py-2 px-2">داخل</th>
              <th className="text-right py-2 px-2">خارج</th>
              <th className="py-2 px-2"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(t => (
              <tr key={t.id} className="border-b" style={{ borderColor: 'var(--border-lt)' }}>
                <td className="py-2 px-2">
                  <span className="font-mono text-xs font-semibold" style={{ color: 'var(--teal)' }}>
                    {t.receipt_no || '—'}
                  </span>
                </td>
                <td className="py-2 px-2 whitespace-nowrap">{fmtDate(t.txn_date)}</td>
                <td className="py-2 px-2">{t.treasury_name}</td>
                <td className="py-2 px-2">
                  {t.category_name || '—'}
                  {t.source === 'transfer' && <span className="text-[11px] text-erp-muted"> (تحويل)</span>}
                  {t.source === 'customer_payment' && <span className="text-[11px] text-erp-muted"> (عميل)</span>}
                  {t.source === 'supplier_payment' && <span className="text-[11px] text-erp-muted"> (مورد)</span>}
                </td>
                <td className="py-2 px-2 text-erp-muted">{t.description || '—'}</td>
                <td className="py-2 px-2">
                  {t.payment_method === 'cheque' ? (
                    <span className="badge" style={{
                      background: t.cheque_status === 'cleared' ? 'var(--green-lt)'
                        : t.cheque_status === 'bounced' ? 'var(--red-lt)'
                        : t.cheque_status === 'cancelled' ? 'var(--surface-2)'
                        : 'var(--yellow-lt, #fef3c7)',
                      color: t.cheque_status === 'cleared' ? 'var(--green)'
                        : t.cheque_status === 'bounced' ? 'var(--red)'
                        : 'var(--orange, #b45309)',
                      fontSize: 11,
                    }}>
                      🧾 شيك #{t.cheque_number}
                    </span>
                  ) : (t.payment_method === 'wallet' ? '📱 محفظة' : '💵 نقدي')}
                </td>
                <td className="py-2 px-2 font-semibold" style={{ color: 'var(--green)' }}>
                  {t.direction === 'in' ? egp(t.amount) : ''}
                </td>
                <td className="py-2 px-2 font-semibold" style={{ color: 'var(--red)' }}>
                  {t.direction === 'out' ? egp(t.amount) : ''}
                </td>
                <td className="py-2 px-2 text-center" style={{ whiteSpace:'nowrap' }}>
                  <button className="btn btn-sm" title="طباعة السند"
                    onClick={() => api.print(`/treasury/transactions/${t.id}/print`)}>
                    🖨️
                  </button>
                  {write && t.source === 'manual' && !['cleared','bounced'].includes(t.cheque_status) && (
                    <button className="btn btn-sm" style={{ marginRight:2 }}
                      onClick={() => openEditTxn(t)}>✏️</button>
                  )}
                  {write && t.source === 'manual' && (
                    <button className="btn btn-sm" style={{ color: 'var(--red)', marginRight:2 }}
                      onClick={() => setDeleteTxn(t)}>🗑</button>
                  )}
                </td>
              </tr>
            ))}
            {!filtered.length && (
              <tr><td colSpan={9} className="text-center text-erp-muted py-6">لا توجد حركات</td></tr>
            )}
          </tbody>
        </table>
      </div>
      </>)}

      {tab === 'cheques' && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-erp-muted text-xs border-b" style={{ borderColor: 'var(--border)' }}>
                <th className="text-right py-2 px-2">رقم الشيك</th>
                <th className="text-right py-2 px-2">البنك</th>
                <th className="text-right py-2 px-2">تاريخ الاستحقاق</th>
                <th className="text-right py-2 px-2">الخزنة</th>
                <th className="text-right py-2 px-2">البيان</th>
                <th className="text-right py-2 px-2">الاتجاه</th>
                <th className="text-right py-2 px-2">المبلغ</th>
                <th className="text-right py-2 px-2">الحالة</th>
                <th className="py-2 px-2"></th>
              </tr>
            </thead>
            <tbody>
              {(cheques || []).map(c => {
                const overdue = c.cheque_status === 'under_collection' && c.cheque_date && c.cheque_date < today()
                return (
                  <tr key={c.id} className="border-b" style={{ borderColor: 'var(--border-lt)' }}>
                    <td className="py-2 px-2 font-mono">{c.cheque_number}</td>
                    <td className="py-2 px-2">{c.cheque_bank || '—'}</td>
                    <td className="py-2 px-2 whitespace-nowrap" style={{ color: overdue ? 'var(--red)' : undefined, fontWeight: overdue ? 700 : undefined }}>
                      {fmtDate(c.cheque_date)} {overdue && '⚠️ متأخر'}
                    </td>
                    <td className="py-2 px-2">{c.treasury_name}</td>
                    <td className="py-2 px-2 text-erp-muted">{c.description || '—'}</td>
                    <td className="py-2 px-2">{c.direction === 'in' ? 'وارد (لنا)' : 'صادر (علينا)'}</td>
                    <td className="py-2 px-2 font-semibold" style={{ color: c.direction === 'in' ? 'var(--green)' : 'var(--red)' }}>
                      {egp(c.amount)}
                    </td>
                    <td className="py-2 px-2">
                      <span className="badge" style={{
                        background: c.cheque_status === 'cleared' ? 'var(--green-lt)'
                          : c.cheque_status === 'bounced' ? 'var(--red-lt)'
                          : c.cheque_status === 'cancelled' ? 'var(--surface-2)'
                          : 'var(--yellow-lt, #fef3c7)',
                        color: c.cheque_status === 'cleared' ? 'var(--green)'
                          : c.cheque_status === 'bounced' ? 'var(--red)'
                          : 'var(--orange, #b45309)',
                        fontSize: 11,
                      }}>
                        {{
                          under_collection: 'تحت التحصيل',
                          cleared: 'تم التحصيل',
                          bounced: 'مرتجع',
                          cancelled: 'ملغي',
                        }[c.cheque_status] || c.cheque_status}
                      </span>
                    </td>
                    <td className="py-2 px-2 text-center" style={{ whiteSpace: 'nowrap' }}>
                      {write && c.cheque_status === 'under_collection' && (
                        <>
                          <button className="btn btn-sm" style={{ color: 'var(--green)' }}
                            onClick={() => setChequeStatus(c.id, 'cleared')}>✅ تحصيل</button>
                          <button className="btn btn-sm" style={{ color: 'var(--red)', marginRight: 2 }}
                            onClick={() => setChequeStatus(c.id, 'bounced')}>↩️ مرتجع</button>
                        </>
                      )}
                    </td>
                  </tr>
                )
              })}
              {!(cheques || []).length && (
                <tr><td colSpan={9} className="text-center text-erp-muted py-6">لا توجد شيكات</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* مودال إيراد/مصروف */}
      {txnModal && (
        <Modal open onClose={() => { setTxnModal(null); setForm({}); setEditingTxnId(null); setEditingOriginal(null) }}
          title={txnModal === 'in' ? '↓ تسجيل إيراد (فلوس داخلة)' : '↑ تسجيل مصروف (فلوس خارجة)'}>
          <FormGrid>
            <Select label="الخزنة *" value={form.treasury_id || ''}
              onChange={e => setForm(f => ({ ...f, treasury_id: e.target.value }))}>
              <option value="">— اختر —</option>
              {treasuries.map(t => <option key={t.id} value={t.id}>{t.name} ({egp(t.balance)})</option>)}
            </Select>
            {txnModal === 'out' && form.treasury_id && (() => {
              const sel = treasuries.find(t => String(t.id) === String(form.treasury_id))
              if (!sel) return null
              // عند تعديل مصروف قائم على نفس الخزنة، رصيد الخزنة (sel.balance) بيكون
              // شامل بالفعل خصم المبلغ القديم — فنضيفه تاني هنا عشان الرصيد "المتاح"
              // المعروض يعكس الحقيقة (وهو نفس المنطق اللي بيطبّقه الباك إند وقت الحفظ)
              const isEditingSameOut = editingOriginal
                && String(editingOriginal.treasury_id) === String(form.treasury_id)
                && editingOriginal.direction === 'out'
              const availableBalance = isEditingSameOut
                ? Number(sel.balance) + Number(editingOriginal.amount)
                : Number(sel.balance)
              const overAmount = Number(form.amount) > availableBalance
              return (
                <div style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '8px 12px', borderRadius: 8,
                  background: overAmount ? 'var(--red-lt)' : 'var(--green-lt)',
                }}>
                  <span className="text-sm text-erp-muted">الرصيد المتاح:</span>
                  <span className="font-bold" style={{ color: overAmount ? 'var(--red)' : 'var(--green)' }}>
                    {egp(availableBalance)}
                    {overAmount && ' ⚠️ المبلغ أكبر من الرصيد'}
                  </span>
                </div>
              )
            })()}
            <Input label="المبلغ *" type="number" value={form.amount || ''}
              onChange={e => setForm(f => ({ ...f, amount: e.target.value }))} />
            <Select label="الفئة" value={form.category_id || ''}
              onChange={e => setForm(f => ({ ...f, category_id: e.target.value }))}>
              <option value="">— بدون —</option>
              {(cats || []).filter(c => c.flow === 'both' || c.flow === txnModal).map(c =>
                <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
            <Input label="التاريخ" type="date" value={form.txn_date || today()}
              onChange={e => setForm(f => ({ ...f, txn_date: e.target.value }))} />
            <Input label="مرجع (اختياري)" value={form.reference || ''}
              onChange={e => setForm(f => ({ ...f, reference: e.target.value }))} />
            <Select label="طريقة الدفع" value={form.payment_method || 'cash'}
              onChange={e => setForm(f => ({ ...f, payment_method: e.target.value }))}>
              <option value="cash">💵 نقدي</option>
              <option value="cheque">🧾 شيك</option>
              <option value="wallet">📱 محفظة إلكترونية</option>
              <option value="other">أخرى</option>
            </Select>
          </FormGrid>
          {form.payment_method === 'cheque' && (
            <FormGrid>
              <Input label="رقم الشيك *" value={form.cheque_number || ''}
                onChange={e => setForm(f => ({ ...f, cheque_number: e.target.value }))} />
              <Input label="تاريخ الاستحقاق *" type="date" value={form.cheque_date || ''}
                onChange={e => setForm(f => ({ ...f, cheque_date: e.target.value }))} />
              <Input label="البنك" value={form.cheque_bank || ''}
                onChange={e => setForm(f => ({ ...f, cheque_bank: e.target.value }))} />
            </FormGrid>
          )}
          <div className="mt-3">
            <Textarea label="البيان" value={form.description || ''}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
          </div>
          <ModalFooter>
            <button className="btn" onClick={() => { setTxnModal(null); setForm({}); setEditingTxnId(null); setEditingOriginal(null) }}>إلغاء</button>
            <button className="btn btn-primary" disabled={savingTxn} onClick={requestSaveTxn}>
              {savingTxn ? '...' : 'التالي ←'}
            </button>
          </ModalFooter>
        </Modal>
      )}

      {/* مودال التحويل */}
      {transferModal && (
        <Modal open onClose={() => { setTransferModal(false); setTrForm({}) }} title="⇄ تحويل بين الخزن">
          <FormGrid>
            <Select label="من خزنة *" value={trForm.from_treasury_id || ''}
              onChange={e => setTrForm(f => ({ ...f, from_treasury_id: e.target.value }))}>
              <option value="">— اختر —</option>
              {treasuries.map(t => <option key={t.id} value={t.id}>{t.name} ({egp(t.balance)})</option>)}
            </Select>
            <Select label="إلى خزنة *" value={trForm.to_treasury_id || ''}
              onChange={e => setTrForm(f => ({ ...f, to_treasury_id: e.target.value }))}>
              <option value="">— اختر —</option>
              {treasuries.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </Select>
            <Input label="المبلغ *" type="number" value={trForm.amount || ''}
              onChange={e => setTrForm(f => ({ ...f, amount: e.target.value }))} />
            <Input label="التاريخ" type="date" value={trForm.txn_date || today()}
              onChange={e => setTrForm(f => ({ ...f, txn_date: e.target.value }))} />
          </FormGrid>
          <ModalFooter>
            <button className="btn" onClick={() => { setTransferModal(false); setTrForm({}) }}>إلغاء</button>
            <button className="btn btn-primary" disabled={transferring} onClick={doTransfer}>
              {transferring ? '...' : 'تحويل'}
            </button>
          </ModalFooter>
        </Modal>
      )}

      {/* مودال إضافة خزنة */}
      {treasuryModal && (
        <Modal open onClose={() => { setTreasuryModal(false); setTForm({}) }} title="+ خزنة جديدة">
          <FormGrid>
            <Input label="اسم الخزنة *" value={tForm.name || ''}
              onChange={e => setTForm(f => ({ ...f, name: e.target.value }))} />
            <Select label="النوع" value={tForm.kind || 'cash'}
              onChange={e => setTForm(f => ({ ...f, kind: e.target.value }))}>
              <option value="cash">نقدي 💵</option>
              <option value="bank">بنك 🏦</option>
            </Select>
            <Input label="الرصيد الافتتاحي" type="number" value={tForm.opening_balance || ''}
              onChange={e => setTForm(f => ({ ...f, opening_balance: e.target.value }))} />
          </FormGrid>
          <ModalFooter>
            <button className="btn" onClick={() => { setTreasuryModal(false); setTForm({}) }}>إلغاء</button>
            <button className="btn btn-primary" disabled={savingT} onClick={saveTreasury}>
              {savingT ? '...' : 'حفظ'}
            </button>
          </ModalFooter>
        </Modal>
      )}

      {/* مودال الفئات */}
      {catModal && (
        <Modal open onClose={() => setCatModal(false)} title="فئات الحركات">
          <div className="flex flex-col gap-4">
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <Input label="فئة جديدة" value={newCat.name}
                  onChange={e => setNewCat(c => ({ ...c, name: e.target.value }))}
                  onKeyDown={e => { if (e.key === 'Enter') addCat() }} />
              </div>
              <Select label="النوع" value={newCat.flow}
                onChange={e => setNewCat(c => ({ ...c, flow: e.target.value }))}>
                <option value="out">مصروف</option>
                <option value="in">إيراد</option>
                <option value="both">الاثنين</option>
              </Select>
              <button className="btn btn-primary" disabled={addingCat} onClick={addCat}>إضافة</button>
            </div>
            <div className="flex flex-col gap-1 max-h-72 overflow-y-auto border-t pt-3" style={{ borderColor: 'var(--border)' }}>
              {(cats || []).map(c => (
                <div key={c.id} className="flex items-center justify-between py-1.5 px-2 rounded" style={{ background: 'var(--surface-2)' }}>
                  <span className="text-sm">{c.name}
                    <span className="text-[11px] text-erp-muted mr-2">
                      ({c.flow === 'in' ? 'إيراد' : c.flow === 'out' ? 'مصروف' : 'الاثنين'})
                    </span>
                  </span>
                  <button className="btn btn-sm" style={{ color: 'var(--red)' }}
                    onClick={() => setConfirmCatDel(c)}>🗑</button>
                </div>
              ))}
            </div>
          </div>
          <ModalFooter>
            <button className="btn" onClick={() => setCatModal(false)}>إغلاق</button>
          </ModalFooter>
        </Modal>
      )}

      <ConfirmDialog
        open={!!confirmCatDel}
        onClose={() => setConfirmCatDel(null)}
        onConfirm={async () => {
          try { await api.treasury.deleteCategory(confirmCatDel.id); toast('تم الحذف'); reloadCats() }
          catch (err) { toast(err?.message || 'تعذّر الحذف', 'error') }
          setConfirmCatDel(null)
        }}
        title="حذف فئة"
        message={`حذف فئة "${confirmCatDel?.name}"؟`}
        danger
      />

      <ConfirmDialog open={!!deleteTxn} onClose={() => setDeleteTxn(null)} onConfirm={removeTxn}
        title="حذف حركة" message={`حذف حركة بمبلغ ${egp(deleteTxn?.amount || 0)}؟`} danger />

      <ConfirmDialog open={confirmTxn} onClose={() => setConfirmTxn(false)} onConfirm={saveTxn}
        title={txnModal === 'in' ? '↓ تأكيد إيداع' : '↑ تأكيد سحب'}
        danger={txnModal === 'out'}
        message={
          txnModal === 'out'
            ? `هيتم خصم ${egp(Number(form.amount) || 0)} من "${treasuries.find(t => String(t.id) === String(form.treasury_id))?.name || ''}". متأكد؟`
            : `هيتم إيداع ${egp(Number(form.amount) || 0)} في "${treasuries.find(t => String(t.id) === String(form.treasury_id))?.name || ''}". متأكد؟`
        } />
    </div>
  )
}
