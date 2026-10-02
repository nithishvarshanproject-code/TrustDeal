import { useEffect, useState } from "react";
import { Bot, Calculator, Gavel, History, LayoutDashboard, Package, ScrollText, Users } from "lucide-react";
import { api } from "../api.js";
import ModeSwitch from "./ModeSwitch.jsx";

export const NAV = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "desk", label: "Deal check", icon: Calculator },
  { key: "decisions", label: "Decisions", icon: History },
  { key: "agent", label: "Agent inbox", icon: Bot },
  { key: "policy", label: "Policy", icon: ScrollText },
  { key: "sellers", label: "Sellers", icon: Users },
  { key: "products", label: "Products", icon: Package },
];

const OMEGA_POLL_MS = 10000;

function EngineStatus() {
  const [online, setOnline] = useState(null);
  const [engine, setEngine] = useState(null);     // "local" | "omega"
  const [omega, setOmega] = useState(null);       // /omega/status, omega mode only

  useEffect(() => {
    fetch("/api/health").then((r) => setOnline(r.ok)).catch(() => setOnline(false));
    api.engine().then((e) => setEngine(e.engine_runner)).catch(() => setEngine(null));
  }, []);

  // Poll Omega's status every 10 s, and only when decisions run through Omega.
  useEffect(() => {
    if (engine !== "omega") return undefined;
    const check = () => api.omegaStatus().then(setOmega).catch(() => setOmega(null));
    check();
    const id = setInterval(check, OMEGA_POLL_MS);
    return () => clearInterval(id);
  }, [engine]);

  if (online === false) {
    return (<><strong>MeTTa rules engine</strong><span style={{ color: "var(--reject)" }}>Backend offline</span></>);
  }
  if (engine === "omega") {
    const ok = omega?.connected && omega?.rules_match;
    return (
      <>
        <strong>Omega agent</strong>
        <span>
          <span className="dot" style={ok ? undefined : { background: "var(--reject)", boxShadow: "0 0 0 3px var(--reject-soft)" }} />
          {omega == null ? "Checking…" : ok ? "Connected · same rules" : "Not connected"}
        </span>
      </>
    );
  }
  return (
    <>
      <strong>MeTTa rules engine</strong>
      <span><span className="dot" />{online ? "Local (hyperon)" : "Connecting…"}</span>
    </>
  );
}

export default function Sidebar({ current, onSelect, onSwitchMode }) {
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="brand">
        <div className="brand-mark"><Gavel size={17} color="#fff" aria-hidden="true" /></div>
        <div className="brand-text">
          <div className="brand-name">TrustDeal</div>
          <div className="brand-sub">The BASIX Deal Agent on Omega</div>
        </div>
      </div>
      <div className="sidebar-mode"><ModeSwitch mode="seller" onSwitch={onSwitchMode} /></div>
      {NAV.map(({ key, label, icon: Icon }) => (
        <button
          key={key}
          className={`nav-item${key === current ? " active" : ""}`}
          aria-current={key === current ? "page" : undefined}
          title={label}
          onClick={() => onSelect(key)}
        >
          <Icon size={18} aria-hidden="true" />
          <span className="nav-label">{label}</span>
        </button>
      ))}
      <div className="sidebar-footer"><EngineStatus /></div>
    </nav>
  );
}
