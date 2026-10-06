import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import QRCode from "qrcode";
import { api, clearToken, getToken, setToken } from "./api";
import "./styles.css";

const TAXFLOW_LEGACY_VERSION = "20260524-inventory-no-demo-rows";

function App() {
  const [tokenReady, setTokenReady] = useState(Boolean(getToken()));
  const [error, setError] = useState("");
  const [userRole, setUserRole] = useState(null);

  useEffect(() => {
    if (!tokenReady) {
      setUserRole(null);
      return;
    }
    api("/auth/me")
      .then((data) => setUserRole(data.role))
      .catch((err) => {
        setError(err.message);
        clearToken();
        setTokenReady(false);
      });
  }, [tokenReady]);

  async function login(email, password) {
    setError("");
    try {
      const data = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setToken(data.access_token);
      const redirect = new URLSearchParams(window.location.search).get("redirect");
      if (redirect === "pos" || redirect === "/pos") {
        window.location.href = "/pos";
        return;
      }
      setTokenReady(true);
    } catch (err) {
      setError("Could not sign in. Use admin@taxflowapp.com / admin123 and make sure the API is running on 127.0.0.1:8000.");
      throw err;
    }
  }

  function handleLogout() {
    clearToken();
    setTokenReady(false);
    setUserRole(null);
  }

  if (!tokenReady) return <Login onLogin={login} error={error} />;

  if (tokenReady && !userRole) {
    return (
      <main className="taxflow-loader" aria-live="polite">
        <div className="taxflow-loader-box">
          <div className="loader-brand">E<span>Tax</span>Flow</div>
          <div className="loader-bar"><span /></div>
          <p>Verifying session...</p>
        </div>
      </main>
    );
  }

  if (userRole === "superadmin") {
    return <SuperAdminDashboard onLogout={handleLogout} />;
  }

  return <TaxFlowLegacyApp onLogout={handleLogout} />;
}

/* ─── Super Admin Dashboard ─────────────────────────────────────────────── */

