export default function Table({ cols, rows, empty = 'لا توجد بيانات', loading }) {
  if (loading) return (
    <div className="p-10 text-center text-erp-muted animate-pulse" role="status" aria-live="polite">⏳ جاري التحميل…</div>
  )
  // حماية من كراش لو rows جت كائن (مثلاً استجابة paginated) أو cols مش متعرّفة
  if (!Array.isArray(rows) || !rows.length) return (
    <div className="p-10 text-center text-erp-muted text-sm">{empty}</div>
  )
  cols = Array.isArray(cols) ? cols.filter(Boolean) : []
  return (
    <div className="overflow-x-auto">
      <table className="erp-table">
        <thead>
          <tr>{cols.map((c, i) => <th key={i} scope="col" style={c.w ? { width: c.w } : {}}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row?.id ?? i}>
              {cols.map((c, j) => (
                <td key={j}>{c.render ? c.render(row) : row?.[c.key] ?? '—'}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
