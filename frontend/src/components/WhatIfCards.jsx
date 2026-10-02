import { ArrowRight, BadgeCheck, Package, Scale, ShieldCheck, Sparkles } from "lucide-react";
import EmptyState from "./EmptyState.jsx";
import { Skeleton } from "./Skeleton.jsx";

const KINDS = {
  "verify-tier": { icon: BadgeCheck, title: "Verify claimed tier", cta: "Review override",
                   detail: (d) => `Claimed tier: ${d}` },
  "verify-competitor": { icon: ShieldCheck, title: "Verify competitor quote", cta: "Apply & re-evaluate",
                         detail: (d) => `Quote: ${d}` },
  "raise-quantity": { icon: Package, title: "Raise quantity", cta: "Apply & re-evaluate",
                      detail: (d) => `${d} units` },
  "lower-discount": { icon: Scale, title: "Lower the discount", cta: "Apply & re-evaluate",
                      detail: (d) => `Request ${d}%` },
};

export default function WhatIfCards({ result, loading, options, sentences, error, onPick }) {
  if (!result) return null;   // shown only once a decision exists
  let body;
  if (result === "APPROVE") {
    body = <EmptyState icon={BadgeCheck} title="Approved as requested">Nothing needs to change.</EmptyState>;
  } else if (error) {
    body = <div className="error">{error}</div>;
  } else if (loading || !options) {
    body = (
      <div className="whatif-grid" aria-busy="true">
        {[0, 1, 2].map((i) => <Skeleton key={i} height={196} style={{ borderRadius: 14 }} />)}
      </div>
    );
  } else if (!options.length) {
    body = (
      <EmptyState icon={Sparkles} title="No single change gets this approved">
        Every option MeTTa tried still failed a rule, so none is suggested.
      </EmptyState>
    );
  } else {
    body = (
      <div className="whatif-grid">
        {options.map((o, i) => {
          const kind = KINDS[o.change];
          const Icon = kind.icon;
          return (
            <button key={o.change} className="whatif" style={{ animationDelay: `${i * 90}ms` }}
                    onClick={() => onPick(o)}>
              <div className="whatif-top">
                <div className="whatif-icon"><Icon size={18} aria-hidden="true" /></div>
                <span className="label">{kind.detail(o.detail)}</span>
              </div>
              <div className="whatif-title">{kind.title}</div>
              <p className="whatif-text">{sentences?.[i]}</p>
              <div className="whatif-value">{o.allowed_max.toFixed(1)}%<small>allowed max</small></div>
              <span className="whatif-cta">{kind.cta} <ArrowRight size={15} aria-hidden="true" /></span>
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <section className="card" aria-labelledby="whatif-title">
      <div className="card-head">
        <h3 className="card-title" id="whatif-title"><Sparkles size={17} aria-hidden="true" />What would it take?</h3>
        <span className="label">Re-checked by MeTTa · up to 3</span>
      </div>
      {body}
    </section>
  );
}
