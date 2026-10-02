// Friendly, field-level messages for the Deal check form. Display only: the backend still validates
// every request. Messages never show internal field names (like "competitor_price").
export const MAX_QUANTITY = 100000;            // backend/validation.py MAX_QUANTITY
export const FIELD_ORDER = ["seller_id", "product_id", "quantity", "discount_requested", "claimed_tier",
  "competitor_price"];

const INVALID = {
  seller_id: "Choose a seller.",
  product_id: "Choose a product.",
  quantity: "Quantity must be a whole number from 1 to 1,00,000.",
  discount_requested: "Discount must be between 0% and 100%.",
  claimed_tier: "Claimed tier must be Gold, Silver or New (or Not stated).",
  competitor_price: "Competitor price must be more than ₹0 (or leave it empty).",
};
const MISSING = {
  quantity: "Enter a quantity (at least 1).",
  discount_requested: "Enter the discount requested (0–100%).",
};
export const GENERAL_ERROR = "Please check the request and try again.";

const text = (v) => String(v ?? "").trim();

/** Form state (strings) -> { field: message } for every field that cannot be sent. */
export function validateDeal(form) {
  const errors = {};
  if (!text(form.seller_id)) errors.seller_id = INVALID.seller_id;
  if (!text(form.product_id)) errors.product_id = INVALID.product_id;

  const qty = text(form.quantity);
  if (qty === "") errors.quantity = MISSING.quantity;
  else if (!/^\d+$/.test(qty) || Number(qty) < 1 || Number(qty) > MAX_QUANTITY) errors.quantity = INVALID.quantity;

  const discount = text(form.discount_requested);
  if (discount === "") errors.discount_requested = MISSING.discount_requested;
  else if (!Number.isFinite(Number(discount)) || Number(discount) < 0 || Number(discount) > 100) {
    errors.discount_requested = INVALID.discount_requested;
  }

  const competitor = text(form.competitor_price);
  if (competitor !== "" && !(Number.isFinite(Number(competitor)) && Number(competitor) > 0)) {
    errors.competitor_price = INVALID.competitor_price;
  }
  return errors;
}

/** A 422 from POST /deals/evaluate -> { field: message }, plus `_form` for anything not tied to a field. */
export function apiFieldErrors(err) {
  const errors = {};
  const detail = err?.detail;
  if (Array.isArray(detail)) {
    for (const d of detail) {
      const field = Array.isArray(d?.loc) ? d.loc[d.loc.length - 1] : null;
      if (INVALID[field]) errors[field] ??= INVALID[field];
      else errors._form = GENERAL_ERROR;
    }
  } else if (typeof detail === "string" && detail.startsWith("missing fields:")) {
    for (const field of detail.slice("missing fields:".length).split(",").map((f) => f.trim())) {
      if (INVALID[field]) errors[field] = MISSING[field] ?? INVALID[field];
    }
  }
  if (!Object.keys(errors).length) errors._form = GENERAL_ERROR;
  return errors;
}

/** The field to scroll to and focus: the first one with an error, in form order. */
export const firstErrorField = (errors) => FIELD_ORDER.find((f) => errors?.[f]) ?? null;

/** Element id of a form field (DealForm), used for labels, messages and focusing. */
export const fieldId = (field) => `deal-${field.replace(/_/g, "-")}`;