function SuperAdminDashboard({ onLogout }) {
  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [editExpiry, setEditExpiry] = useState(null);
  const [resetPwd, setResetPwd] = useState(null);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const data = await api("/superadmin/companies");
      setCompanies(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleDelete(companyId, companyName) {
    if (!window.confirm(`Delete company "${companyName}" and ALL its data?\n\nThis cannot be undone.`)) return;
    try {
      await api(`/superadmin/companies/${companyId}`, { method: "DELETE" });
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  const now = new Date();
  const expiringSoon = companies.filter((c) => {
    if (!c.subscription_expires_at) return false;
    const exp = new Date(c.subscription_expires_at);
    return exp > now && exp - now < 30 * 24 * 60 * 60 * 1000;
  }).length;
  const expired = companies.filter((c) => c.subscription_expires_at && new Date(c.subscription_expires_at) < now).length;

  return (
    <div style={{ minHeight: "100vh", background: "#f0f5ff", fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif", fontSize: "13.5px" }}>
      {/* Header */}
      <header style={{ background: "#1e3a8a", color: "#fff", padding: "0 24px", height: "54px", display: "flex", alignItems: "center", justifyContent: "space-between", boxShadow: "0 2px 8px rgba(0,0,0,.18)" }}>
        <div style={{ fontWeight: 900, fontSize: "18px", letterSpacing: "-0.5px" }}>
          E<span style={{ color: "#60a5fa" }}>Tax</span>Flow
          <span style={{ marginLeft: "10px", fontSize: "11px", fontWeight: 400, opacity: 0.65, background: "rgba(255,255,255,.12)", padding: "2px 8px", borderRadius: "20px" }}>Super Admin</span>
        </div>
        <button
          onClick={onLogout}
          style={{ background: "rgba(255,255,255,.15)", border: "1px solid rgba(255,255,255,.25)", color: "#fff", borderRadius: "7px", padding: "6px 16px", cursor: "pointer", fontSize: "13px" }}
        >
          Logout
        </button>
      </header>

      <div style={{ padding: "24px", maxWidth: "1440px", margin: "0 auto" }}>
        {/* KPI Row */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "14px", marginBottom: "24px" }}>
          <KpiCard label="Companies" value={companies.length} color="#2563eb" icon="🏢" />
          <KpiCard label="Total Users" value={companies.reduce((a, c) => a + c.users.length, 0)} color="#0891b2" icon="👤" />
          <KpiCard label="Employees" value={companies.reduce((a, c) => a + c.employee_count, 0)} color="#7c3aed" icon="👷" />
          <KpiCard label="Expiring Soon" value={expiringSoon} color="#d97706" icon="⏰" />
          <KpiCard label="Expired" value={expired} color={expired > 0 ? "#dc2626" : "#059669"} icon="❌" />
        </div>

        {/* Toolbar */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
          <h2 style={{ margin: 0, fontSize: "15px", fontWeight: 700, color: "#1e3a8a" }}>Client Companies</h2>
          <div style={{ display: "flex", gap: "10px" }}>
            <button onClick={load} style={{ background: "#fff", border: "1px solid #dde8f8", borderRadius: "7px", padding: "7px 14px", cursor: "pointer", color: "#475569" }}>
              Refresh
            </button>
            <button
              onClick={() => setShowCreate(true)}
              style={{ background: "#2563eb", color: "#fff", border: "none", borderRadius: "7px", padding: "7px 16px", cursor: "pointer", fontWeight: 600 }}
            >
              + Add Company
            </button>
          </div>
        </div>

        {error && (
          <div style={{ background: "#fee2e2", color: "#dc2626", padding: "12px 16px", borderRadius: "8px", marginBottom: "16px", fontSize: "13px" }}>
            {error}
          </div>
        )}

        {/* Table */}
        <div style={{ background: "#fff", borderRadius: "12px", border: "1px solid #e2e8f0", overflow: "hidden", boxShadow: "0 1px 4px rgba(0,0,0,.06)" }}>
          {loading ? (
            <div style={{ padding: "64px", textAlign: "center", color: "#94a3b8" }}>Loading companies...</div>
          ) : companies.length === 0 ? (
            <div style={{ padding: "64px", textAlign: "center", color: "#94a3b8" }}>No companies yet. Add one to get started.</div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                <thead>
                  <tr style={{ background: "#f8faff", borderBottom: "2px solid #e2e8f0" }}>
                    <Th>#</Th>
                    <Th>Company</Th>
                    <Th>Admin Name</Th>
                    <Th>Email</Th>
                    <Th>Password</Th>
                    <Th>Created</Th>
                    <Th>Expiry</Th>
                    <Th align="center">Employees</Th>
                    <Th align="center">Sub Users</Th>
                    <Th>Actions</Th>
                  </tr>
                </thead>
                <tbody>
                  {companies.map((company, idx) => (
                    <CompanyRow
                      key={company.id}
                      idx={idx}
                      company={company}
                      onSetExpiry={() => setEditExpiry({ company_id: company.id, expires_at: company.subscription_expires_at || "" })}
                      onResetPwd={(user) => setResetPwd({ company_id: company.id, user_id: user.id, full_name: user.full_name, password: "" })}
                      onDelete={() => handleDelete(company.id, company.name)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Modals */}
      {showCreate && (
        <CreateCompanyModal
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); load(); }}
          onError={setError}
        />
      )}
      {editExpiry && (
        <SetExpiryModal
          data={editExpiry}
          onClose={() => setEditExpiry(null)}
          onSaved={() => { setEditExpiry(null); load(); }}
          onError={setError}
        />
      )}
      {resetPwd && (
        <ResetPasswordModal
          data={resetPwd}
          onClose={() => setResetPwd(null)}
          onSaved={() => { setResetPwd(null); load(); }}
          onError={setError}
        />
      )}
    </div>
  );
}

function KpiCard({ label, value, color, icon }) {
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: "10px", padding: "16px", boxShadow: "0 1px 3px rgba(0,0,0,.05)" }}>
      <div style={{ fontSize: "20px", marginBottom: "6px" }}>{icon}</div>
      <div style={{ fontSize: "24px", fontWeight: 800, color }}>{value}</div>
      <div style={{ fontSize: "12px", color: "#64748b", marginTop: "2px" }}>{label}</div>
    </div>
  );
}

function Th({ children, align }) {
  return (
    <th style={{ padding: "10px 14px", textAlign: align || "left", fontWeight: 600, color: "#475569", fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.04em", whiteSpace: "nowrap" }}>
      {children}
    </th>
  );
}

function Td({ children, style }) {
  return (
    <td style={{ padding: "11px 14px", borderBottom: "1px solid #f1f5f9", verticalAlign: "middle", ...style }}>
      {children}
    </td>
  );
}

function CompanyRow({ idx, company, onSetExpiry, onResetPwd, onDelete }) {
  const admin = company.users.find((u) => u.role === "admin") || company.users[0];
  const now = new Date();
  const expDate = company.subscription_expires_at ? new Date(company.subscription_expires_at) : null;
  const isExpired = expDate && expDate < now;
  const isExpiringSoon = expDate && !isExpired && expDate - now < 30 * 24 * 60 * 60 * 1000;

  return (
    <tr style={{ transition: "background .1s" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "#f8faff")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "")}
    >
      <Td style={{ color: "#94a3b8", fontSize: "12px" }}>{idx + 1}</Td>
      <Td>
        <div style={{ fontWeight: 700, color: "#1e3a8a" }}>{company.name}</div>
        {company.trn && <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "1px" }}>TRN: {company.trn}</div>}
      </Td>
      <Td style={{ color: "#374151" }}>{admin?.full_name || <span style={{ color: "#94a3b8" }}>—</span>}</Td>
      <Td style={{ color: "#2563eb" }}>{admin?.email || <span style={{ color: "#94a3b8" }}>—</span>}</Td>
      <Td>
        {/* Passwords are only ever stored hashed; never shown. */}
        <span style={{ color: "#94a3b8" }}>••••••</span>
      </Td>
      <Td style={{ color: "#64748b", fontSize: "12px" }}>
        {company.created_at ? new Date(company.created_at).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "—"}
      </Td>
      <Td>
        {expDate ? (
          <span style={{
            background: isExpired ? "#fee2e2" : isExpiringSoon ? "#fef9c3" : "#dcfce7",
            color: isExpired ? "#dc2626" : isExpiringSoon ? "#92400e" : "#166534",
            padding: "3px 9px", borderRadius: "20px", fontSize: "11px", fontWeight: 600, whiteSpace: "nowrap",
          }}>
            {expDate.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}
            {isExpired && " ✗"}
            {isExpiringSoon && " ⚠"}
          </span>
        ) : (
          <span style={{ color: "#94a3b8", fontSize: "11px" }}>No expiry</span>
        )}
      </Td>
      <Td style={{ textAlign: "center", fontWeight: 600, color: "#374151" }}>{company.employee_count}</Td>
      <Td style={{ textAlign: "center", fontWeight: 600, color: "#374151" }}>{company.sub_user_count}</Td>
      <Td>
        <div style={{ display: "flex", gap: "5px", flexWrap: "nowrap" }}>
          <ActionBtn color="#2563eb" onClick={onSetExpiry} title="Set subscription expiry date">Expiry</ActionBtn>
          {admin && <ActionBtn color="#059669" onClick={() => onResetPwd(admin)} title="Reset admin password">Pwd</ActionBtn>}
          <ActionBtn color="#dc2626" onClick={onDelete} title="Delete company and all data">Del</ActionBtn>
        </div>
      </Td>
    </tr>
  );
}

function ActionBtn({ children, color, onClick, title }) {
  return (
    <button
      title={title}
      onClick={onClick}
      style={{ background: color + "18", border: "1px solid " + color + "40", color, borderRadius: "6px", padding: "4px 9px", cursor: "pointer", fontSize: "11px", fontWeight: 600, whiteSpace: "nowrap" }}
      onMouseEnter={(e) => { e.currentTarget.style.background = color; e.currentTarget.style.color = "#fff"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = color + "18"; e.currentTarget.style.color = color; }}
    >
      {children}
    </button>
  );
}

function Modal({ title, onClose, children }) {
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "grid", placeItems: "center", zIndex: 9999, padding: "24px" }}>
      <div style={{ background: "#fff", borderRadius: "14px", width: "min(440px, 100%)", boxShadow: "0 20px 60px rgba(0,0,0,.18)", overflow: "hidden" }}>
        <div style={{ padding: "18px 22px 14px", borderBottom: "1px solid #f1f5f9", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontWeight: 700, fontSize: "15px", color: "#1e3a8a" }}>{title}</span>
          <button onClick={onClose} style={{ background: "none", border: "none", fontSize: "20px", cursor: "pointer", color: "#94a3b8", lineHeight: 1, padding: "0 4px" }}>×</button>
        </div>
        <div style={{ padding: "20px 22px 22px" }}>{children}</div>
      </div>
    </div>
  );
}

