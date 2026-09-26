/**
 * المخزون — صفحة مدمجة (Materials + Movements)
 */
import { useState } from 'react'
import Materials from './Materials'
import Movements from './Movements'

const TABS = [
  { key: 'materials', label: '📦 المواد والخامات' },
  { key: 'movements', label: '🔄 حركات المخزون' },
]

export default function Inventory() {
  const [activeTab, setActiveTab] = useState('materials')

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

      {activeTab === 'materials' && <Materials />}
      {activeTab === 'movements' && <Movements />}
    </div>
  )
}
