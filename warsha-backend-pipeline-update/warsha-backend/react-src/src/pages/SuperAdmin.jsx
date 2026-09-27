import { useState, useEffect } from "react";

const SA_API = (import.meta.env.VITE_API_URL || "http://localhost:8000") + "/api/v1/super-admin";
const CAT_API = (import.meta.env.VITE_API_URL || "http://localhost:8000") + "/api/v1/catalog";

// إصلاح: النسخة القديمة كانت تعمل .then(r => r.json()) بدون أي معالجة —
// أي خطأ شبكة، أو استجابة بدون JSON (500 من الخادم، انقطاع الاتصال...)
// كان بيرمي استثناء غير ملتقط في الدوال المستدعية (خصوصاً اللي مفيهاش
// try/catch أصلاً)، فتفضل شاشة "جاري التحميل..." أو الأزرار عالقة للأبد.
// دلوقتي: أي فشل شبكة/تحليل بيرجع { detail } بدل ما يرمي، وأي 401 بيبعت
// حدث لتسجيل خروج تلقائي من لوحة Super Admin (نفس فكرة session-expired
// في باقي التطبيق).
async function saFetch(path, method = "GET", body = null, token = "") {
  let res;
  try {
    res = await fetch(SA_API + path, {
      method,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body ? JSON.stringify(body) : null,
    });
  } catch {
    return { detail: "تعذر الاتصال بالخادم" };
  }
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent("erp:sa-session-expired"));
  }
  try {
    return await res.json();
  } catch {
    return res.ok ? {} : { detail: `خطأ في الخادم (${res.status})` };
  }
}

async function catFetch(path, method = "GET", body = null, token = "") {
  let res;
  try {
    res = await fetch(CAT_API + path, {
      method,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body ? JSON.stringify(body) : null,
    });
  } catch {
    return { detail: "تعذر الاتصال بالخادم" };
  }
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent("erp:sa-session-expired"));
  }
  try {
    return await res.json();
  } catch {
    return res.ok ? {} : { detail: `خطأ في الخادم (${res.status})` };
  }
}

const ICONS = { 'حديد وصلب':'🔩','ألمونيوم':'🪨','نحاس':'🟡','بلاستيك وبولي':'🟠','كيماويات':'🧪','كهرباء':'⚡','موتورات':'⚙️','أدوات':'🔧','عام':'📦' };

