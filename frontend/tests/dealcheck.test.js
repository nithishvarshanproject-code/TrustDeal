import test from "node:test";
import assert from "node:assert/strict";
import { GENERAL_ERROR, apiFieldErrors, fieldId, firstErrorField, validateDeal } from "../src/lib/dealValidation.js";
import { focusField, scrollBehavior, scrollToDecision, shouldScrollToDecision } from "../src/lib/scroll.js";

const OK = { seller_id: "1", product_id: "1", quantity: "150", discount_requested: "10", claimed_tier: "Gold",
  competitor_price: "", competitor_verified: false };
const INTERNAL = /seller_id|product_id|discount_requested|competitor_price|claimed_tier|Input should/;

test("a valid request has no field messages", () => {
  assert.deepEqual(validateDeal(OK), {});
  assert.deepEqual(validateDeal({ ...OK, competitor_price: "84" }), {});
});

test("friendly messages next to each field, never internal names", () => {
  const errors = validateDeal({ ...OK, seller_id: "", quantity: "0", discount_requested: "101", competitor_price: "0" });
  assert.equal(errors.seller_id, "Choose a seller.");
  assert.equal(errors.quantity, "Quantity must be a whole number from 1 to 1,00,000.");
  assert.equal(errors.discount_requested, "Discount must be between 0% and 100%.");
  assert.equal(errors.competitor_price, "Competitor price must be more than ₹0 (or leave it empty).");
  assert.equal(validateDeal({ ...OK, quantity: "", discount_requested: " " }).quantity, "Enter a quantity (at least 1).");
  assert.equal(validateDeal({ ...OK, quantity: "2.5" }).quantity, "Quantity must be a whole number from 1 to 1,00,000.");
  assert.equal(validateDeal({ ...OK, quantity: "100001" }).quantity, "Quantity must be a whole number from 1 to 1,00,000.");
  assert.equal(validateDeal({ ...OK, competitor_price: "-5" }).competitor_price,
    "Competitor price must be more than ₹0 (or leave it empty).");
  for (const message of Object.values(errors)) assert.doesNotMatch(message, INTERNAL);
});

test("the first field with a problem, in form order, gets the focus", () => {
  assert.equal(firstErrorField({ competitor_price: "x", quantity: "y" }), "quantity");
  assert.equal(firstErrorField({}), null);
  assert.equal(fieldId("competitor_price"), "deal-competitor-price");
});

test("API 422s become the same friendly field messages", () => {
  const err = { status: 422, detail: [
    { type: "greater_than", loc: ["body", "competitor_price"], msg: "Input should be greater than 0" },
    { type: "less_than_equal", loc: ["body", "quantity"], msg: "Input should be less than or equal to 100000" }] };
  const errors = apiFieldErrors(err);
  assert.deepEqual(errors, {
    competitor_price: "Competitor price must be more than ₹0 (or leave it empty).",
    quantity: "Quantity must be a whole number from 1 to 1,00,000.",
  });
  assert.deepEqual(apiFieldErrors({ detail: "missing fields: seller_id, quantity" }),
    { seller_id: "Choose a seller.", quantity: "Enter a quantity (at least 1)." });
  assert.deepEqual(apiFieldErrors({ detail: [{ loc: ["body"], msg: "JSON decode error" }] }), { _form: GENERAL_ERROR });
  assert.deepEqual(apiFieldErrors({ detail: "invalid symbol: 'x'" }), { _form: GENERAL_ERROR });
  for (const message of Object.values(errors)) assert.doesNotMatch(message, INTERNAL);
});

// ---------- scrolling (mocked DOM) ----------

function element(rect) {
  return { rect, scrolled: null, focused: null,
    getBoundingClientRect() { return this.rect; },
    scrollIntoView(opts) { this.scrolled = opts; },
    focus(opts) { this.focused = opts; } };
}
const page = (els, innerHeight = 800) => ({ doc: { getElementById: (id) => els[id] ?? null }, win: { innerHeight } });

test("side by side: scroll only when the card's top is off screen", () => {
  const form = { top: -400, bottom: 500 };
  assert.equal(shouldScrollToDecision({ top: 40 }, form, 800), false);     // already visible
  assert.equal(shouldScrollToDecision({ top: -300 }, form, 800), true);    // scrolled past its top
  assert.equal(shouldScrollToDecision({ top: 760 }, { top: 700, bottom: 1500 }, 800), true);  // barely visible
});

test("stacked on narrow screens: always scroll to the decision card", () => {
  assert.equal(shouldScrollToDecision({ top: 300 }, { top: -500, bottom: 290 }, 800), true);
});

test("smooth scroll to the card's top; reduced motion jumps instead", () => {
  const card = element({ top: -250 });
  const { doc, win } = page({ "decision-card": card, "deal-form": element({ top: -600, bottom: 400 }) });
  assert.equal(scrollToDecision(doc, win, false), true);
  assert.deepEqual(card.scrolled, { behavior: "smooth", block: "start" });
  assert.equal(scrollToDecision(doc, win, true), true);
  assert.deepEqual(card.scrolled, { behavior: "auto", block: "start" });
  assert.equal(scrollBehavior(true), "auto");
  const visible = element({ top: 30 });
  assert.equal(scrollToDecision(page({ "decision-card": visible, "deal-form": element({ top: 20, bottom: 900 }) }).doc,
    { innerHeight: 800 }, false), false);
  assert.equal(visible.scrolled, null);
  assert.equal(scrollToDecision(page({}).doc, { innerHeight: 800 }, false), false);   // no card: nothing to do
});

test("the first bad field is scrolled into view and focused", () => {
  const input = element({ top: 900 });
  const { doc } = page({ "deal-competitor-price": input });
  assert.equal(focusField(doc, "deal-competitor-price", false), true);
  assert.deepEqual(input.scrolled, { behavior: "smooth", block: "center" });
  assert.deepEqual(input.focused, { preventScroll: true });
  assert.equal(focusField(doc, "deal-quantity", true), false);
});
