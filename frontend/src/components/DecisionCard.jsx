import { useState } from "react";
import { BadgeCheck, Clock, Cpu, Gavel, RotateCcw, Scale, ScrollText, Tag, ThumbsDown, ThumbsUp } from "lucide-react";
import { CATEGORY_LABELS, formatISTDateTime } from "../lib/format.js";
import { api } from "../api.js";
import AuditTrail from "./AuditTrail.jsx";
import CountUp from "./CountUp.jsx";
import { evaluateLabel } from "./DealForm.jsx";
import EmptyState from "./EmptyState.jsx";
import { DecisionSkeleton } from "./Skeleton.jsx";
import StatusBadge from "./StatusBadge.jsx";
import TrustBars from "./TrustBars.jsx";

function decidedAt(decision) {
  return new Date(decision.created_at.endsWith("Z") || decision.created_at.includes("+")
    ? decision.created_at : `${decision.created_at}Z`);
}

/** "Decided by MeTTa rules · engine: omega · 03 Oct 2026, 09:46 IST" as one line (on every decision). */
export function EngineFooter({ decision }) {
  const engine = decision.engine;
  const when = decidedAt(decision);
  return (
    <span className="engine" title={engine ? `Answered in ${engine.elapsed_s.toFixed(2)} s` : undefined}>
      <Cpu size={15} aria-hidden="true" />
      <span>
        Decided by MeTTa rules · engine: <b>{engine?.runner ?? "unknown"}</b> ·{" "}
        <time dateTime={when.toISOString()}>{formatISTDateTime(decision.created_at)}</time>
      </span>
    </span>
  );
}

/** The category rules MeTTa used for this product (from the category-profile query). */
export function CategoryChip({ profile }) {
  if (!profile) return null;
  const max = profile.category_max >= 100 ? "no max" : `max ${profile.category_max}%`;
  return (
    <span className="category-chip">
      <Tag size={13} aria-hidden="true" />
      Category: <b>{CATEGORY_LABELS[profile.category] ?? profile.category}</b>
      <span className="muted">· margin floor {profile.margin_floor}% · {max}</span>
    </span>
  );
}

function allowedMax(trail) {
  return trail.find((e) => e.rule_id === "ALLOWED")?.value ?? null;
}

