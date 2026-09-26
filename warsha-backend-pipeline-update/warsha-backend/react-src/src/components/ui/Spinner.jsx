export default function Spinner({ size = 20, className = '' }) {
  return (
    <div
      className={`animate-spin rounded-full border-2 border-erp-border ${className}`}
      style={{
        width: size,
        height: size,
        borderTopColor: 'var(--teal)',
        flexShrink: 0,
      }}
    />
  )
}

export function PageSpinner() {
  return (
    <div className="flex items-center justify-center h-64">
      <Spinner size={32} />
    </div>
  )
}
