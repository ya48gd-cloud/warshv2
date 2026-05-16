import { useState, useEffect, useCallback, Suspense } from 'react'
import { useAuth } from './store/auth'
import Sidebar from './components/layout/Sidebar'
import Topbar from './components/layout/Topbar'
import Login from './pages/Login'
import { PageSpinner } from './components/ui/Spinner'

// Pages
import Dashboard  from './pages/Dashboard'
import Materials  from './pages/Materials'
import Movements  from './pages/Movements'
import WorkOrders from './pages/WorkOrders'
import Workers    from './pages/Workers'
import Customers  from './pages/Customers'
import Orders     from './pages/Orders'
import Invoices   from './pages/Invoices'
import Equipment  from './pages/Equipment'
import BOM        from './pages/BOM'
import Users      from './pages/Users'
import Reports    from './pages/Reports'
import WorkshopProfile from './pages/WorkshopProfile'
import {
  Quotations, Payroll, Attendance,
  Costing, Production, MRP, Scrap, StockCount,
} from './pages/AllPages'

const PAGE_MAP = {
  dashboard:  Dashboard,
  materials:  Materials,
  movements:  Movements,
  workorders: WorkOrders,
  costing:    Costing,
  equipment:  Equipment,
  bom:        BOM,
  workers:    Workers,
  payroll:    Payroll,
  attendance: Attendance,
  customers:  Customers,
  orders:     Orders,
  quotations: Quotations,
  invoices:   Invoices,
  production: Production,
  mrp:        MRP,
  scrap:      Scrap,
  stockcount: StockCount,
  users:      Users,
  reports:    Reports,
  workshop:   WorkshopProfile,
}

export default function App() {
  const { user, loading } = useAuth()
  const [page, setPage] = useState('dashboard')
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

  const navigate = useCallback((p) => setPage(p), [])
  const refresh   = useCallback(() => setRefreshKey(k => k + 1), [])

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center bg-erp">
      <PageSpinner />
    </div>
  )

  if (!user) return <Login />

  const PageComponent = PAGE_MAP[page] || Dashboard

  return (
    <div className="flex min-h-screen bg-erp" dir="rtl">
      <Sidebar current={page} onNavigate={navigate} apiStatus={apiStatus} />
      <div className="main-content">
        <Topbar page={page} user={user} onRefresh={refresh} />
        <main className="flex-1 p-5 overflow-auto">
          <Suspense fallback={<PageSpinner />}>
            <PageComponent key={`${page}-${refreshKey}`} onNavigate={navigate} />
          </Suspense>
        </main>
      </div>
    </div>
  )
}
