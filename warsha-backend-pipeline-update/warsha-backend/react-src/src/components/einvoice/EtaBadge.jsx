/**
 * شارة حالة الفاتورة الإلكترونية. تقبل كائن المستند (eta_status / eta_errors / eta_submitted_at)
 * أو status مباشرة. onClick اختياري (يفتح نافذة الحالة).
 */
import { etaStatusOf, etaMeta, etaErrorsToList, fmtEtaDateTime } from '../../utils/einvoice'

export default function EtaBadge({ doc, status, errors, onClick }) {
  const st = status !== undefined ? (status || null) : etaStatusOf(doc)
  const meta = etaMeta(st)
  const errs = etaErrorsToList(errors !== undefined ? errors : doc?.eta_errors)
  let title = meta.label
  if (errs.length) title = `${meta.label} — ${errs[0].message || errs[0].code}`
  else if (doc?.eta_submitted_at) title = `${meta.label} — ${fmtEtaDateTime(doc.eta_submitted_at)}`

  if (onClick && st) {
    return (
      <button type="button" className={`badge ${meta.badge}`} title={title}
              onClick={(e) => { e.stopPropagation(); onClick() }}
              style={{ cursor: 'pointer', border: 'none' }}>
        {meta.label}
      </button>
    )
  }
  return <span className={`badge ${meta.badge}`} title={title}>{meta.label}</span>
}
