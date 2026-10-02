import { useCallback, useEffect, useState } from "react";
import { CircleCheck, FileCode, GitCompareArrows, History, LoaderCircle, ScrollText, ShieldCheck } from "lucide-react";
import { api } from "../api.js";
import EmptyState from "../components/EmptyState.jsx";
import { Skeleton } from "../components/Skeleton.jsx";
import { formatISTDateTime } from "../lib/format.js";
import { LOAD_FAILED, loadState } from "../lib/loadState.js";

const fmt = (v) => (typeof v === "number" ? v.toFixed(1) : v);

function ProposalCard({ proposal, onApplied }) {
  const [approver, setApprover] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const { fact, key, old_value: oldValue, new_value: newValue, evidence } = proposal;

  async function approve(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const applied = await api.applyProposal({ fact, key, old_value: oldValue, new_value: newValue, approved_by: approver });
      onApplied(applied);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="proposal">
      <div className="card-head" style={{ marginBottom: 14 }}>
        <h3 className="card-title"><GitCompareArrows size={17} aria-hidden="true" />Raise the {key} tier cap</h3>
        <span className="label">from {evidence.length} overrides · proposed by MeTTa</span>
      </div>

      <div className="diff" role="figure" aria-label={`Change ${fact} ${key} from ${oldValue} to ${newValue}`}>
        <div className="diff-file"><FileCode size={14} aria-hidden="true" /> engine/policy.metta</div>
        <div className="diff-line del"><span className="diff-sign">−</span>({fact} {key} {fmt(oldValue)})</div>
        <div className="diff-line add"><span className="diff-sign">+</span>({fact} {key} {fmt(newValue)})</div>
      </div>

      <div className="label" style={{ margin: "18px 0 8px" }}>Evidence · COUNTER → APPROVE overrides for {key}</div>
      <ul className="mini-list">
        {evidence.map((e) => (
          <li key={e.override_id}>
            <span><span className="mono muted">#{e.override_id}</span>&nbsp;&nbsp;“{e.reason}”</span>
          </li>
        ))}
      </ul>

      <form className="approve-row" onSubmit={approve}>
        <label className="field" style={{ flex: 1 }}>
          <span className="label">Approver</span>
          <input className="input" required value={approver} onChange={(e) => setApprover(e.target.value)}
                 placeholder="Name of the person approving this change" />
        </label>
        <button className="btn btn-primary" disabled={busy || !approver.trim()}>
          {busy ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <ShieldCheck size={16} aria-hidden="true" />}
          Approve change
        </button>
      </form>
      <p className="hint" style={{ margin: "8px 0 0" }}>
        Only this one line changes. The old file is kept in engine/policy_history/ and the change is logged.
        The margin floor can never be changed this way.
      </p>
      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}
    </article>
  );
}

export default function Policy() {
  const [proposals, setProposals] = useState(null);
  const [history, setHistory] = useState(null);
  const [facts, setFacts] = useState(null);
  const [error, setError] = useState(null);
  const [failed, setFailed] = useState({});   // sections whose load failed: show the error, not a skeleton
  const [applied, setApplied] = useState(null);

  const load = useCallback(() => {
    setError(null);
    setFailed({});
    setProposals(null); // MeTTa recomputes proposals; show skeletons, never stale ones
    const fail = (section) => (err) => { setError(err.message); setFailed((f) => ({ ...f, [section]: true })); };
    api.proposals().then(setProposals).catch(fail("proposals"));
    api.policyHistory().then(setHistory).catch(fail("history"));
    api.policyCurrent().then(setFacts).catch(fail("facts"));
  }, []);

  useEffect(load, [load]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Policy</h1>
          <p className="page-sub">MeTTa learns from human overrides and proposes changes. A person approves every one.</p>
        </div>
      </header>

      {error && <div className="error" role="alert">{error}</div>}
      {applied && (
        <div className="success" role="status">
          <CircleCheck size={15} style={{ verticalAlign: -3, marginRight: 6 }} aria-hidden="true" />
          ({applied.fact} {applied.key}) changed {fmt(applied.old_value)} → {fmt(applied.new_value)}, approved by{" "}
          {applied.approved_by}. Old policy saved as <span className="mono">{applied.history_file}</span>.
        </div>
      )}

      <div className="policy-grid">
        <section className="card" aria-labelledby="proposals-title">
          <div className="card-head">
            <h2 className="card-title" id="proposals-title"><GitCompareArrows size={17} aria-hidden="true" />Proposed changes</h2>
            {proposals?.length > 0 && <span className="label">{proposals.length} pending</span>}
          </div>
          {proposals == null
            ? (loadState(proposals, failed.proposals) === "failed" ? <p className="muted" style={{ margin: 0 }}>{LOAD_FAILED}</p>
              : <Skeleton height={260} style={{ borderRadius: 12 }} />)
            : proposals.length === 0
              ? (
                <EmptyState icon={GitCompareArrows} title="No changes proposed">
                  MeTTa proposes a change when 3 or more overrides point the same way, for example
                  COUNTER → APPROVE for one tier.
                </EmptyState>
              )
              : proposals.map((p) => (
                <ProposalCard key={`${p.fact}-${p.key}`} proposal={p}
                              onApplied={(a) => { setApplied(a); load(); }} />
              ))}
        </section>

        <div className="stack">
          <section className="card" aria-labelledby="facts-title">
            <div className="card-head">
              <h2 className="card-title" id="facts-title"><ScrollText size={17} aria-hidden="true" />Current policy</h2>
              <span className="label mono" style={{ textTransform: "none" }}>policy.metta</span>
            </div>
            {facts == null
              ? (loadState(facts, failed.facts) === "failed" ? <p className="muted" style={{ margin: 0 }}>{LOAD_FAILED}</p>
                : Array.from({ length: 6 }, (_, i) => <Skeleton key={i} height={16} style={{ margin: "12px 0" }} />))
              : (
                <div className="code">
                  {facts.map((f, i) => {
                    // Render the line exactly as written in policy.metta, with light highlighting.
                    const [head, ...rest] = f.text.slice(1, -1).split(/\s+/);
                    return (
                      <div key={i} className="code-line">
                        (<span className="tok-fact">{head}</span>
                        {rest.map((t, j) => (
                          <span key={j} className={/^-?[0-9.]+$/.test(t) ? "tok-num" : "tok-sym"}> {t}</span>
                        ))})
                      </div>
                    );
                  })}
                </div>
              )}
          </section>

          <section className="card" aria-labelledby="changes-title">
            <div className="card-head">
              <h2 className="card-title" id="changes-title"><History size={17} aria-hidden="true" />Applied changes</h2>
            </div>
            {history == null
              ? (loadState(history, failed.history) === "failed" ? <p className="muted" style={{ margin: 0 }}>{LOAD_FAILED}</p>
                : <Skeleton height={60} style={{ borderRadius: 10 }} />)
              : history.length === 0
                ? <EmptyState icon={History} title="No changes yet">Approved proposals are listed here with who approved them.</EmptyState>
                : (
                  <ul className="mini-list">
                    {history.map((h) => (
                      <li key={h.history_file} style={{ alignItems: "flex-start" }}>
                        <span>
                          <span className="mono">({h.fact} {h.key}) {fmt(h.old_value)} → {fmt(h.new_value)}</span>
                          <br />
                          <span className="muted">
                            by {h.approved_by} · evidence #{h.evidence.join(", #")} · {formatISTDateTime(h.timestamp)}
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
          </section>
        </div>
      </div>
    </div>
  );
}
