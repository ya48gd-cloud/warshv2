/**
 * سجل تاريخ كيان محدد (عميل / مستخدم ...) — GET /audit/entities/{type}/{id}.
 * الزر يظهر للمدير فقط (الـ API للأدمن فقط). الاستجابة تحتوي changes جاهزة.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Modal, { ModalFooter } from '../ui/Modal'
import Spinner from '../ui/Spinner'
import api from '../../api/client'
import { useAuth } from '../../store/auth'
import AuditDiff from './AuditDiff'
import {
  actionInfo, changedFieldsSummary, fmtAuditDateTime, userLabel,
} from '../../utils/audit'

const PAGE = 20

function HistoryModal({ entityType, entityId, label, onClose, onNavigate }) {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState({})
  const aliveRef = useRef(true)
  const seqRef = useRef(0)

  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false } }, [])

  const load = useCallback(async (offset) => {
    const seq = ++seqRef.current
    setLoading(true); setError(null)
    try {
      const r = await api.audit.entity(entityType, entityId, { limit: PAGE, offset })
      if (!aliveRef.current || seq !== seqRef.current) return
      const list = Array.isArray(r?.items) ? r.items : []
      setItems(prev => (offset === 0 ? list : [...prev, ...list]))
      setTotal(Number.isFinite(r?.total) ? r.total : list.length)
    } catch (e) {
      if (!aliveRef.current || seq !== seqRef.current) return
      setError(e)
    } finally {
      if (aliveRef.current && seq === seqRef.current) setLoading(false)
    }
  }, [entityType, entityId])

  useEffect(() => { setItems([]); setTotal(0); setOpen({}); load(0) }, [load])

  const toggle = (id) => setOpen(o => ({ ...o, [id]: !o[id] }))

  return (
    <Modal open onClose={onClose} title={`سجل التغييرات${label ? ` — ${label}` : ''}`} size="lg">
      {error && items.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: '24px 16px' }} role="alert">
          <div className="text-sm" style={{ marginBottom: 10 }}>
            {error.status === 403 ? 'ليس لديك صلاحية لعرض سجل التدقيق' : (error.message || 'تعذّر تحميل السجل')}
          </div>
          {error.status !== 403 && <button className="btn btn-sm" onClick={() => load(0)}>إعادة المحاولة</button>}
        </div>
      )}

      {loading && items.length === 0 && !error && (
        <div className="flex items-center justify-center" style={{ padding: 28 }}><Spinner size={28} /></div>
      )}

      {!loading && !error && items.length === 0 && (
        <div className="text-sm text-erp-muted text-center" style={{ padding: '28px 8px' }}>
          لا توجد عمليات مسجّلة لهذا السجل
        </div>
      )}

      {items.length > 0 && (
        <div className="flex flex-col gap-2">
          {items.map(r => {
            const act = actionInfo(r.action, r.action_label)
            const isOpen = !!open[r.id]
            const hasChanges = r.changes && typeof r.changes === 'object' && Object.keys(r.changes).length > 0
            return (
              <div key={r.id} className="card" style={{ padding: '10px 12px' }}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`badge ${act.badge}`}>{act.label}</span>
                  <span className="text-xs">{fmtAuditDateTime(r.created_at)}</span>
                  <span className="text-xs text-erp-muted">بواسطة {userLabel(r)}</span>
                  {hasChanges && (
                    <button type="button" className="btn btn-sm text-xs" style={{ marginRight: 'auto' }}
                      aria-expanded={isOpen} onClick={() => toggle(r.id)}>
                      {isOpen ? 'إخفاء التفاصيل' : 'عرض التفاصيل'}
                    </button>
                  )}
                </div>
                {!isOpen && r.changed_fields?.length > 0 && (
                  <div className="text-xs text-erp-muted" style={{ marginTop: 4 }}>
                    {changedFieldsSummary(r.changed_fields)}
                  </div>
                )}
                {isOpen && hasChanges && (
                  <div style={{ marginTop: 8 }}><AuditDiff changes={r.changes} /></div>
                )}
              </div>
            )
          })}
          {items.length < total && (
            <button className="btn btn-sm" disabled={loading} onClick={() => load(items.length)}>
              {loading ? 'جاري التحميل…' : `تحميل المزيد (${items.length} من ${total})`}
            </button>
          )}
          {error && <div className="text-xs text-red" role="alert">{error.message || 'تعذّر تحميل المزيد'}</div>}
        </div>
      )}

      <ModalFooter>
        {onNavigate && (
          <button className="btn" onClick={() => {
            onClose()
            onNavigate('auditlog', { entity_type: entityType, entity_id: entityId })
          }}>فتح في سجل التدقيق</button>
        )}
        <button className="btn btn-primary" onClick={onClose}>إغلاق</button>
      </ModalFooter>
    </Modal>
  )
}

/** زر "السجل" — يُخفى لغير المدير. */
export function EntityHistoryButton({ entityType, entityId, label, onNavigate, className = 'btn btn-sm text-xs' }) {
  const { user } = useAuth()
  const [open, setOpen] = useState(false)
  if (user?.role !== 'admin' || entityId == null) return null
  return (
    <>
      <button type="button" className={className} title="سجل التغييرات" onClick={() => setOpen(true)}>
        🕘 السجل
      </button>
      {open && (
        <HistoryModal entityType={entityType} entityId={entityId} label={label}
          onClose={() => setOpen(false)} onNavigate={onNavigate} />
      )}
    </>
  )
}

export default EntityHistoryButton
