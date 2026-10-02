import test from "node:test";
import assert from "node:assert/strict";
import { formatQuoteValidity } from "../src/lib/format.js";
import { entrySummary, shortHash, verifyMessage } from "../src/lib/ledger.js";

test("verify result: intact with a count, or the first broken entry", () => {
  assert.equal(verifyMessage({ intact: true, entries: 12, head: "ab" }), "Ledger intact ✓ (12 entries)");
  assert.equal(verifyMessage({ intact: true, entries: 1, head: "ab" }), "Ledger intact ✓ (1 entry)");
  assert.equal(verifyMessage({ intact: false, entries: 6, broken_at: 7, reason: "contents do not match its hash" }),
    "Ledger broken at entry #7: contents do not match its hash");
  assert.equal(verifyMessage(null), "");
});

test("one-line summaries for each kind", () => {
  const s = (kind, payload) => entrySummary({ kind, payload }).replace(/ /g, " ");
  assert.equal(s("decision", { source: "deal", deal_id: 3, seller: "Meridian Supply", product: "Industrial Widget",
    result: "COUNTER", approved_discount: 12, asked: 20 }), "Deal #3 · Meridian Supply · Industrial Widget: COUNTER 12% (asked 20%)");
  assert.equal(s("decision", { source: "agent", request_id: 4, customer: "Priya Sharma", product: "Phone",
    result: "REJECT", approved_discount: 0, asked: 35 }), "Request #4 · Priya Sharma · Phone: REJECT (asked 35%)");
  assert.equal(s("agent_action", { request_id: 4, rule_id: "A3", action: "send-counter", from: "NEW",
    to: "WAITING_CUSTOMER", offer: 12 }), "Request #4: A3 send-counter (NEW → WAITING_CUSTOMER), offer 12%");
  assert.equal(s("quote", { quote_ref: "Q-00001", product: "Phone", quantity: 2, total: 35200, discount: 12 }),
    "Q-00001 · Phone × 2: ₹35,200 (12% off)");
  assert.equal(s("order", { order_ref: "ORD-00001", quote_ref: "Q-00001", total: 35200 }), "ORD-00001 for Q-00001: ₹35,200");
  assert.equal(s("policy_change", { fact: "tier-cap", key: "Silver", old_value: 10, new_value: 12, approved_by: "Head" }),
    "tier-cap Silver: 10 → 12, approved by Head");
  assert.equal(entrySummary({ kind: "quote", payload: null }), "Unreadable payload");
});

test("hashes are shortened; ledger timestamps (microseconds, +00:00) show in IST", () => {
  assert.equal(shortHash("0123456789abcdef"), "0123456789…");
  assert.equal(formatQuoteValidity("2026-10-02T04:16:00.123456+00:00"), "02 Oct 2026, 09:46 IST");
});
