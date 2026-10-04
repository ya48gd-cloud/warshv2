/**
 * الخزينة والمدفوعات — صفحة مدمجة (Treasury + Statements + PaymentHistory)
 */
import { useState } from 'react'
import Treasury from './Treasury'
import Statements from './Statements'
import { PaymentHistory } from './AllPages'

const TABS = [
  { key: 'treasury',      label: '💰 الخزينة' },
  { key: 'statements',    label: '📑 كشوف الحسابات' },
  { key: 'paymenthistory', label: '🧾 سجل المدفوعات' },
]

export default function FinanceHub() {
  const [activeTab, setActiveTab] = useState('treasury')

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-1 border-b border-erp-border pb-0">
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

      {activeTab === 'treasury'       && <Treasury />}
      {activeTab === 'statements'     && <Statements />}
      {activeTab === 'paymenthistory' && <PaymentHistory />}
    </div>
  )
}
