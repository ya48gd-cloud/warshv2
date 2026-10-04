import { useState, useEffect, useCallback, Suspense, Component } from 'react'
import { useAuth } from './store/auth'
import Sidebar from './components/layout/Sidebar'
import Topbar from './components/layout/Topbar'
import Login from './pages/Login'

// ── Error Boundary — يمنع الشاشة البيضاء عند أي خطأ غير متوقع ──
class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null } }
  static getDerivedStateFromError(error) { return { error } }
  componentDidCatch(error, info) { console.error('ERP Error:', error, info) }
  render() {
    if (this.state.error) {
      return (
        <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center',
                      minHeight:'100vh', gap:16, padding:24, background:'var(--bg,#f8f7f3)', fontFamily:'Tajawal,sans-serif', direction:'rtl' }}>
          <div style={{ fontSize:48 }}>⚠️</div>
          <div style={{ fontSize:18, fontWeight:700, color:'var(--red,#c0392b)' }}>حدث خطأ في الصفحة</div>
          <div style={{ fontSize:13, color:'#6b7280', maxWidth:420, textAlign:'center' }}>
            {this.state.error?.message || 'خطأ غير متوقع'}
          </div>
          <button
            onClick={() => this.setState({ error: null })}
            style={{ marginTop:8, padding:'8px 24px', background:'#0F6E56', color:'#fff',
                     border:'none', borderRadius:8, cursor:'pointer', fontSize:14, fontFamily:'inherit' }}>
            إعادة المحاولة
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
import { PageSpinner } from './components/ui/Spinner'

// Pages
import Dashboard  from './pages/Dashboard'
// Analytics مدموجة في Dashboard
import Inventory  from './pages/Inventory'
import StockBatches from './pages/StockBatches'
import Warehouses from './pages/Warehouses'
import ProductionHub from './pages/ProductionHub'
import EquipmentPipeline from './pages/EquipmentPipeline'
import Workers    from './pages/Workers'
import Customers  from './pages/Customers'
import SalesHub   from './pages/SalesHub'
import InvoicingHub from './pages/InvoicingHub'
import Equipment  from './pages/Equipment'
import BOM        from './pages/BOM'
import SettingsHub from './pages/SettingsHub'
import Reports    from './pages/Reports'
import PurchasingHub from './pages/PurchasingHub'
import Accounting from './pages/Accounting'
import Suppliers  from './pages/Suppliers'
import ExternalProjects from './pages/ExternalProjects'
import PriceCatalog     from './pages/PriceCatalog'
import FinanceHub       from './pages/FinanceHub'
import SupplyContracts  from './pages/SupplyContracts'
import DeliveryNotes    from './pages/DeliveryNotes'
import CertificatesHub       from './pages/CertificatesHub'
import SuperAdmin from './pages/SuperAdmin'
import FieldService          from './pages/FieldService'
import ScrapHub               from './pages/ScrapHub'
import Installments           from './pages/Installments'
import {
  Payroll, Attendance,
  MRP, StockCount,
} from './pages/AllPages'

const PAGE_MAP = {
  dashboard:  Dashboard,
  inventory:  Inventory,
  stockbatches: StockBatches,
  warehouses: Warehouses,
  workorders: ProductionHub,
  pipeline:   EquipmentPipeline,
  equipment:  Equipment,
  bom:        BOM,
  workers:    Workers,
  payroll:    Payroll,
  attendance: Attendance,
  customers:  Customers,
  orders:     SalesHub,
  invoices:   InvoicingHub,
  mrp:        MRP,
  scrap:      ScrapHub,
  stockcount: StockCount,
  users:      SettingsHub,
  reports:    Reports,
  purchaseorders: PurchasingHub,
  accounting:    Accounting,
  suppliers:  Suppliers,
  extprojects:     ExternalProjects,
  pricecatalog:    PriceCatalog,
  treasury:        FinanceHub,
  supplycontracts: SupplyContracts,
  deliverynotes:   DeliveryNotes,
  workcompletioncerts:  CertificatesHub,
  fieldservice:         FieldService,
  installments:         Installments,
}

// مفاتيح صفحات قديمة/بديلة ما زالت تُستدعى من بعض الصفحات (مثل Dashboard و
// البحث العام في Topbar: onNavigate('materials')) — كانت تقع بصمت على لوحة
// التحكم. نوجّهها للصفحة الحالية المكافئة.
const PAGE_ALIASES = {
  materials:  'inventory',
  movements:  'inventory',
  analytics:  'dashboard',
  production: 'workorders',
  costing:    'workorders',
  purchases:  'purchaseorders',
  quotations: 'orders',
  creditnotes: 'invoices',
  workshop:   'users',
}

function NotFoundPage({ page, onNavigate }) {
  return (
    <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }} role="alert">
      <div style={{ fontSize: 40, marginBottom: 12 }}>🔎</div>
      <div className="text-base font-semibold" style={{ marginBottom: 6 }}>الصفحة غير موجودة</div>
      <div className="text-sm text-erp-muted" style={{ marginBottom: 16 }}>
        لم يتم العثور على الصفحة المطلوبة{page ? ` (${String(page)})` : ''}.
      </div>
      <button className="btn btn-primary btn-sm" onClick={() => onNavigate('dashboard')}>العودة للوحة التحكم</button>
    </div>
  )
}

