import { useId } from 'react'

export function Field({ label, error, children, htmlFor }) {
  return (
    <div className="flex flex-col gap-1">
      {label && <label className="field-label" htmlFor={htmlFor}>{label}</label>}
      {children}
      {error && <span className="text-xs text-red" role="alert">{error}</span>}
    </div>
  )
}

export function Input({ label, error, ...props }) {
  const autoId = useId()
  const id = props.id || autoId
  return (
    <Field label={label} error={error} htmlFor={id}>
      <input className="field-input" aria-invalid={error ? true : undefined} {...props} id={id} />
    </Field>
  )
}

export function Select({ label, error, options = [], children, ...props }) {
  const autoId = useId()
  const id = props.id || autoId
  return (
    <Field label={label} error={error} htmlFor={id}>
      <select className="field-select" aria-invalid={error ? true : undefined} {...props} id={id}>
        {(options || []).map(o => (
          <option key={o.value ?? o} value={o.value ?? o}>{o.label ?? o}</option>
        ))}
        {children}
      </select>
    </Field>
  )
}

export function Textarea({ label, error, ...props }) {
  const autoId = useId()
  const id = props.id || autoId
  return (
    <Field label={label} error={error} htmlFor={id}>
      <textarea className="field-input" rows={3} aria-invalid={error ? true : undefined} {...props} id={id} />
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
