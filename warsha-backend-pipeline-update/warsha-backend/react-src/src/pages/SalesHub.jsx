/**
 * المبيعات — صفحة مدمجة (Quotations + Orders)
 */
import { useState } from 'react'
import Quotations from './Quotations'
import Orders from './Orders'

const TABS = [
  { key: 'quotations', label: '📄 عروض الأسعار' },
  { key: 'orders',     label: '🧰 طلبات العملاء' },
]

export default function SalesHub() {
  const [activeTab, setActiveTab] = useState('quotations')

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

      {activeTab === 'quotations' && <Quotations />}
      {activeTab === 'orders'     && <Orders />}
    </div>
  )
}
