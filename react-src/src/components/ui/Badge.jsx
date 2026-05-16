export default function Badge({ label, type = 'gray', className = '' }) {
  return (
    <span className={`badge badge-${type} ${className}`}>{label}</span>
  )
}

export function StatusBadge({ map, value }) {
  const entry = map?.[value]
  if (!entry) return <span className="badge badge-gray">{value || '—'}</span>
  return <span className={`badge ${entry.badge}`}>{entry.label}</span>
}
