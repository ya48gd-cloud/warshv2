/**
 * الإنتاج — صفحة مدمجة (WorkOrders + ProductionControl + Costing)
 */
import { useState } from 'react'
import WorkOrders from './WorkOrders'
import ProductionControl from './ProductionControl'
import { Costing } from './AllPages'

const TABS = [
  { key: 'workorders',  label: '🛠️ أوامر الإنتاج' },
  { key: 'prodcontrol', label: '📊 متابعة الإنتاج والجودة' },
  { key: 'costing',     label: '💵 التكاليف والقيود' },
]

export default function ProductionHub() {
  const [activeTab, setActiveTab] = useState('workorders')

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

      {activeTab === 'workorders'  && <WorkOrders />}
      {activeTab === 'prodcontrol' && <ProductionControl />}
      {activeTab === 'costing'     && <Costing />}
    </div>
  )
}
