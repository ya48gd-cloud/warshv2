import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import api from '../api/client'

const AuthCtx = createContext(null)

export const ROLE_META = {
  admin:      { label: 'مدير النظام', color: 'role-admin',      icon: '🔑' },
  accountant: { label: 'محاسب',       color: 'role-accountant', icon: '📊' },
  production: { label: 'إنتاج',       color: 'role-production', icon: '🏭' },
  viewer:     { label: 'مشاهد',       color: 'role-viewer',     icon: '👁' },
}

export const ROLE_NAV = {
  admin:      ['dashboard','analytics','inventory','stockbatches','warehouses','workorders','pipeline','equipment','bom',
               'workers','payroll','attendance','customers','orders','invoices','installments',
               'suppliers','purchaseorders','extprojects','fieldservice','pricecatalog','production','mrp',
               'scrap','stockcount','accounting','treasury',
               'supplycontracts','deliverynotes','workcompletioncerts',
               'users','reports'],
  accountant: ['dashboard','analytics','inventory','stockbatches','warehouses','workorders','pipeline','workers','payroll','attendance',
               'customers','orders','invoices','installments','suppliers','purchaseorders','extprojects','fieldservice',
               'pricecatalog','scrap','accounting','treasury',
               'supplycontracts','deliverynotes','workcompletioncerts',
               'reports','users'],
  production: ['dashboard','analytics','inventory','stockbatches','warehouses','workorders','pipeline','equipment','bom',
               'extprojects','fieldservice','pricecatalog','production','mrp','scrap','stockcount','reports','users'],
  viewer:     ['dashboard','analytics','inventory','stockbatches','warehouses','workorders','pipeline','equipment','bom','customers','orders','invoices','installments',
               'supplycontracts','deliverynotes','treasury','reports','users'],
}

export const ROLE_WRITE = {
  admin:      ['inventory','production','sales','workers','customers','users','field_service','accounting'],
  accountant: ['sales','workers','customers','field_service','accounting'],
  production: ['inventory','production','field_service'],
  viewer:     [],
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  const login = useCallback(async (username, password, tenantSlug) => {
    const data = await api.login(username, password, tenantSlug)
    sessionStorage.setItem('erp_token', data.token)
    sessionStorage.setItem('erp_role', data.user.role)
    if (data.user.tenant_id) sessionStorage.setItem('erp_tenant_id', String(data.user.tenant_id))
    setUser(data.user)
    return data.user
  }, [])

  const signup = useCallback(async (payload) => {
    const data = await api.signup(payload)
    sessionStorage.setItem('erp_token', data.token)
    sessionStorage.setItem('erp_role', data.user.role)
    if (data.user.tenant_id) sessionStorage.setItem('erp_tenant_id', String(data.user.tenant_id))
    setUser(data.user)
    return data
  }, [])

  const logout = useCallback(() => {
    sessionStorage.removeItem('erp_token')
    sessionStorage.removeItem('erp_role')
    sessionStorage.removeItem('erp_tenant_id')
    setUser(null)
  }, [])

  const canWrite = useCallback((module) => {
    if (!user) return false
    return (ROLE_WRITE[user.role] || []).includes(module)
  }, [user])

  const canSee = useCallback((page) => {
    if (!user) return false
    return (ROLE_NAV[user.role] || []).includes(page)
  }, [user])

  useEffect(() => {
    const token = sessionStorage.getItem('erp_token')
    if (!token) { setLoading(false); return }
    api.me()
      .then(u => { setUser(u); sessionStorage.setItem('erp_role', u.role) })
      .catch(() => {
        sessionStorage.removeItem('erp_token')
        // مفيش استخدام لـ useToast هنا (ده Provider نفسه بيلف التطبيق كله) —
        // بنستخدم نفس الحدث العام اللي بيسمعه ToastProvider في أي حتة تانية
        window.dispatchEvent(new CustomEvent('erp:toast', {
          detail: { msg: 'انتهت صلاحية الجلسة — سجّل الدخول تاني', type: 'warn' },
        }))
      })
      .finally(() => setLoading(false))
  }, [])

  return (
    <AuthCtx.Provider value={{ user, loading, login, signup, logout, canWrite, canSee }}>
      {children}
    </AuthCtx.Provider>
  )
}

export function useAuth() {
  return useContext(AuthCtx)
}
