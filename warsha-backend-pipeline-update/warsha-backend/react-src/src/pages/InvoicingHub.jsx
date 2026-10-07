/**
 * الفواتير — صفحة مدمجة (Invoices + CreditNotes + Installments)
 */
import { useState } from 'react'
import Invoices from './Invoices'
import CreditNotes from './CreditNotes'
import Installments from './Installments'

const TABS = [
  { key: 'invoices',      label: '🧾 الفواتير' },
  { key: 'creditnotes',   label: '📋 إشعارات دائنة' },
  { key: 'installments',  label: '📅 الأقساط' },
]

export default function InvoicingHub({ onNavigate, pageParams }) {
  const [activeTab, setActiveTab] = useState(TABS.some(t => t.key === pageParams?.tab) ? pageParams.tab : 'invoices')
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
        {TABS.map(tab => (
          <button
            key={tab.key}
            className="px-4 py-2 text-sm font-medium transition-colors"
            style={activeTab === tab.key
              ? { color: 'var(--teal)', borderBottom: '2px solid var(--teal)' }
              : { color: 'var(--erp-muted)', borderBottom: '2px solid transparent' }}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'invoices'     && <Invoices highlightId={highlightId} />}
      {activeTab === 'creditnotes'  && <CreditNotes />}
      {activeTab === 'installments' && <Installments onNavigate={handleNavigate} />}
    </div>
  )
}
