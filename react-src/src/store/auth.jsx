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
  admin:      ['dashboard','materials','movements','workorders','costing','equipment','bom',
               'workers','payroll','attendance','customers','orders','quotations','invoices',
               'production','mrp','scrap','stockcount','users','reports','workshop'],
  accountant: ['dashboard','materials','movements','workorders','workers','payroll','attendance',
               'customers','orders','quotations','invoices','scrap','reports','workshop'],
  production: ['dashboard','materials','movements','workorders','costing','equipment','bom',
               'production','mrp','scrap','stockcount','reports','workshop'],
  viewer:     ['dashboard','materials','workorders','equipment','bom','customers','quotations','invoices','reports','workshop'],
}

export const ROLE_WRITE = {
  admin:      ['inventory','production','sales','workers','customers','users'],
  accountant: ['sales','workers','customers'],
  production: ['inventory','production'],
  viewer:     [],
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  const login = useCallback(async (username, password) => {
    const data = await api.login(username, password)
    localStorage.setItem('erp_token', data.token)
    localStorage.setItem('erp_role', data.user.role)
    setUser(data.user)
    return data.user
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem('erp_token')
    localStorage.removeItem('erp_role')
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
    const token = localStorage.getItem('erp_token')
    if (!token) { setLoading(false); return }
    api.me()
      .then(u => { setUser(u); localStorage.setItem('erp_role', u.role) })
      .catch(() => { localStorage.removeItem('erp_token') })
      .finally(() => setLoading(false))
  }, [])

  return (
    <AuthCtx.Provider value={{ user, loading, login, logout, canWrite, canSee }}>
      {children}
    </AuthCtx.Provider>
  )
}

export function useAuth() {
  return useContext(AuthCtx)
}
