/**
 * المشتريات — صفحة مدمجة (PurchaseOrders + Purchases)
 */
import { useState } from 'react'
import PurchaseOrders from './PurchaseOrders'
import Purchases from './Purchases'

const TABS = [
  { key: 'purchaseorders', label: '📝 أوامر الشراء' },
  { key: 'purchases',      label: '🧾 فواتير الشراء' },
]

export default function PurchasingHub() {
  const [activeTab, setActiveTab] = useState('purchaseorders')

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

      {activeTab === 'purchaseorders' && <PurchaseOrders />}
      {activeTab === 'purchases'      && <Purchases />}
    </div>
  )
}
