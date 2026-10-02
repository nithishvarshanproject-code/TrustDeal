import { useEffect, useState } from "react";
import { ShieldCheck, Users } from "lucide-react";
import { api } from "../api.js";
import EmptyState from "../components/EmptyState.jsx";
import { Skeleton } from "../components/Skeleton.jsx";
import TrustBars from "../components/TrustBars.jsx";

function history(s) {
  if (s.total_orders == null) return "No payment history on record";
  return `${s.total_orders - (s.late_payments ?? 0)} on time · ${s.late_payments ?? 0} late · ${s.total_orders} orders`;
}

export default function Sellers() {
  const [sellers, setSellers] = useState(null);
  const [error, setError] = useState(null);

  const load = () => { setError(null); setSellers(null); api.sellers().then(setSellers).catch((err) => setError(err.message)); };
  useEffect(load, []);

  let body;
  if (error) body = <div className="error" role="alert">{error} <button className="btn btn-sm" onClick={load}>Retry</button></div>;
  else if (!sellers) {
    body = (
      <div className="seller-grid">
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} height={210} style={{ borderRadius: 14 }} />)}
      </div>
    );
  } else if (!sellers.length) {
    body = <div className="card"><EmptyState icon={Users} title="No sellers yet">Run python -m data.seed to load the demo sellers.</EmptyState></div>;
  } else {
    body = (
      <div className="seller-grid">
        {sellers.map((s, i) => (
          <article key={s.seller_id} className="card seller" style={{ animationDelay: `${i * 70}ms` }}>
            <div className="seller-head">
              <div className="avatar" aria-hidden="true">{s.name.split(" ").map((w) => w[0]).slice(0, 2).join("")}</div>
              <div style={{ minWidth: 0 }}>
                <div className="seller-name">{s.name}</div>
                <div className="muted" style={{ fontSize: 13 }}>{history(s)}</div>
              </div>
              <span className={`tier tier-${s.tier}`}>{s.tier}</span>
            </div>
            <TrustBars after={s.trust} compact />
            <div className="seller-foot mono">
              (seller-trust "{s.name}" (stv {s.trust.strength.toFixed(2)} {s.trust.confidence.toFixed(2)}))
            </div>
          </article>
        ))}
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Sellers</h1>
          <p className="page-sub">Trust is evidence, not a label: MeTTa turns each payment history into a truth value.</p>
        </div>
      </header>

      <div className="card explainer">
        <ShieldCheck size={20} aria-hidden="true" />
        <p>
          <b>strength</b> = on-time orders ÷ all orders &nbsp;·&nbsp; <b>confidence</b> = orders ÷ (orders + 10)
          &nbsp;·&nbsp; no history = <span className="mono">(stv 0.5 0.0)</span>. Every recorded outcome revises it
          with MeTTa <span className="mono">update-trust</span>.
        </p>
      </div>

      {body}
    </div>
  );
}
