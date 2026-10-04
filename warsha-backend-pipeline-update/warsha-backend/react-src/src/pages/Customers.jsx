/**
 * Customers — سجل العملاء
 * Matches original UI: table with كود/اسم/تليفون/رصيد + بروفايل/تعديل/دفعة buttons
 * Profile view: KPIs + طلبات + فواتير + مدفوعات
 */
import { useState, useMemo, useCallback } from 'react'
import { useApi, useAction } from '../hooks/useApi'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp, fmtDate, today } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select, Textarea, FormGrid } from '../components/ui/Field'
import SearchInput from '../components/ui/SearchInput'
import ConfirmDialog from '../components/ui/ConfirmDialog'
import { PageSpinner } from '../components/ui/Spinner'
import PaymentAttachment from '../components/ui/PaymentAttachment'

const MOVEMENT_TYPE_AR = {
  manufactured:'تصنيع', delivered:'تسليم للعميل', installed:'تركيب',
  relocated:'نقل لموقع آخر', maintained:'صيانة', returned:'إرجاع',
}
const MOVEMENT_TYPES = ['installed', 'relocated', 'maintained', 'returned']

// ── Customer profile page ──────────────────────────────────────
function CustomerProfile({ customer, onBack, write, onNavigate }) {
  const toast = useToast()
  const { data: statement, loading, reload: reloadStatement } = useApi(
    () => api.get(`/customers/${customer.id}/statement`),
    [customer.id]
  )
  const [payModal, setPayModal] = useState(false)
  const [payForm, setPayForm] = useState({ amount: '', payment_date: today(), payment_type: 'cash', reference: '', notes: '' })
  const [payFile, setPayFile] = useState(null)

  // بروفايل الأصل
  const [assetId, setAssetId] = useState(null)
  const { data: assetDetail, loading: assetLoading, reload: reloadAsset } = useApi(
    () => assetId ? api.get(`/field-service/assets/${assetId}`) : Promise.resolve(null),
    [assetId]
  )
  const { data: assetEquipment } = useApi(
    () => (assetId && assetDetail?.equipment_id) ? api.get(`/equipment/${assetDetail.equipment_id}`) : Promise.resolve(null),
    [assetId, assetDetail?.equipment_id]
  )
  const [moveModal, setMoveModal] = useState(false)
  const [moveForm, setMoveForm] = useState({ event_type: 'relocated', event_date: today() })
  const [saveMove, savingMove] = useAction(async () => {
    if (!moveForm.event_type) { toast('اختر نوع الحركة', 'error'); return }
    if (['installed', 'relocated'].includes(moveForm.event_type) && !moveForm.to_location_id) {
      toast('اختر الموقع الجديد', 'error'); return
    }
    await api.fieldService.addMovement(assetId, {
      event_type: moveForm.event_type,
      to_location_id: moveForm.to_location_id ? Number(moveForm.to_location_id) : null,
      event_date: moveForm.event_date || today(),
      notes: moveForm.notes || null,
    })
    toast('✅ تم تسجيل الحركة')
    setMoveModal(false)
    setMoveForm({ event_type: 'relocated', event_date: today() })
    reloadAsset()
  })


  // المواقع
  const { data: locations, reload: reloadLocs } = useApi(
    () => api.customers.locations(customer.id).catch(bgErr('عناوين العميل', [])), [customer.id]
  )
  const [locModal, setLocModal] = useState(false)
  const [locForm, setLocForm] = useState({ name: '', address: '', phone: '', contact_person: '' })
  const [confirmLocDel, setConfirmLocDel] = useState(null)

  const [addLoc, addingLoc] = useAction(async () => {
    if (!locForm.name?.trim()) { toast('اسم الموقع مطلوب', 'error'); return }
    await api.customers.addLocation(customer.id, {
      name: locForm.name.trim(), address: locForm.address || null,
      phone: locForm.phone || null, contact_person: locForm.contact_person || null,
    })
    toast('تمت إضافة الموقع')
    setLocForm({ name: '', address: '', phone: '', contact_person: '' })
    reloadLocs()
  })

  const [recordPay, paying] = useAction(async () => {
    if (!payForm.amount || Number(payForm.amount) <= 0) { toast('أدخل مبلغ الدفعة', 'error'); return }
    const result = await api.post('/customers/payments', {
      customer_id: customer.id,
      amount: Number(payForm.amount),
      payment_type: payForm.payment_type || 'cash',
      payment_date: payForm.payment_date || today(),
      reference: payForm.reference || null,
      notes: payForm.notes || null,
    })
    // رفع إثبات الدفع إن وُجد
    if (payFile && result?.id) {
      try {
        await api.uploadAttachment('customer_payment', result.id, payFile, 'إثبات دفعة')
      } catch (_) { toast('تم تسجيل الدفعة — فشل رفع الملف', 'error') }
    }
    toast('تم تسجيل الدفعة')
    setPayModal(false)
    setPayForm({ amount: '', payment_date: today(), payment_type: 'cash', reference: '', notes: '' })
    setPayFile(null)
    reloadStatement()
  })

  // accordion بنود الفواتير
  const [expandedInv, setExpandedInv] = useState({})
  const [invLines, setInvLines] = useState({})
  const toggleInvLines = useCallback(async (inv) => {
    const id = inv.id
    setExpandedInv(prev => ({ ...prev, [id]: !prev[id] }))
    if (!invLines[id]) {
      try {
        const data = await api.get(`/sales/invoices/${id}`)
        setInvLines(prev => ({ ...prev, [id]: data?.lines || [] }))
      } catch (e) { toast(e?.message || 'تعذّر تحميل بنود الفاتورة', 'error') }
    }
  }, [invLines])

  const openPrint = (mode) => {
    api.customers.printStatement(customer.id, mode)
  }

  const orders   = statement?.orders   || []
  const invoices = statement?.invoices  || []
  const payments = statement?.payments  || []
  const assets   = statement?.assets    || []
  const workOrders   = statement?.work_orders   || []
  const extProjects  = statement?.ext_projects  || []
  const fieldServiceOrders = statement?.field_service_orders || []
  const totalOwed    = statement?.total_owed    ?? statement?.balance_due ?? customer.balance ?? 0
  const totalPaid    = statement?.total_paid    || 0
  const totalOrdered = statement?.total_ordered ?? statement?.total_charged ?? 0

  const [activeTab, setActiveTab] = useState('overview')
  const [profileLocId, setProfileLocId] = useState(null)
  const tabs = [
    { id: 'overview',     label: 'نظرة عامة' },
    { id: 'production',   label: `أوامر الإنتاج (${workOrders.length})` },
    { id: 'assets',       label: `الأصول (${assets.length})` },
    { id: 'invoices',     label: `الفواتير (${invoices.length})` },
    { id: 'payments',     label: `المدفوعات (${payments.length})` },
    { id: 'projects',     label: `المشاريع (${extProjects.length})` },
    { id: 'fieldservice', label: `الخدمة الميدانية (${fieldServiceOrders.length})` },
    { id: 'location',     label: 'بروفايل الموقع' },
  ]

  // كشف حساب مفلتر حسب الموقع المختار
  const locOrders   = useMemo(() => orders.filter(o => o.location_id === profileLocId), [orders, profileLocId])
  const locInvoices = useMemo(() => invoices.filter(i => i.location_id === profileLocId), [invoices, profileLocId])
  const locPayments = useMemo(() => payments.filter(p => p.location_id === profileLocId), [payments, profileLocId])
  const locAssets   = useMemo(() => assets.filter(a => a.current_location_id === profileLocId), [assets, profileLocId])
  const locProjects = useMemo(() => extProjects.filter(p => p.location_id === profileLocId), [extProjects, profileLocId])
  const locFieldService = useMemo(() => fieldServiceOrders.filter(f => f.location_id === profileLocId), [fieldServiceOrders, profileLocId])
  const locTotalOrdered = useMemo(() => locOrders.reduce((s, o) => s + (o.total_price || 0), 0) + locInvoices.reduce((s, i) => s + (i.total || 0), 0), [locOrders, locInvoices])
  const locTotalPaid    = useMemo(() => locInvoices.reduce((s, i) => s + (i.paid_amount || 0), 0) + locPayments.reduce((s, p) => s + (p.amount || 0), 0), [locInvoices, locPayments])

  const WO_STATUS_AR    = { draft:'مسودة', in_progress:'جاري', done:'مكتمل', cancelled:'ملغي' }
  const WO_STATUS_BADGE = { draft:'badge-gray', in_progress:'badge-blue', done:'badge-green', cancelled:'badge-red' }
  const PROJECT_TYPE_AR = { installation:'تركيب', maintenance:'صيانة', repair:'إصلاح', commissioning:'تشغيل', expansion:'توسعة', warranty:'ضمان' }
  const PROJECT_STATUS_AR    = { draft:'مسودة', approved:'معتمد', mobilizing:'تجهيز', in_progress:'جاري', testing:'اختبار', handover:'تسليم', warranty:'ضمان', closed:'مغلق', cancelled:'ملغي' }
  const PROJECT_STATUS_BADGE = { draft:'badge-gray', approved:'badge-blue', mobilizing:'badge-amber', in_progress:'badge-blue', testing:'badge-amber', handover:'badge-green', warranty:'badge-green', closed:'badge-gray', cancelled:'badge-red' }
  const FSO_TYPE_AR    = { maintenance:'صيانة', repair:'إصلاح', installation:'تركيب', inspection:'فحص', other:'أخرى' }
  const FSO_STATUS_AR    = { pending:'معلق', in_progress:'جاري', completed:'مكتمل', cancelled:'ملغي' }
  const FSO_STATUS_BADGE = { pending:'badge-amber', in_progress:'badge-blue', completed:'badge-green', cancelled:'badge-red' }

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button className="btn btn-sm" onClick={onBack}>← العودة</button>
          <h2 className="text-base font-semibold">{customer.name}</h2>
        </div>
        <div className="flex gap-2">
          {write && (
            <button className="btn btn-primary btn-sm" onClick={() => setPayModal(true)}>+ دفعة</button>
          )}
          {write && (
            <button className="btn btn-sm" onClick={() => setLocModal(true)}>📍 المواقع ({(locations || []).length})</button>
          )}
          <button className="btn btn-sm" onClick={() => onNavigate?.('orders')}>+ طلب</button>
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: 'إجمالي المطلوب', val: egp(totalOrdered), sub: 'طلبات + فواتير', color: 'var(--blue)' },
          { label: 'إجمالي المدفوع', val: egp(totalPaid),    sub: '',               color: 'var(--green)' },
          { label: 'الرصيد المستحق', val: egp(totalOwed),    sub: 'مستحق للتحصيل',  color: Number(totalOwed) > 0 ? 'var(--red)' : 'var(--green)' },
          { label: 'التليفون',       val: customer.phone || '—', sub: customer.city || '', color: 'var(--text)' },
        ].map(k => (
          <div key={k.label} className="card">
            <div className="text-xs text-erp-muted mb-1">{k.label}</div>
            <div className="text-xl font-bold" style={{ color: k.color }}>{k.val}</div>
            {k.sub && <div className="text-[11px] text-erp-muted mt-0.5">{k.sub}</div>}
          </div>
        ))}
      </div>

      {/* أقساط متأخرة + أقرب قسط قادم */}
      {(statement?.overdue_installments_count > 0 || statement?.next_installment_date) && (
        <div style={{ display: 'flex', gap: 10 }}>
          {statement?.overdue_installments_count > 0 && (
            <div style={{
              flex: 1, background: '#FEF2F2', border: '1px solid #FCA5A5',
              borderRadius: 8, padding: '10px 14px',
            }}>
              <div style={{ fontSize: 11, color: '#991B1B', fontWeight: 700, marginBottom: 2 }}>
                🔴 أقساط متأخرة
              </div>
              <div style={{ fontSize: 18, fontWeight: 800, color: '#DC2626' }}>
                {egp(statement.overdue_installments_total)}
              </div>
              <div style={{ fontSize: 11, color: '#6B7280' }}>
                {statement.overdue_installments_count} قسط متأخر
              </div>
            </div>
          )}
          {statement?.next_installment_date && (
            <div style={{
              flex: 1, background: '#FFF7ED', border: '1px solid #FCD34D',
              borderRadius: 8, padding: '10px 14px',
            }}>
              <div style={{ fontSize: 11, color: '#92400E', fontWeight: 700, marginBottom: 2 }}>
                📅 أقرب قسط قادم
              </div>
              <div style={{ fontSize: 15, fontWeight: 800, color: '#D97706' }}>
                {statement.next_installment_date}
              </div>
            </div>
          )}
          {customer.credit_limit > 0 && (
            <div style={{
              flex: 1, background: '#F0F9FF', border: '1px solid #BAE6FD',
              borderRadius: 8, padding: '10px 14px',
            }}>
              <div style={{ fontSize: 11, color: '#0369A1', fontWeight: 700, marginBottom: 2 }}>
                🏦 حد الائتمان
              </div>
              <div style={{ fontSize: 15, fontWeight: 800, color: '#0369A1' }}>
                {egp(customer.credit_limit)}
              </div>
              <div style={{ fontSize: 11, color: Number(totalOwed) / customer.credit_limit > 0.8 ? '#DC2626' : '#6B7280' }}>
                مُستخدم {((Number(totalOwed) / customer.credit_limit) * 100).toFixed(0)}%
              </div>
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-erp-border pb-0">
        {tabs.map(t => (
          <button key={t.id}
            className="px-4 py-2 text-sm font-medium border-b-2 transition-colors"
            style={{ marginBottom: '-1px', ...(activeTab === t.id ? { borderColor:'var(--teal)', color:'var(--teal)' } : { borderColor:'transparent', color:'var(--text-muted)' }) }}
            onClick={() => setActiveTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading ? <PageSpinner /> : (
        <div className="grid gap-4" style={{ gridTemplateColumns: activeTab === 'overview' ? '1fr 1fr' : '1fr' }}>
          {/* Overview: Orders + Invoices side by side */}
          {activeTab === 'overview' && (
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
              الطلبات ({orders.length})
            </div>
            {!orders.length ? (
              <div className="p-6 text-center text-erp-muted text-sm">لا توجد طلبات.</div>
            ) : (
              <table className="erp-table">
                <thead>
                  <tr><th>الكود</th><th>الوصف</th><th>الكمية</th><th>السعر</th><th>الإجمالي</th><th>التاريخ</th><th>الحالة</th></tr>
                </thead>
                <tbody>
                  {orders.map(o => {
                    const statusMap = { pending:'معلق', in_progress:'جاري', delivered:'مُسلَّم', cancelled:'ملغي' }
                    const statusColor = { pending:'badge-amber', in_progress:'badge-blue', delivered:'badge-green', cancelled:'badge-red' }
                    return (
                      <tr key={o.id}>
                        <td className="font-mono text-xs">{o.code}</td>
                        <td className="text-sm">{o.description}</td>
                        <td>{o.quantity}</td>
                        <td className="text-sm">{egp(o.unit_price)}</td>
                        <td className="font-semibold">{egp(o.total_price ?? o.total_amount ?? (o.quantity * o.unit_price))}</td>
                        <td className="text-xs text-erp-muted">{fmtDate(o.order_date)}</td>
                        <td><span className={`badge ${statusColor[o.status] || 'badge-gray'}`}>{statusMap[o.status] || o.status}</span></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
          )}

          {activeTab === 'overview' && (
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
              الفواتير ({invoices.length})
            </div>
            {!invoices.length ? (
              <div className="p-6 text-center text-erp-muted text-sm">لا توجد فواتير.</div>
            ) : (
              <table className="erp-table">
                <thead>
                  <tr><th>الكود</th><th>الإجمالي</th><th>المدفوع</th><th>الحالة</th><th>التاريخ</th></tr>
                </thead>
                <tbody>
                  {invoices.map(inv => (
                    <tr key={inv.id}>
                      <td className="font-mono text-xs">{inv.code}</td>
                      <td className="font-semibold">{egp(inv.total ?? inv.total_amount)}</td>
                      <td style={{ color: 'var(--green)' }}>{egp(inv.paid_amount)}</td>
                      <td><span className={`badge ${inv.status === 'paid' ? 'badge-green' : inv.status === 'partial' ? 'badge-amber' : 'badge-red'}`}>
                        {inv.status === 'paid' ? 'مدفوعة' : inv.status === 'partial' ? 'جزئي' : 'غير مدفوعة'}
                      </span></td>
                      <td className="text-xs text-erp-muted">{fmtDate(inv.date ?? inv.issue_date)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          )}

          {/* أوامر الإنتاج */}
          {activeTab === 'production' && (
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
              أوامر الإنتاج ({workOrders.length})
            </div>
            {!workOrders.length ? (
              <div className="p-6 text-center text-erp-muted text-sm">لا توجد أوامر إنتاج مرتبطة بهذا العميل.</div>
            ) : (
              <table className="erp-table">
                <thead>
                  <tr><th>الكود</th><th>الحالة</th><th>التكلفة المخططة</th><th>التكلفة الفعلية</th><th>تاريخ البدء</th><th>تاريخ الانتهاء</th></tr>
                </thead>
                <tbody>
                  {workOrders.map(wo => (
                    <tr key={wo.id}>
                      <td className="font-mono text-xs">{wo.code}</td>
                      <td><span className={`badge ${WO_STATUS_BADGE[wo.status] || 'badge-gray'}`}>{WO_STATUS_AR[wo.status] || wo.status}</span></td>
                      <td className="text-sm">{egp(wo.planned_cost)}</td>
                      <td className="font-semibold">{egp(wo.actual_cost)}</td>
                      <td className="text-xs text-erp-muted">{fmtDate(wo.start_date)}</td>
                      <td className="text-xs text-erp-muted">{fmtDate(wo.end_date)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          )}

          {/* Payments */}
          {activeTab === 'payments' && (
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
              المدفوعات ({payments.length})
            </div>
            {!payments.length ? (
              <div className="p-6 text-center text-erp-muted text-sm">لا توجد مدفوعات.</div>
            ) : (
              <table className="erp-table">
                <thead>
                  <tr><th>التاريخ</th><th>المبلغ</th><th>ملاحظات</th><th>إثبات الدفع</th></tr>
                </thead>
                <tbody>
                  {payments.map(p => (
                    <tr key={p.id}>
                      <td className="text-xs text-erp-muted">{fmtDate(p.payment_date)}</td>
                      <td className="font-bold" style={{ color: 'var(--green)' }}>{egp(p.amount)}</td>
                      <td className="text-erp-muted text-xs">{p.notes || '—'}</td>
                      <td><PaymentAttachment entityType="customer_payment" entityId={p.id} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          )}

          {/* الفواتير (تاب مستقل) */}
          {activeTab === 'invoices' && (
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm flex items-center justify-between">
              <span>الفواتير ({invoices.length})</span>
              {invoices.length > 0 && (
                <div className="flex gap-2">
                  <button className="btn btn-xs" style={{ fontSize: 11 }} onClick={() => openPrint('summary')}>🖨️ طباعة إجمالي</button>
                  <button className="btn btn-xs" style={{ fontSize: 11 }} onClick={() => openPrint('detailed')}>🖨️ طباعة مفصّل</button>
                </div>
              )}
            </div>
            {!invoices.length ? (
              <div className="p-6 text-center text-erp-muted text-sm">لا توجد فواتير.</div>
            ) : (
              <table className="erp-table">
                <thead>
                  <tr><th></th><th>الكود</th><th>الإجمالي</th><th>المدفوع</th><th>المتبقي</th><th>الحالة</th><th>التاريخ</th></tr>
                </thead>
                <tbody>
                  {invoices.map(inv => (
                    <>
                      <tr key={inv.id}
                        onClick={() => toggleInvLines(inv)}
                        style={{ cursor: 'pointer' }}
                        className="hover:bg-gray-50">
                        <td style={{ width: 28, color: '#94a3b8', fontSize: 11 }}>
                          {expandedInv[inv.id] ? '▼' : '▶'}
                        </td>
                        <td className="font-mono text-xs">{inv.code}</td>
                        <td className="font-semibold">{egp(inv.total ?? inv.total_amount)}</td>
                        <td style={{ color: 'var(--green)' }}>{egp(inv.paid_amount)}</td>
                        <td style={{ color: 'var(--red)' }}>{egp(inv.remaining)}</td>
                        <td><span className={`badge ${inv.status === 'paid' ? 'badge-green' : inv.status === 'partial' ? 'badge-amber' : 'badge-red'}`}>
                          {inv.status === 'paid' ? 'مدفوعة' : inv.status === 'partial' ? 'جزئي' : 'غير مدفوعة'}
                        </span></td>
                        <td className="text-xs text-erp-muted">{fmtDate(inv.date ?? inv.issue_date)}</td>
                      </tr>
                      {expandedInv[inv.id] && (
                        <tr key={`${inv.id}-lines`}>
                          <td colSpan={7} style={{ padding: 0, background: '#f8fafc' }}>
                            {!invLines[inv.id] ? (
                              <div style={{ padding: '8px 24px', color: '#94a3b8', fontSize: 12 }}>جارٍ التحميل…</div>
                            ) : invLines[inv.id].length === 0 ? (
                              <div style={{ padding: '8px 24px', color: '#94a3b8', fontSize: 12 }}>لا توجد بنود</div>
                            ) : (
                              <table style={{ width: '100%', fontSize: 11 }}>
                                <thead>
                                  <tr style={{ background: '#e2e8f0' }}>
                                    <th style={{ padding: '4px 24px', textAlign: 'right', fontWeight: 600 }}>البيان</th>
                                    <th style={{ padding: '4px 8px', textAlign: 'center' }}>الكمية</th>
                                    <th style={{ padding: '4px 8px', textAlign: 'center' }}>الوحدة</th>
                                    <th style={{ padding: '4px 8px', textAlign: 'right' }}>سعر الوحدة</th>
                                    <th style={{ padding: '4px 8px', textAlign: 'right' }}>الإجمالي</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {invLines[inv.id].map((l, i) => (
                                    <tr key={i} style={{ borderBottom: '1px solid #e2e8f0' }}>
                                      <td style={{ padding: '5px 24px' }}>{l.description}</td>
                                      <td style={{ padding: '5px 8px', textAlign: 'center' }}>{l.qty}</td>
                                      <td style={{ padding: '5px 8px', textAlign: 'center', color: '#64748b' }}>{l.unit}</td>
                                      <td style={{ padding: '5px 8px' }}>{egp(l.unit_price)}</td>
                                      <td style={{ padding: '5px 8px', fontWeight: 600 }}>{egp(l.total_price)}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            )}
                          </td>
                        </tr>
                      )}
                    </>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          )}

          {/* المشاريع الخارجية */}
          {activeTab === 'projects' && (
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
              المشاريع ({extProjects.length})
            </div>
            {!extProjects.length ? (
              <div className="p-6 text-center text-erp-muted text-sm">لا توجد مشاريع خارجية مرتبطة بهذا العميل.</div>
            ) : (
              <table className="erp-table">
                <thead>
                  <tr><th>الكود</th><th>العنوان</th><th>النوع</th><th>الحالة</th></tr>
                </thead>
                <tbody>
                  {extProjects.map(p => (
                    <tr key={p.id} onClick={() => onNavigate?.('fieldservice')} style={{ cursor: 'pointer' }}
                      title="فتح في المشاريع الخارجية">
                      <td className="font-mono text-xs">{p.code}</td>
                      <td className="text-sm" style={{ color: 'var(--teal)', fontWeight: 600 }}>{p.title}</td>
                      <td className="text-xs">{PROJECT_TYPE_AR[p.project_type] || p.project_type}</td>
                      <td><span className={`badge ${PROJECT_STATUS_BADGE[p.status] || 'badge-gray'}`}>{PROJECT_STATUS_AR[p.status] || p.status}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          )}

          {/* الخدمة الميدانية */}
          {activeTab === 'fieldservice' && (
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
              أوامر الخدمة الميدانية ({fieldServiceOrders.length})
            </div>
            {!fieldServiceOrders.length ? (
              <div className="p-6 text-center text-erp-muted text-sm">لا توجد أوامر خدمة ميدانية لهذا العميل.</div>
            ) : (
              <table className="erp-table">
                <thead>
                  <tr><th>الكود</th><th>العنوان</th><th>النوع</th><th>الحالة</th><th>التكلفة</th><th>التاريخ المخطط</th><th>تاريخ الإتمام</th></tr>
                </thead>
                <tbody>
                  {fieldServiceOrders.map(fso => (
                    <tr key={fso.id}>
                      <td className="font-mono text-xs">{fso.code}</td>
                      <td className="text-sm">{fso.title}</td>
                      <td className="text-xs">{FSO_TYPE_AR[fso.order_type] || fso.order_type}</td>
                      <td><span className={`badge ${FSO_STATUS_BADGE[fso.status] || 'badge-gray'}`}>{FSO_STATUS_AR[fso.status] || fso.status}</span></td>
                      <td className="font-semibold">{egp(fso.total_cost)}</td>
                      <td className="text-xs text-erp-muted">{fmtDate(fso.planned_date)}</td>
                      <td className="text-xs text-erp-muted">{fmtDate(fso.completion_date)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          )}

          {/* Assets */}
          {activeTab === 'assets' && (
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
              الأصول المُسلَّمة ({assets.length})
            </div>
            {!assets.length ? (
              <div className="p-6 text-center text-erp-muted text-sm">لا توجد أصول مُسلَّمة لهذا العميل بعد.</div>
            ) : (
              <table className="erp-table">
                <thead>
                  <tr><th>الاسم</th><th>الموديل</th><th>الرقم التسلسلي</th><th>تاريخ التسليم</th><th>نهاية الضمان</th><th>الحالة</th></tr>
                </thead>
                <tbody>
                  {assets.map(a => {
                    const warrantyActive = a.warranty_end && new Date(a.warranty_end) >= new Date()
                    const statusMap = { active: 'نشطة', inactive: 'متوقفة', retired: 'متقاعدة' }
                    const statusColor = { active: 'badge-green', inactive: 'badge-gray', retired: 'badge-red' }
                    return (
                      <tr key={a.id} onClick={() => setAssetId(a.id)} style={{ cursor: 'pointer' }}
                        title="عرض بروفايل الأصل">
                        <td className="text-sm" style={{ color: 'var(--teal)', fontWeight: 600 }}>{a.name}</td>
                        <td className="font-mono text-xs">{a.model || '—'}</td>
                        <td className="font-mono text-xs">{a.serial_number || '—'}</td>
                        <td className="text-xs text-erp-muted">{fmtDate(a.delivery_date)}</td>
                        <td className="text-xs">
                          {a.warranty_end ? (
                            <span className={`badge ${warrantyActive ? 'badge-green' : 'badge-amber'}`}>
                              {fmtDate(a.warranty_end)} {warrantyActive ? '(سارٍ)' : '(منتهٍ)'}
                            </span>
                          ) : '—'}
                        </td>
                        <td><span className={`badge ${statusColor[a.status] || 'badge-gray'}`}>{statusMap[a.status] || a.status}</span></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
          )}

          {/* بروفايل الموقع */}
          {activeTab === 'location' && (
          <div className="flex flex-col gap-4">
            <div className="card p-0 overflow-hidden">
              <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
                اختر الموقع
              </div>
              <div className="p-4">
                <Select label="الموقع" value={profileLocId ?? ''}
                  onChange={e => setProfileLocId(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">— اختر موقعًا —</option>
                  {(locations || []).map(l => (
                    <option key={l.id} value={l.id}>{l.name}</option>
                  ))}
                </Select>
              </div>
            </div>

            {!profileLocId ? (
              <div className="card p-6 text-center text-erp-muted text-sm">اختر موقعًا لعرض كشف الحساب الخاص به.</div>
            ) : (
              <>
                {/* KPI cards */}
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: 'إجمالي المطلوب', val: egp(locTotalOrdered), color: 'var(--blue)' },
                    { label: 'إجمالي المدفوع', val: egp(locTotalPaid),    color: 'var(--green)' },
                    { label: 'الرصيد المستحق', val: egp(locTotalOrdered - locTotalPaid), color: (locTotalOrdered - locTotalPaid) > 0 ? 'var(--red)' : 'var(--green)' },
                  ].map(k => (
                    <div key={k.label} className="card">
                      <div className="text-xs text-erp-muted mb-1">{k.label}</div>
                      <div className="text-xl font-bold" style={{ color: k.color }}>{k.val}</div>
                    </div>
                  ))}
                </div>

                <div className="grid gap-4" style={{ gridTemplateColumns: '1fr 1fr' }}>
                  {/* الطلبات */}
                  <div className="card p-0 overflow-hidden">
                    <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
                      الطلبات ({locOrders.length})
                    </div>
                    {!locOrders.length ? (
                      <div className="p-6 text-center text-erp-muted text-sm">لا توجد طلبات على هذا الموقع.</div>
                    ) : (
                      <table className="erp-table">
                        <thead>
                          <tr><th>الكود</th><th>الوصف</th><th>الإجمالي</th><th>التاريخ</th></tr>
                        </thead>
                        <tbody>
                          {locOrders.map(o => (
                            <tr key={o.id}>
                              <td className="font-mono text-xs">{o.code}</td>
                              <td className="text-sm">{o.description}</td>
                              <td className="font-semibold">{egp(o.total_price)}</td>
                              <td className="text-xs text-erp-muted">{fmtDate(o.order_date)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>

                  {/* الفواتير */}
                  <div className="card p-0 overflow-hidden">
                    <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
                      الفواتير ({locInvoices.length})
                    </div>
                    {!locInvoices.length ? (
                      <div className="p-6 text-center text-erp-muted text-sm">لا توجد فواتير على هذا الموقع.</div>
                    ) : (
                      <table className="erp-table">
                        <thead>
                          <tr><th>الكود</th><th>الإجمالي</th><th>المدفوع</th><th>التاريخ</th></tr>
                        </thead>
                        <tbody>
                          {locInvoices.map(inv => (
                            <tr key={inv.id}>
                              <td className="font-mono text-xs">{inv.code}</td>
                              <td className="font-semibold">{egp(inv.total)}</td>
                              <td style={{ color: 'var(--green)' }}>{egp(inv.paid_amount)}</td>
                              <td className="text-xs text-erp-muted">{fmtDate(inv.date)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>

                  {/* المدفوعات */}
                  <div className="card p-0 overflow-hidden" style={{ gridColumn: '1 / -1' }}>
                    <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
                      المدفوعات ({locPayments.length})
                    </div>
                    {!locPayments.length ? (
                      <div className="p-6 text-center text-erp-muted text-sm">لا توجد مدفوعات على هذا الموقع.</div>
                    ) : (
                      <table className="erp-table">
                        <thead>
                          <tr><th>التاريخ</th><th>المبلغ</th><th>ملاحظات</th></tr>
                        </thead>
                        <tbody>
                          {locPayments.map(p => (
                            <tr key={p.id}>
                              <td className="text-xs text-erp-muted">{fmtDate(p.payment_date)}</td>
                              <td className="font-bold" style={{ color: 'var(--green)' }}>{egp(p.amount)}</td>
                              <td className="text-erp-muted text-xs">{p.notes || '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>

                  {/* الأصول */}
                  <div className="card p-0 overflow-hidden" style={{ gridColumn: '1 / -1' }}>
                    <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
                      الأصول الموجودة بالموقع ({locAssets.length})
                    </div>
                    {!locAssets.length ? (
                      <div className="p-6 text-center text-erp-muted text-sm">لا توجد أصول مُسجَّلة في هذا الموقع.</div>
                    ) : (
                      <table className="erp-table">
                        <thead>
                          <tr><th>الاسم</th><th>الموديل</th><th>الرقم التسلسلي</th><th>الحالة</th></tr>
                        </thead>
                        <tbody>
                          {locAssets.map(a => {
                            const statusMap = { active: 'نشطة', inactive: 'متوقفة', retired: 'متقاعدة' }
                            const statusColor = { active: 'badge-green', inactive: 'badge-gray', retired: 'badge-red' }
                            return (
                              <tr key={a.id} onClick={() => setAssetId(a.id)} style={{ cursor: 'pointer' }} title="عرض بروفايل الأصل">
                                <td className="text-sm" style={{ color: 'var(--teal)', fontWeight: 600 }}>{a.name}</td>
                                <td className="font-mono text-xs">{a.model || '—'}</td>
                                <td className="font-mono text-xs">{a.serial_number || '—'}</td>
                                <td><span className={`badge ${statusColor[a.status] || 'badge-gray'}`}>{statusMap[a.status] || a.status}</span></td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    )}
                  </div>

                  {/* المشاريع وأوامر الخدمة */}
                  <div className="card p-0 overflow-hidden" style={{ gridColumn: '1 / -1' }}>
                    <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
                      المشاريع وأوامر الخدمة بالموقع ({locProjects.length + locFieldService.length})
                    </div>
                    {!(locProjects.length + locFieldService.length) ? (
                      <div className="p-6 text-center text-erp-muted text-sm">لا توجد مشاريع أو أوامر خدمة على هذا الموقع.</div>
                    ) : (
                      <table className="erp-table">
                        <thead>
                          <tr><th>الكود</th><th>العنوان/النوع</th><th>النوع</th><th>الحالة</th></tr>
                        </thead>
                        <tbody>
                          {locProjects.map(p => (
                            <tr key={`proj-${p.id}`}>
                              <td className="font-mono text-xs">{p.code}</td>
                              <td className="text-sm" style={{ color: 'var(--teal)', fontWeight: 600 }}>{p.title}</td>
                              <td className="text-xs">مشروع — {PROJECT_TYPE_AR[p.project_type] || p.project_type}</td>
                              <td><span className={`badge ${PROJECT_STATUS_BADGE[p.status] || 'badge-gray'}`}>{PROJECT_STATUS_AR[p.status] || p.status}</span></td>
                            </tr>
                          ))}
                          {locFieldService.map(f => (
                            <tr key={`fso-${f.id}`}>
                              <td className="font-mono text-xs">{f.code}</td>
                              <td className="text-sm">{f.title}</td>
                              <td className="text-xs">خدمة ميدانية — {FSO_TYPE_AR[f.order_type] || f.order_type}</td>
                              <td><span className={`badge ${FSO_STATUS_BADGE[f.status] || 'badge-gray'}`}>{FSO_STATUS_AR[f.status] || f.status}</span></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
          )}
        </div>
      )}

      {/* Payment modal */}
      <Modal open={payModal} onClose={() => setPayModal(false)}
        title={`تسجيل دفعة — ${customer.name}`} size="sm">
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between px-3 py-2 rounded-erp"
               style={{ background: 'var(--red-lt)' }}>
            <span className="text-sm text-erp-muted">الرصيد المستحق</span>
            <span className="font-bold" style={{ color: 'var(--red)' }}>{egp(totalOwed)}</span>
          </div>
          <Input label="المبلغ المدفوع (ج) *" type="number" step="0.01"
            value={payForm.amount}
            onChange={e => setPayForm(f => ({ ...f, amount: e.target.value }))}
            placeholder="0.00" />
          <FormGrid cols={2}>
            <Input label="تاريخ الدفعة" type="date"
              value={payForm.payment_date}
              onChange={e => setPayForm(f => ({ ...f, payment_date: e.target.value }))} />
            <Select label="طريقة الدفع"
              value={payForm.payment_type || 'cash'}
              onChange={e => setPayForm(f => ({ ...f, payment_type: e.target.value }))}>
              <option value="cash">نقدي</option>
              <option value="transfer">تحويل بنكي</option>
              <option value="cheque">شيك</option>
              <option value="bill">كمبيالة</option>
            </Select>
          </FormGrid>
          <Input label="رقم المرجع (رقم الشيك / رقم التحويل)"
            value={payForm.reference || ''}
            onChange={e => setPayForm(f => ({ ...f, reference: e.target.value }))}
            placeholder="اختياري" />
          <Textarea label="ملاحظات" rows={2}
            value={payForm.notes}
            onChange={e => setPayForm(f => ({ ...f, notes: e.target.value }))} />
          <div>
            <div className="text-xs text-erp-muted mb-1">إثبات الدفع (اختياري — صورة)</div>
            <input type="file" accept="image/*"
              onChange={e => setPayFile(e.target.files?.[0] || null)}
              style={{ fontSize: 13 }} />
            {payFile && <div className="text-xs text-erp-muted mt-1">📎 {payFile.name}</div>}
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setPayModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={recordPay} disabled={paying}
            style={{ background: 'var(--green)', borderColor: 'var(--green)' }}>
            {paying ? '…' : '💵 تسجيل الدفعة'}
          </button>
        </ModalFooter>
      </Modal>

      {/* مودال إدارة المواقع */}
      <Modal open={locModal} onClose={() => setLocModal(false)}
        title={`مواقع العميل — ${customer.name}`}>
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-2 items-end">
            <Input label="اسم الموقع *" value={locForm.name}
              onChange={e => setLocForm(f => ({ ...f, name: e.target.value }))}
              placeholder="فرع المعادي / المصنع..." />
            <Input label="تليفون" value={locForm.phone}
              onChange={e => setLocForm(f => ({ ...f, phone: e.target.value }))} />
            <Input label="العنوان" value={locForm.address}
              onChange={e => setLocForm(f => ({ ...f, address: e.target.value }))} />
            <Input label="مسؤول الموقع" value={locForm.contact_person}
              onChange={e => setLocForm(f => ({ ...f, contact_person: e.target.value }))} />
          </div>
          <div className="flex justify-end">
            <button className="btn btn-primary btn-sm" disabled={addingLoc} onClick={addLoc}>
              {addingLoc ? '...' : '+ إضافة موقع'}
            </button>
          </div>
          <div className="border-t pt-3" style={{ borderColor: 'var(--border)' }}>
            <div className="text-xs text-erp-muted mb-2">المواقع ({(locations || []).length})</div>
            <div className="flex flex-col gap-1 max-h-64 overflow-y-auto">
              {(locations || []).map(l => (
                <div key={l.id} className="flex items-center justify-between py-1.5 px-2 rounded"
                  style={{ background: 'var(--surface-2)' }}>
                  <div className="text-sm">
                    <strong>{l.name}</strong>
                    {l.address && <span className="text-[11px] text-erp-muted"> — {l.address}</span>}
                    {l.phone && <span className="text-[11px] text-erp-muted"> — ☎ {l.phone}</span>}
                  </div>
                  <button className="btn btn-sm" style={{ color: 'var(--red)' }}
                    onClick={() => setConfirmLocDel(l)}>🗑</button>
                </div>
              ))}
              {!(locations || []).length && (
                <div className="text-sm text-erp-muted text-center py-4">لا توجد مواقع — أضف أول موقع</div>
              )}
            </div>
          </div>
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setLocModal(false)}>إغلاق</button>
        </ModalFooter>
      </Modal>
      <ConfirmDialog
        open={!!confirmLocDel}
        onClose={() => setConfirmLocDel(null)}
        onConfirm={async () => {
          try { await api.customers.deleteLocation(confirmLocDel.id); toast('تم الحذف'); reloadLocs() }
          catch (err) { toast(err?.message || 'تعذّر الحذف', 'error') }
          setConfirmLocDel(null)
        }}
        title="حذف موقع"
        message={`حذف موقع "${confirmLocDel?.name}"؟`}
        danger
      />

      {/* بروفايل الأصل */}
      <Modal open={!!assetId} onClose={() => setAssetId(null)}
        title={assetDetail ? `بروفايل الأصل — ${assetDetail.name}` : 'بروفايل الأصل'}>
        {assetLoading || !assetDetail ? <PageSpinner /> : (
          <div className="flex flex-col gap-4">
            {(() => {
              const statusMap = { active: 'نشطة', inactive: 'متوقفة', retired: 'متقاعدة' }
              const statusColor = { active: 'badge-green', inactive: 'badge-gray', retired: 'badge-red' }
              const calcUnitMap = { piece: 'قطعة', meter: 'متر', ton_hr: 'طن/ساعة', kw: 'كيلوواط' }
              return (
                <>
                  {/* معلومات أساسية */}
                  <div className="card p-0 overflow-hidden">
                    <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
                      معلومات الأصل
                    </div>
                    <div className="grid grid-cols-3 gap-3 p-4">
                      <div>
                        <div className="text-xs text-erp-muted mb-1">العميل</div>
                        <div className="text-sm font-medium">{assetDetail.customer_name || '—'}</div>
                      </div>
                      <div>
                        <div className="text-xs text-erp-muted mb-1">الموديل</div>
                        <div className="font-mono text-sm">{assetDetail.model || '—'}</div>
                      </div>
                      <div>
                        <div className="text-xs text-erp-muted mb-1">الرقم التسلسلي</div>
                        <div className="font-mono text-sm">{assetDetail.serial_number || '—'}</div>
                      </div>
                      <div>
                        <div className="text-xs text-erp-muted mb-1">الموقع</div>
                        <div className="text-sm">{assetDetail.location || '—'}</div>
                      </div>
                      <div>
                        <div className="text-xs text-erp-muted mb-1">تاريخ التسليم</div>
                        <div className="text-sm">{fmtDate(assetDetail.delivery_date)}</div>
                      </div>
                      <div>
                        <div className="text-xs text-erp-muted mb-1">نهاية الضمان</div>
                        <div className="text-sm">
                          {assetDetail.warranty_end ? (
                            <span className={`badge ${assetDetail.in_warranty ? 'badge-green' : 'badge-amber'}`}>
                              {fmtDate(assetDetail.warranty_end)} {assetDetail.in_warranty ? '(سارٍ)' : '(منتهٍ)'}
                            </span>
                          ) : '—'}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-erp-muted mb-1">الحالة</div>
                        <span className={`badge ${statusColor[assetDetail.status] || 'badge-gray'}`}>
                          {statusMap[assetDetail.status] || assetDetail.status}
                        </span>
                      </div>
                      <div>
                        <div className="text-xs text-erp-muted mb-1">تاريخ الإنشاء</div>
                        <div className="text-sm">{fmtDate(assetDetail.created_at)}</div>
                      </div>
                      <div style={{ gridColumn: '1 / -1' }}>
                        <div className="text-xs text-erp-muted mb-1">ملاحظات</div>
                        <div className="text-sm">{assetDetail.notes || '—'}</div>
                      </div>
                    </div>
                  </div>

                  {/* معدة القالب المرتبطة */}
                  {assetDetail.equipment_id && (
                    <div className="card p-0 overflow-hidden">
                      <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
                        معدة القالب المرتبطة
                      </div>
                      {!assetEquipment ? (
                        <div className="p-4 text-sm text-erp-muted">جارٍ التحميل…</div>
                      ) : (
                        <div className="grid grid-cols-3 gap-3 p-4">
                          <div>
                            <div className="text-xs text-erp-muted mb-1">الكود</div>
                            <div className="font-mono text-sm">{assetEquipment.code}</div>
                          </div>
                          <div>
                            <div className="text-xs text-erp-muted mb-1">الاسم</div>
                            <div className="text-sm">{assetEquipment.name_ar}</div>
                          </div>
                          <div>
                            <div className="text-xs text-erp-muted mb-1">الوزن</div>
                            <div className="text-sm">{assetEquipment.weight_kg ? `${assetEquipment.weight_kg} كجم` : '—'}</div>
                          </div>
                          <div>
                            <div className="text-xs text-erp-muted mb-1">وحدة الحساب</div>
                            <div className="text-sm">{calcUnitMap[assetEquipment.calc_unit] || assetEquipment.calc_unit}</div>
                          </div>
                          {!!(assetEquipment.dimensions?.length) && (
                            <div style={{ gridColumn: '1 / -1' }}>
                              <div className="text-xs text-erp-muted mb-1">الأبعاد</div>
                              <div className="flex flex-wrap gap-2">
                                {assetEquipment.dimensions.map(d => (
                                  <span key={d.id} className="badge badge-gray">{d.dim_key}: {d.dim_value} {d.unit}</span>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* سجل حركة الأصل (Timeline) */}
                  <div className="card p-0 overflow-hidden">
                    <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm flex items-center justify-between">
                      <span>سجل حركة الأصل ({(assetDetail.movements || []).length})</span>
                      {write && (
                        <button className="btn btn-primary btn-sm" onClick={() => setMoveModal(true)}>+ تسجيل حركة</button>
                      )}
                    </div>
                    {!(assetDetail.movements || []).length ? (
                      <div className="p-6 text-center text-erp-muted text-sm">لا توجد حركات مسجلة لهذا الأصل.</div>
                    ) : (
                      <div className="flex flex-col gap-2 p-3">
                        {assetDetail.movements.map(m => {
                          const moveBadge = {
                            manufactured:'badge-gray', delivered:'badge-blue', installed:'badge-green',
                            relocated:'badge-amber', maintained:'badge-amber', returned:'badge-red',
                          }[m.event_type] || 'badge-gray'
                          return (
                            <div key={m.id} className="flex items-center justify-between gap-3 flex-wrap rounded p-2" style={{ background:'var(--bg-secondary)' }}>
                              <div className="flex items-center gap-2">
                                <span className={`badge ${moveBadge} text-[11px]`}>{m.event_type_ar}</span>
                                <span className="text-xs">
                                  {m.from_location_name || m.to_location_name
                                    ? `${m.from_location_name || '—'} ← ${m.to_location_name || '—'}`
                                    : (m.notes || '—')}
                                </span>
                              </div>
                              <div className="text-[11px] text-erp-muted">{fmtDate(m.event_date)}</div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>

                  {/* سجل الخدمة الميدانية */}
                  <div className="card p-0 overflow-hidden">
                    <div className="px-4 py-3 border-b border-erp-border font-semibold text-sm">
                      سجل الخدمة الميدانية ({(assetDetail.service_history || []).length})
                    </div>
                    {!(assetDetail.service_history || []).length ? (
                      <div className="p-6 text-center text-erp-muted text-sm">لا يوجد سجل خدمة ميدانية لهذا الأصل.</div>
                    ) : (
                      <table className="erp-table">
                        <thead>
                          <tr><th>الكود</th><th>العنوان</th><th>النوع</th><th>الحالة</th><th>التاريخ المخطط</th><th>تاريخ الإتمام</th></tr>
                        </thead>
                        <tbody>
                          {assetDetail.service_history.map(so => (
                            <tr key={so.id}>
                              <td className="font-mono text-xs">{so.code}</td>
                              <td className="text-sm">{so.title}</td>
                              <td className="text-xs">{so.order_type_ar || so.order_type}</td>
                              <td><span className="badge badge-blue">{so.status_ar || so.status}</span></td>
                              <td className="text-xs text-erp-muted">{fmtDate(so.planned_date)}</td>
                              <td className="text-xs text-erp-muted">{fmtDate(so.completion_date)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </>
              )
            })()}
          </div>
        )}
        <ModalFooter>
          <button className="btn" onClick={() => setAssetId(null)}>إغلاق</button>
          {write && (
            <button className="btn btn-primary btn-sm" onClick={() => { setAssetId(null); onNavigate?.('fieldservice') }}>
              🛠️ فتح في الخدمة الميدانية
            </button>
          )}
        </ModalFooter>
      </Modal>

      {/* تسجيل حركة للأصل */}
      <Modal open={moveModal} onClose={() => setMoveModal(false)} title="تسجيل حركة للمعدة" size="sm">
        <div className="flex flex-col gap-3">
          <Select label="نوع الحركة *" value={moveForm.event_type}
            onChange={e => setMoveForm(f => ({ ...f, event_type: e.target.value }))}>
            {MOVEMENT_TYPES.map(t => <option key={t} value={t}>{MOVEMENT_TYPE_AR[t]}</option>)}
          </Select>
          {['installed', 'relocated'].includes(moveForm.event_type) && (
            <Select label="الموقع الجديد *" value={moveForm.to_location_id || ''}
              onChange={e => setMoveForm(f => ({ ...f, to_location_id: e.target.value }))}>
              <option value="">— اختر —</option>
              {(locations || []).map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            </Select>
          )}
          <Input label="تاريخ الحركة *" type="date" value={moveForm.event_date}
            onChange={e => setMoveForm(f => ({ ...f, event_date: e.target.value }))} />
          <Textarea label="ملاحظات" rows={2} value={moveForm.notes || ''}
            onChange={e => setMoveForm(f => ({ ...f, notes: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setMoveModal(false)}>إلغاء</button>
          <button className="btn btn-primary" onClick={saveMove} disabled={savingMove}>{savingMove ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
export default function Customers({ onNavigate }) {
  const { canWrite } = useAuth()
  const toast = useToast()
  const write = canWrite('customers')

  const { data: customers, loading, reload } = useApi(() => api.customers.list())
  const [q, setQ] = useState('')
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState({})
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [profile, setProfile] = useState(null)

  const filtered = useMemo(() => {
    if (!customers) return []
    const lq = q.toLowerCase()
    return !lq ? customers : customers.filter(c =>
      c.name?.toLowerCase().includes(lq) ||
      c.code?.toLowerCase().includes(lq) ||
      c.phone?.includes(lq)
    )
  }, [customers, q])

  const openAdd  = async () => {
    setForm({})
    setModal({ mode: 'add' })
    try { const r = await api.customers.nextCode(); setForm(f => ({ ...f, code: r.code })) } catch (_) {}
  }
  const openEdit = (c) => { setForm({ ...c }); setModal({ mode: 'edit', item: c }) }

  const [save, saving] = useAction(async () => {
    if (!form.name?.trim()) { toast('الاسم مطلوب', 'error'); return }
    if (form.credit_limit && Number(form.credit_limit) < 0) { toast('حد الائتمان لا يمكن أن يكون سالباً', 'error'); return }
    const payload = {
      code: form.code || '', name: form.name,
      phone: form.phone || null, city: form.city || null,
      address: form.address || null, tax_id: form.tax_id || null,
      credit_limit: Number(form.credit_limit || 0),
      payment_terms: form.payment_terms || null,
      notes: form.notes || null,
    }
    if (modal?.mode === 'edit') await api.customers.update(form.id, payload)
    else await api.customers.create(payload)
    toast('تم الحفظ')
    setModal(null)
    reload()
  })

  const [doDelete] = useAction(async () => {
    await api.customers.delete(deleteTarget.id)
    toast('تم الحذف')
    setDeleteTarget(null)
    reload()
  })

  if (loading) return <PageSpinner />

  // Show profile view
  if (profile) {
    const fullCustomer = customers?.find(c => c.id === profile.id) || profile
    return <CustomerProfile customer={fullCustomer} onBack={() => setProfile(null)} write={write} onNavigate={onNavigate} />
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="card flex items-center gap-3 py-3">
        <div className="flex-1">
          <SearchInput value={q} onChange={setQ} placeholder="بحث بالاسم أو الكود أو التليفون…" />
        </div>
        <div className="text-xs text-erp-muted">العملاء ({filtered.length})</div>
        <button className="btn btn-sm text-xs" title="تصدير Excel"
          onClick={() => api.export.customers()}>📥 Excel</button>
        {write && <button className="btn btn-primary btn-sm" onClick={openAdd}>+ إضافة</button>}
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="erp-table">
          <thead>
            <tr>
              <th>الكود</th>
              <th>الاسم</th>
              <th>التليفون</th>
              <th>الرصيد</th>
              <th>حد الائتمان</th>
              <th>شروط الدفع</th>
              <th style={{ width: 180 }}></th>
            </tr>
          </thead>
          <tbody>
            {!filtered.length && (
              <tr><td colSpan={7} className="text-center text-erp-muted py-10">لا يوجد عملاء</td></tr>
            )}
            {filtered.map(c => {
              const bal = Number(c.balance || 0)
              const TERMS = { cash: 'نقدي', net_15: '15 يوم', net_30: '30 يوم', net_60: '60 يوم', net_90: '90 يوم' }
              return (
                <tr key={c.id}>
                  <td className="font-mono text-xs">{c.code}</td>
                  <td className="font-medium">{c.name}</td>
                  <td className="font-mono text-sm">{c.phone || '—'}</td>
                  <td className="font-semibold text-sm"
                      style={{ color: bal > 0 ? 'var(--red)' : bal < 0 ? 'var(--green)' : 'var(--muted)' }}>
                    {bal === 0 ? 'صفر' : bal > 0 ? `عليه ${egp(bal)}` : `له ${egp(Math.abs(bal))}`}
                  </td>
                  <td className="text-xs text-erp-muted">
                    {c.credit_limit ? egp(c.credit_limit) : '—'}
                  </td>
                  <td className="text-xs text-erp-muted">
                    {TERMS[c.payment_terms] || c.payment_terms || '—'}
                  </td>
                  <td>
                    <div className="flex gap-1.5">
                      <button className="btn btn-sm text-xs"
                        style={{ color: 'var(--teal)', borderColor: 'var(--teal-lt)', background: 'var(--teal-lt)' }}
                        onClick={() => setProfile(c)}>
                        بروفايل
                      </button>
                      {write && (
                        <>
                          <button className="btn btn-sm text-xs" onClick={() => openEdit(c)}>تعديل</button>
                          <button className="btn btn-sm text-xs"
                            style={{ color: 'var(--blue)', borderColor: 'var(--blue-lt)' }}
                            onClick={() => setProfile(c)}>
                            دفعة
                          </button>
                          <button className="btn btn-sm text-xs"
                            style={{ color: 'var(--red)', borderColor: 'var(--red-lt)' }}
                            onClick={() => setDeleteTarget(c)}>
                            🗑
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <Modal open={!!modal} onClose={() => setModal(null)}
        title={modal?.mode === 'edit' ? 'تعديل عميل' : 'عميل جديد'}>
        <div className="flex flex-col gap-3">
          <FormGrid cols={2}>
            {modal?.mode === 'edit'
              ? <Input label="الكود" value={form.code || ''} readOnly
                  style={{ background: 'var(--bg-alt)', cursor: 'not-allowed', opacity: 0.7 }} />
              : <Input label="الكود (اختياري — يُولَّد تلقائياً)" value={form.code || ''}
                  onChange={e => setForm(f => ({ ...f, code: e.target.value }))} placeholder="CUST-0001" />
            }
            <Input label="الاسم *" value={form.name || ''}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="التليفون" value={form.phone || ''}
              onChange={e => setForm(f => ({ ...f, phone: e.target.value }))} />
            <Input label="المدينة" value={form.city || ''}
              onChange={e => setForm(f => ({ ...f, city: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Input label="الرقم الضريبي" value={form.tax_id || ''}
              onChange={e => setForm(f => ({ ...f, tax_id: e.target.value }))} />
            <Input label="حد الائتمان (ج)" type="number" value={form.credit_limit || ''}
              onChange={e => setForm(f => ({ ...f, credit_limit: e.target.value }))} />
          </FormGrid>
          <FormGrid cols={2}>
            <Select label="شروط الدفع" value={form.payment_terms || ''}
              onChange={e => setForm(f => ({ ...f, payment_terms: e.target.value }))}>
              <option value="">— اختر —</option>
              <option value="cash">نقدي فوري</option>
              <option value="net_15">صافي 15 يوم</option>
              <option value="net_30">صافي 30 يوم</option>
              <option value="net_60">صافي 60 يوم</option>
              <option value="net_90">صافي 90 يوم</option>
            </Select>
            <Textarea label="ملاحظات" rows={2} value={form.notes || ''}
              onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
          </FormGrid>
          <Textarea label="العنوان" rows={2} value={form.address || ''}
            onChange={e => setForm(f => ({ ...f, address: e.target.value }))} />
        </div>
        <ModalFooter>
          <button className="btn" onClick={() => setModal(null)}>إلغاء</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'حفظ'}</button>
        </ModalFooter>
      </Modal>

      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={doDelete}
        title="حذف العميل"
        message={`حذف العميل "${deleteTarget?.name}"؟ لا يمكن التراجع.`}
        danger
      />
    </div>
  )
}
