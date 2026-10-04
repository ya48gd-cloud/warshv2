/**
 * تحويل كمية مادة بين مخزنين — الرصيد الإجمالي للمادة ثابت، بس التوزيع
 * بين المخازن بيتغير (POST /inventory/movements/transfer بالمخازن).
 */
import { useEffect, useMemo, useState } from 'react'
import api from '../../api/client'
import { useToast } from '../../store/toast'
import Modal, { ModalFooter } from './Modal'
import { Input, Textarea, FormGrid } from './Field'

export default function StockTransferModal({ open, onClose, materials, warehouses, initialMaterialId, initialFromWarehouseId, onDone }) {
  const toast = useToast()
  const [form, setForm] = useState({ material_id: '', from: '', to: '', qty: '', notes: '' })
  const [quants, setQuants] = useState([])
  const [saving, setSaving] = useState(false)
  const f = (k, v) => setForm(p => ({ ...p, [k]: v }))

  const activeWh = useMemo(() => (warehouses || []).filter(w => w.is_active !== false), [warehouses])

  useEffect(() => {
    if (!open) return
    // المخزن المصدر: المخزن المفتوح في شاشة المخزون لو فيه، وإلا مخزن الخامات
    const preset = initialFromWarehouseId && activeWh.find(w => String(w.id) === String(initialFromWarehouseId))
    const raw = preset || activeWh.find(w => w.code === 'RAW') || activeWh[0]
    setForm({ material_id: initialMaterialId ? String(initialMaterialId) : '', from: raw ? String(raw.id) : '', to: '', qty: '', notes: '' })
  }, [open, initialMaterialId, initialFromWarehouseId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open || !form.material_id) { setQuants([]); return }
    let alive = true
    api.inventory.quants(form.material_id)
      .then(r => { if (alive) setQuants(r || []) })
      .catch(() => { if (alive) setQuants([]) })
    return () => { alive = false }
  }, [open, form.material_id])

  const whQty = (id) => quants
    .filter(q => String(q.warehouse_id) === String(id))
    .reduce((s, q) => s + Number(q.qty || 0), 0)

  const mat = (materials || []).find(m => String(m.id) === String(form.material_id))
  const unit = mat?.unit || ''
  const available = form.from ? whQty(form.from) : 0
  const qty = Number(form.qty || 0)
  const distText = Object.entries(quants.reduce((acc, q) => {
    const k = q.warehouse_name || 'بدون مخزن'
    acc[k] = (acc[k] || 0) + Number(q.qty || 0)
    return acc
  }, {})).map(([k, v]) => `${k}: ${Number(v).toFixed(3)}`).join(' · ')

  const submit = async () => {
    if (!form.material_id) return toast('اختر المادة', 'error')
    if (!form.from) return toast('اختر المخزن المحوَّل منه', 'error')
    if (!form.to) return toast('اختر المخزن المحوَّل إليه', 'error')
    if (form.from === form.to) return toast('لا يمكن التحويل لنفس المخزن', 'error')
    if (!(qty > 0)) return toast('أدخل كمية أكبر من صفر', 'error')
    if (qty > available + 1e-9) return toast(`الكمية المتاحة في المخزن المحوَّل منه ${available.toFixed(3)} ${unit} فقط`, 'error')
    setSaving(true)
    try {
      await api.inventory.transfer({
        material_id: Number(form.material_id),
        source_warehouse_id: Number(form.from),
        destination_warehouse_id: Number(form.to),
        qty,
        notes: form.notes || null,
      })
      toast('تم التحويل بين المخازن بنجاح')
      onDone?.()
      onClose?.()
    } catch (e) {
      toast(e?.message || 'تعذّر التحويل', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="⇄ تحويل بين المخازن">
      <div className="flex flex-col gap-3">
        <div>
          <label className="field-label">المادة *</label>
          <select className="field-select w-full" value={form.material_id}
            onChange={e => f('material_id', e.target.value)}>
            <option value="">— اختر مادة —</option>
            {(materials || []).map(m => (
              <option key={m.id} value={m.id}>{m.code} — {m.name_ar}</option>
            ))}
          </select>
          {mat && (
            <div className="text-xs text-erp-muted mt-1">
              الرصيد الإجمالي: {Number(mat.stock_qty || 0).toFixed(3)} {unit}
              {distText && ` · ${distText}`}
            </div>
          )}
        </div>

        <FormGrid cols={2}>
          <div>
            <label className="field-label">من مخزن *</label>
            <select className="field-select w-full" value={form.from} onChange={e => f('from', e.target.value)}>
              <option value="">— اختر —</option>
              {activeWh.map(w => (
                <option key={w.id} value={w.id}>
                  {w.name_ar}{form.material_id ? ` (${whQty(w.id).toFixed(3)})` : ''}
                </option>
              ))}
            </select>
            {form.material_id && form.from && (
              <div className="text-xs mt-1" style={{ color: qty > available + 1e-9 ? 'var(--red, #EF4444)' : 'var(--erp-muted)' }}>
                المتاح: <span className="font-semibold font-mono">{available.toFixed(3)}</span> {unit}
              </div>
            )}
          </div>
          <div>
            <label className="field-label">إلى مخزن *</label>
            <select className="field-select w-full" value={form.to} onChange={e => f('to', e.target.value)}>
              <option value="">— اختر —</option>
              {activeWh.filter(w => String(w.id) !== String(form.from)).map(w => (
                <option key={w.id} value={w.id}>{w.name_ar}</option>
              ))}
            </select>
          </div>
        </FormGrid>

        <Input label={`الكمية *${unit ? ` (${unit})` : ''}`} type="number" min="0" step="any"
          value={form.qty} onChange={e => f('qty', e.target.value)} />
        <Textarea label="ملاحظات" rows={2} value={form.notes} onChange={e => f('notes', e.target.value)} />
      </div>
      <ModalFooter>
        <button className="btn" onClick={onClose} disabled={saving}>إلغاء</button>
        <button className="btn btn-primary" onClick={submit}
          disabled={saving || !form.material_id || !form.from || !form.to || !(qty > 0) || qty > available + 1e-9}>
          {saving ? 'جاري التحويل…' : 'تحويل'}
        </button>
      </ModalFooter>
    </Modal>
  )
}
