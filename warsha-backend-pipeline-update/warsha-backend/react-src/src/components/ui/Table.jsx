export default function Table({ cols, rows, empty = 'لا توجد بيانات', loading }) {
  if (loading) return (
    <div className="p-10 text-center text-erp-muted animate-pulse">⏳ جاري التحميل…</div>
  )
  if (!rows?.length) return (
    <div className="p-10 text-center text-erp-muted text-sm">{empty}</div>
  )
  return (
    <div className="overflow-x-auto">
      <table className="erp-table">
        <thead>
          <tr>{cols.map((c, i) => <th key={i} style={c.w ? { width: c.w } : {}}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.id ?? i}>
              {cols.map((c, j) => (
                <td key={j}>{c.render ? c.render(row) : row[c.key] ?? '—'}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
