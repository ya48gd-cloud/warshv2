/**
 * Installments — الأقساط المستحقة
 * صفحة مركزية لمتابعة تحصيل الأقساط بدون الحاجة للدخول على كل فاتورة.
 * المراحل 1 + 2 من خطة تطوير الأقساط.
 */
import { useState, useEffect, useCallback } from 'react'
import { useToast } from '../store/toast'
import api, { bgErr } from '../api/client'
import { egp, fmtDate } from '../utils/fmt'
import Modal, { ModalFooter } from '../components/ui/Modal'
import { Input, Select } from '../components/ui/Field'
import { PageSpinner } from '../components/ui/Spinner'

// ── أزرار التبويب ─────────────────────────────────────────────
const TABS = [
  { key: 'overdue', label: 'متأخرة', icon: '🔴' },
  { key: 'week',    label: 'خلال 7 أيام', icon: '🟡' },
  { key: 'month',   label: 'خلال 30 يوم', icon: '🟠' },
  { key: 'all',     label: 'الكل', icon: '📋' },
]

const PAY_METHODS = [
  { value: 'cash',     label: 'نقداً' },
  { value: 'transfer', label: 'تحويل بنكي' },
  { value: 'check',    label: 'شيك' },
  { value: 'other',    label: 'أخرى' },
]

