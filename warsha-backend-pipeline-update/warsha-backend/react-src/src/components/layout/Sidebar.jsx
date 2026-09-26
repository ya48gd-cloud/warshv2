import { useState, useEffect } from 'react'
import { useAuth, ROLE_META, ROLE_NAV } from '../../store/auth'
import api, { bgErr } from '../../api/client'

// عنصر "لوحة التحكم" — ثابت دائمًا فوق كل الموديولز
const DASHBOARD_ITEM = { page: 'dashboard', icon: NavIconDashboard, label: 'لوحة التحكم والتحليلات' }

// الموديولز — كل موديول له تاب أعلى القائمة الجانبية + قطاعات فرعية
const MODULES = [
  {
    id: 'inventory_production', emoji: '📦', label: 'المخازن والإنتاج',
    sections: [
      {
        label: 'المخازن',
        items: [
          { page: 'inventory',  icon: NavIconBox,  label: 'المخزون' },
          { page: 'stockbatches', icon: NavIconBox, label: 'دفعات المخزون (Lot)' },
          { page: 'warehouses', icon: NavIconBox, label: 'المخازن المنطقية' },
        ]
      },
      {
        label: 'الإنتاج',
        items: [
          { page: 'workorders',  icon: NavIconWrench, label: 'الإنتاج (أوامر/متابعة/تكاليف)' },
          { page: 'pipeline',    icon: NavIconMove,   label: 'تتبع دورة حياة المعدة' },
          { page: 'equipment',   icon: NavIconEquip,  label: 'المعدات والآلات' },
          { page: 'bom',         icon: NavIconBom,    label: 'تفكيك BOM' },
          { page: 'mrp',         icon: NavIconMrp,    label: 'MRP — اقتراحات الشراء' },
          { page: 'scrap',       icon: NavIconScrap,  label: 'إدارة الهالك' },
        ]
      },
    ]
  },
  {
    id: 'hr', emoji: '👷', label: 'الموارد البشرية',
    sections: [
      {
        label: 'الموارد البشرية',
        items: [
          { page: 'workers',    icon: NavIconWorkers,  label: 'العاملون' },
          { page: 'attendance', icon: NavIconCalendar, label: 'الحضور والغياب' },
          { page: 'payroll',    icon: NavIconPayroll,  label: 'الرواتب الأسبوعية' },
        ]
      },
    ]
  },
  {
    id: 'sales', emoji: '🧾', label: 'المبيعات والعملاء',
    sections: [
      {
        label: 'العملاء والمبيعات',
        items: [
          { page: 'customers',  icon: NavIconCustomer,  label: 'سجل العملاء' },
          { page: 'orders',     icon: NavIconOrders,    label: 'المبيعات (عروض/طلبات)' },
          { page: 'supplycontracts',     icon: NavIconContract,    label: '📜 عقود التوريد' },
          { page: 'deliverynotes',       icon: NavIconContract,    label: '🚛 أوامر التوريد' },
          { page: 'workcompletioncerts', icon: NavIconWCC,         label: '🏆 الشهادات (إتمام/ضمان)' },
          { page: 'pricecatalog',        icon: NavIconPriceCatalog, label: '💰 كتالوج الأسعار' },
          { page: 'invoices',      icon: NavIconInvoice,   label: 'الفواتير (فواتير/إشعارات دائنة)' },
          { page: 'installments',  icon: NavIconInvoice,   label: 'الأقساط المستحقة', badge: 'overdue_installments' },
        ]
      },
    ]
  },
  {
    id: 'aftersales', emoji: '🛠️', label: 'ما بعد البيع',
    sections: [
      {
        label: 'ما بعد البيع',
        items: [
          { page: 'fieldservice', icon: NavIconField, label: '🛠️ الخدمة الميدانية ومعدات العملاء' },
        ]
      },
    ]
  },
  {
    id: 'purchasing', emoji: '🛒', label: 'المشتريات',
    sections: [
      {
        label: 'المشتريات',
        items: [
          { page: 'suppliers',      icon: NavIconSupplier, label: 'الموردون' },
          { page: 'purchaseorders', icon: NavIconPurchase, label: 'المشتريات (أوامر/فواتير)' },
        ]
      },
    ]
  },
  {
    id: 'extprojects', emoji: '🏗️', label: 'المشاريع الخارجية',
    sections: [
      {
        label: 'المشاريع الخارجية',
        items: [
          { page: 'extprojects', icon: NavIconExtProjects, label: '🏗️ مشاريع التركيب والتشغيل' },
        ]
      },
    ]
  },
  {
    id: 'finance', emoji: '💰', label: 'المالية والحسابات',
    sections: [
      {
        label: 'المالية والحسابات',
        items: [
          { page: 'accounting',     icon: NavIconReport, label: 'المحاسبة والحسابات' },
          { page: 'treasury',       icon: NavIconReport, label: '💰 الخزينة والمدفوعات' },
          { page: 'reports',        icon: NavIconReport, label: 'التقارير' },
        ]
      },
    ]
  },
  {
    id: 'admin', emoji: '⚙️', label: 'الإدارة',
    sections: [
      {
        label: 'الإدارة',
        items: [
          { page: 'users',    icon: NavIconUsers,    label: 'إعدادات النظام (مستخدمون/ورشة)' },
        ]
      },
    ]
  },
]

