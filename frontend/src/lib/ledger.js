// Audit ledger display helpers (seller side). No business logic: the backend appends and verifies.
import { formatPct, formatPrice } from "./format.js";

export const KIND_LABELS = {
  decision: "Decision", agent_action: "Agent action", override: "Override", policy_change: "Policy change",
  task_resolution: "Task resolved", quote: "Quote", order: "Order", invoice: "Invoice", demo_reset: "Demo reset",
};

const pct = (v) => (v == null ? "—" : formatPct(v));

/** One line per entry, from its payload summary. */
export function entrySummary(entry) {
  const p = entry?.payload;
  if (!p) return "Unreadable payload";
  switch (entry.kind) {
    case "decision": {
      const who = p.source === "deal" ? `Deal #${p.deal_id} · ${p.seller}` : `Request #${p.request_id} · ${p.customer}`;
      const given = p.approved_discount != null && p.result !== "REJECT" ? ` ${pct(p.approved_discount)}` : "";
      return `${who} · ${p.product}: ${p.result}${given} (asked ${pct(p.asked)})`;
    }
    case "agent_action":
      return `Request #${p.request_id}: ${p.rule_id} ${p.action} (${p.from} → ${p.to})`
        + (p.offer != null && p.action !== "wait" ? `, offer ${pct(p.offer)}` : "");
    case "override":
      return `Decision #${p.decision_id}: ${p.original_result} → ${p.new_result} by ${p.reviewer}`;
    case "policy_change":
      return `${p.fact} ${p.key}: ${p.old_value} → ${p.new_value}, approved by ${p.approved_by}`;
    case "task_resolution":
      return `Task #${p.task_id} (${p.kind}): ${p.answer} by ${p.reviewer}`;
    case "quote":
      return `${p.quote_ref} · ${p.product}${p.quantity > 1 ? ` × ${p.quantity}` : ""}: ${formatPrice(p.total)}`
        + (p.discount > 0 ? ` (${pct(p.discount)} off)` : "");
    case "order":
      return `${p.order_ref} for ${p.quote_ref}: ${formatPrice(p.total)}`;
    case "invoice":
      return `${p.invoice_no} for ${p.order_ref}: ${formatPrice(p.total)} incl. CGST ${formatPrice(p.cgst)}`
        + ` + SGST ${formatPrice(p.sgst)} (HSN ${p.hsn})`;
    case "demo_reset":
      return "Demo data reset (the ledger is kept)";
    default:
      return entry.kind;
  }
}

export const shortHash = (hash) => (hash ? `${hash.slice(0, 10)}…` : "—");

/** "Ledger intact ✓ (N entries)" or the first broken entry. */
export function verifyMessage(result) {
  if (!result) return "";
  if (result.intact) return `Ledger intact ✓ (${result.entries} ${result.entries === 1 ? "entry" : "entries"})`;
  return `Ledger broken at entry #${result.broken_at}: ${result.reason}`;
}
