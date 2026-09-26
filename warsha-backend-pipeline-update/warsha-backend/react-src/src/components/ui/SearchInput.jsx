export default function SearchInput({ value, onChange, placeholder = 'بحث…' }) {
  return (
    <div className="relative">
      <span className="absolute right-3 top-1/2 -translate-y-1/2 text-erp-muted text-sm select-none">🔍</span>
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="field-input pr-8 w-full"
        style={{ paddingRight: '2rem' }}
      />
    </div>
  )
}
