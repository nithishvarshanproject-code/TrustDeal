import test from "node:test";
import assert from "node:assert/strict";
import { formatISTSummaryDateTime, formatISTTime, formatQuoteValidity } from "../src/lib/format.js";

test("quote validity matches the PDF date and India time format", () => {
  assert.equal(formatQuoteValidity("2026-10-03T04:16:00Z"), "03 Oct 2026, 09:46 IST");
});

test("activity time is always displayed in India time", () => {
  assert.equal(formatISTTime("2026-10-03T04:16:00Z"), "09:46 IST");
});

test("summary timestamps retain the app's fixed full date and time style", () => {
  assert.equal(formatISTSummaryDateTime("03 Oct 2026, 09:46 IST"), "03 Oct 2026, 09:46 IST");
  assert.equal(formatISTSummaryDateTime("2026-10-03 09:46 IST"), "03 Oct 2026, 09:46 IST");
});
