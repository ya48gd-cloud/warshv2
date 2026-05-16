import { ROLE_META } from '../../store/auth'

const PAGE_TITLES = {
  dashboard:  'لوحة التحكم',
  materials:  'المواد والخامات',
  movements:  'حركات المخزون',
  workorders: 'أوامر الإنتاج',
  costing:    'التكاليف والقيود',
  equipment:  'المعدات والبنية',
  bom:        'تفكيك BOM',
  workers:    'العاملون',
  payroll:    'الرواتب الأسبوعية',
  attendance: 'الحضور والغياب',
  customers:  'سجل العملاء',
  orders:     'الطلبات',
  quotations: 'عروض الأسعار',
  invoices:   'الفواتير',
  production: 'إدارة الإنتاج',
  mrp:        'اقتراحات الشراء (MRP)',
  scrap:      'الهالك والمردود',
  stockcount: 'جرد المخزون',
  users:      'إدارة المستخدمين',
  reports:    'التقارير',
  workshop:   'بروفايل الورشة',
}

export default function Topbar({ page, onRefresh, user, onAddClick, showAdd }) {
  const meta = user ? ROLE_META[user.role] : null

  return (
    <header className="bg-white border-b border-erp-border h-13 px-6 flex items-center justify-between sticky top-0 z-40" style={{ height: 52 }}>
      <div className="flex items-center gap-3">
        <h1 className="text-[15px] font-semibold">{PAGE_TITLES[page] || page}</h1>
      </div>

      <div className="flex items-center gap-2">
        {/* Role badge */}
        {meta && (
          <span className={`badge ${meta.color} text-[11px]`}>
            {meta.icon} {meta.label}
          </span>
        )}

        {user && (
          <span className="text-xs text-erp-muted hidden sm:block">
            {user.full_name || user.username}
          </span>
        )}

        <button className="btn btn-sm" onClick={onRefresh} title="تحديث">
          🔄
        </button>

        {showAdd && (
          <button className="btn btn-primary btn-sm" onClick={onAddClick}>
            + إضافة
          </button>
        )}
      </div>
    </header>
  )
}
