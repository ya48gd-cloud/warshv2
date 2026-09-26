/**
 * إعدادات النظام — صفحة مدمجة (Users + WorkshopProfile)
 */
import { useState } from 'react'
import Users from './Users'
import WorkshopProfile from './WorkshopProfile'

const TABS = [
  { key: 'users',    label: '👥 المستخدمون' },
  { key: 'workshop', label: '🏭 بروفايل الورشة' },
]

export default function SettingsHub() {
  const [activeTab, setActiveTab] = useState('users')

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

      {activeTab === 'users'    && <Users />}
      {activeTab === 'workshop' && <WorkshopProfile />}
    </div>
  )
}