// ── مودال تسجيل دفعة ─────────────────────────────────────────
function PayModal({ installment, onClose, onDone }) {
  const toast = useToast()
  const remaining = installment.remaining_amount
  const [amount, setAmount]   = useState(remaining.toFixed(2))
  const [method, setMethod]   = useState('cash')
  const [notes, setNotes]     = useState('')
  const [loading, setLoading] = useState(false)

  async function submit() {
    const amt = parseFloat(amount)
    if (!amt || amt <= 0) return toast('أدخل مبلغ صحيح', 'error')
    if (amt > remaining + 0.01) return toast(`المبلغ أكبر من المتبقي (${remaining.toFixed(2)})`, 'error')
    setLoading(true)
    try {
      await api.sales.payInstallment(installment.invoice_id, installment.installment_id, {
        amount: amt, payment_type: method, notes,
      })
      toast(`✅ تم تسجيل دفعة ${egp(amt)} بنجاح`)
      onDone()
    } catch (e) {
      toast(e.message || 'فشل تسجيل الدفعة', 'error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal open onClose={onClose} title={`💰 تسجيل دفعة — ${installment.label || 'قسط ' + installment.installment_no}`}>
      <div style={{ padding: '0 4px' }}>
        <div style={{ background: 'var(--bg)', borderRadius: 8, padding: '10px 14px', marginBottom: 16, fontSize: 13 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>{installment.invoice_code} — {installment.customer_name}</div>
          <div style={{ color: 'var(--text-2)' }}>
            الاستحقاق: {fmtDate(installment.due_date)} ·
            المتبقي: <span style={{ color: 'var(--red)', fontWeight: 700 }}>{egp(remaining)}</span>
          </div>
        </div>
        <div style={{ display: 'grid', gap: 12 }}>
          <Input
            label="المبلغ المدفوع"
            type="number" step="0.01" min="0.01"
            value={amount}
            onChange={e => setAmount(e.target.value)}
          />
          <Select
            label="طريقة الدفع"
            value={method}
            onChange={e => setMethod(e.target.value)}
            options={PAY_METHODS}
          />
          <Input
            label="ملاحظات (اختياري)"
            value={notes}
            onChange={e => setNotes(e.target.value)}
          />
        </div>
      </div>
      <ModalFooter>
        <button className="btn btn-primary" onClick={submit} disabled={loading}>
          {loading ? '...' : 'تسجيل الدفعة'}
        </button>
        <button className="btn" onClick={onClose}>إلغاء</button>
      </ModalFooter>
    </Modal>
  )
}

// ── مودال تعديل قسط ─────────────────────────────────────────
function EditInstallmentModal({ installment, onClose, onDone }) {
  const toast = useToast()
  const [dueDate, setDueDate] = useState(installment.due_date)
  const [amount, setAmount]   = useState(installment.amount_due.toFixed(2))
  const [label, setLabel]     = useState(installment.label || '')
  const [loading, setLoading] = useState(false)

  async function submit() {
    setLoading(true)
    try {
      await api.sales.patchInstallment(installment.invoice_id, installment.installment_id, {
        due_date:   dueDate || undefined,
        amount_due: amount ? parseFloat(amount) : undefined,
        label:      label || undefined,
      })
      toast('✅ تم تعديل القسط')
      onDone()
    } catch (e) {
      toast(e.message || 'فشل تعديل القسط', 'error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal open onClose={onClose} title={`✏️ تعديل قسط — ${installment.invoice_code}`}>
      <div style={{ display: 'grid', gap: 12, padding: '0 4px' }}>
        <Input
          label="تسمية القسط"
          value={label}
          onChange={e => setLabel(e.target.value)}
          placeholder="مثال: دفعة مقدمة"
        />
        <Input
          label="تاريخ الاستحقاق"
          type="date"
          value={dueDate}
          onChange={e => setDueDate(e.target.value)}
        />
        <Input
          label="المبلغ"
          type="number" step="0.01" min="0.01"
          value={amount}
          onChange={e => setAmount(e.target.value)}
        />
        <div style={{ fontSize: 12, color: 'var(--text-2)', background: 'var(--bg)', borderRadius: 6, padding: '8px 10px' }}>
          ⚠️ تعديل المبلغ يجب أن يبقى مجموع الأقساط مساوياً لإجمالي الفاتورة.
        </div>
      </div>
      <ModalFooter>
        <button className="btn btn-primary" onClick={submit} disabled={loading}>
          {loading ? '...' : 'حفظ التعديل'}
        </button>
        <button className="btn" onClick={onClose}>إلغاء</button>
      </ModalFooter>
    </Modal>
  )
}

// ── صف قسط واحد ──────────────────────────────────────────────
function InstallmentRow({ ins, onPay, onEdit, onNavigate }) {
  const isOverdue  = ins.days_overdue > 0
  const daysLabel  = isOverdue
    ? <span style={{ color: 'var(--red)', fontWeight: 700 }}>متأخر {ins.days_overdue} يوم</span>
    : <span style={{ color: 'var(--amber)' }}>خلال {-ins.days_overdue} يوم</span>

  return (
    <tr style={{ borderBottom: '1px solid var(--border)' }}>
      <td style={{ padding: '10px 12px' }}>
        <button
          className="link-btn"
          style={{ fontWeight: 700, color: 'var(--primary)', textDecoration: 'underline', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
          onClick={() => onNavigate && onNavigate('invoices', { highlight: ins.invoice_id })}
        >
          {ins.invoice_code}
        </button>
      </td>
      <td style={{ padding: '10px 12px' }}>
        {ins.customer_name}
        {ins.customer_phone && (
          <a href={`tel:${ins.customer_phone}`} style={{ marginRight: 6, fontSize: 12, color: 'var(--primary)' }}>
            📞
          </a>
        )}
      </td>
      <td style={{ padding: '10px 12px' }}>
        {ins.label || `قسط ${ins.installment_no}`}
      </td>
      <td style={{ padding: '10px 12px' }}>{fmtDate(ins.due_date)}</td>
      <td style={{ padding: '10px 12px' }}>{daysLabel}</td>
      <td style={{ padding: '10px 12px', fontWeight: 700 }}>
        {egp(ins.remaining_amount)}
      </td>
      <td style={{ padding: '10px 12px' }}>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn btn-sm btn-primary" onClick={() => onPay(ins)}>
            💰 دفع
          </button>
          <button className="btn btn-sm" onClick={() => onEdit(ins)}>
            ✏️ تعديل
          </button>
        </div>
      </td>
    </tr>
  )
}

// ── الصفحة الرئيسية ───────────────────────────────────────────
export default function Installments({ onNavigate }) {
  const toast = useToast()
  const [tab, setTab]         = useState('overdue')
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [payModal, setPayModal]   = useState(null)   // installment object
  const [editModal, setEditModal] = useState(null)   // installment object

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.reports.installmentsAll(tab)
      setData(res)
    } catch (e) {
      toast(e.message || 'فشل تحميل الأقساط', 'error')
    } finally {
      setLoading(false)
    }
  }, [tab])

  useEffect(() => { load() }, [load])

  function afterAction() {
    setPayModal(null)
    setEditModal(null)
    load()
  }

  const items = data?.items || []
  const overdueItems  = items.filter(x => x.days_overdue > 0)
  const upcomingItems = items.filter(x => x.days_overdue <= 0)

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      {/* رأس الصفحة */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>📅 الأقساط المستحقة</h1>
        {data && data.overdue_count > 0 && (
          <span style={{
            background: 'var(--red)', color: '#fff',
            borderRadius: 20, padding: '2px 10px', fontSize: 12, fontWeight: 700,
          }}>
            {data.overdue_count} متأخر
          </span>
        )}
      </div>

      {/* ملخص أرقام */}
      {data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginBottom: 20 }}>
          <StatCard label="إجمالي المتأخر" value={egp(data.overdue_total)} color="var(--red)" />
          <StatCard label="إجمالي القادم" value={egp(data.upcoming_total)} color="var(--amber)" />
          <StatCard label="إجمالي الكل" value={egp((data.overdue_total || 0) + (data.upcoming_total || 0))} color="var(--primary)" />
        </div>
      )}

      {/* التبويبات */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, borderBottom: '2px solid var(--border)', paddingBottom: 0 }}>
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              padding: '8px 16px', border: 'none', background: 'none', cursor: 'pointer',
              fontFamily: 'inherit', fontSize: 13, fontWeight: tab === t.key ? 800 : 400,
              color: tab === t.key ? 'var(--primary)' : 'var(--text-2)',
              borderBottom: tab === t.key ? '2px solid var(--primary)' : '2px solid transparent',
              marginBottom: -2,
            }}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {/* المحتوى */}
      {loading ? (
        <PageSpinner />
      ) : items.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-2)' }}>
          <div style={{ fontSize: 32, marginBottom: 8 }}>✅</div>
          <div>لا توجد أقساط في هذا التصنيف</div>
        </div>
      ) : (
        <>
          {/* الأقساط المتأخرة */}
          {overdueItems.length > 0 && (
            <section style={{ marginBottom: 28 }}>
              <h3 style={{ fontSize: 14, fontWeight: 700, color: 'var(--red)', marginBottom: 10 }}>
                🔴 متأخرة ({overdueItems.length})
              </h3>
              <InstallmentTable rows={overdueItems} onPay={setPayModal} onEdit={setEditModal} onNavigate={onNavigate} />
            </section>
          )}

          {/* الأقساط القادمة */}
          {upcomingItems.length > 0 && (
            <section>
              <h3 style={{ fontSize: 14, fontWeight: 700, color: 'var(--amber)', marginBottom: 10 }}>
                🟡 قادمة ({upcomingItems.length})
              </h3>
              <InstallmentTable rows={upcomingItems} onPay={setPayModal} onEdit={setEditModal} onNavigate={onNavigate} />
            </section>
          )}
        </>
      )}

      {/* مودالات */}
      {payModal && (
        <PayModal
          installment={payModal}
          onClose={() => setPayModal(null)}
          onDone={afterAction}
        />
      )}
      {editModal && (
        <EditInstallmentModal
          installment={editModal}
          onClose={() => setEditModal(null)}
          onDone={afterAction}
        />
      )}
    </div>
  )
}

function StatCard({ label, value, color }) {
  return (
    <div style={{ background: 'var(--card)', borderRadius: 10, padding: '14px 16px', border: '1px solid var(--border)' }}>
      <div style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, color }}>{value}</div>
    </div>
  )
}

function InstallmentTable({ rows, onPay, onEdit, onNavigate }) {
  return (
    <div style={{ overflowX: 'auto', borderRadius: 8, border: '1px solid var(--border)' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ background: 'var(--bg)', borderBottom: '2px solid var(--border)' }}>
            {['الفاتورة', 'العميل', 'القسط', 'تاريخ الاستحقاق', 'الحالة', 'المتبقي', 'إجراء'].map(h => (
              <th key={h} style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 700, whiteSpace: 'nowrap' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(ins => (
            <InstallmentRow
              key={ins.installment_id}
              ins={ins}
              onPay={onPay}
              onEdit={onEdit}
              onNavigate={onNavigate}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}