const MODULE_STORAGE_KEY = 'warsha_sidebar_module'

/* ── SVG Icon Components ─────────────────────────────────── */
function NavIconAnalytics() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
}
function NavIconDashboard() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
}
function NavIconBox() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>
}
function NavIconMove() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
}
function NavIconWrench() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
}
function NavIconGear() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
}
function NavIconCost() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
}
function NavIconEquip() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
}
function NavIconBom() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
}
function NavIconScrap() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
}
function NavIconMrp() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg>
}
function NavIconWorkers() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
}
function NavIconCalendar() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
}
function NavIconPayroll() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>
}
function NavIconCustomer() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
}
function NavIconOrders() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
}
function NavIconQuote() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
}
function NavIconInvoice() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/></svg>
}
function NavIconSupplier() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>
}
function NavIconPurchase() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>
}
function NavIconField() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
}
function NavIconExtProjects() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/><path d="M7 8h10M7 12h6"/></svg>
}
function NavIconContract() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
}
function NavIconWCC() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="8" r="6"/><path d="M15.477 12.89L17 22l-5-3-5 3 1.523-9.11"/></svg>
}
function NavIconCreditNote() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/></svg>
}
function NavIconWarranty() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
}
function NavIconPriceCatalog() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2a10 10 0 1 0 0 20A10 10 0 0 0 12 2z"/><path d="M12 6v2m0 8v2M9.5 9.5a2.5 2.5 0 0 1 5 0c0 1.5-2.5 2-2.5 3m0 2h.01"/></svg>
}
function NavIconReport() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
}
function NavIconUsers() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="8" r="4"/><path d="M20 21a8 8 0 1 0-16 0"/><line x1="12" y1="12" x2="12" y2="21"/><line x1="8" y1="17" x2="16" y2="17"/></svg>
}
function NavIconWorkshop() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
}
function NavIconLogout() {
  return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
}

/* ── Warsha Logo Image ─────────────────────────────────── */
function WarshaLogo() {
  return (
    <div className="warsha-logo-wrap">
      <img
        src="/logo_new.png"
        alt="Warsha ERP"
        style={{ width: 200, height: 200, objectFit: 'contain' }}
      />
    </div>
  )
}