function FieldRow({ label, children }) {
  return (
    <div style={{ marginBottom: "13px" }}>
      <label style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "#374151", marginBottom: "5px" }}>{label}</label>
      {children}
    </div>
  );
}

function Input({ value, onChange, type = "text", placeholder, required }) {
  return (
    <input
      type={type}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      required={required}
      style={{ width: "100%", padding: "8px 11px", border: "1px solid #dde8f8", borderRadius: "7px", fontSize: "13px", background: "#f8faff", color: "#0f172a", outline: "none", boxSizing: "border-box" }}
    />
  );
}

function CreateCompanyModal({ onClose, onCreated, onError }) {
  const [form, setForm] = useState({ name: "", email: "", password: "", full_name: "", trn: "", expires_at: "" });
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/superadmin/companies", { method: "POST", body: JSON.stringify(form) });
      onCreated();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function field(key) {
    return { value: form[key], onChange: (e) => setForm((f) => ({ ...f, [key]: e.target.value })) };
  }

  return (
    <Modal title="Add New Company" onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <FieldRow label="Company Name *"><Input {...field("name")} placeholder="e.g. Acme Trading LLC" required /></FieldRow>
        <FieldRow label="Admin Full Name"><Input {...field("full_name")} placeholder="e.g. John Smith" /></FieldRow>
        <FieldRow label="Admin Email *"><Input type="email" {...field("email")} placeholder="admin@company.com" required /></FieldRow>
        <FieldRow label="Admin Password *"><Input type="text" {...field("password")} placeholder="Choose a password" required /></FieldRow>
        <FieldRow label="TRN (optional)"><Input {...field("trn")} placeholder="e.g. 100000000000001" /></FieldRow>
        <FieldRow label="Subscription Expiry"><Input type="date" {...field("expires_at")} /></FieldRow>
        <div style={{ display: "flex", gap: "10px", marginTop: "4px" }}>
          <button type="button" onClick={onClose} style={{ flex: 1, padding: "9px", border: "1px solid #dde8f8", borderRadius: "7px", background: "#fff", cursor: "pointer", color: "#475569" }}>
            Cancel
          </button>
          <button type="submit" disabled={busy} style={{ flex: 2, padding: "9px", border: "none", borderRadius: "7px", background: "#2563eb", color: "#fff", fontWeight: 700, cursor: "pointer" }}>
            {busy ? "Creating..." : "Create Company"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function SetExpiryModal({ data, onClose, onSaved, onError }) {
  const [expiresAt, setExpiresAt] = useState(data.expires_at || "");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await api(`/superadmin/companies/${data.company_id}/set-expiry`, {
        method: "POST",
        body: JSON.stringify({ expires_at: expiresAt || null }),
      });
      onSaved();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Set Subscription Expiry" onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <FieldRow label="Expiry Date (leave blank to remove expiry)">
          <Input type="date" value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} />
        </FieldRow>
        <div style={{ display: "flex", gap: "10px", marginTop: "4px" }}>
          <button type="button" onClick={onClose} style={{ flex: 1, padding: "9px", border: "1px solid #dde8f8", borderRadius: "7px", background: "#fff", cursor: "pointer", color: "#475569" }}>Cancel</button>
          <button type="submit" disabled={busy} style={{ flex: 2, padding: "9px", border: "none", borderRadius: "7px", background: "#2563eb", color: "#fff", fontWeight: 700, cursor: "pointer" }}>
            {busy ? "Saving..." : "Save Expiry"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ResetPasswordModal({ data, onClose, onSaved, onError }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!password.trim()) return;
    setBusy(true);
    try {
      await api(`/superadmin/companies/${data.company_id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ user_id: data.user_id, password }),
      });
      onSaved();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Reset Password — ${data.full_name}`} onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <FieldRow label="New Password">
          <Input type="text" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Enter new password" required />
        </FieldRow>
        <div style={{ display: "flex", gap: "10px", marginTop: "4px" }}>
          <button type="button" onClick={onClose} style={{ flex: 1, padding: "9px", border: "1px solid #dde8f8", borderRadius: "7px", background: "#fff", cursor: "pointer", color: "#475569" }}>Cancel</button>
          <button type="submit" disabled={busy} style={{ flex: 2, padding: "9px", border: "none", borderRadius: "7px", background: "#059669", color: "#fff", fontWeight: 700, cursor: "pointer" }}>
            {busy ? "Saving..." : "Reset Password"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/* ─── Legacy App wrapper ─────────────────────────────────────────────────── */

function TaxFlowLegacyApp({ onLogout }) {
  const mountedRef = useRef(false);
  const [loadMessage, setLoadMessage] = useState("Preparing ETaxFlow...");

  useEffect(() => {
    if (mountedRef.current) return;
    mountedRef.current = true;
    let cancelled = false;

    async function mountOriginalDesign() {
      setLoadMessage("Loading workspace...");
      preloadTaxFlowAssets();
      const response = await fetch(versionedTaxFlowUrl("/taxflow/index.html"), { cache: "default" });
      if (!response.ok) throw new Error(`ETaxFlow shell returned ${response.status}`);
      const html = await response.text();
      if (cancelled) return;

      setLoadMessage("Building interface...");
      const doc = new DOMParser().parseFromString(html, "text/html");
      document.body.className = doc.body.className || "theme-light";
      document.title = doc.title || "ETaxFlow UAE - Business Management Platform";
      ensureStylesheet("taxflow-fonts", "https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700&family=DM+Sans:wght@300;400;500&family=DM+Mono:wght@400;500&display=swap");
      ensureStylesheet("taxflow-original-css", versionedTaxFlowUrl("/taxflow/src/styles.css"));

      document.body.innerHTML = doc.body.innerHTML
        .replace(/<script[\s\S]*?<\/script>/gi, "")
        .replace(/<noscript[\s\S]*?<\/noscript>/gi, "");

      window.__taxflowLogout = onLogout;
      const localApiHost = ["localhost", "::1", ""].includes(window.location.hostname) ? "127.0.0.1" : window.location.hostname;
      window.TAXFLOW_API_BASE_URL = import.meta.env.VITE_API_BASE_URL || `http://${localApiHost}:8000/api/v1`;
      window.TaxFlowQRCode = QRCode;
      setLoadMessage("Starting app...");
      await loadScriptOnce("taxflow-original-js", versionedTaxFlowUrl("/taxflow/src/app.js"));
      patchLegacyLogout(onLogout);
      patchBackendBridge();
      if (!window.__taxflowAppInitialized) window.initApp?.();
      window.go?.("dashboard");
      window.setTimeout?.(() => {
        window.forceDbRefresh?.();
      }, 250);
      window.toast?.("ETaxFlow app loaded", "ok");
    }

    mountOriginalDesign().catch((err) => {
      document.body.innerHTML = `<div class="mount-error">Could not load ETaxFlow design: ${err.message}</div>`;
    });

    return () => {
      cancelled = true;
    };
  }, [onLogout]);

  return (
    <main className="taxflow-loader" aria-live="polite">
      <div className="taxflow-loader-box">
        <div className="loader-brand">E<span>Tax</span>Flow</div>
        <div className="loader-bar"><span /></div>
        <p>{loadMessage}</p>
      </div>
    </main>
  );
}

/* ─── Helpers ────────────────────────────────────────────────────────────── */

function preloadTaxFlowAssets() {
  ensureResourceHint("taxflow-fonts-preconnect", "preconnect", "https://fonts.googleapis.com");
  ensureResourceHint("taxflow-fonts-static-preconnect", "preconnect", "https://fonts.gstatic.com", true);
  ensureResourceHint("taxflow-html-prefetch", "prefetch", versionedTaxFlowUrl("/taxflow/index.html"));
  ensureResourceHint("taxflow-css-preload", "preload", versionedTaxFlowUrl("/taxflow/src/styles.css"), false, "style");
  ensureResourceHint("taxflow-js-preload", "preload", versionedTaxFlowUrl("/taxflow/src/app.js"), false, "script");
}

function versionedTaxFlowUrl(path) {
  return `${path}?v=${TAXFLOW_LEGACY_VERSION}`;
}

function ensureStylesheet(id, href) {
  const existing = document.getElementById(id);
  if (existing) {
    existing.href = href;
    return;
  }
  const link = document.createElement("link");
  link.id = id;
  link.rel = "stylesheet";
  link.href = href;
  document.head.appendChild(link);
}

function ensureResourceHint(id, rel, href, crossOrigin = false, as) {
  if (document.getElementById(id)) return;
  const link = document.createElement("link");
  link.id = id;
  link.rel = rel;
  link.href = href;
  if (as) link.as = as;
  if (crossOrigin) link.crossOrigin = "";
  document.head.appendChild(link);
}

function loadScriptOnce(id, src) {
  return new Promise((resolve, reject) => {
    const existing = document.getElementById(id);
    if (existing) {
      if (existing.dataset.loaded === "true") {
        resolve();
        return;
      }
      existing.addEventListener("load", resolve, { once: true });
      existing.addEventListener("error", () => reject(new Error(`Failed to load ${src}`)), { once: true });
      return;
    }
    const script = document.createElement("script");
    script.id = id;
    script.src = src;
    script.defer = true;
    script.onload = () => {
      script.dataset.loaded = "true";
      resolve();
    };
    script.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.body.appendChild(script);
  });
}

function patchLegacyLogout(onLogout) {
  window.logout = () => {
    window.toast?.("Logged out. Session cleared locally.", "info");
    onLogout();
    window.location.reload();
  };
}

function patchBackendBridge() {
  window.TaxFlowAPI = {
    async createInvoice(payload) {
      return api("/invoices", { method: "POST", body: JSON.stringify(payload) });
    },
    async listInvoices() {
      return api("/invoices");
    },
    async runVatSummary() {
      return api("/jobs/vat-summary", { method: "POST", body: JSON.stringify({}) });
    },
  };
}

/* ─── Login page ─────────────────────────────────────────────────────────── */

function Login({ onLogin, error }) {
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      await onLogin(form.get("email"), form.get("password"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form className="login-box" onSubmit={submit}>
        <div>
          <div className="brand">E<span>Tax</span>Flow</div>
          <p>Sign in to your ETaxFlow account.</p>
        </div>
        <input name="email" type="email" placeholder="Email address" defaultValue="admin@taxflowapp.com" required />
        <input name="password" type="password" placeholder="Password" required />
        {error && <div className="alert">{error}</div>}
        <button className="primary" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
        <div style={{textAlign:"center",marginTop:"12px",fontSize:"13px",color:"#94a3b8"}}>
          Don't have an account?{" "}
          <a href="/signup.html" style={{color:"#60a5fa",textDecoration:"none",fontWeight:600}}>
            Sign up free →
          </a>
        </div>
        <div style={{textAlign:"center",marginTop:"6px"}}>
          <a href="/landing.html" style={{fontSize:"12px",color:"#475569",textDecoration:"none"}}>
            ← Back to website
          </a>
        </div>
      </form>
    </main>
  );
}

if (window.location.pathname === '/pos') {
  fetch('/taxflow/pos.html')
    .then(r => r.text())
    .then(html => { document.open(); document.write(html); document.close(); })
    .catch(() => { window.location.replace('/taxflow/pos.html'); });
} else {
  createRoot(document.getElementById("root")).render(<App />);
}
