/**
 * الشهادات — صفحة مدمجة (WorkCompletionCerts + WarrantyCerts)
 */
import { useState } from 'react'
import WorkCompletionCerts from './WorkCompletionCerts'
import WarrantyCerts from './WarrantyCerts'

const TABS = [
  { key: 'workcompletioncerts', label: '🏆 شهادات إتمام' },
  { key: 'warrantycerts',       label: '🛡️ شهادات ضمان' },
]

export default function CertificatesHub() {
  const [activeTab, setActiveTab] = useState('workcompletioncerts')

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

      {activeTab === 'workcompletioncerts' && <WorkCompletionCerts />}
      {activeTab === 'warrantycerts'       && <WarrantyCerts />}
    </div>
  )
}
