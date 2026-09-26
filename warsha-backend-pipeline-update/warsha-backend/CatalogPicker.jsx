import { useState, useEffect, useCallback } from 'react'

const BASE = '/api/v1'
const tok = () => sessionStorage.getItem('erp_token')
const hdr = (json=false) => ({ ...(json?{'Content-Type':'application/json'}:{}), Authorization: `Bearer ${tok()}` })

const ICONS = { 'حديد وصلب':'🔩','ألمونيوم':'🪨','نحاس':'🟡','بلاستيك وبولي':'🟠','كيماويات':'🧪','كهرباء':'⚡','موتورات':'⚙️','أدوات':'🔧','عام':'📦' }

export default function CatalogPicker({ onImport, onClose }) {
  const [q, setQ] = useState('')
  const [cat, setCat] = useState('')
  const [cats, setCats] = useState([])
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [sel, setSel] = useState(null)
  const [form, setForm] = useState({ unit_cost:'', stock_qty:'0', reorder_level:'0' })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    fetch(`${BASE}/catalog/categories`, { headers: hdr() }).then(r=>r.json()).then(r=>setCats(Array.isArray(r)?r:[])).catch(()=>{})
  }, [])

  const search = useCallback(async () => {
    setLoading(true)
    const p = new URLSearchParams()
    if (q) p.append('q', q)
    if (cat) p.append('category', cat)
    const res = await fetch(`${BASE}/catalog?${p}`, { headers: hdr() }).then(r=>r.json()).catch(()=>[])
    setItems(Array.isArray(res) ? res : [])
    setLoading(false)
  }, [q, cat])

  useEffect(() => { const t = setTimeout(search, 300); return ()=>clearTimeout(t) }, [q, cat, search])

  async function doImport() {
    if (!sel) return
    setBusy(true); setMsg('')
    const res = await fetch(`${BASE}/catalog/${sel.id}/import`, {
      method:'POST', headers: hdr(true),
      body: JSON.stringify({ unit_cost: parseFloat(form.unit_cost)||0, stock_qty: parseFloat(form.stock_qty)||0, reorder_level: parseFloat(form.reorder_level)||0 })
    }).then(r=>r.json()).catch(()=>({}))
    if (res.material_id) { setMsg(`✅ ${res.message}`); onImport&&onImport(res); setTimeout(onClose,1500) }
    else setMsg(`❌ ${res.detail||'خطأ في الاستيراد'}`)
    setBusy(false)
  }

  const inp = (style={}) => ({ padding:'8px 10px', border:'1px solid var(--border,#E8E6E0)', borderRadius:7, fontSize:13, outline:'none', fontFamily:'Cairo,sans-serif', boxSizing:'border-box', width:'100%', ...style })

  return (
    <div style={{ position:'fixed',inset:0,background:'rgba(0,0,0,.6)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:9999,direction:'rtl',fontFamily:'Cairo,sans-serif' }}>
      <div style={{ background:'var(--surface,#fff)',borderRadius:16,width:'90%',maxWidth:820,maxHeight:'85vh',display:'flex',flexDirection:'column',boxShadow:'0 20px 60px rgba(0,0,0,.3)',border:'1px solid var(--border,#E8E6E0)' }}>

        <div style={{ padding:'16px 20px',borderBottom:'1px solid var(--border,#E8E6E0)',display:'flex',justifyContent:'space-between',alignItems:'center' }}>
          <div>
            <div style={{ fontWeight:800,fontSize:16,color:'var(--text,#1A1A2E)' }}>📦 الكتالوج العام للمواد</div>
            <div style={{ fontSize:12,color:'var(--muted,#7A7870)',marginTop:2 }}>اختر مادة لإضافتها لمخزونك مباشرة</div>
          </div>
          <button onClick={onClose} style={{ background:'none',border:'none',fontSize:22,cursor:'pointer',color:'var(--muted,#7A7870)' }}>✕</button>
        </div>

        <div style={{ padding:'12px 20px',borderBottom:'1px solid var(--border,#E8E6E0)',display:'flex',gap:10 }}>
          <input value={q} onChange={e=>setQ(e.target.value)} placeholder="🔍 ابحث بالاسم أو الكود أو الوسوم..." style={{ ...inp(), flex:1 }} />
          <select value={cat} onChange={e=>setCat(e.target.value)} style={{ ...inp({minWidth:150,flex:'none'}) }}>
            <option value="">كل التصنيفات</option>
            {cats.map(c=><option key={c} value={c}>{ICONS[c]||'📦'} {c}</option>)}
          </select>
        </div>

        <div style={{ display:'flex',flex:1,overflow:'hidden' }}>
          <div style={{ flex:1,overflowY:'auto',padding:'6px 0' }}>
            {loading ? <div style={{ padding:40,textAlign:'center',color:'var(--muted,#7A7870)' }}>جاري البحث...</div>
            : items.length===0 ? <div style={{ padding:40,textAlign:'center',color:'var(--muted,#7A7870)' }}>لا توجد نتائج — جرب كلمة أخرى</div>
            : items.map(item=>(
              <div key={item.id} onClick={()=>{setSel(item);setMsg('')}}
                style={{ padding:'10px 20px',cursor:'pointer',display:'flex',alignItems:'center',gap:12,background:sel?.id===item.id?'rgba(24,95,165,.07)':'transparent',borderRight:sel?.id===item.id?'3px solid var(--blue,#185FA5)':'3px solid transparent',transition:'all .12s' }}>
                <div style={{ fontSize:20,flexShrink:0 }}>{ICONS[item.category]||'📦'}</div>
                <div style={{ flex:1,minWidth:0 }}>
                  <div style={{ fontWeight:700,fontSize:13,color:'var(--text,#1A1A2E)' }}>{item.name_ar}</div>
                  <div style={{ fontSize:11,color:'var(--muted,#7A7870)',marginTop:2,display:'flex',gap:8,flexWrap:'wrap' }}>
                    <span style={{ fontFamily:'monospace' }}>{item.code}</span>
                    <span>·</span>
                    <span>{item.unit}</span>
                    {item.category&&<span style={{ background:'var(--surface-alt,#F8F7F4)',padding:'0px 6px',borderRadius:4 }}>{item.category}</span>}
                  </div>
                  {item.description&&<div style={{ fontSize:11,color:'var(--muted,#7A7870)',marginTop:2,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis' }}>{item.description}</div>}
                </div>
                {item.usage_count>0&&<span style={{ fontSize:10,color:'#0F6E56',background:'rgba(15,110,86,.08)',padding:'2px 7px',borderRadius:99,flexShrink:0,whiteSpace:'nowrap' }}>{item.usage_count} ورشة</span>}
              </div>
            ))}
          </div>

          {sel&&(
            <div style={{ width:260,borderRight:'1px solid var(--border,#E8E6E0)',padding:16,display:'flex',flexDirection:'column',gap:12,overflowY:'auto' }}>
              <div style={{ background:'var(--surface-alt,#F8F7F4)',borderRadius:10,padding:12,borderTop:'3px solid var(--green,#0F6E56)' }}>
                <div style={{ fontWeight:800,fontSize:14,color:'var(--text,#1A1A2E)',marginBottom:3 }}>{sel.name_ar}</div>
                <div style={{ fontSize:11,color:'var(--muted,#7A7870)' }}>{sel.code} · {sel.unit}</div>
                {sel.name_en&&<div style={{ fontSize:11,color:'var(--muted,#7A7870)',marginTop:2,fontStyle:'italic' }}>{sel.name_en}</div>}
              </div>

              {[['سعر الوحدة','unit_cost'],['الكمية الحالية','stock_qty'],['حد إعادة الطلب','reorder_level']].map(([label,key])=>(
                <div key={key}>
                  <label style={{ fontSize:11,fontWeight:700,color:'var(--muted,#7A7870)',display:'block',marginBottom:4 }}>{label}</label>
                  <input type="number" value={form[key]} onChange={e=>setForm(f=>({...f,[key]:e.target.value}))} placeholder="0" style={inp()} />
                </div>
              ))}

              {msg&&<div style={{ background:msg.startsWith('✅')?'rgba(15,110,86,.1)':'rgba(220,50,50,.1)',color:msg.startsWith('✅')?'#0F6E56':'#C0392B',borderRadius:7,padding:'8px 10px',fontSize:12 }}>{msg}</div>}

              <button onClick={doImport} disabled={busy}
                style={{ background:'var(--green,#0F6E56)',color:'#fff',border:'none',borderRadius:8,padding:'10px',fontSize:13,fontWeight:700,cursor:busy?'not-allowed':'pointer',opacity:busy?.7:1,fontFamily:'Cairo,sans-serif' }}>
                {busy?'جاري الإضافة...':'➕ إضافة للمخزون'}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