export default function SuperAdmin() {
  const [token, setToken] = useState(() => sessionStorage.getItem("sa_token") || "");
  const [loginData, setLoginData] = useState({ username: "", password: "" });
  const [loginErr, setLoginErr] = useState("");
  const [tab, setTab] = useState("workshops"); // workshops | catalog

  const [workshops, setWorkshops] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [createData, setCreateData] = useState({
    workshop_name: "", workshop_slug: "", admin_username: "",
    admin_password: "", admin_full_name: "", plan: "trial",
  });
  const [msg, setMsg] = useState("");

  // ── Catalog state ──────────────────────────────────────────
  const [catItems, setCatItems] = useState([]);
  const [catLoading, setCatLoading] = useState(false);
  const [catQ, setCatQ] = useState("");
  const [showAddItem, setShowAddItem] = useState(false);
  const [editItem, setEditItem] = useState(null);
  const [itemForm, setItemForm] = useState({ code: "", name_ar: "", name_en: "", unit: "كجم", category: "", description: "", tags: "" });
  const [catMsg, setCatMsg] = useState("");
  const [showBulk, setShowBulk] = useState(false);
  const [bulkText, setBulkText] = useState("");

  useEffect(() => {
    if (token) { loadWorkshops(); loadCatalog(); }
  }, [token]);

  useEffect(() => {
    if (token && tab === "catalog") loadCatalog();
  }, [catQ]);

  // إصلاح: جلسة Super Admin (sa_token) لم تكن تُعالَج عند انتهائها/رفضها —
  // أي طلب يرجع 401 كان بيفضل يفشل بصمت. دلوقتي أي 401 من saFetch/catFetch
  // بيبعت الحدث ده فنسجل خروج تلقائي ونرجّع المستخدم لشاشة الدخول.
  useEffect(() => {
    const h = () => logout();
    window.addEventListener("erp:sa-session-expired", h);
    return () => window.removeEventListener("erp:sa-session-expired", h);
  }, []);

  async function login(e) {
    e.preventDefault();
    setLoginErr("");
    try {
      const res = await saFetch("/login", "POST", loginData);
      if (res.token) {
        sessionStorage.setItem("sa_token", res.token);
        setToken(res.token);
      } else {
        setLoginErr(res.detail || "بيانات خاطئة");
      }
    } catch {
      setLoginErr("خطأ في الاتصال");
    }
  }

  async function loadWorkshops() {
    // إصلاح: setLoading(false) كان بعد الـ await مباشرة بدون finally —
    // أي خطأ (شبكة، JSON غير صالح) كان يوقف التنفيذ وتفضل الشاشة "جاري
    // التحميل..." للأبد. saFetch نفسه بقى مايرميش استثناء، لكن نضيف
    // finally دفاعياً برضه.
    setLoading(true);
    try {
      const res = await saFetch("/workshops", "GET", null, token);
      if (Array.isArray(res)) setWorkshops(res);
      else if (res?.detail) setMsg(`❌ ${res.detail}`);
    } finally {
      setLoading(false);
    }
  }

  async function toggleWorkshop(id) {
    const res = await saFetch(`/workshops/${id}/toggle`, "PUT", null, token);
    if (res?.detail && !res?.message) { setMsg(`❌ ${res.detail}`); return; }
    loadWorkshops();
  }

  async function deleteWorkshop(id, name) {
    if (!confirm(`هل أنت متأكد من حذف ورشة "${name}"؟ هذا الإجراء لا يمكن التراجع عنه.`)) return;
    const res = await saFetch(`/workshops/${id}`, "DELETE", null, token);
    if (res?.detail && !res?.message) { setMsg(`❌ ${res.detail}`); return; }
    loadWorkshops();
  }

  async function createWorkshop(e) {
    e.preventDefault();
    setMsg("");
    const res = await saFetch("/workshops", "POST", createData, token);
    if (res.tenant) {
      setMsg(`✅ تم إنشاء ورشة "${res.tenant.name}" بنجاح`);
      setCreateData({ workshop_name: "", workshop_slug: "", admin_username: "", admin_password: "", admin_full_name: "", plan: "trial" });
      setShowCreate(false);
      loadWorkshops();
    } else {
      setMsg(`❌ ${res.detail || "خطأ في الإنشاء"}`);
    }
  }

  function logout() {
    sessionStorage.removeItem("sa_token");
    setToken("");
    setWorkshops([]);
  }

  // ── Catalog functions ──────────────────────────────────────
  async function loadCatalog() {
    // إصلاح: كان بيستخدم fetch مباشرة (بدون التحقق من 401) بدل catFetch
    // الموحّدة — يعني لو التوكن انتهى هنا تحديدًا، الصفحة كانت تفشل بصمت
    // بدون أي تسجيل خروج تلقائي زي باقي الأماكن.
    setCatLoading(true);
    try {
      const params = new URLSearchParams();
      if (catQ) params.append("q", catQ);
      params.append("limit", "200");
      const res = await catFetch(`?${params}`, "GET", null, token);
      if (Array.isArray(res)) {
        setCatItems(res);
      } else {
        window.dispatchEvent(new CustomEvent('erp:toast', {
          detail: { msg: res?.detail || 'تعذّر تحميل الكتالوج', type: 'warn' },
        }))
      }
    } finally {
      setCatLoading(false);
    }
  }

  function openAddItem() {
    setEditItem(null);
    setItemForm({ code: "", name_ar: "", name_en: "", unit: "كجم", category: "", description: "", tags: "" });
    setShowAddItem(true);
    setCatMsg("");
  }

  function openEditItem(item) {
    setEditItem(item);
    setItemForm({
      code: item.code, name_ar: item.name_ar, name_en: item.name_en || "",
      unit: item.unit, category: item.category || "", description: item.description || "", tags: item.tags || "",
    });
    setShowAddItem(true);
    setCatMsg("");
  }

  async function saveItem(e) {
    e.preventDefault();
    setCatMsg("");
    const path = editItem ? `/${editItem.id}` : "";
    const method = editItem ? "PUT" : "POST";
    const res = await catFetch(path, method, itemForm, token);
    if (res.message) {
      setCatMsg(`✅ ${res.message}`);
      setShowAddItem(false);
      loadCatalog();
    } else {
      setCatMsg(`❌ ${res.detail || "خطأ"}`);
    }
  }

  async function deleteItem(id, name) {
    if (!confirm(`حذف "${name}" من الكتالوج؟`)) return;
    const res = await catFetch(`/${id}`, "DELETE", null, token);
    if (res?.detail && !res?.message) { setCatMsg(`❌ ${res.detail}`); return; }
    loadCatalog();
  }

  async function doBulkImport() {
    setCatMsg("");
    try {
      const lines = bulkText.trim().split("\n").filter(l => l.trim());
      const items = lines.map(line => {
        const [code, name_ar, unit, category, name_en] = line.split("|").map(s => s.trim());
        return { code, name_ar, unit: unit || "قطعة", category: category || "", name_en: name_en || "" };
      });
      const res = await catFetch("/bulk", "POST", { items }, token);
      setCatMsg(`✅ تمت إضافة ${res.added} مادة، تخطي ${res.skipped} (مكررة)`);
      setBulkText("");
      setShowBulk(false);
      loadCatalog();
    } catch {
      setCatMsg("❌ خطأ في تنسيق البيانات");
    }
  }

  if (!token) return (
    <div style={{ minHeight: "100vh", background: "#0F172A", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "Cairo, sans-serif", direction: "rtl" }}>
      <div style={{ background: "#1E293B", borderRadius: 16, padding: "40px 48px", width: 380, boxShadow: "0 20px 60px rgba(0,0,0,.5)", border: "1px solid #334155" }}>
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <div style={{ fontSize: 36, marginBottom: 8 }}>⚙️</div>
          <h1 style={{ color: "#F1F5F9", fontSize: 22, fontWeight: 800, margin: 0 }}>Super Admin</h1>
          <p style={{ color: "#64748B", fontSize: 13, marginTop: 6 }}>لوحة إدارة ورشة ERP</p>
        </div>
        <form onSubmit={login}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ color: "#94A3B8", fontSize: 12, fontWeight: 700, display: "block", marginBottom: 6 }}>اسم المستخدم</label>
            <input value={loginData.username} onChange={e => setLoginData({ ...loginData, username: e.target.value })}
              style={{ width: "100%", background: "#0F172A", border: "1px solid #334155", borderRadius: 8, padding: "10px 14px", color: "#F1F5F9", fontSize: 14, outline: "none", boxSizing: "border-box" }}
              placeholder="superadmin" />
          </div>
          <div style={{ marginBottom: 24 }}>
            <label style={{ color: "#94A3B8", fontSize: 12, fontWeight: 700, display: "block", marginBottom: 6 }}>كلمة المرور</label>
            <input type="password" value={loginData.password} onChange={e => setLoginData({ ...loginData, password: e.target.value })}
              style={{ width: "100%", background: "#0F172A", border: "1px solid #334155", borderRadius: 8, padding: "10px 14px", color: "#F1F5F9", fontSize: 14, outline: "none", boxSizing: "border-box" }} />
          </div>
          {loginErr && <div style={{ background: "#450A0A", border: "1px solid #7F1D1D", borderRadius: 8, padding: "10px 14px", color: "#FCA5A5", fontSize: 13, marginBottom: 16 }}>{loginErr}</div>}
          <button type="submit" style={{ width: "100%", background: "linear-gradient(135deg,#6366F1,#8B5CF6)", color: "#fff", border: "none", borderRadius: 8, padding: "12px", fontSize: 15, fontWeight: 700, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
            دخول
          </button>
        </form>
      </div>
    </div>
  );

  const inputStyle = { width: "100%", background: "#0F172A", border: "1px solid #334155", borderRadius: 7, padding: "9px 12px", color: "#F1F5F9", fontSize: 13, outline: "none", boxSizing: "border-box", fontFamily: "Cairo, sans-serif" };
  const labelStyle = { color: "#94A3B8", fontSize: 11, fontWeight: 700, display: "block", marginBottom: 5 };

  return (
    <div style={{ minHeight: "100vh", background: "#0F172A", fontFamily: "Cairo, sans-serif", direction: "rtl" }}>
      {/* Header */}
      <div style={{ background: "#1E293B", borderBottom: "1px solid #334155", padding: "14px 28px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 24 }}>⚙️</span>
          <div>
            <div style={{ color: "#F1F5F9", fontWeight: 800, fontSize: 18 }}>Super Admin Dashboard</div>
            <div style={{ color: "#64748B", fontSize: 12 }}>إدارة ورش ERP</div>
          </div>
        </div>
        <button onClick={logout}
          style={{ background: "#1E293B", color: "#94A3B8", border: "1px solid #334155", borderRadius: 8, padding: "8px 18px", fontSize: 13, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
          خروج
        </button>
      </div>

      {/* Tabs */}
      <div style={{ background: "#1E293B", borderBottom: "1px solid #334155", padding: "0 28px", display: "flex", gap: 4 }}>
        {[["workshops", "🏭 الورش"], ["catalog", "📦 الكتالوج العام"]].map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)}
            style={{
              background: "transparent", border: "none", borderBottom: tab === key ? "2px solid #6366F1" : "2px solid transparent",
              color: tab === key ? "#F1F5F9" : "#64748B", padding: "12px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer", fontFamily: "Cairo, sans-serif"
            }}>
            {label}
          </button>
        ))}
      </div>

      <div style={{ padding: "24px 28px" }}>

        {/* ════════ WORKSHOPS TAB ════════ */}
        {tab === "workshops" && (
          <>
            {msg && <div style={{ background: msg.startsWith("✅") ? "#052E16" : "#450A0A", border: `1px solid ${msg.startsWith("✅") ? "#166534" : "#7F1D1D"}`, borderRadius: 8, padding: "12px 16px", color: msg.startsWith("✅") ? "#86EFAC" : "#FCA5A5", marginBottom: 20, fontSize: 14 }}>{msg}</div>}

            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16 }}>
              <button onClick={() => setShowCreate(!showCreate)}
                style={{ background: "#6366F1", color: "#fff", border: "none", borderRadius: 8, padding: "8px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
                + ورشة جديدة
              </button>
            </div>

            {showCreate && (
              <div style={{ background: "#1E293B", border: "1px solid #334155", borderRadius: 12, padding: 24, marginBottom: 24 }}>
                <h3 style={{ color: "#F1F5F9", margin: "0 0 20px", fontSize: 16 }}>إنشاء ورشة جديدة</h3>
                <form onSubmit={createWorkshop}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                    {[
                      ["اسم الورشة", "workshop_name", "ورشة الأمل"],
                      ["معرّف الورشة (slug)", "workshop_slug", "al-amal"],
                      ["اسم المدير", "admin_username", "admin"],
                      ["كلمة مرور المدير", "admin_password", "••••••"],
                      ["الاسم الكامل للمدير", "admin_full_name", "محمد أحمد"],
                    ].map(([label, key, ph]) => (
                      <div key={key}>
                        <label style={labelStyle}>{label}</label>
                        <input
                          type={key === "admin_password" ? "password" : "text"}
                          value={createData[key]} placeholder={ph}
                          onChange={e => setCreateData({ ...createData, [key]: e.target.value })}
                          style={inputStyle} />
                      </div>
                    ))}
                    <div>
                      <label style={labelStyle}>الباقة</label>
                      <select value={createData.plan} onChange={e => setCreateData({ ...createData, plan: e.target.value })} style={inputStyle}>
                        <option value="trial">تجريبية (Trial)</option>
                        <option value="basic">أساسية (Basic)</option>
                        <option value="pro">احترافية (Pro)</option>
                      </select>
                    </div>
                  </div>
                  <div style={{ marginTop: 16, display: "flex", gap: 10 }}>
                    <button type="submit" style={{ background: "#6366F1", color: "#fff", border: "none", borderRadius: 8, padding: "10px 24px", fontSize: 14, fontWeight: 700, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>إنشاء</button>
                    <button type="button" onClick={() => setShowCreate(false)} style={{ background: "transparent", color: "#94A3B8", border: "1px solid #334155", borderRadius: 8, padding: "10px 24px", fontSize: 14, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>إلغاء</button>
                  </div>
                </form>
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginBottom: 24 }}>
              {[
                ["🏭", "إجمالي الورش", workshops.length],
                ["✅", "ورش نشطة", workshops.filter(w => w.is_active).length],
                ["⛔", "ورش موقوفة", workshops.filter(w => !w.is_active).length],
              ].map(([icon, label, val]) => (
                <div key={label} style={{ background: "#1E293B", border: "1px solid #334155", borderRadius: 12, padding: "20px 24px" }}>
                  <div style={{ fontSize: 28 }}>{icon}</div>
                  <div style={{ color: "#94A3B8", fontSize: 12, marginTop: 8 }}>{label}</div>
                  <div style={{ color: "#F1F5F9", fontSize: 28, fontWeight: 900 }}>{val}</div>
                </div>
              ))}
            </div>

            <div style={{ background: "#1E293B", border: "1px solid #334155", borderRadius: 12, overflow: "hidden" }}>
              <div style={{ padding: "16px 20px", borderBottom: "1px solid #334155", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ color: "#F1F5F9", fontWeight: 700 }}>قائمة الورش</span>
                <button onClick={loadWorkshops} style={{ background: "transparent", color: "#6366F1", border: "1px solid #6366F1", borderRadius: 6, padding: "5px 14px", fontSize: 12, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>تحديث</button>
              </div>
              {loading ? (
                <div style={{ padding: 40, textAlign: "center", color: "#64748B" }}>جاري التحميل...</div>
              ) : workshops.length === 0 ? (
                <div style={{ padding: 40, textAlign: "center", color: "#64748B" }}>لا توجد ورش بعد</div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ background: "#0F172A" }}>
                      {["#", "اسم الورشة", "المعرّف", "الباقة", "المستخدمون", "الحالة", "إجراءات"].map(h => (
                        <th key={h} style={{ padding: "10px 16px", color: "#64748B", fontSize: 11, fontWeight: 700, textAlign: "right" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {workshops.map(w => (
                      <tr key={w.id} style={{ borderTop: "1px solid #334155" }}>
                        <td style={{ padding: "12px 16px", color: "#64748B", fontSize: 12 }}>{w.id}</td>
                        <td style={{ padding: "12px 16px", color: "#F1F5F9", fontWeight: 700 }}>{w.name}</td>
                        <td style={{ padding: "12px 16px", color: "#6366F1", fontSize: 12, fontFamily: "monospace" }}>{w.slug}</td>
                        <td style={{ padding: "12px 16px" }}>
                          <span style={{ background: w.plan === "pro" ? "#1E1B4B" : w.plan === "basic" ? "#0C2340" : "#1A1A1A", color: w.plan === "pro" ? "#A5B4FC" : w.plan === "basic" ? "#7DD3FC" : "#94A3B8", border: `1px solid ${w.plan === "pro" ? "#4338CA" : w.plan === "basic" ? "#0369A1" : "#334155"}`, borderRadius: 4, padding: "2px 8px", fontSize: 11, fontWeight: 700 }}>
                            {w.plan === "pro" ? "احترافية" : w.plan === "basic" ? "أساسية" : "تجريبية"}
                          </span>
                        </td>
                        <td style={{ padding: "12px 16px", color: "#94A3B8", fontSize: 13 }}>{w.user_count}</td>
                        <td style={{ padding: "12px 16px" }}>
                          <span style={{ background: w.is_active ? "#052E16" : "#450A0A", color: w.is_active ? "#86EFAC" : "#FCA5A5", border: `1px solid ${w.is_active ? "#166534" : "#7F1D1D"}`, borderRadius: 99, padding: "2px 10px", fontSize: 11, fontWeight: 700 }}>
                            {w.is_active ? "نشطة" : "موقوفة"}
                          </span>
                        </td>
                        <td style={{ padding: "12px 16px" }}>
                          <div style={{ display: "flex", gap: 8 }}>
                            <button onClick={() => toggleWorkshop(w.id)}
                              style={{ background: w.is_active ? "#422006" : "#052E16", color: w.is_active ? "#FED7AA" : "#86EFAC", border: "none", borderRadius: 6, padding: "5px 12px", fontSize: 11, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
                              {w.is_active ? "إيقاف" : "تفعيل"}
                            </button>
                            <button onClick={() => deleteWorkshop(w.id, w.name)}
                              style={{ background: "#450A0A", color: "#FCA5A5", border: "none", borderRadius: 6, padding: "5px 12px", fontSize: 11, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
                              حذف
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}

        {/* ════════ CATALOG TAB ════════ */}
        {tab === "catalog" && (
          <>
            {catMsg && <div style={{ background: catMsg.startsWith("✅") ? "#052E16" : "#450A0A", border: `1px solid ${catMsg.startsWith("✅") ? "#166534" : "#7F1D1D"}`, borderRadius: 8, padding: "12px 16px", color: catMsg.startsWith("✅") ? "#86EFAC" : "#FCA5A5", marginBottom: 20, fontSize: 14 }}>{catMsg}</div>}

            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16, gap: 10 }}>
              <input value={catQ} onChange={e => setCatQ(e.target.value)} placeholder="🔍 بحث في الكتالوج..."
                style={{ ...inputStyle, maxWidth: 320 }} />
              <div style={{ display: "flex", gap: 10 }}>
                <button onClick={() => setShowBulk(true)}
                  style={{ background: "#1E293B", color: "#A5B4FC", border: "1px solid #4338CA", borderRadius: 8, padding: "9px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
                  📋 استيراد دفعة
                </button>
                <button onClick={openAddItem}
                  style={{ background: "#6366F1", color: "#fff", border: "none", borderRadius: 8, padding: "9px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
                  + مادة جديدة
                </button>
              </div>
            </div>

            {/* Add/Edit Item Modal */}
            {showAddItem && (
              <div style={{ background: "#1E293B", border: "1px solid #334155", borderRadius: 12, padding: 24, marginBottom: 24 }}>
                <h3 style={{ color: "#F1F5F9", margin: "0 0 18px", fontSize: 16 }}>{editItem ? "تعديل المادة" : "إضافة مادة جديدة للكتالوج"}</h3>
                <form onSubmit={saveItem}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
                    <div>
                      <label style={labelStyle}>الكود *</label>
                      <input required value={itemForm.code} onChange={e => setItemForm({ ...itemForm, code: e.target.value })} placeholder="STL-001" style={inputStyle} />
                    </div>
                    <div>
                      <label style={labelStyle}>الاسم بالعربي *</label>
                      <input required value={itemForm.name_ar} onChange={e => setItemForm({ ...itemForm, name_ar: e.target.value })} placeholder="حديد مسطح 3مم" style={inputStyle} />
                    </div>
                    <div>
                      <label style={labelStyle}>الاسم بالإنجليزي</label>
                      <input value={itemForm.name_en} onChange={e => setItemForm({ ...itemForm, name_en: e.target.value })} placeholder="Flat Steel 3mm" style={inputStyle} />
                    </div>
                    <div>
                      <label style={labelStyle}>الوحدة *</label>
                      <select required value={itemForm.unit} onChange={e => setItemForm({ ...itemForm, unit: e.target.value })} style={inputStyle}>
                        <option value="كجم">كجم</option>
                        <option value="طن">طن</option>
                        <option value="متر">متر</option>
                        <option value="قطعة">قطعة</option>
                        <option value="لتر">لتر</option>
                        <option value="عبوة">عبوة</option>
                        <option value="لفة">لفة</option>
                      </select>
                    </div>
                    <div>
                      <label style={labelStyle}>التصنيف</label>
                      <input value={itemForm.category} onChange={e => setItemForm({ ...itemForm, category: e.target.value })} placeholder="حديد وصلب" style={inputStyle} list="cat-suggestions" />
                      <datalist id="cat-suggestions">
                        {Object.keys(ICONS).map(c => <option key={c} value={c} />)}
                      </datalist>
                    </div>
                    <div>
                      <label style={labelStyle}>وسوم بحث (مفصولة بفاصلة)</label>
                      <input value={itemForm.tags} onChange={e => setItemForm({ ...itemForm, tags: e.target.value })} placeholder="حديد, صلب, مسطح" style={inputStyle} />
                    </div>
                  </div>
                  <div style={{ marginTop: 14 }}>
                    <label style={labelStyle}>وصف مختصر</label>
                    <input value={itemForm.description} onChange={e => setItemForm({ ...itemForm, description: e.target.value })} placeholder="مواصفات إضافية..." style={inputStyle} />
                  </div>
                  <div style={{ marginTop: 16, display: "flex", gap: 10 }}>
                    <button type="submit" style={{ background: "#6366F1", color: "#fff", border: "none", borderRadius: 8, padding: "10px 24px", fontSize: 14, fontWeight: 700, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
                      {editItem ? "حفظ التعديلات" : "إضافة"}
                    </button>
                    <button type="button" onClick={() => setShowAddItem(false)} style={{ background: "transparent", color: "#94A3B8", border: "1px solid #334155", borderRadius: 8, padding: "10px 24px", fontSize: 14, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>إلغاء</button>
                  </div>
                </form>
              </div>
            )}

            {/* Bulk Import Modal */}
            {showBulk && (
              <div style={{ background: "#1E293B", border: "1px solid #334155", borderRadius: 12, padding: 24, marginBottom: 24 }}>
                <h3 style={{ color: "#F1F5F9", margin: "0 0 10px", fontSize: 16 }}>استيراد دفعة مواد</h3>
                <p style={{ color: "#64748B", fontSize: 12, marginBottom: 14 }}>
                  كل سطر مادة بالتنسيق: <code style={{ background: "#0F172A", padding: "2px 6px", borderRadius: 4, color: "#A5B4FC" }}>الكود | الاسم بالعربي | الوحدة | التصنيف | الاسم بالإنجليزي (اختياري)</code>
                </p>
                <textarea value={bulkText} onChange={e => setBulkText(e.target.value)}
                  placeholder={"STL-001 | حديد مسطح 3مم | كجم | حديد وصلب | Flat Steel 3mm\nALM-001 | ألمونيوم صفائح | كجم | ألمونيوم"}
                  rows={8}
                  style={{ ...inputStyle, fontFamily: "monospace", resize: "vertical" }} />
                <div style={{ marginTop: 16, display: "flex", gap: 10 }}>
                  <button onClick={doBulkImport} style={{ background: "#6366F1", color: "#fff", border: "none", borderRadius: 8, padding: "10px 24px", fontSize: 14, fontWeight: 700, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>استيراد</button>
                  <button onClick={() => setShowBulk(false)} style={{ background: "transparent", color: "#94A3B8", border: "1px solid #334155", borderRadius: 8, padding: "10px 24px", fontSize: 14, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>إلغاء</button>
                </div>
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 16, marginBottom: 24 }}>
              <div style={{ background: "#1E293B", border: "1px solid #334155", borderRadius: 12, padding: "20px 24px" }}>
                <div style={{ fontSize: 28 }}>📦</div>
                <div style={{ color: "#94A3B8", fontSize: 12, marginTop: 8 }}>إجمالي المواد في الكتالوج</div>
                <div style={{ color: "#F1F5F9", fontSize: 28, fontWeight: 900 }}>{catItems.length}</div>
              </div>
              <div style={{ background: "#1E293B", border: "1px solid #334155", borderRadius: 12, padding: "20px 24px" }}>
                <div style={{ fontSize: 28 }}>🔥</div>
                <div style={{ color: "#94A3B8", fontSize: 12, marginTop: 8 }}>إجمالي مرات الاستخدام</div>
                <div style={{ color: "#F1F5F9", fontSize: 28, fontWeight: 900 }}>{catItems.reduce((s, i) => s + (i.usage_count || 0), 0)}</div>
              </div>
            </div>

            <div style={{ background: "#1E293B", border: "1px solid #334155", borderRadius: 12, overflow: "hidden" }}>
              <div style={{ padding: "16px 20px", borderBottom: "1px solid #334155", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ color: "#F1F5F9", fontWeight: 700 }}>مواد الكتالوج ({catItems.length})</span>
                <button onClick={loadCatalog} style={{ background: "transparent", color: "#6366F1", border: "1px solid #6366F1", borderRadius: 6, padding: "5px 14px", fontSize: 12, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>تحديث</button>
              </div>
              {catLoading ? (
                <div style={{ padding: 40, textAlign: "center", color: "#64748B" }}>جاري التحميل...</div>
              ) : catItems.length === 0 ? (
                <div style={{ padding: 40, textAlign: "center", color: "#64748B" }}>لا توجد مواد بعد — ابدأ بإضافة مواد للكتالوج</div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ background: "#0F172A" }}>
                      {["الكود", "الاسم", "الوحدة", "التصنيف", "الاستخدام", "إجراءات"].map(h => (
                        <th key={h} style={{ padding: "10px 16px", color: "#64748B", fontSize: 11, fontWeight: 700, textAlign: "right" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {catItems.map(item => (
                      <tr key={item.id} style={{ borderTop: "1px solid #334155" }}>
                        <td style={{ padding: "10px 16px", color: "#6366F1", fontSize: 12, fontFamily: "monospace" }}>{item.code}</td>
                        <td style={{ padding: "10px 16px", color: "#F1F5F9", fontWeight: 700 }}>
                          {ICONS[item.category] || "📦"} {item.name_ar}
                        </td>
                        <td style={{ padding: "10px 16px", color: "#94A3B8", fontSize: 12 }}>{item.unit}</td>
                        <td style={{ padding: "10px 16px", color: "#94A3B8", fontSize: 12 }}>{item.category || "—"}</td>
                        <td style={{ padding: "10px 16px" }}>
                          <span style={{ background: "#052E16", color: "#86EFAC", borderRadius: 99, padding: "2px 10px", fontSize: 11, fontWeight: 700 }}>{item.usage_count || 0} ورشة</span>
                        </td>
                        <td style={{ padding: "10px 16px" }}>
                          <div style={{ display: "flex", gap: 8 }}>
                            <button onClick={() => openEditItem(item)}
                              style={{ background: "#1E1B4B", color: "#A5B4FC", border: "none", borderRadius: 6, padding: "5px 12px", fontSize: 11, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
                              تعديل
                            </button>
                            <button onClick={() => deleteItem(item.id, item.name_ar)}
                              style={{ background: "#450A0A", color: "#FCA5A5", border: "none", borderRadius: 6, padding: "5px 12px", fontSize: 11, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
                              حذف
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
