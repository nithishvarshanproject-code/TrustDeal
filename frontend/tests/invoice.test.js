import test from "node:test";
import assert from "node:assert/strict";
import { KIND_LABELS, entrySummary } from "../src/lib/ledger.js";

test("the ledger panel shows invoice entries with their GST split", () => {
  assert.equal(KIND_LABELS.invoice, "Invoice");
  const line = entrySummary({ kind: "invoice", payload: { invoice_no: "INV-00001", order_ref: "ORD-00001", total: 35200,
    cgst: 2684.75, sgst: 2684.75, hsn: "8517" } }).replace(/ /g, " ");
  assert.equal(line, "INV-00001 for ORD-00001: ₹35,200 incl. CGST ₹2,684.75 + SGST ₹2,684.75 (HSN 8517)");
});
