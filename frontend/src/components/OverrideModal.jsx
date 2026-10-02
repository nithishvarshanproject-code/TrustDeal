import { useEffect, useRef, useState } from "react";
import { Gavel, LoaderCircle, X } from "lucide-react";
import { api } from "../api.js";

const RESULTS = ["APPROVE", "COUNTER", "REJECT", "ESCALATE"];

/** Records a human override. The original MeTTa decision is never changed. */
export default function OverrideModal({ dealId, defaults, onClose, onDone }) {
  const [reviewer, setReviewer] = useState("");
  const [newResult, setNewResult] = useState(defaults?.new_result ?? "APPROVE");
  const [reason, setReason] = useState(defaults?.reason ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const firstField = useRef(null);

  useEffect(() => {
    firstField.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const saved = await api.override(dealId, { reviewer, new_result: newResult, reason });
      onDone?.(saved);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <form className="card modal form" role="dialog" aria-modal="true" aria-labelledby="ov-title" onSubmit={submit}>
        <div className="card-head" style={{ marginBottom: 0 }}>
          <h3 className="card-title" id="ov-title"><Gavel size={17} aria-hidden="true" />Human override · deal #{dealId}</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Recorded next to the MeTTa decision. The original decision stays unchanged in the audit log.
        </p>
        <div className="field-row">
          <label className="field">
            <span className="label">Reviewer</span>
            <input ref={firstField} className="input" required value={reviewer}
                   onChange={(e) => setReviewer(e.target.value)} placeholder="Your name" />
          </label>
          <label className="field">
            <span className="label">New result</span>
            <select className="select" value={newResult} onChange={(e) => setNewResult(e.target.value)}>
              {RESULTS.map((r) => <option key={r}>{r}</option>)}
            </select>
          </label>
        </div>
        <label className="field">
          <span className="label">Reason</span>
          <textarea className="textarea" required value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        {error && <div className="error">{error}</div>}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={busy}>
            {busy ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <Gavel size={16} aria-hidden="true" />}
            Record override
          </button>
        </div>
      </form>
    </div>
  );
}
