import { useAuth, ROLE_META, ROLE_NAV } from '../../store/auth'

const NAV_SECTIONS = [
  { label: 'الرئيسية', items: [
    { page: 'dashboard',  icon: '📊', label: 'لوحة التحكم' },
  ]},
  { label: 'المخازن', items: [
    { page: 'materials',  icon: '🟠', label: 'المواد والخامات' },
    { page: 'movements',  icon: '🔵', label: 'حركات المخزون' },
  ]},
  { label: 'المحاسبة', items: [
    { page: 'workorders', icon: '🗒️', label: 'أوامر الإنتاج' },
    { page: 'costing',    icon: '🔥', label: 'التكاليف والقيود' },
  ]},
  { label: 'المعدات', items: [
    { page: 'equipment',  icon: '🟢', label: 'المعدات والبنية' },
    { page: 'bom',        icon: '🗂️', label: 'تفكيك BOM' },
  ]},
  { label: 'الموارد البشرية', items: [
    { page: 'workers',    icon: '🟡', label: 'العاملون' },
    { page: 'attendance', icon: '🗓️', label: 'الحضور والغياب' },
    { page: 'payroll',    icon: '🟢', label: 'الرواتب الأسبوعية' },
  ]},
  { label: 'العملاء', items: [
    { page: 'customers',  icon: '🗃️', label: 'سجل العملاء' },
    { page: 'orders',     icon: '🟠', label: 'الطلبات' },
  ]},
  { label: 'المبيعات', items: [
    { page: 'quotations', icon: '🗒️', label: 'عروض الأسعار' },
    { page: 'invoices',   icon: '🗒️', label: 'الفواتير' },
  ]},
  { label: 'الإنتاج', items: [
    { page: 'production', icon: '🏭', label: 'إدارة الإنتاج' },
    { page: 'mrp',        icon: '🛒', label: 'اقتراحات الشراء' },
    { page: 'scrap',      icon: '🗑️', label: 'الهالك والمردود' },
    { page: 'stockcount', icon: '📊', label: 'جرد المخزون' },
  ]},
  { label: 'النظام', items: [
    { page: 'users',      icon: '👥', label: 'إدارة المستخدمين' },
    { page: 'reports',    icon: '🖨️', label: 'التقارير' },
    { page: 'workshop',   icon: '🏢', label: 'بروفايل الورشة' },
  ]},
]

export default function Sidebar({ current, onNavigate, apiStatus }) {
  const { user, logout } = useAuth()
  const allowed = user ? (ROLE_NAV[user.role] || []) : []
  const meta = user ? ROLE_META[user.role] : null

  return (
    <nav className="sidebar sidebar-enter">
      {/* Logo */}
      <div className="px-4 py-3 border-b border-erp-border">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-erp bg-teal flex items-center justify-center text-white text-sm font-bold flex-shrink-0">H</div>
          <div>
            <div className="text-sm font-semibold text-teal leading-tight">Heavy ERP</div>
            <div className="text-[10px] text-erp-muted">ورشة المعدات الثقيلة</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <div className="flex-1 overflow-y-auto py-1">
        {NAV_SECTIONS.map(sec => {
          const visible = sec.items.filter(i => allowed.includes(i.page))
          if (!visible.length) return null
          return (
            <div key={sec.label} className="mb-0.5">
              <div className="px-4 py-1.5 text-[10px] font-semibold text-erp-muted uppercase tracking-wider">
                {sec.label}
              </div>
              {visible.map(item => (
                <div
                  key={item.page}
                  className={`nav-item ${current === item.page ? 'active' : ''}`}
                  onClick={() => onNavigate(item.page)}
                >
                  <span className="text-sm leading-none">{item.icon}</span>
                  <span className="text-sm">{item.label}</span>
                </div>
              ))}
            </div>
          )
        })}
      </div>

      {/* Footer */}
      <div className="border-t border-erp-border">
        {user && (
          <div className="px-3 py-2.5 flex items-center gap-2">
            <div className="w-7 h-7 rounded-full bg-teal-lt flex items-center justify-center text-sm flex-shrink-0">
              {meta?.icon}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-medium truncate">{user.full_name || user.username}</div>
              <div className={`text-[10px] badge ${meta?.color} px-1.5 py-0 mt-0.5`}>{meta?.label}</div>
            </div>
            <button
              onClick={logout}
              className="text-erp-muted hover:text-red transition-colors text-base cursor-pointer"
              title="تسجيل الخروج"
            >⏻</button>
          </div>
        )}
        <div className="px-3 pb-2 text-[10px] text-erp-muted flex justify-between">
          <span>API: <span style={{ color: apiStatus === 'ok' ? 'var(--green)' : apiStatus === 'checking' ? 'var(--amber-md)' : 'var(--red)' }}>
            {apiStatus === 'ok' ? '✓ متصل' : apiStatus === 'checking' ? 'جاري…' : '✗ غير متصل'}
          </span></span>
          <span>v1.0.0</span>
        </div>
      </div>
    </nav>
  )
}
