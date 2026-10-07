/**
 * الموافقات — صندوق الوارد + طلباتي + قواعد الموافقة (للمدير)
 */
import { useEffect, useState } from 'react'
import { useAuth } from '../store/auth'
import { useApprovalsBadge } from '../hooks/useApprovalsBadge'
import ApprovalRequests, { ErrorBox } from './approvals/ApprovalRequests'
import ApprovalRules from './approvals/ApprovalRules'

const ALL_TABS = [
  { key: 'inbox', label: '📥 صندوق الموافقات', roles: ['admin', 'accountant'] },
  { key: 'mine',  label: '📤 طلباتي',           roles: ['admin', 'accountant', 'production'] },
  { key: 'rules', label: '⚙️ قواعد الموافقة',   roles: ['admin'] },
]

export default function ApprovalsHub({ onNavigate, pageParams }) {
  const { user } = useAuth()
  const role = user?.role
  const allowed = !!role && role !== 'viewer'
  const { pending, ready } = useApprovalsBadge(allowed)

  const tabs = ALL_TABS.filter(t => t.roles.includes(role))
  const [activeTab, setActiveTab] = useState(pageParams?.tab || 'inbox')
  const [highlightId, setHighlightId] = useState(pageParams?.highlight ?? null)

  // التنقل لنفس الصفحة بمعاملات جديدة (App ما بيعمل remount) — نغيّر التبويب
  useEffect(() => {
    if (pageParams?.tab) setActiveTab(pageParams.tab)
    setHighlightId(pageParams?.highlight ?? null)
  }, [pageParams])

  if (!allowed) return <ErrorBox error={{ status: 403 }} />

  const currentTab = tabs.some(t => t.key === activeTab) ? activeTab : tabs[0]?.key
  const countFor = (k) => (k === 'inbox' ? pending : k === 'mine' ? ready : 0)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-1 border-b border-erp-border pb-0 flex-wrap">
        {tabs.map(t => {
          const n = countFor(t.key)
          return (
            <button
              key={t.key}
              className="px-4 py-2 text-sm font-medium transition-colors"
              style={currentTab === t.key
                ? { color: 'var(--teal)', borderBottom: '2px solid var(--teal)' }
                : { color: 'var(--erp-muted)', borderBottom: '2px solid transparent' }}
              onClick={() => setActiveTab(t.key)}
            >
              {t.label}
              {n > 0 && (
                <span style={{
                  marginRight: 6, padding: '0 6px', borderRadius: 10, fontSize: 11, fontWeight: 700, color: '#fff',
                  background: t.key === 'inbox' ? 'var(--red, #ef4444)' : 'var(--green, #16a34a)',
                }}>{n}</span>
              )}
            </button>
          )
        })}
      </div>

      {currentTab === 'inbox' && <ApprovalRequests key="inbox" scope="inbox" highlightId={highlightId} onNavigate={onNavigate} />}
      {currentTab === 'mine'  && <ApprovalRequests key="mine" scope="mine" highlightId={highlightId} onNavigate={onNavigate} />}
      {currentTab === 'rules' && <ApprovalRules />}
    </div>
  )
}
