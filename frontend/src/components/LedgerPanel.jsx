import { useCallback, useEffect, useState } from "react";
import { Link2, LoaderCircle, RefreshCw, ShieldAlert, ShieldCheck } from "lucide-react";
import { api } from "../api.js";
import { formatQuoteValidity } from "../lib/format.js";
import { KIND_LABELS, entrySummary, shortHash, verifyMessage } from "../lib/ledger.js";
import { loadState } from "../lib/loadState.js";

const LIMIT = 25;

/** Seller-only: the latest audit ledger entries and a full re-check of the keyed hash chain. */
export default function LedgerPanel() {
  const [data, setData] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    setError(null);
    api.ledger(LIMIT).then(setData).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function verify() {
    setBusy(true); setError(null);
    try {
      setResult(await api.verifyLedger());
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card ledger" aria-labelledby="ledger-title">
      <div className="card-head">
        <h2 className="card-title" id="ledger-title"><Link2 size={17} aria-hidden="true" />Audit ledger</h2>
        <div className="ledger-actions">
          {data && <span className="label">{data.total} entries</span>}
          <button className="btn btn-ghost btn-sm" onClick={load} aria-label="Refresh ledger"><RefreshCw size={14} /></button>
          <button className="btn btn-primary btn-sm" onClick={verify} disabled={busy}>
            {busy ? <LoaderCircle size={14} className="spin" /> : <ShieldCheck size={14} aria-hidden="true" />} Verify ledger
          </button>
        </div>
      </div>
      <p className="muted ledger-sub">Append-only: every decision, agent action, override, policy change, task
        resolution, quote and order, each sealed with an HMAC of the entry before it.</p>
      {result && (
        <div className={`ledger-result ${result.intact ? "ok" : "bad"}`} role="status">
          {result.intact ? <ShieldCheck size={16} aria-hidden="true" /> : <ShieldAlert size={16} aria-hidden="true" />}
          <span>{verifyMessage(result)}</span>
          {result.intact && result.head && <span className="mono muted" title={result.head}>head {shortHash(result.head)}</span>}
        </div>
      )}
      {error && <div className="error" role="alert">{error}</div>}
      {loadState(data, error) === "failed" ? null           /* the error above, no spinner */
        : !data ? <p className="muted"><LoaderCircle size={14} className="spin" /> Loading…</p>
        : data.entries.length === 0 ? <p className="muted">No entries yet. Evaluate a deal to start the ledger.</p>
          : (
            <div className="ledger-scroll">
              <table className="ledger-table">
                <thead><tr><th>#</th><th>Time</th><th>Kind</th><th>Summary</th><th>Hash</th></tr></thead>
                <tbody>
                  {data.entries.map((e) => (
                    <tr key={e.seq}>
                      <td className="mono">{e.seq}</td>
                      <td className="nowrap">{formatQuoteValidity(e.ts)}</td>
                      <td><span className={`ledger-kind k-${e.kind}`}>{KIND_LABELS[e.kind] ?? e.kind}</span></td>
                      <td>{entrySummary(e)}</td>
                      <td className="mono muted" title={`hash ${e.hash}\nprev ${e.prev_hash}`}>{shortHash(e.hash)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
    </section>
  );
}
