export function Field({ label, error, children }) {
  return (
    <div className="flex flex-col gap-1">
      {label && <label className="field-label">{label}</label>}
      {children}
      {error && <span className="text-xs text-red">{error}</span>}
    </div>
  )
}

export function Input({ label, error, ...props }) {
  return (
    <Field label={label} error={error}>
      <input className="field-input" {...props} />
    </Field>
  )
}

export function Select({ label, error, options = [], children, ...props }) {
  return (
    <Field label={label} error={error}>
      <select className="field-select" {...props}>
        {options.map(o => (
          <option key={o.value ?? o} value={o.value ?? o}>{o.label ?? o}</option>
        ))}
        {children}
      </select>
    </Field>
  )
}

export function Textarea({ label, error, ...props }) {
  return (
    <Field label={label} error={error}>
      <textarea className="field-input" rows={3} {...props} />
    </Field>
  )
}

export function FormGrid({ cols = 2, children }) {
  return (
    <div className={`grid gap-3`} style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
      {children}
    </div>
  )
}
