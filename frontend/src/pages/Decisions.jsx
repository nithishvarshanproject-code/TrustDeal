import { useCallback, useEffect, useState } from "react";
import { BadgeCheck, Calculator, Gavel, History, LoaderCircle, MousePointerClick, ScrollText, Sparkles, UserCheck } from "lucide-react";
import { api } from "../api.js";
import AskWhy from "../components/AskWhy.jsx";
import AuditTrail from "../components/AuditTrail.jsx";
import { CategoryChip, EngineFooter } from "../components/DecisionCard.jsx";
import EmptyState from "../components/EmptyState.jsx";
import LedgerPanel from "../components/LedgerPanel.jsx";
import OverrideModal from "../components/OverrideModal.jsx";
import { Skeleton } from "../components/Skeleton.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import { formatISTDateTime } from "../lib/format.js";

const pct = (v) => (v == null ? "—" : `${v.toFixed(1)}%`);
const time = (iso) => formatISTDateTime(iso);   // "03 Oct 2026, 09:46 IST"

const WHAT_IF_TITLES = {
  "verify-tier": "Verify claimed tier",
  "verify-competitor": "Verify competitor quote",
  "raise-quantity": "Raise quantity",
  "lower-discount": "Lower the discount",
};

function DealDetail({ dealId, onChanged }) {
  const [deal, setDeal] = useState(null);
  const [error, setError] = useState(null);
  const [whatIf, setWhatIf] = useState(null);
  const [whatIfBusy, setWhatIfBusy] = useState(false);
  const [overrideOpen, setOverrideOpen] = useState(false);

  const load = useCallback(() => {
    setError(null);
    return api.deal(dealId).then(setDeal).catch((err) => setError(err.message));
  }, [dealId]);

  useEffect(() => { setDeal(null); setWhatIf(null); load(); }, [load]);

  if (error) return <div className="error" role="alert">{error} <button className="btn btn-sm" onClick={load}>Retry</button></div>;
  if (!deal) {
    return (
      <div aria-busy="true">
        <Skeleton width={140} height={36} style={{ borderRadius: 999 }} />
        {Array.from({ length: 9 }, (_, i) => <Skeleton key={i} height={18} style={{ margin: "16px 0" }} />)}
      </div>
    );
  }

  const d = deal.decision;
  const options = whatIf?.what_if ?? d.what_if;
  const sentences = whatIf?.explanation ?? d.explanation?.what_if ?? [];

  async function computeWhatIf() {
    setWhatIfBusy(true);
    try { setWhatIf(await api.whatIf(dealId)); } catch (err) { setError(err.message); } finally { setWhatIfBusy(false); }
  }

  return (
    <>
      <div className="decision-top">
        <StatusBadge result={d.result} size="lg" animate key={`b-${dealId}`} />
        <div className="decision-meta">
          Deal <strong>#{deal.deal_id}</strong> · <strong>{deal.seller.name}</strong> · {deal.seller.tier} on record
          {deal.claimed_tier && deal.claimed_tier !== deal.seller.tier && <> · claims <strong>{deal.claimed_tier}</strong></>}
        </div>
      </div>
      {d.category && <div style={{ marginTop: 12 }}><CategoryChip profile={d.category} /></div>}

      <div className="tiles">
        <div className="tile"><div className="label">Requested</div>
          <div className="tile-value">{deal.discount_requested.toFixed(1)}<small>%</small></div>
          <div className="tile-note">{deal.quantity} units · {deal.product.name}</div></div>
        <div className="tile"><div className="label">Approved</div>
          <div className="tile-value">{d.approved_discount == null ? "—" : <>{d.approved_discount.toFixed(1)}<small>%</small></>}</div>
          <div className="tile-note">{d.result === "COUNTER" ? "counter offer" : " "}</div></div>
        <div className="tile"><div className="label">Confidence</div>
          <div className="tile-value">{Math.round(d.confidence * 100)}<small>%</small></div>
          <div className="tile-note">{deal.competitor_price != null
            ? `competitor ${deal.competitor_price} ${deal.competitor_verified ? "(verified)" : "(unverified)"}` : " "}</div></div>
      </div>

      {d.override_hint && (
        <div className="callout">
          <BadgeCheck size={20} aria-hidden="true" />
          <p>Seller claims <b>{d.override_hint.claimed_tier}</b>. If verified, the allowed max would be{" "}
            <b>{d.override_hint.allowed_max.toFixed(1)}%</b>.</p>
        </div>
      )}

      <div className="card-head" style={{ marginBottom: 8 }}>
        <h3 className="card-title"><ScrollText size={17} aria-hidden="true" />Audit trail</h3>
      </div>
      <AuditTrail trail={d.trail} replayKey={`t-${dealId}`} />
      <AskWhy source="deal" decisionId={d.decision_id} />

      {d.result !== "APPROVE" && (
        <div className="subsection">
          <div className="card-head" style={{ marginBottom: 10 }}>
            <h3 className="card-title"><Sparkles size={17} aria-hidden="true" />What would it take?</h3>
            {options == null && (
              <button className="btn btn-sm" onClick={computeWhatIf} disabled={whatIfBusy}>
                {whatIfBusy ? <LoaderCircle size={14} className="spin" aria-hidden="true" /> : <Sparkles size={14} aria-hidden="true" />}
                Compute with MeTTa
              </button>
            )}
          </div>
          {options == null
            ? <p className="muted" style={{ margin: 0, fontSize: 13 }}>Not computed yet for this decision.</p>
            : options.length === 0
              ? <p className="muted" style={{ margin: 0, fontSize: 13 }}>No single change gets this approved.</p>
              : (
                <ul className="mini-list">
                  {options.map((o, i) => (
                    <li key={o.change}>
                      <span><b>{WHAT_IF_TITLES[o.change]}</b> <span className="muted">· {sentences[i]}</span></span>
                      <span className="mono">{o.allowed_max.toFixed(1)}%</span>
                    </li>
                  ))}
                </ul>
              )}
        </div>
      )}

      <div className="subsection">
        <div className="card-head" style={{ marginBottom: 10 }}>
          <h3 className="card-title"><UserCheck size={17} aria-hidden="true" />Human overrides</h3>
          <button className="btn btn-sm" onClick={() => setOverrideOpen(true)}>
            <Gavel size={14} aria-hidden="true" /> Add override
          </button>
        </div>
        {deal.overrides.length === 0
          ? <p className="muted" style={{ margin: 0, fontSize: 13 }}>None. The MeTTa decision stands.</p>
          : (
            <ul className="mini-list">
              {deal.overrides.map((o) => (
                <li key={o.override_id}>
                  <span>
                    <StatusBadge result={d.result} size="sm" /> → <StatusBadge result={o.new_result} size="sm" />{" "}
                    <b style={{ marginLeft: 6 }}>{o.reviewer}</b> <span className="muted">· “{o.reason}”</span>
                  </span>
                  <span className="muted">{time(o.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
      </div>

      <div className="decision-foot">
        <EngineFooter decision={d} />
        <span>Original decision is never modified</span>
      </div>

      {overrideOpen && (
        <OverrideModal
          dealId={dealId}
          defaults={{ new_result: d.result === "APPROVE" ? "REJECT" : "APPROVE", reason: "" }}
          onClose={() => setOverrideOpen(false)}
          onDone={() => { setOverrideOpen(false); load(); onChanged?.(); }}
        />
      )}
    </>
  );
}

export default function Decisions({ onNavigate }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  const loadRows = useCallback(() => {
    setError(null);
    setRows(null);
    api.history()
      .then((all) => {
        const decided = all.filter((r) => r.result);
        setRows(decided);
        setSelected((cur) => cur ?? decided[0]?.deal_id ?? null);
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(loadRows, [loadRows]);

  let list;
  if (error) list = <div className="error" role="alert">{error} <button className="btn btn-sm" onClick={loadRows}>Retry</button></div>;
  else if (!rows) list = Array.from({ length: 8 }, (_, i) => <Skeleton key={i} height={44} style={{ margin: "8px 0" }} />);
  else if (!rows.length) {
    list = (
      <EmptyState icon={History} title="No seller decisions yet">
        Evaluate a deal on the Deal check page and it will appear here with its full audit trail.
        <br /><br />
        <button className="btn btn-primary btn-sm" onClick={() => onNavigate("desk")}>
          <Calculator size={14} aria-hidden="true" /> Go to Deal check
        </button>
        <p className="muted" style={{ margin: "18px 0 8px", fontSize: 13 }}>
          Customer chat decisions appear in the Agent inbox.
        </p>
        <button className="btn btn-ghost btn-sm" onClick={() => onNavigate("agent")}>
          Open Agent inbox
        </button>
      </EmptyState>
    );
  } else {
    list = (
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>#</th><th>Seller</th><th className="num">Qty</th><th className="num">Asked</th>
              <th>Result</th><th className="num">Approved</th><th className="num">Conf.</th><th>When</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.deal_id} className={r.deal_id === selected ? "selected" : ""} tabIndex={0}
                  onClick={() => setSelected(r.deal_id)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(r.deal_id); } }}>
                <td className="muted">{r.deal_id}</td>
                <td>{r.seller_name}</td>
                <td className="num">{r.quantity}</td>
                <td className="num">{pct(r.discount_requested)}</td>
                <td><StatusBadge result={r.result} size="sm" /></td>
                <td className="num">{pct(r.approved_discount)}</td>
                <td className="num">{Math.round(r.confidence * 100)}%</td>
                <td className="muted">{time(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Decisions</h1>
          <p className="page-sub">Every decision MeTTa made, with its audit trail and any human overrides.</p>
        </div>
        {rows?.length > 0 && <span className="label">{rows.length} decisions</span>}
      </header>
      <div className="split">
        <section className="card" aria-label="Decision history" style={{ padding: 12 }}>{list}</section>
        <section className="card" aria-live="polite" aria-label="Decision detail">
          {selected
            ? <DealDetail dealId={selected} onChanged={loadRows} />
            : <EmptyState icon={MousePointerClick} title="Pick a decision">Select a row to see its reasoning.</EmptyState>}
        </section>
      </div>
      <LedgerPanel />
    </div>
  );
}
