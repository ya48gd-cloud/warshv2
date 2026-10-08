/**
 * إعدادات النظام — صفحة مدمجة (Users + WorkshopProfile)
 */
import { useState } from 'react'
import { useAuth } from '../store/auth'
import Users from './Users'
import WorkshopProfile from './WorkshopProfile'
import TenantBackup from './TenantBackup'
import EInvoiceSettings from './EInvoiceSettings'

const TABS = [
  { key: 'users',    label: '👥 المستخدمون' },
  { key: 'workshop', label: '🏭 بروفايل الورشة' },
  { key: 'backup',   label: '💾 النسخ الاحتياطي' },
  { key: 'einvoice', label: '🧾 الفاتورة الإلكترونية' },
]

// تبويبات للمدير (admin) فقط
const ADMIN_ONLY_TABS = ['workshop', 'backup', 'einvoice']

export default function SettingsHub({ onNavigate, pageParams }) {
  const { user: me } = useAuth()
  const [activeTab, setActiveTab] = useState(TABS.some(t => t.key === pageParams?.tab) ? pageParams.tab : 'users')

  // إصلاح: تبويب "بروفايل الورشة" كان ظاهراً وقابلاً للتعديل لكل الأدوار
  // (حتى "مشاهد" read-only) رغم أن هذه بيانات على مستوى الورشة كلها —
  // نعرضه للمديرين فقط، ونمنع البقاء على تبويب غير متاح لو تغيّر الدور.
  const tabs = me?.role === 'admin' ? TABS : TABS.filter(t => !ADMIN_ONLY_TABS.includes(t.key))
  const currentTab = tabs.some(t => t.key === activeTab) ? activeTab : tabs[0]?.key

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-1 border-b border-erp-border pb-0">
        {tabs.map(t => (
          <button
            key={t.key}
            className="px-4 py-2 text-sm font-medium transition-colors"
            style={currentTab === t.key
              ? { color: 'var(--teal)', borderBottom: '2px solid var(--teal)' }
              : { color: 'var(--erp-muted)', borderBottom: '2px solid transparent' }}
            onClick={() => setActiveTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {currentTab === 'users'    && <Users onNavigate={onNavigate} />}
      {currentTab === 'workshop' && <WorkshopProfile />}
      {currentTab === 'backup'   && <TenantBackup />}
      {currentTab === 'einvoice' && <EInvoiceSettings />}
    </div>
  )
}
