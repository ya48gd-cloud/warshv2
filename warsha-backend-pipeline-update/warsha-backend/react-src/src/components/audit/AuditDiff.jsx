/**
 * عارض الفروقات قبل/بعد لسجل التدقيق.
 * changes = { field: { old?, new?, old_unknown? }, _truncated? }
 */
import { normalizeChanges, formatValue, isMasked, isLargePlaceholder } from '../../utils/audit'

const cellBase = {
  verticalAlign: 'top', wordBreak: 'break-word', whiteSpace: 'pre-wrap',
  maxWidth: 280, fontSize: 12,
}

function ValueCell({ present, value, unknown, tone }) {
  if (unknown) {
    return <span className="text-erp-muted" style={{ fontStyle: 'italic' }}>غير معروف</span>
  }
  if (!present) return <span className="text-erp-muted">—</span>
  if (isMasked(value)) {
    return <span className="badge badge-gray" title="قيمة حساسة مخفية">*** مخفي</span>
  }
  if (isLargePlaceholder(value)) {
    return <span className="text-erp-muted" style={{ fontStyle: 'italic' }}>{value}</span>
  }
  const isNull = value === null || value === undefined
  return (
    <span dir="auto" style={{
      display: 'inline-block', padding: '1px 6px', borderRadius: 4,
      background: isNull ? 'transparent' : tone === 'old' ? 'var(--red-lt)' : 'var(--green-lt)',
      color: isNull ? 'var(--muted)' : tone === 'old' ? 'var(--red)' : 'var(--green)',
    }}>
      {formatValue(value)}
    </span>
  )
}

export default function AuditDiff({ changes }) {
  const { rows, truncated } = normalizeChanges(changes)
  if (!rows.length) {
    return (
      <div className="text-xs text-erp-muted" style={{ padding: '8px 0' }}>
        لا توجد تفاصيل حقول لهذه العملية
        {truncated && ' (تم اختصار بعض الحقول)'}
      </div>
    )
  }
  return (
    <div>
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="erp-table text-sm">
            <thead>
              <tr><th style={{ width: '24%' }}>الحقل</th><th>قبل</th><th>بعد</th></tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.field}>
                  <td className="text-xs" style={{ verticalAlign: 'top' }}>
                    <div className="font-semibold">{r.label}</div>
                    {r.label !== r.field && (
                      <div className="font-mono text-erp-muted" dir="ltr" style={{ fontSize: 10, textAlign: 'right' }}>{r.field}</div>
                    )}
                  </td>
                  <td style={cellBase}>
                    <ValueCell present={r.hasOld} value={r.old} unknown={r.oldUnknown} tone="old" />
                  </td>
                  <td style={cellBase}>
                    <ValueCell present={r.hasNew} value={r.new} tone="new" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {truncated && (
        <div className="text-xs text-erp-muted" style={{ marginTop: 6 }}>تم اختصار بعض الحقول</div>
      )}
    </div>
  )
}
