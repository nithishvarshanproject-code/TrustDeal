import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, ExternalLink, LoaderCircle, Send, X } from "lucide-react";
import { api } from "../api.js";

const POLL_MS = 3000;

/** "Connect Telegram": a one-time code (10 minutes) links a Telegram chat to this customer
 *  (kind="customer") or to the seller alerts (kind="seller"). Shows only status and the code. */
export default function TelegramConnect({ kind, customerId }) {
  const [status, setStatus] = useState(null);
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState(null);
  const [baseline, setBaseline] = useState(0);       // linked chats when the code was made
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [now, setNow] = useState(Date.now());
  const [copied, setCopied] = useState(false);
  const closeRef = useRef(null);

  const linkedCount = (s) => (kind === "customer" ? (s?.linked ? 1 : 0) : (s?.linked_chats ?? 0));
  const load = useCallback(() => (kind === "customer"
    ? api.customerTelegram(customerId) : api.sellerTelegram()), [kind, customerId]);

  useEffect(() => {
    if (kind === "customer" && customerId == null) return;
    setStatus(null); setCode(null); setOpen(false);
    load().then(setStatus).catch(() => setStatus(null));
  }, [kind, customerId, load]);

  // While the dialog is open: count down the code and notice when the chat gets linked.
  useEffect(() => {
    if (!open) return undefined;
    closeRef.current?.focus();
    const tick = setInterval(() => setNow(Date.now()), 1000);
    const poll = setInterval(() => load().then(setStatus).catch(() => {}), POLL_MS);
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => { clearInterval(tick); clearInterval(poll); window.removeEventListener("keydown", onKey); };
  }, [open, load]);

  async function newCode() {
    setBusy(true); setError(null); setCopied(false);
    try {
      setBaseline(linkedCount(status));
      setCode(await (kind === "customer" ? api.customerTelegramCode(customerId) : api.sellerTelegramCode()));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function openDialog() {
    setOpen(true);
    if (status?.enabled && !code && linkedCount(status) === 0) newCode();
  }

  if (!status) return null;
  const linked = linkedCount(status) > 0;
  const justLinked = code && linkedCount(status) > baseline;
  const left = code ? Math.max(0, Math.round((new Date(code.expires_at).getTime() - now) / 1000)) : 0;
  const label = kind === "customer"
    ? (linked ? "Telegram connected" : "Connect Telegram")
    : (linked ? `Telegram alerts · ${status.linked_chats}` : "Connect Telegram");

  return (
    <>
      <button type="button" className={`btn btn-sm tg-btn${linked ? " linked" : ""}`} onClick={openDialog}>
        {linked ? <Check size={14} aria-hidden="true" /> : <Send size={14} aria-hidden="true" />} {label}
      </button>
      {open && (
        <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
          <div className="card modal form" role="dialog" aria-modal="true" aria-labelledby="tg-title">
            <div className="card-head" style={{ marginBottom: 0 }}>
              <h3 className="card-title" id="tg-title"><Send size={17} aria-hidden="true" />Connect Telegram</h3>
              <button ref={closeRef} type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}
                      aria-label="Close"><X size={16} aria-hidden="true" /></button>
            </div>
            <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>
              {kind === "customer"
                ? "Chat with the store's TrustDeal agent on Telegram: ask for a discount, accept offers, get your quote PDF and place the order."
                : "Get a Telegram message when a request needs a manager (escalation or tier verification), with Approve / Reject buttons."}
            </p>
            {!status.enabled ? (
              <div className="notice">
                {kind === "customer"
                  ? "Telegram isn't available for this store right now."
                  : "Telegram is not set up: add TELEGRAM_BOT_TOKEN to omega/omega.env and restart (README, Telegram)."}
              </div>
            ) : justLinked ? (
              <div className="success" role="status">
                <Check size={15} aria-hidden="true" /> Connected! {kind === "customer"
                  ? "Say hello to the bot and ask for a discount." : "New tasks will be sent to that chat."}
              </div>
            ) : linked && !code ? (
              <div className="tg-steps">
                <div className="success" role="status"><Check size={15} aria-hidden="true" />
                  {kind === "customer" ? " Connected to a Telegram chat." : ` ${status.linked_chats} chat(s) get the alerts.`}</div>
                <button type="button" className="btn btn-sm" onClick={newCode} disabled={busy}>
                  {kind === "customer" ? "Use a different chat" : "Add another chat"}</button>
              </div>
            ) : busy && !code ? (
              <div><LoaderCircle size={16} className="spin" aria-hidden="true" /> Creating a code…</div>
            ) : code && left > 0 ? (
              <ol className="tg-steps">
                <li>
                  {code.link
                    ? <a className="btn btn-primary btn-sm" href={code.link} target="_blank" rel="noopener noreferrer">
                        <ExternalLink size={14} aria-hidden="true" /> Open @{code.bot_username} in Telegram</a>
                    : "Open the store's bot in Telegram."}
                  <span className="muted"> and press Start, or send it this message:</span>
                </li>
                <li className="tg-code-row">
                  <code className="tg-code">{code.command}</code>
                  <button type="button" className="btn btn-ghost btn-sm" aria-label="Copy"
                          onClick={() => navigator.clipboard?.writeText(code.command).then(() => setCopied(true)).catch(() => {})}>
                    {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
                  </button>
                </li>
                <li className="muted" style={{ fontSize: 12.5 }}>
                  One-time code · expires in {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}
                  {linked && kind === "customer" && " · connecting a new chat replaces the current one"}
                </li>
              </ol>
            ) : (
              <button type="button" className="btn btn-sm" onClick={newCode} disabled={busy}>
                {code ? "Code expired: get a new code" : "Get a code"}
              </button>
            )}
            {error && <div className="error">{error}</div>}
          </div>
        </div>
      )}
    </>
  );
}
