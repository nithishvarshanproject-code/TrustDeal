import test from "node:test";
import assert from "node:assert/strict";
import { formatISTDateTime } from "../src/lib/format.js";
import { LOAD_FAILED, loadState } from "../src/lib/loadState.js";

test("every timestamp uses the app's IST style, whatever the stored form", () => {
  const expected = "03 Oct 2026, 09:46 IST";
  assert.equal(formatISTDateTime("2026-10-03T04:16:00Z"), expected);              // decisions, overrides
  assert.equal(formatISTDateTime("2026-10-03T04:16:00"), expected);               // naive UTC from SQLite
  assert.equal(formatISTDateTime("2026-10-03T04:16:00.123456+00:00"), expected);  // policy history, market data
});

test("a panel shows a spinner OR the error, never both", () => {
  assert.equal(loadState(null, false), "loading");
  assert.equal(loadState(null, "Network error"), "failed");
  assert.equal(loadState(null, true), "failed");
  assert.equal(loadState([], "old error"), "ready");          // data arrived: show it
  assert.equal(loadState({ entries: [] }, null), "ready");
  assert.doesNotMatch(LOAD_FAILED, /undefined|null|Error:/);
});
