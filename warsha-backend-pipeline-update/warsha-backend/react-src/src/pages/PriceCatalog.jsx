import { useState, useEffect, useCallback } from 'react'
import { api as http, bgErr } from '../api/client'
import { useToast } from '../store/toast'
import CatalogPickerModal from '../components/ui/CatalogPicker'

const BASE = '/price-catalog'

/* ── API ──────────────────────────────────────────────────────────── */
const catalogApi = {
  list:       (params = {}) => http.get(`${BASE}?${new URLSearchParams(params)}`),
  categories: ()            => http.get(`${BASE}/categories`),
  create:     (body)        => http.post(BASE, body),
  update:     (id, body)    => http.put(`${BASE}/${id}`, body),
  updatePrice:(id, p)       => http.patch(`${BASE}/${id}/price?${new URLSearchParams(p)}`),
  delete:     (id)          => http.delete(`${BASE}/${id}`),
  costSource: (type, id)    => http.get(`${BASE}/cost-source/${type}/${id}`),
  allItems:   ()            => http.get(`${BASE}/all-items`),
  upsertSource:    (body) => http.post(`${BASE}/upsert-from-source`, body),
  refreshAutoPrice: ()   => http.post(`${BASE}/refresh-auto-prices`, {}),
}

const UNITS = ['قطعة', 'طن', 'كجم', 'متر', 'م²', 'م³', 'لتر', 'ساعة', 'يوم', 'مجموعة', 'دستة']
const CATEGORIES_DEFAULT = ['معدات', 'خدمات تركيب', 'مواد خام', 'قطع غيار', 'مواصلات', 'أعمال مدنية', 'أخرى']