export default function App() {
  const { user, loading } = useAuth()
  const [page, setPage] = useState('dashboard')
  const [pageParams, setPageParams] = useState(null)
  const [apiStatus, setApiStatus] = useState('checking')
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    fetch('/health')
      .then(r => r.ok ? setApiStatus('ok') : setApiStatus('error'))
      .catch(() =>
        fetch('http://localhost:8000/health')
          .then(r => r.ok ? setApiStatus('ok') : setApiStatus('error'))
          .catch(() => setApiStatus('error'))
      )
  }, [])

  // تحميل عملة الورشة وتخزينها globally لاستخدامها في egp()
  useEffect(() => {
    if (!user) return
    import('./api/client').then(({ default: apiClient, bgErr }) => {
      apiClient.get('/workshop/profile')
        .then(prof => {
          if (prof?.currency) window.__ERP_CURRENCY__ = prof.currency
        })
        .catch(bgErr('إعدادات الورشة (العملة الافتراضية)', () => {}))
    })
  }, [user])

  // استماع لحدث انتهاء الجلسة من client.js — تسجيل خروج تلقائي
  const { logout } = useAuth()
  useEffect(() => {
    const handler = () => {
      // أكتر من طلب ممكن يرجع 401 في نفس اللحظة — نعرض التنبيه مرة واحدة بس
      if (!window.__ERP_SESSION_TOAST_SHOWN__) {
        window.__ERP_SESSION_TOAST_SHOWN__ = true
        window.dispatchEvent(new CustomEvent('erp:toast', {
          detail: { msg: 'انتهت صلاحية الجلسة — سجّل الدخول مرة أخرى', type: 'warn' },
        }))
        setTimeout(() => { window.__ERP_SESSION_TOAST_SHOWN__ = false }, 5000)
      }
      logout()
    }
    window.addEventListener('erp:session-expired', handler)
    return () => window.removeEventListener('erp:session-expired', handler)
  }, [logout])

  const navigate = useCallback((p, params = null) => {
    const target = PAGE_ALIASES[p] || p
    setPage(target); setPageParams(params)
  }, [])

  // بعد تسجيل الخروج (أو انتهاء الجلسة) نرجع للوحة التحكم — عشان لو دخل
  // مستخدم بدور مختلف مايفتحش على صفحة المستخدم السابق
  useEffect(() => {
    if (!user) { setPage('dashboard'); setPageParams(null) }
  }, [user])
  const refresh   = useCallback(() => setRefreshKey(k => k + 1), [])

  // Super Admin route
  if (window.location.search.includes('sa=1') || window.location.hash === '#super-admin') return <SuperAdmin />

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center bg-erp">
      <PageSpinner />
    </div>
  )

  if (!user) return <Login />

  const PageComponent = PAGE_MAP[page] || null

  return (
    <ErrorBoundary>
      <div className="flex min-h-screen bg-erp" dir="rtl">
        <Sidebar current={page} onNavigate={navigate} apiStatus={apiStatus} />
        <div className="main-content">
          <Topbar page={page} user={user} onRefresh={refresh} onNavigate={navigate} />
          <main className="flex-1 p-5 overflow-auto">
            {/* key={page}: الانتقال لصفحة أخرى يصفّر حالة الخطأ بدل ما تفضل شاشة الخطأ عالقة */}
            <ErrorBoundary key={page}>
              <Suspense fallback={<PageSpinner />}>
                {PageComponent
                  ? <PageComponent key={`${page}-${refreshKey}`} onNavigate={navigate} pageParams={pageParams} />
                  : <NotFoundPage page={page} onNavigate={navigate} />}
              </Suspense>
            </ErrorBoundary>
          </main>
        </div>
      </div>
    </ErrorBoundary>
  )
}