/* ── Sidebar Component ─────────────────────────────────── */
export default function Sidebar({ current, onNavigate, apiStatus }) {
  const { user, logout } = useAuth()
  const allowed = user ? (ROLE_NAV[user.role] || []) : []
  const meta = user ? ROLE_META[user.role] : null
  const [collapsed, setCollapsed] = useState({})
  const [badges, setBadges] = useState({ overdue_installments: 0 })

  // تحميل الـ badge counts كل دقيقتين
  useEffect(() => {
    if (!user) return
    const fetchBadges = () => {
      api.reports.installmentsBadge()
        .then(r => setBadges(b => ({ ...b, overdue_installments: r.overdue_count || 0 })))
        .catch(bgErr('badge count', () => {}))
    }
    fetchBadges()
    const interval = setInterval(fetchBadges, 120_000)
    return () => clearInterval(interval)
  }, [user])

  const toggleSection = (label) => setCollapsed(c => ({ ...c, [label]: !c[label] }))

  // الموديولز المرئية بناءً على صلاحيات الدور
  const visibleModules = MODULES
    .map(m => ({
      ...m,
      sections: m.sections
        .map(sec => ({ ...sec, items: sec.items.filter(i => allowed.includes(i.page)) }))
        .filter(sec => sec.items.length),
    }))
    .filter(m => m.sections.length)

  // الموديول النشط — يُحفظ في localStorage، ويُحدَّث تلقائيًا حسب الصفحة الحالية
  const [activeModule, setActiveModule] = useState(() => {
    const fromCurrent = visibleModules.find(m => m.sections.some(sec => sec.items.some(i => i.page === current)))
    if (fromCurrent) return fromCurrent.id
    const saved = localStorage.getItem(MODULE_STORAGE_KEY)
    if (saved && visibleModules.some(m => m.id === saved)) return saved
    return visibleModules[0]?.id
  })

  useEffect(() => {
    const fromCurrent = visibleModules.find(m => m.sections.some(sec => sec.items.some(i => i.page === current)))
    if (fromCurrent && fromCurrent.id !== activeModule) setActiveModule(fromCurrent.id)
  }, [current])

  useEffect(() => {
    if (activeModule) localStorage.setItem(MODULE_STORAGE_KEY, activeModule)
  }, [activeModule])

  const currentModule = visibleModules.find(m => m.id === activeModule) || visibleModules[0]

  // First letter of name for avatar
  const initials = (user?.full_name || user?.username || 'U').charAt(0).toUpperCase()
  const roleColors = { admin: '#FF8A00', accountant: '#22C55E', production: '#3B82F6', viewer: '#94A3B8' }
  const roleColor = roleColors[user?.role] || '#FF8A00'
  const dashboardAllowed = allowed.includes(DASHBOARD_ITEM.page)

  return (
    <nav className="warsha-sidebar">
      {/* Top: Logo + Collapse toggle */}
      <div className="warsha-sidebar-top">
        <WarshaLogo />
        <div className="sidebar-api-dot" title={`API: ${apiStatus}`}
          style={{ background: apiStatus === 'ok' ? '#22C55E' : apiStatus === 'checking' ? '#F59E0B' : '#EF4444' }} />
      </div>

      {/* Dashboard — ثابت دائمًا */}
      {dashboardAllowed && (
        <div className="warsha-nav-section" style={{ paddingBottom: 0 }}>
          {(() => {
            const IconComp = DASHBOARD_ITEM.icon
            const isActive = current === DASHBOARD_ITEM.page
            return (
              <button
                className={`warsha-nav-item${isActive ? ' active' : ''}`}
                onClick={() => onNavigate(DASHBOARD_ITEM.page)}
              >
                <span className="warsha-nav-icon"><IconComp /></span>
                <span className="warsha-nav-label">{DASHBOARD_ITEM.label}</span>
                {isActive && <span className="warsha-nav-pip" />}
              </button>
            )
          })()}
        </div>
      )}

      {/* شريط تابات الموديولز */}
      <div className="warsha-module-tabs">
        {visibleModules.map(m => (
          <button
            key={m.id}
            className={`warsha-module-tab${m.id === activeModule ? ' active' : ''}`}
            title={m.label}
            onClick={() => setActiveModule(m.id)}
          >
            <span className="warsha-module-emoji">{m.emoji}</span>
            <span className="warsha-module-tab-label">{m.label}</span>
          </button>
        ))}
      </div>

      {/* Scrollable Nav */}
      <div className="warsha-nav-scroll">
        {(currentModule?.sections || []).map(sec => {
          const visible = sec.items
          const isCollapsed = collapsed[sec.label]

          return (
            <div key={sec.label} className="warsha-nav-section">
              <button
                className="warsha-nav-group-label"
                onClick={() => toggleSection(sec.label)}
              >
                <span>{sec.label}</span>
                <svg
                  width="10" height="10" viewBox="0 0 10 10" fill="currentColor"
                  style={{ transform: isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}
                  >
                  <path d="M1 3l4 4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round"/>
                </svg>
              </button>

              {!isCollapsed && visible.map(item => {
                const IconComp = item.icon
                const isActive = current === item.page
                const badgeCount = item.badge ? (badges[item.badge] || 0) : 0
                return (
                  <button
                    key={item.page}
                    className={`warsha-nav-item${isActive ? ' active' : ''}`}
                    onClick={() => onNavigate(item.page)}
                  >
                    <span className="warsha-nav-icon">
                      <IconComp />
                    </span>
                    <span className="warsha-nav-label">{item.label}</span>
                    {badgeCount > 0 && (
                      <span style={{
                        background: 'var(--red, #ef4444)', color: '#fff',
                        borderRadius: 10, padding: '1px 7px', fontSize: 11,
                        fontWeight: 700, marginRight: 'auto', minWidth: 18, textAlign: 'center',
                      }}>
                        {badgeCount}
                      </span>
                    )}
                    {isActive && <span className="warsha-nav-pip" />}
                  </button>
                )
              })}
            </div>
          )
        })}
      </div>

      {/* Bottom: User profile */}
      <div className="warsha-sidebar-footer">
        <div className="warsha-user-card">
          <div className="warsha-avatar" style={{ background: roleColor }}>
            {initials}
          </div>
          <div className="warsha-user-info">
            <div className="warsha-user-name">{user?.full_name || user?.username}</div>
            <div className="warsha-user-role" style={{ color: roleColor }}>{meta?.label}</div>
          </div>
          <button className="warsha-logout-btn" onClick={logout} title="تسجيل الخروج">
            <NavIconLogout />
          </button>
        </div>
        <div className="warsha-version">v1.0.0 © 2024 ورشة</div>
      </div>
    </nav>
  )
}
