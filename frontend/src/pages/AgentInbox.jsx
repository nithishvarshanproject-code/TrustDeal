import { useCallback, useEffect, useState } from "react";
import { Activity, Bot, Check, ClipboardCheck, Cpu, Inbox, LoaderCircle, Mic, RefreshCw, ScrollText, Send, X } from "lucide-react";
import { api } from "../api.js";
import AskWhy from "../components/AskWhy.jsx";
import AuditTrail from "../components/AuditTrail.jsx";
import EmptyState from "../components/EmptyState.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import TelegramConnect from "../components/TelegramConnect.jsx";
import { formatISTDateTime, formatISTSummaryDateTime, formatISTTime, formatPct, formatPrice } from "../lib/format.js";

const POLL_MS = 4000;
const STATE_LABELS = {
  NEW: "New", WAITING_CUSTOMER: "Waiting for customer", WAITING_VERIFICATION: "Waiting for verification",
  ESCALATED: "Escalated", QUOTED: "Quoted", DECLINED: "Declined", ORDERED: "Ordered", CLOSED: "Closed",
};
const REVIEWER = "Store manager";

/** Seller side of the agent: every conversation with its MeTTa audit trail and activity log,
 *  the human tasks the agent created, drafted replies, and the autonomy switch. */
export default function AgentInbox() {
  const [inbox, setInbox] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [settings, setSettings] = useState(null);
  const [summary, setSummary] = useState(null);
  const [followupReport, setFollowupReport] = useState(null);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(() => {
    api.agentInbox().then(setInbox).catch((e) => setError(e.message));
    api.agentTasks("open").then(setTasks).catch(() => {});
    api.dailySummary().then(setSummary).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    api.agentSettings().then(setSettings).catch(() => {});
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (selected == null) { setDetail(null); return undefined; }
    const load = () => api.agentDeal(selected).then(setDetail).catch((e) => setError(e.message));
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [selected]);

  async function run(key, fn) {
    setBusy(key); setError(null);
    try {
      const d = await fn();
      if (d?.request_id) { setDetail(d); setSelected(d.request_id); }
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  const resolve = (task, answer) => run(`task-${task.task_id}`,
    () => api.resolveTask(task.task_id, { answer, reviewer: REVIEWER }));
  const approve = (m) => run(`msg-${m.message_id}`, () => api.approveMessage(m.message_id, { reviewer: REVIEWER }));
  const setMode = (mode) => run("mode", async () => { setSettings(await api.setAgentSettings({ mode })); return null; });
  const runNow = () => run("followups", async () => { setFollowupReport(await api.runFollowups()); return null; });
  const sendSummary = () => run("summary", async () => { setSummary(await api.sendDailySummary()); return null; });

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Agent inbox</h1>
          <p className="page-sub">Customer requests the agent is handling. MeTTa decides every step; you resolve the tasks.</p>
        </div>
        <div className="page-head-actions">
          <TelegramConnect kind="seller" />
          {settings && (
            <div className="autonomy" role="group" aria-label="Autonomy">
              <Bot size={15} aria-hidden="true" />
              <span className="label">Autonomy</span>
              {settings.modes.map((m) => (
                <button key={m} className={`btn btn-sm${settings.mode === m ? " btn-primary" : ""}`}
                        disabled={busy === "mode"} onClick={() => setMode(m)}>
                  {m === "auto-send" ? "Auto-send" : "Draft for approval"}
                </button>
              ))}
            </div>
          )}
        </div>
      </header>

      {error && <div className="error" role="alert">{error}</div>}

      <section className="card followup-panel" aria-labelledby="daily-summary-title">
        <div className="card-head">
          <div>
            <h2 className="card-title" id="daily-summary-title">Daily summary</h2>
            <p className="muted">India time · scheduled for {summary?.send_time_ist ?? "21:00"} IST</p>
          </div>
          <div className="summary-actions">
            <button className="btn btn-sm" disabled={busy === "followups"} onClick={runNow}>
              {busy === "followups" ? "Checking…" : "Run follow-ups now"}
            </button>
            <button className="btn btn-sm btn-primary" disabled={busy === "summary"} onClick={sendSummary}>
              {busy === "summary" ? "Preparing…" : "Send summary now"}
            </button>
          </div>
        </div>
        {summary ? (
          <>
            <p className="muted">{summary.sent ? `Created ${formatISTSummaryDateTime(summary.sent_at_ist)} · queued for linked seller Telegram chats`
              : `Preview for ${summary.day_ist} (IST)`}</p>
            <pre className="summary-message">{summary.summary_text}</pre>
          </>
        ) : <LoaderCircle className="spin" size={18} />}
        {followupReport && (
          <div className="followup-result" role="status">
            <b>{followupReport.message}</b>
            {followupReport.actions.length > 0 && (
              <ul>{followupReport.actions.map((item, i) => (
                <li key={`${item.action}-${item.request_id}-${i}`}>
                  {item.action === "reminder"
                    ? item.channel === "telegram" ? "Queued Telegram quote reminder" : "Added quote reminder to web inbox"
                    : item.channel === "telegram" ? "Closed expired quote; queued Telegram notice"
                      : "Closed expired quote; added notice to web inbox"}
                  {` for ${item.quote_ref}, request #${item.request_id}.`}
                </li>
              ))}</ul>
            )}
          </div>
        )}
      </section>

      <Tasks tasks={tasks} busy={busy} onResolve={resolve} onOpen={setSelected} />

      <div className="inbox-grid">
        <section className="card" aria-labelledby="inbox-title" style={{ padding: 16 }}>
          <div className="card-head" style={{ marginBottom: 10 }}>
            <h2 className="card-title" id="inbox-title"><Inbox size={17} aria-hidden="true" />Requests</h2>
            <button className="btn btn-ghost btn-sm" onClick={refresh} aria-label="Refresh"><RefreshCw size={14} /></button>
          </div>
          {inbox && (
            <div className="state-counts">
              {Object.entries(inbox.counts).filter(([, n]) => n > 0).map(([s, n]) => (
                <span key={s} className={`status-pill st-${s.toLowerCase()}`}>{STATE_LABELS[s]} · {n}</span>
              ))}
            </div>
          )}
          {!inbox ? <LoaderCircle className="spin" size={18} />
            : inbox.deals.length === 0
              ? <EmptyState icon={Inbox} title="No requests yet">Open the Customer view and ask for a discount.</EmptyState>
              : (
                <ul className="myreq-list">
                  {inbox.deals.map((d) => (
                    <li key={d.request_id}>
                      <button className={`myreq${d.request_id === selected ? " active" : ""}`}
                              onClick={() => setSelected(d.request_id)}>
                        <span className="inbox-row">
                          <b>#{d.request_id} {d.customer.name}{d.channel === "telegram" && <ChannelTag />}</b>
                          <span className="muted">{d.product.name}{d.quantity > 1 ? ` × ${d.quantity}` : ""}
                            {d.discount_asked != null ? ` · asked ${formatPct(d.discount_asked)}` : ""}</span>
                        </span>
                        <span className={`status-pill st-${d.state.toLowerCase()}`}>
                          {STATE_LABELS[d.state]}{d.open_tasks ? " · task" : ""}{d.drafts ? " · draft" : ""}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
        </section>

        {detail ? <Conversation detail={detail} busy={busy} onApprove={approve} />
          : (
            <section className="card">
              <EmptyState icon={ScrollText} title="Choose a request">
                See the conversation, every MeTTa decision with its audit trail, and the agent's activity.
              </EmptyState>
            </section>
          )}
      </div>
    </div>
  );
}

function Tasks({ tasks, busy, onResolve, onOpen }) {
  if (!tasks.length) return null;
  return (
    <section className="card tasks" aria-labelledby="tasks-title">
      <div className="card-head" style={{ marginBottom: 10 }}>
        <h2 className="card-title" id="tasks-title"><ClipboardCheck size={17} aria-hidden="true" />Tasks for you</h2>
        <span className="label">{tasks.length} open</span>
      </div>
      <ul className="task-list">
        {tasks.map((t) => (
          <li key={t.task_id} className="task">
            <button className="task-title" onClick={() => onOpen(t.request_id)}>
              <span className={`status-pill ${t.kind === "escalation" ? "st-escalated" : "st-waiting_verification"}`}>
                {t.kind}</span>
              {t.title}
            </button>
            <div className="task-actions">
              {t.answers.map((a) => (
                <button key={a} className={`btn btn-sm ${a === "approve" || a === "verified" ? "btn-good" : "btn-bad"}`}
                        disabled={busy === `task-${t.task_id}`} onClick={() => onResolve(t, a)}>
                  {a === "approve" || a === "verified" ? <Check size={14} aria-hidden="true" /> : <X size={14} aria-hidden="true" />}
                  {{ approve: "Approve", reject: "Reject", verified: "Verified", "not-verified": "Not verified" }[a]}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Conversation({ detail, busy, onApprove }) {
  const [openDecision, setOpenDecision] = useState(null);
  const decisions = Object.fromEntries(detail.decisions.map((d) => [d.decision_id, d]));
  const last = detail.decisions[detail.decisions.length - 1];
  return (
    <div className="convo">
      <section className="card" aria-labelledby="convo-title">
        <div className="card-head" style={{ marginBottom: 12 }}>
          <h2 className="card-title" id="convo-title">
            #{detail.request_id} · {detail.customer.name} · {detail.product.name}
          </h2>
          <span className="convo-pills">
            {detail.channel === "telegram" && <ChannelTag />}
            <span className={`status-pill st-${detail.state.toLowerCase()}`}>{STATE_LABELS[detail.state]}</span>
          </span>
        </div>
        <div className="convo-meta muted">
          {detail.customer_record.tier} customer · {detail.customer_record.total_orders ?? "no"} orders
          {detail.customer_record.late_payments != null ? `, ${detail.customer_record.late_payments} late` : ""}
          {" · trust "}{detail.customer_record.trust.strength.toFixed(2)}/{detail.customer_record.trust.confidence.toFixed(2)}
          {detail.claimed_tier && ` · claims ${detail.claimed_tier}`}{detail.verified_tier && ` (verified)`}
          {` · round ${detail.round}`}
        </div>
        <ol className="seller-chat">
          {detail.messages.map((m) => (
            <li key={m.message_id} className={`bubble ${m.sender}${m.status === "draft" ? " draft" : ""}`}>
              <div>{m.text}</div>
              {m.sender === "customer" && m.channel === "telegram" && (
                <div className="bubble-meta"><ChannelTag /></div>
              )}
              {m.sender === "customer" && m.channel === "web-voice" && (
                <div className="bubble-meta"><VoiceTag /></div>
              )}
              {m.sender === "agent" && (
                <div className="bubble-meta">
                  {m.channel === "telegram" && <ChannelTag />}
                  <span>{m.source === "llm" ? "drafted by ASI:One · numbers checked" : "template"}</span>
                  {m.decision_id && (
                    <button className="link" onClick={() => setOpenDecision(openDecision === m.decision_id ? null : m.decision_id)}>
                      audit trail
                    </button>
                  )}
                  {m.status === "draft" && (
                    <button className="btn btn-sm btn-primary" disabled={busy === `msg-${m.message_id}`}
                            onClick={() => onApprove(m)}>Approve &amp; send</button>
                  )}
                </div>
              )}
              {openDecision === m.decision_id && decisions[m.decision_id] && (
                <DecisionAudit d={decisions[m.decision_id]} />
              )}
            </li>
          ))}
        </ol>
      </section>

      <div className="convo-side">
        {last && (
          <section className="card" aria-label="Latest MeTTa decision">
            <div className="card-head" style={{ marginBottom: 8 }}>
              <h3 className="card-title"><Cpu size={16} aria-hidden="true" />Latest MeTTa decision</h3>
              <StatusBadge result={last.result} size="sm" />
            </div>
            <DecisionAudit d={last} />
          </section>
        )}
        {detail.market_evidence.length > 0 && (
          <section className="card" aria-label="Market evidence">
            <h3 className="card-title">Market evidence (unverified)</h3>
            {detail.market_evidence.map((m) => <MarketEvidence key={m.activity_id} item={m} />)}
          </section>
        )}
        <section className="card" aria-labelledby="activity-title">
          <div className="card-head" style={{ marginBottom: 8 }}>
            <h3 className="card-title" id="activity-title"><Activity size={16} aria-hidden="true" />Activity</h3>
          </div>
          <ol className="timeline">
            {detail.activity.map((a) => (
              <li key={a.activity_id} className={`tl tl-${a.kind}`}>
                <span className="tl-time">{formatISTTime(a.created_at)}</span>
                <span className="tl-kind">{a.kind.replace(/_/g, " ")}</span>
                <span className="tl-text">{a.summary}</span>
              </li>
            ))}
          </ol>
        </section>
      </div>
    </div>
  );
}

function ChannelTag() {
  return <span className="channel-tag" title="channel = telegram"><Send size={11} aria-hidden="true" /> Telegram</span>;
}

function VoiceTag() {
  return <span className="channel-tag voice-tag" title="channel = web-voice (spoken in the browser, text stored)">
    <Mic size={11} aria-hidden="true" /> Voice</span>;
}

const ACTION_LABELS = {
  "create-quote": "quote", "send-counter": "counter", "send-decline": "decline",
  "create-escalation-task": "escalation task", "request-verification": "verification task",
  "place-order": "order", wait: "wait", close: "close",
};

function DecisionAudit({ d }) {
  return (
    <div className="decision-audit">
      <div className="muted" style={{ fontSize: 12.5, marginBottom: 6 }}>
        Round {d.round} · {d.event} · confidence {Math.round(d.confidence * 100)}% · engine {d.engine?.runner}
      </div>
      <div className="decision-chain" aria-label="MeTTa decision and agent action">
        <span className="chain-step">
          <span className="label">MeTTa decision</span>
          <b>{d.result}{d.approved_discount != null ? ` ${formatPct(d.approved_discount)}` : ""}</b>
        </span>
        {(d.agent_actions ?? []).map((a, i) => (
          <span key={i} className="chain-step">
            <span className="chain-arrow" aria-hidden="true">→</span>
            <span className="label">Agent [{a.rule_id}]</span>
            <b>{ACTION_LABELS[a.action] ?? a.action}{a.offer != null && a.action !== "wait" && a.action !== "close"
              ? ` ${formatPct(a.offer)}` : ""}</b>
            <span className="muted">→ {a.to}</span>
          </span>
        ))}
      </div>
      <AuditTrail trail={d.trail} replayKey={`agent-${d.decision_id}`} />
      <AskWhy source="agent" decisionId={d.decision_id} />
    </div>
  );
}

function MarketEvidence({ item }) {
  const prices = item.detail?.result?.prices ?? [];
  return (
    <ul className="market-list">
      {prices.map((p, i) => (
        <li key={i}>
          <b>{p.currency === "INR" ? formatPrice(p.amount) : `${p.currency} ${p.amount}`}</b>
          {" · "}
          <a href={p.source_url} target="_blank" rel="noopener noreferrer nofollow">{p.source_title || p.source_url}</a>
          <span className="muted"> · {formatISTDateTime(p.retrieved_at)}</span>
        </li>
      ))}
    </ul>
  );
}