export default function DecisionCard({ decision, seller, loading, onOverride, onOutcome, engine }) {
  const [replayKey, setReplayKey] = useState(0);
  const [outcome, setOutcome] = useState(null);
  const [outcomeError, setOutcomeError] = useState(null);
  const [recording, setRecording] = useState(false);
  const [shownFor, setShownFor] = useState(null);

  // Reset per-decision state when a new decision arrives.
  if (decision && shownFor !== decision.deal_id) {
    setShownFor(decision.deal_id);
    setOutcome(null);
    setOutcomeError(null);
  }

  let body;
  if (loading) {
    body = <DecisionSkeleton />;
  } else if (!decision) {
    body = (
      <EmptyState icon={Scale} title="No decision yet">
        Load a test deal or fill in the request, then press <b>{evaluateLabel(engine)}</b>.
        Every rule it checks shows up here, line by line.
      </EmptyState>
    );
  } else {
    const hint = decision.override_hint;
    const allowed = allowedMax(decision.trail);
    const sold = decision.result === "APPROVE" || decision.result === "COUNTER";

    async function record(paidOnTime) {
      setRecording(true);
      setOutcomeError(null);
      try {
        setOutcome(await api.outcome(decision.deal_id, paidOnTime));
        onOutcome?.();
      } catch (err) {
        setOutcomeError(err.message);
      } finally {
        setRecording(false);
      }
    }

    body = (
      <>
        <div className="decision-top">
          <StatusBadge result={decision.result} size="lg" animate key={`badge-${decision.deal_id}`} />
          <div className="decision-meta">
            Deal <strong>#{decision.deal_id}</strong>
            {seller && <> · <strong>{seller.name}</strong> · {seller.tier} on record</>}
          </div>
        </div>
        <div className="decision-engine" data-testid="decision-engine">
          <EngineFooter decision={decision} />
          <span className="decision-engine-note">
            <Clock size={13} aria-hidden="true" />
            {decision.engine ? `${decision.engine.elapsed_s.toFixed(2)} s` : "—"} · every line maps to a rule ID
          </span>
        </div>
        {decision.category && <div style={{ marginTop: 12 }}><CategoryChip profile={decision.category} /></div>}

        <div className="tiles" key={`tiles-${decision.deal_id}`}>
          <div className="tile">
            <div className="label">Approved discount</div>
            <div className="tile-value">
              {decision.approved_discount == null ? "—" : <><CountUp value={decision.approved_discount} /><small>%</small></>}
            </div>
            <div className="tile-note">{decision.result === "COUNTER" ? "counter offer" : " "}</div>
          </div>
          <div className="tile">
            <div className="label">Confidence</div>
            <div className="tile-value"><CountUp value={decision.confidence * 100} decimals={0} /><small>%</small></div>
            <div className="tile-note">
              {decision.confidence >= 1 ? "no penalties applied" : "lowered by conflict, thin evidence, quote or R7"}
            </div>
          </div>
          <div className="tile">
            <div className="label">Allowed max</div>
            <div className="tile-value">
              {allowed == null ? "—" : <><CountUp value={allowed} /><small>%</small></>}
            </div>
            <div className="tile-note">cap + bonuses, limited by margin</div>
          </div>
        </div>

        {hint && (
          <div className="callout">
            <BadgeCheck size={20} aria-hidden="true" />
            <p>
              Seller claims <b>{hint.claimed_tier}</b>. If that tier is verified, the allowed max would be{" "}
              <b>{hint.allowed_max.toFixed(1)}%</b>.
            </p>
            <button className="btn btn-sm" onClick={() => onOverride({
              new_result: "COUNTER",
              reason: `Verified ${hint.claimed_tier} tier: engine hint says allowed max ${hint.allowed_max.toFixed(1)}%.`,
            })}>
              <Gavel size={15} aria-hidden="true" /> Review override
            </button>
          </div>
        )}

        <div className="card-head" style={{ marginBottom: 8 }}>
          <h3 className="card-title"><ScrollText size={17} aria-hidden="true" />Reasoning replay</h3>
          <button className="btn btn-ghost btn-sm" onClick={() => setReplayKey((k) => k + 1)}>
            <RotateCcw size={14} aria-hidden="true" /> Replay
          </button>
        </div>
        <AuditTrail trail={decision.trail} replayKey={`${decision.deal_id}-${replayKey}`} />

        {sold && (
          <div style={{ marginTop: 14 }}>
            <div className="outcome-row">
              <span className="label" style={{ marginRight: 4 }}>Record outcome</span>
              <button className="btn btn-sm btn-good" disabled={recording || outcome} onClick={() => record(true)}>
                <ThumbsUp size={14} aria-hidden="true" /> Paid on time
              </button>
              <button className="btn btn-sm btn-bad" disabled={recording || outcome} onClick={() => record(false)}>
                <ThumbsDown size={14} aria-hidden="true" /> Paid late
              </button>
              {outcome && (
                <span className="muted" style={{ fontSize: 13 }}>
                  Recorded · {outcome.total_orders} orders, {outcome.late_payments} late
                </span>
              )}
            </div>
            {outcomeError && <div className="error" style={{ marginTop: 12 }}>{outcomeError}</div>}
            {outcome && (
              <TrustBars title={`Seller trust (MeTTa update-trust) · ${seller?.name ?? ""}`}
                         before={outcome.trust_before} after={outcome.trust_after} />
            )}
          </div>
        )}
      </>
    );
  }

  return (
    <section id="decision-card" className="card" aria-live="polite" aria-labelledby="decision-title">
      <h2 id="decision-title" className="label" style={{ margin: "0 0 16px" }}>Decision</h2>
      {body}
    </section>
  );
}