const fmt = (n) => Number(n).toLocaleString('ar-EG', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const calcSelling = (cost, margin) => Math.round(Number(cost) * (1 + Number(margin) / 100) * 100) / 100

/* ── Empty form ───────────────────────────────────────────────────── */
const EMPTY = { code: '', name: '', category: '', description: '', unit: 'قطعة', cost_price: '', margin_pct: '25', selling_price: '', price_mode: 'auto', source_type: null, source_id: null, is_active: true }

/* ══════════════════════════════════════════════════════════════════ */
export default function PriceCatalog() {
  const toast = useToast()
  const [items, setItems]           = useState([])
  const [categories, setCategories] = useState([])
  const [loading, setLoading]       = useState(true)
  const [search, setSearch]         = useState('')
  const [filterCat, setFilterCat]   = useState('')
  const [showForm, setShowForm]     = useState(false)
  const [editing, setEditing]       = useState(null)   // item being edited
  const [form, setForm]             = useState(EMPTY)
  const [srcPicker, setSrcPicker]   = useState(null)   // 'material' | 'equipment' | null
  const [saving, setSaving]         = useState(false)
  const [err, setErr]               = useState('')

  /* inline price edit */
  const [inlineEdit, setInlineEdit] = useState(null)   // {id, field, value}

  /* refresh auto-prices */
  const [refreshing, setRefreshing] = useState(false)

  /* العرض الموحّد: مواد + معدات + كتالوج */
  const [viewMode, setViewMode] = useState('all')      // 'all' | 'mine'
  const [allRows, setAllRows]   = useState([])
  const [loadingAll, setLoadingAll] = useState(false)

  const loadAllItems = useCallback(async () => {
    setLoadingAll(true)
    try {
      const rows = await catalogApi.allItems().catch(bgErr('كتالوج الأسعار', []))
      setAllRows(Array.isArray(rows) ? rows : [])
    } catch { setAllRows([]) }
    finally { setLoadingAll(false) }
  }, [])

  useEffect(() => { if (viewMode === 'all') loadAllItems() }, [viewMode, loadAllItems])

  const saveRowPrice = async (row, patch) => {
    const updated = { ...row, ...patch }
    // أعد حساب سعر البيع لو الوضع auto
    if (updated.price_mode !== 'manual') {
      updated.selling_price = Math.round(Number(updated.cost_price) * (1 + Number(updated.margin_pct) / 100) * 100) / 100
    }
    setAllRows(prev => prev.map(r => (r.kind === row.kind && r.ref_id === row.ref_id) ? updated : r))
    try {
      if (row.kind === 'catalog') {
        // ده صنف كتالوج حقيقي (مش مادة/معدة) — لازم نحدّثه مباشرة بدل upsert-from-source
        // (upsert-from-source بيفترض kind = material|equipment وبيعتبر أي حاجة تانية "equipment"،
        // فكان بيعمل صنف كتالوج مكرر غلط بدل ما يحدّث الصنف الأصلي)
        const full = await http.get(`${BASE}/${row.ref_id}`)
        await catalogApi.update(row.ref_id, {
          ...full,
          cost_price: Number(updated.cost_price),
          margin_pct: Number(updated.margin_pct),
          selling_price: updated.price_mode === 'manual' ? Number(updated.selling_price) : null,
          price_mode: updated.price_mode,
        })
      } else {
        await catalogApi.upsertSource({
          kind: updated.kind, ref_id: updated.ref_id, name: updated.name, unit: updated.unit,
          cost_price: Number(updated.cost_price), margin_pct: Number(updated.margin_pct),
          selling_price: updated.price_mode === 'manual' ? Number(updated.selling_price) : null,
          price_mode: updated.price_mode,
        })
      }
    } catch (e) { toast(e.message || 'تعذّر حفظ التعديل — حاول تاني', 'error') }
  }

  const [eqChildrenMap, setEqChildrenMap] = useState({}) // { [equipmentId]: rows[] | 'loading' }
  const toggleEquipmentChildren = async (row) => {
    if (eqChildrenMap[row.ref_id] && eqChildrenMap[row.ref_id] !== 'loading') {
      setEqChildrenMap(m => { const n = { ...m }; delete n[row.ref_id]; return n })
      return
    }
    setEqChildrenMap(m => ({ ...m, [row.ref_id]: 'loading' }))
    try {
      const kids = await http.get(`/price-catalog/equipment-children/${row.ref_id}`)
      const rows = kids.map(k => ({
        kind: 'equipment', ref_id: k.id, code: k.model_no || '', name: k.name,
        unit: k.unit, cost_price: k.cost_price, margin_pct: k.margin_pct,
        selling_price: k.unit_price, price_mode: 'auto', editable: true,
        description: k.description === k.name ? '' : k.description, description_editable: true,
        has_children: k.has_children, _isChild: true,
      }))
      setEqChildrenMap(m => ({ ...m, [row.ref_id]: rows }))
    } catch {
      setEqChildrenMap(m => { const n = { ...m }; delete n[row.ref_id]; return n })
      toast('تعذّر تحميل تفاصيل البند', 'error')
    }
  }

  const saveRowDescription = async (row, description) => {
    setAllRows(prev => prev.map(r => (r.kind === row.kind && r.ref_id === row.ref_id) ? { ...r, description } : r))
    try {
      if (row.kind === 'equipment') {
        await http.equipment.updateDescription(row.ref_id, description)
      } else if (row.kind === 'catalog') {
        const full = await http.get(`${BASE}/${row.ref_id}`)
        await catalogApi.update(row.ref_id, { ...full, description })
      }
    } catch (e) { toast(e.message || 'تعذّر حفظ الوصف — حاول تاني', 'error') }
  }

  const staleCount = items.filter(it => it.stale).length

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      const r = await catalogApi.refreshAutoPrice()
      await load()
      toast(
        r.updated > 0 ? `✅ تم تحديث ${r.updated} بند من أصل ${r.total}` : `كل الأسعار محدّثة بالفعل (${r.total} بند)`,
        r.updated > 0 ? 'success' : 'warn'
      )
    } catch (e) { toast(e.message || 'فشل التحديث', 'error') }
    finally { setRefreshing(false) }
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = { active_only: false }
      if (search)    params.q        = search
      if (filterCat) params.category = filterCat
      const [data, cats] = await Promise.all([
        catalogApi.list(params).catch(bgErr('عناصر الكتالوج', [])),
        catalogApi.categories().catch(bgErr('فئات الكتالوج', [])),
      ])
      const itemsArr = Array.isArray(data) ? data : []
      const catsArr  = Array.isArray(cats) ? cats : []
      setItems(itemsArr)
      setCategories([...new Set([...CATEGORIES_DEFAULT, ...catsArr])])
    } catch {
      setItems([]); setCategories(CATEGORIES_DEFAULT)
    } finally { setLoading(false) }
  }, [search, filterCat])

  useEffect(() => { load() }, [load])

  /* ── form helpers ── */
  const openNew  = ()   => { setEditing(null); setForm(EMPTY); setErr(''); setShowForm(true) }
  const openEdit = (it) => { setEditing(it); setForm({ ...it, cost_price: String(it.cost_price), margin_pct: String(it.margin_pct) }); setErr(''); setShowForm(true) }
  const closeForm= ()   => { setShowForm(false); setEditing(null) }

  const save = async () => {
    if (!form.name.trim()) { setErr('اسم الصنف مطلوب'); return }
    const isManual = form.price_mode === 'manual'
    if (!isManual && (form.cost_price === '' || isNaN(Number(form.cost_price)))) { setErr('سعر التكلفة مطلوب'); return }
    if (isManual && (form.selling_price === '' || isNaN(Number(form.selling_price)))) { setErr('سعر البيع مطلوب في الوضع اليدوي'); return }
    setSaving(true); setErr('')
    try {
      const body = {
        ...form,
        cost_price: Number(form.cost_price || 0),
        margin_pct: Number(form.margin_pct || 0),
        selling_price: isManual ? Number(form.selling_price) : null,
        price_mode: form.price_mode || 'auto',
      }
      if (editing) await catalogApi.update(editing.id, body)
      else         await catalogApi.create(body)
      closeForm(); load()
    } catch (e) { setErr(e.message || 'خطأ') }
    finally { setSaving(false) }
  }

  /* ── inline price save ── */
  const saveInline = async () => {
    if (!inlineEdit) return
    const { id, field, value } = inlineEdit
    const item = items.find(i => i.id === id)
    if (!item) return
    const patch = field === 'cost_price'
      ? { cost_price: Number(value) }
      : { margin_pct: Number(value) }
    try {
      const updated = await catalogApi.updatePrice(id, patch)
      setItems(prev => prev.map(i => i.id === id ? { ...i, ...updated } : i))
    } catch (e) {
      toast(e.message || 'تعذّر حفظ السعر — حاول تاني', 'error')
    }
    setInlineEdit(null)
  }

  const toggleActive = async (item) => {
    await catalogApi.update(item.id, { ...item, is_active: !item.is_active, cost_price: Number(item.cost_price), margin_pct: Number(item.margin_pct) })
    load()
  }

  const sellingPreview = calcSelling(form.cost_price || 0, form.margin_pct || 0)

  /* ── group by category ── */
  const grouped = items.reduce((acc, it) => {
    const cat = it.category || 'أخرى'
    if (!acc[cat]) acc[cat] = []
    acc[cat].push(it)
    return acc
  }, {})

  return (
    <div style={{ padding: '24px', fontFamily: 'inherit', direction: 'rtl' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 22, color: 'var(--text-primary)' }}>💰 كتالوج الأسعار</h2>
          <p style={{ margin: '4px 0 0', color: 'var(--text-secondary)', fontSize: 13 }}>
            أسعار التكلفة + هامش الربح = سعر البيع — يتحدث تلقائياً
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: 4, background: 'var(--surface)', borderRadius: 8, padding: 3 }}>
            <button onClick={() => setViewMode('all')} style={{
              padding: '6px 14px', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', border: 'none',
              background: viewMode === 'all' ? '#FF8A00' : 'transparent',
              color: viewMode === 'all' ? '#fff' : 'var(--text-secondary)' }}>
              كل الأصناف (مواد + معدات)
            </button>
            <button onClick={() => setViewMode('mine')} style={{
              padding: '6px 14px', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', border: 'none',
              background: viewMode === 'mine' ? '#FF8A00' : 'transparent',
              color: viewMode === 'mine' ? '#fff' : 'var(--text-secondary)' }}>
              أصنافي اليدوية
            </button>
          </div>
          {viewMode === 'mine' && (
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              title="تحديث أسعار البنود المربوطة بمواد/معدات من التكلفة الحالية في المخزون"
              style={{ ...btnStyle('#0369a1'), position: 'relative' }}
            >
              {refreshing ? '⏳ جاري التحديث…' : '🔄 تحديث الأسعار'}
              {!refreshing && staleCount > 0 && (
                <span style={{ position: 'absolute', top: -6, left: -6, background: '#ef4444', color: '#fff', borderRadius: '50%', fontSize: 10, fontWeight: 700, width: 18, height: 18, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  {staleCount}
                </span>
              )}
            </button>
          )}
          <button onClick={openNew} style={btnStyle('#FF8A00')}>+ صنف جديد</button>
        </div>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        <input
          placeholder="🔍 ابحث باسم أو كود..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={inputStyle}
        />
        <select value={filterCat} onChange={e => setFilterCat(e.target.value)} style={{ ...inputStyle, maxWidth: 180 }}>
          <option value="">كل الفئات</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      {/* Stats bar */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 20, flexWrap: 'wrap' }}>
        {[
          { label: 'إجمالي الأصناف', val: items.length, color: '#3B82F6' },
          { label: 'نشط', val: items.filter(i => i.is_active).length, color: '#22C55E' },
          { label: 'فئات', val: Object.keys(grouped).length, color: '#FF8A00' },
        ].map(s => (
          <div key={s.label} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '10px 20px', textAlign: 'center', minWidth: 100 }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: s.color }}>{s.val}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* العرض الموحّد: مواد + معدات + كتالوج */}
      {viewMode === 'all' ? (
        loadingAll ? (
          <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-secondary)' }}>جاري التحميل...</div>
        ) : (
          <UnifiedTable
            rows={allRows.filter(r =>
              (!search || r.name?.toLowerCase().includes(search.toLowerCase()) || r.code?.toLowerCase().includes(search.toLowerCase())) &&
              (!filterCat || r.category === filterCat)
            ).flatMap(r => {
              const kids = r.kind === 'equipment' ? eqChildrenMap[r.ref_id] : null
              if (!kids || kids === 'loading') return [r]
              return [r, ...kids]
            })}
            onSave={saveRowPrice}
            onSaveDescription={saveRowDescription}
            eqChildrenMap={eqChildrenMap}
            onToggleChildren={toggleEquipmentChildren}
          />
        )
      ) : (
      <>
      {/* Table grouped by category */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-secondary)' }}>جاري التحميل...</div>
      ) : items.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-secondary)' }}>
          <div style={{ fontSize: 40 }}>📋</div>
          <div>لا توجد أصناف — ابدأ بإضافة أول صنف</div>
        </div>
      ) : (
        Object.entries(grouped).map(([cat, catItems]) => (
          <div key={cat} style={{ marginBottom: 28 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: '#FF8A00', marginBottom: 8, borderBottom: '2px solid #FF8A0033', paddingBottom: 4 }}>
              📦 {cat} ({catItems.length})
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={tableStyle}>
                <thead>
                  <tr style={{ background: 'var(--surface-hover)' }}>
                    {['كود','الاسم','الوحدة','سعر التكلفة','هامش الربح %','سعر البيع','الحالة',''].map(h => (
                      <th key={h} style={thStyle}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {catItems.map(it => (
                    <tr key={it.id} style={{ opacity: it.is_active ? 1 : 0.5, borderBottom: '1px solid var(--border)' }}>
                      <td style={tdStyle}><span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{it.code || '—'}</span></td>
                      <td style={{ ...tdStyle, fontWeight: 600, color: 'var(--text-primary)' }}>{it.name}</td>
                      <td style={tdStyle}>{it.unit}</td>

                      {/* Inline cost edit */}
                      <td style={tdStyle}>
                        {inlineEdit?.id === it.id && inlineEdit?.field === 'cost_price' ? (
                          <input
                            autoFocus
                            type="number"
                            value={inlineEdit.value}
                            onChange={e => setInlineEdit({ ...inlineEdit, value: e.target.value })}
                            onBlur={saveInline}
                            onKeyDown={e => e.key === 'Enter' && saveInline()}
                            style={{ ...inputStyle, width: 100, padding: '4px 8px' }}
                          />
                        ) : (
                          <span
                            style={{ cursor: 'pointer', borderBottom: '1px dashed var(--text-secondary)' }}
                            onClick={() => setInlineEdit({ id: it.id, field: 'cost_price', value: String(it.cost_price) })}
                            title={it.stale ? 'السعر المخزّن قديم — اضغط "تحديث الأسعار" لحفظه' : 'اضغط للتعديل'}
                          >
                            {fmt(it.cost_price)}
                            {it.stale && <span style={{ marginRight: 4, color: '#f59e0b', fontSize: 11 }} title="السعر تغيّر في المصدر ولم يُحفظ بعد">⚠</span>}
                          </span>
                        )}
                      </td>

                      {/* Inline margin edit */}
                      <td style={tdStyle}>
                        {inlineEdit?.id === it.id && inlineEdit?.field === 'margin_pct' ? (
                          <input
                            autoFocus
                            type="number"
                            value={inlineEdit.value}
                            onChange={e => setInlineEdit({ ...inlineEdit, value: e.target.value })}
                            onBlur={saveInline}
                            onKeyDown={e => e.key === 'Enter' && saveInline()}
                            style={{ ...inputStyle, width: 80, padding: '4px 8px' }}
                          />
                        ) : (
                          <span
                            style={{ cursor: 'pointer', borderBottom: '1px dashed #22C55E', color: '#22C55E', fontWeight: 600 }}
                            onClick={() => setInlineEdit({ id: it.id, field: 'margin_pct', value: String(it.margin_pct) })}
                            title="اضغط لتعديل الهامش"
                          >
                            {it.margin_pct}%
                          </span>
                        )}
                      </td>

                      <td style={{ ...tdStyle, fontWeight: 700, color: '#FF8A00', fontSize: 15 }}>
                        {fmt(it.selling_price)}
                      </td>

                      <td style={tdStyle}>
                        <span
                          onClick={() => toggleActive(it)}
                          style={{ cursor: 'pointer', padding: '2px 10px', borderRadius: 20, fontSize: 12, fontWeight: 600,
                            background: it.is_active ? '#22C55E22' : '#EF444422',
                            color: it.is_active ? '#22C55E' : '#EF4444' }}
                        >
                          {it.is_active ? 'نشط' : 'متوقف'}
                        </span>
                      </td>

                      <td style={tdStyle}>
                        <button onClick={() => openEdit(it)} style={btnSmall('#3B82F6')}>تعديل</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))
      )}
      </>
      )}

      {/* ── Modal Form ── */}
      {showForm && (
        <div style={overlayStyle} onClick={e => e.target === e.currentTarget && closeForm()}>
          <div style={modalStyle}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
              <h3 style={{ margin: 0, color: 'var(--text-primary)' }}>{editing ? 'تعديل صنف' : 'صنف جديد'}</h3>
              <button onClick={closeForm} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: 'var(--text-secondary)' }}>✕</button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              <Field label="الاسم *" col={2}>
                <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} style={inputStyle} placeholder="مثال: مجرشة أعلاف 5 طن/ساعة" />
              </Field>
              <Field label="كود">
                <input value={form.code} onChange={e => setForm(f => ({ ...f, code: e.target.value }))} style={inputStyle} placeholder="اختياري" />
              </Field>
              <Field label="الفئة">
                <select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))} style={inputStyle}>
                  <option value="">اختر فئة</option>
                  {categories.map(c => <option key={c} value={c}>{c}</option>)}
                  <option value="__new__">+ فئة جديدة</option>
                </select>
                {form.category === '__new__' && (
                  <input
                    style={{ ...inputStyle, marginTop: 6 }}
                    placeholder="اكتب اسم الفئة الجديدة"
                    onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
                  />
                )}
              </Field>
              <Field label="الوحدة">
                <select value={form.unit} onChange={e => setForm(f => ({ ...f, unit: e.target.value }))} style={inputStyle}>
                  {UNITS.map(u => <option key={u} value={u}>{u}</option>)}
                </select>
              </Field>
              <Field label="الوصف" col={2}>
                <textarea value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} style={{ ...inputStyle, minHeight: 60 }} placeholder="تفاصيل إضافية..." />
              </Field>

              {/* Pricing section */}
              <div style={{ gridColumn: '1/-1', background: 'var(--surface)', borderRadius: 10, padding: 16, border: '1px solid var(--border)' }}>
                <div style={{ fontWeight: 700, marginBottom: 12, color: '#FF8A00' }}>💰 التسعير</div>

                {/* نمط التسعير */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
                  <button type="button" onClick={() => setForm(f => ({ ...f, price_mode: 'auto' }))}
                    style={{ flex: 1, padding: '8px', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: 13,
                      border: '1px solid ' + (form.price_mode === 'auto' ? '#FF8A00' : 'var(--border)'),
                      background: form.price_mode === 'auto' ? '#FF8A0022' : 'transparent',
                      color: form.price_mode === 'auto' ? '#FF8A00' : 'var(--text-secondary)' }}>
                    🧮 محسوب (تكلفة × هامش)
                  </button>
                  <button type="button" onClick={() => setForm(f => ({ ...f, price_mode: 'manual' }))}
                    style={{ flex: 1, padding: '8px', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: 13,
                      border: '1px solid ' + (form.price_mode === 'manual' ? '#FF8A00' : 'var(--border)'),
                      background: form.price_mode === 'manual' ? '#FF8A0022' : 'transparent',
                      color: form.price_mode === 'manual' ? '#FF8A00' : 'var(--text-secondary)' }}>
                    ✍️ سعر بيع يدوي
                  </button>
                </div>

                {/* جلب التكلفة من مصدر */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>اجلب التكلفة من:</span>
                  <button type="button" onClick={() => setSrcPicker('material')}
                    style={{ padding: '5px 12px', borderRadius: 16, fontSize: 12, cursor: 'pointer', border: '1px solid #22C55E', background: 'transparent', color: '#22C55E' }}>
                    🧱 مادة
                  </button>
                  <button type="button" onClick={() => setSrcPicker('equipment')}
                    style={{ padding: '5px 12px', borderRadius: 16, fontSize: 12, cursor: 'pointer', border: '1px solid #3B82F6', background: 'transparent', color: '#3B82F6' }}>
                    ⚙️ معدة (من BOM)
                  </button>
                  {form.source_type && (
                    <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                      ✓ مرتبط بـ {form.source_type === 'material' ? 'مادة' : 'معدة'}
                    </span>
                  )}
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                  <Field label={form.price_mode === 'manual' ? 'سعر التكلفة' : 'سعر التكلفة *'}>
                    <input type="number" value={form.cost_price} onChange={e => setForm(f => ({ ...f, cost_price: e.target.value }))} style={inputStyle} placeholder="0.00" min="0" step="0.01" />
                  </Field>
                  <Field label="هامش الربح %">
                    <input type="number" value={form.margin_pct} disabled={form.price_mode === 'manual'}
                      onChange={e => setForm(f => ({ ...f, margin_pct: e.target.value }))}
                      style={{ ...inputStyle, opacity: form.price_mode === 'manual' ? 0.5 : 1 }} placeholder="25" min="0" max="1000" step="0.5" />
                  </Field>
                  {form.price_mode === 'manual' ? (
                    <Field label="سعر البيع (يدوي) *">
                      <input type="number" value={form.selling_price} onChange={e => setForm(f => ({ ...f, selling_price: e.target.value }))}
                        style={{ ...inputStyle, border: '1px solid #FF8A00', fontWeight: 700 }} placeholder="0.00" min="0" step="0.01" />
                    </Field>
                  ) : (
                    <Field label="سعر البيع (محسوب)">
                      <div style={{ ...inputStyle, background: '#FF8A0011', border: '1px solid #FF8A00', color: '#FF8A00', fontWeight: 700, fontSize: 16, textAlign: 'center', lineHeight: '38px' }}>
                        {fmt(sellingPreview)}
                      </div>
                    </Field>
                  )}
                </div>
                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
                  {form.price_mode === 'manual'
                    ? 'الوضع اليدوي: تكتب سعر البيع بنفسك مباشرة'
                    : 'سعر البيع = سعر التكلفة × (1 + هامش الربح%) — يتحدث تلقائياً'}
                </div>
              </div>
            </div>

            {/* منتقي مصدر التكلفة */}
            {srcPicker && (
              <CatalogPickerModal
                open
                onClose={() => setSrcPicker(null)}
                onlySource={srcPicker}
                onSelect={async (item) => {
                  try {
                    const r = await catalogApi.costSource(srcPicker, item.id)
                    setForm(f => ({ ...f, cost_price: String(r.cost_price), source_type: r.source_type, source_id: r.source_id,
                      name: f.name || r.name, unit: f.unit || r.unit }))
                  } catch (e) { setErr(e.message || 'تعذّر جلب التكلفة') }
                  setSrcPicker(null)
                }}
              />
            )}

            {err && <div style={{ color: '#EF4444', marginTop: 12, fontSize: 13 }}>⚠️ {err}</div>}

            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 20 }}>
              <button onClick={closeForm} style={btnStyle('#6B7280')}>إلغاء</button>
              <button onClick={save} disabled={saving} style={btnStyle('#FF8A00')}>
                {saving ? 'جاري الحفظ...' : editing ? 'حفظ التعديلات' : 'إضافة للكتالوج'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ── tiny helpers ─────────────────────────────────────────────────── */

const kindBadge = { catalog: ['💰','كتالوج','#FF8A00'], material: ['🧱','مادة','#22C55E'], equipment: ['⚙️','معدة','#3B82F6'] }

function UnifiedTable({ rows, onSave, onSaveDescription, eqChildrenMap, onToggleChildren }) {
  const [descEdit, setDescEdit] = useState(null) // {kind, ref_id, value}
  if (!rows.length) return (
    <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-secondary)' }}>
      <div style={{ fontSize: 40 }}>📋</div>
      <div>لا توجد أصناف أو مواد أو معدات بعد</div>
    </div>
  )
  return (
    <div style={{ background: 'var(--surface)', borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)' }}>
      <table style={tableStyle}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            {['النوع','الكود','الاسم','الوحدة','سعر التكلفة','هامش %','سعر البيع','المواصفات الفنية'].map(h => <th key={h} style={thStyle}>{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const [icon,label,color] = kindBadge[r.kind] || ['•','',''+'#888']
            const isEditingDesc = descEdit && descEdit.kind === r.kind && descEdit.ref_id === r.ref_id
            const childState = r.kind === 'equipment' && eqChildrenMap ? eqChildrenMap[r.ref_id] : null
            return (
              <tr key={`${r.kind}-${r.ref_id}`} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={tdStyle}>
                  <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 12, background: color+'22', color }}>{icon} {label}</span>
                </td>
                <td style={{ ...tdStyle, color: 'var(--text-secondary)', fontSize: 12 }}>{r.code || '—'}</td>
                <td style={tdStyle}>
                  {r._isChild && <span style={{ color: 'var(--text-secondary)', fontSize: 11, marginInlineEnd: 6 }}>↳</span>}
                  {r.name}
                  {r.kind === 'equipment' && r.has_children && !r._isChild && (
                    <button onClick={() => onToggleChildren(r)}
                      style={{ marginInlineStart: 8, fontSize: 11, fontWeight: 600, padding: '1px 8px', borderRadius: 12, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                      {childState === 'loading' ? '⏳' : childState ? '▾ إخفاء الفرعيات' : '▸ عرض الفرعيات'}
                    </button>
                  )}
                </td>
                <td style={{ ...tdStyle, color: 'var(--text-secondary)' }}>{r.unit}</td>
                <td style={tdStyle}>
                  <input type="number" defaultValue={r.cost_price}
                    onBlur={e => { const v = Number(e.target.value); if (v !== r.cost_price) onSave(r, { cost_price: v }) }}
                    style={{ ...inputStyle, width: 90, padding: '4px 8px' }} />
                </td>
                <td style={tdStyle}>
                  <input type="number" defaultValue={r.margin_pct} disabled={r.price_mode === 'manual'}
                    onBlur={e => { const v = Number(e.target.value); if (v !== r.margin_pct) onSave(r, { margin_pct: v }) }}
                    style={{ ...inputStyle, width: 70, padding: '4px 8px', opacity: r.price_mode === 'manual' ? 0.5 : 1 }} />
                </td>
                <td style={tdStyle}>
                  <input type="number" defaultValue={r.selling_price}
                    onBlur={e => { const v = Number(e.target.value); if (v !== r.selling_price) onSave(r, { selling_price: v, price_mode: 'manual' }) }}
                    style={{ ...inputStyle, width: 100, padding: '4px 8px', border: '1px solid #FF8A00', fontWeight: 700, color: '#FF8A00' }} />
                </td>
                <td style={{ ...tdStyle, minWidth: 220 }}>
                  {!r.description_editable ? (
                    <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>—</span>
                  ) : isEditingDesc ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <textarea autoFocus rows={3} value={descEdit.value}
                        onChange={e => setDescEdit(d => ({ ...d, value: e.target.value }))}
                        style={{ ...inputStyle, padding: '4px 8px', resize: 'vertical' }} />
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button onClick={() => { onSaveDescription(r, descEdit.value); setDescEdit(null) }} style={btnSmall('#22C55E')}>حفظ</button>
                        <button onClick={() => setDescEdit(null)} style={btnSmall('#888')}>إلغاء</button>
                      </div>
                    </div>
                  ) : (
                    <div onClick={() => setDescEdit({ kind: r.kind, ref_id: r.ref_id, value: r.description || '' })}
                      style={{ cursor: 'pointer', fontSize: 12, color: r.description ? 'var(--text-primary)' : 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}
                      title="اضغط لتعديل المواصفات">
                      {r.description || '— اضغط لإضافة مواصفات —'}
                    </div>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div style={{ padding: '10px 14px', fontSize: 12, color: 'var(--text-secondary)' }}>
        عدّل التكلفة أو الهامش وسعر البيع يتحسب — أو اكتب سعر البيع يدوي. اضغط على خلية المواصفات لتعديلها. التغيير بيتحفظ تلقائياً.
      </div>
    </div>
  )
}

function Field({ label, children, col }) {
  return (
    <div style={{ gridColumn: col ? `1/-1` : undefined }}>
      <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>{label}</label>
      {children}
    </div>
  )
}

const inputStyle = {
  width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid var(--border)',
  background: 'var(--surface)', color: 'var(--text-primary)', fontSize: 14, boxSizing: 'border-box',
}
const tableStyle = { width: '100%', borderCollapse: 'collapse', fontSize: 14 }
const thStyle    = { padding: '10px 12px', textAlign: 'right', fontWeight: 600, fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }
const tdStyle    = { padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)' }
const overlayStyle = { position: 'fixed', inset: 0, background: '#00000088', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }
const modalStyle = { background: 'var(--bg)', borderRadius: 16, padding: 28, width: '100%', maxWidth: 700, maxHeight: '90vh', overflowY: 'auto', direction: 'rtl' }
const btnStyle  = (bg) => ({ background: bg, color: '#fff', border: 'none', borderRadius: 8, padding: '8px 20px', cursor: 'pointer', fontSize: 14, fontWeight: 600 })
const btnSmall  = (bg) => ({ ...btnStyle(bg), padding: '4px 12px', fontSize: 12 })
