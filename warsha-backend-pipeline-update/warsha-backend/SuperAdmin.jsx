import { useState, useEffect } from "react";

const SA_API = (import.meta.env.VITE_API_URL || "") + "/api/v1/super-admin";

function saFetch(path, method = "GET", body = null, token = "") {
  return fetch(SA_API + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : null,
  }).then((r) => r.json());
}

export default function SuperAdmin() {
  const [token, setToken] = useState(() => sessionStorage.getItem("sa_token") || "");
  const [loginData, setLoginData] = useState({ username: "", password: "" });
  const [loginErr, setLoginErr] = useState("");
  const [workshops, setWorkshops] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [createData, setCreateData] = useState({
    workshop_name: "", workshop_slug: "", admin_username: "",
    admin_password: "", admin_full_name: "", plan: "trial",
  });
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (token) loadWorkshops();
  }, [token]);

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
    setLoading(true);
    const res = await saFetch("/workshops", "GET", null, token);
    setLoading(false);
    if (Array.isArray(res)) setWorkshops(res);
  }

  async function toggleWorkshop(id) {
    await saFetch(`/workshops/${id}/toggle`, "PUT", null, token);
    loadWorkshops();
  }

  async function deleteWorkshop(id, name) {
    if (!confirm(`هل أنت متأكد من حذف ورشة "${name}"؟ هذا الإجراء لا يمكن التراجع عنه.`)) return;
    await saFetch(`/workshops/${id}`, "DELETE", null, token);
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
        <div style={{ display: "flex", gap: 10 }}>
          <button onClick={() => setShowCreate(!showCreate)}
            style={{ background: "#6366F1", color: "#fff", border: "none", borderRadius: 8, padding: "8px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
            + ورشة جديدة
          </button>
          <button onClick={logout}
            style={{ background: "#1E293B", color: "#94A3B8", border: "1px solid #334155", borderRadius: 8, padding: "8px 18px", fontSize: 13, cursor: "pointer", fontFamily: "Cairo, sans-serif" }}>
            خروج
          </button>
        </div>
      </div>

      <div style={{ padding: "24px 28px" }}>
        {msg && <div style={{ background: msg.startsWith("✅") ? "#052E16" : "#450A0A", border: `1px solid ${msg.startsWith("✅") ? "#166534" : "#7F1D1D"}`, borderRadius: 8, padding: "12px 16px", color: msg.startsWith("✅") ? "#86EFAC" : "#FCA5A5", marginBottom: 20, fontSize: 14 }}>{msg}</div>}

        {/* Create Form */}
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
                    <label style={{ color: "#94A3B8", fontSize: 11, fontWeight: 700, display: "block", marginBottom: 5 }}>{label}</label>
                    <input
                      type={key === "admin_password" ? "password" : "text"}
                      value={createData[key]} placeholder={ph}
                      onChange={e => setCreateData({ ...createData, [key]: e.target.value })}
                      style={{ width: "100%", background: "#0F172A", border: "1px solid #334155", borderRadius: 7, padding: "9px 12px", color: "#F1F5F9", fontSize: 13, outline: "none", boxSizing: "border-box" }} />
                  </div>
                ))}
                <div>
                  <label style={{ color: "#94A3B8", fontSize: 11, fontWeight: 700, display: "block", marginBottom: 5 }}>الباقة</label>
                  <select value={createData.plan} onChange={e => setCreateData({ ...createData, plan: e.target.value })}
                    style={{ width: "100%", background: "#0F172A", border: "1px solid #334155", borderRadius: 7, padding: "9px 12px", color: "#F1F5F9", fontSize: 13, outline: "none", boxSizing: "border-box" }}>
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

        {/* Stats */}
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

        {/* Workshops Table */}
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
      </div>
    </div>
  );
}
