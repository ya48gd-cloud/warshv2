/**
 * الفواتير — صفحة مدمجة (Invoices + CreditNotes + Installments)
 */
import { useState } from 'react'
import Invoices from './Invoices'
import CreditNotes from './CreditNotes'
import Installments from './Installments'
import EInvoiceQueue from './EInvoiceQueue'
import { useAuth } from '../store/auth'
import useEtaEnabled from '../hooks/useEtaEnabled'

const TABS = [
  { key: 'invoices',      label: '🧾 الفواتير' },
  { key: 'creditnotes',   label: '📋 إشعارات دائنة' },
  { key: 'installments',  label: '📅 الأقساط' },
]
// تبويب الفاتورة الإلكترونية: يظهر للمحاسب/المدير عند التفعيل، وللمدير دائماً (لرابط الإعدادات)
const ETA_TAB = { key: 'einvoice', label: '🇪🇬 الفاتورة الإلكترونية' }

export default function InvoicingHub({ onNavigate, pageParams }) {
  const { user: me, canWrite } = useAuth()
  const eta = useEtaEnabled()
  const showEta = canWrite('sales') && (eta.enabled || me?.role === 'admin')
  const tabs = showEta ? [...TABS, ETA_TAB] : TABS
  const [activeTab, setActiveTab] = useState([...TABS, ETA_TAB].some(t => t.key === pageParams?.tab) ? pageParams.tab : 'invoices')
  const currentTab = tabs.some(t => t.key === activeTab) ? activeTab : 'invoices'
  const [highlightId, setHighlightId] = useState(pageParams?.highlight ?? null)

  // إصلاح: الضغط على كود الفاتورة من تبويب الأقساط كان بينادي navigate('invoices')
  // وإحنا أصلاً في نفس الصفحة، فمكانش بيحصل أي حاجة. نحوّل للتبويب ونفلتر عليها.
  const handleNavigate = (p, params = null) => {
    if (p === 'invoices') {
      setActiveTab('invoices')
      setHighlightId(params?.highlight ?? null)
      return
    }
    onNavigate?.(p, params)
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-1 border-b border-erp-border pb-0">
        {tabs.map(tab => (
          <button
            key={tab.key}
            className="px-4 py-2 text-sm font-medium transition-colors"
            style={currentTab === tab.key
              ? { color: 'var(--teal)', borderBottom: '2px solid var(--teal)' }
              : { color: 'var(--erp-muted)', borderBottom: '2px solid transparent' }}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {currentTab === 'invoices'     && <Invoices highlightId={highlightId} onNavigate={handleNavigate} />}
      {currentTab === 'creditnotes'  && <CreditNotes onNavigate={handleNavigate} />}
      {currentTab === 'installments' && <Installments onNavigate={handleNavigate} />}
      {currentTab === 'einvoice'     && <EInvoiceQueue onNavigate={handleNavigate} />}
    </div>
  )
}
