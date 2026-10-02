import { useEffect, useState } from "react";
import { LoaderCircle, MessageCircleQuestion, Send } from "lucide-react";
import { api } from "../api.js";

const SUGGESTED = ["Why this result?", "How was the allowed max calculated?", "What would make this approve?"];
const MAX = 300;

/** Seller-only "Ask why" about ONE stored decision. The answer comes only from that decision's
 *  audit trail (phrased by ASI:One when it passes the guardrail, else the fixed template). */
export default function AskWhy({ source, decisionId, onAsked }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => { setAnswer(null); setError(null); setQuestion(""); }, [source, decisionId]);

  async function ask(text) {
    const q = text.trim();
    if (!q || busy) return;
    setBusy(true);
    setError(null);
    try {
      setAnswer(await api.askWhy(decisionId, { question: q, source }));
      onAsked?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ask-why">
      <div className="ask-why-head">
        <MessageCircleQuestion size={16} aria-hidden="true" />
        <b>Ask why</b>
        <span className="muted">· answered only from this decision's audit trail</span>
      </div>
      <div className="ask-why-chips">
        {SUGGESTED.map((s) => (
          <button key={s} type="button" className="btn btn-sm" disabled={busy}
            onClick={() => { setQuestion(s); ask(s); }}>{s}</button>
        ))}
      </div>
      <form className="ask-why-form" onSubmit={(e) => { e.preventDefault(); ask(question); }}>
        <input className="input" value={question} maxLength={MAX} placeholder="e.g. Why was this countered at 12%?"
          aria-label="Question about this decision" onChange={(e) => setQuestion(e.target.value)} />
        <span className="muted ask-why-count">{question.length}/{MAX}</span>
        <button className="btn btn-sm" type="submit" disabled={busy || !question.trim()}>
          {busy ? <LoaderCircle size={14} className="spin" aria-hidden="true" /> : <Send size={14} aria-hidden="true" />}
          Ask
        </button>
      </form>
      {error && <div className="error" role="alert">{error}</div>}
      {answer && (
        <div className="ask-why-answer" aria-live="polite">
          <div className="muted" style={{ fontSize: 12 }}>
            Q: {answer.question} · {answer.answer_source === "llm" ? "phrased by ASI:One" : "template"}
            {answer.fallback_reason ? ` (${answer.fallback_reason})` : ""}
          </div>
          <p className="ask-why-text">{answer.answer}</p>
          <div className="ask-why-grounded">Grounded in: <span className="mono">{answer.grounded_in.join(", ")}</span></div>
        </div>
      )}
    </div>
  );
}
