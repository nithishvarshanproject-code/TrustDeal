import { useEffect } from "react";
import { Calculator, FileText, LoaderCircle } from "lucide-react";
import { useReducedMotion } from "../hooks/useReducedMotion.js";
import { fieldId, firstErrorField } from "../lib/dealValidation.js";
import { CATEGORY_LABELS, formatINR, groupByCategory } from "../lib/format.js";
import { focusField } from "../lib/scroll.js";

// The six test deals from data/test_deals.json (product 1, Industrial Widget), as one-click presets.
export const PRESETS = [
  { label: "1 · Approve", deal: { seller_id: 1, quantity: 150, discount_requested: 10, claimed_tier: "Gold" } },
  { label: "2 · Reject", deal: { seller_id: 2, quantity: 10, discount_requested: 35, claimed_tier: "New" } },
  { label: "3 · Counter", deal: { seller_id: 3, quantity: 60, discount_requested: 20, claimed_tier: "Gold" } },
  { label: "4 · No history", deal: { seller_id: 4, quantity: 30, discount_requested: 8, claimed_tier: "Silver" } },
  { label: "5 · Competitor", deal: { seller_id: 5, quantity: 40, discount_requested: 15, claimed_tier: "Silver",
                                     competitor_price: 84 } },
  { label: "6 · Repeat", deal: { seller_id: 6, quantity: 50, discount_requested: 10, claimed_tier: "Gold" } },
];

// Category demos (seeded catalog). Phone and earbuds: the same 15% request, different categories.
export const CATEGORY_PRESETS = [
  { label: "Phone 15%", hint: "COUNTER 12 · category max",
    deal: { seller_id: 1, product_id: 2, quantity: 20, discount_requested: 15, claimed_tier: "Gold" } },
  { label: "Earbuds 15%", hint: "APPROVE 15",
    deal: { seller_id: 1, product_id: 7, quantity: 20, discount_requested: 15, claimed_tier: "Gold" } },
  { label: "Laptop 22%", hint: "REJECT · below cost",
    deal: { seller_id: 3, product_id: 4, quantity: 5, discount_requested: 22, claimed_tier: "Silver" } },
];

export const EMPTY_FORM = {
  seller_id: "", product_id: "1", quantity: "", discount_requested: "",
  claimed_tier: "", competitor_price: "", competitor_verified: false,
};

export function presetToForm(deal) {
  return {
    ...EMPTY_FORM,
    seller_id: String(deal.seller_id),
    product_id: String(deal.product_id ?? 1),
    quantity: String(deal.quantity),
    discount_requested: String(deal.discount_requested),
    claimed_tier: deal.claimed_tier ?? "",
    competitor_price: deal.competitor_price != null ? String(deal.competitor_price) : "",
  };
}

/** Form state (strings) -> POST /deals/evaluate body. */
export function formToRequest(f) {
  return {
    seller_id: Number(f.seller_id),
    product_id: Number(f.product_id),
    quantity: Number(f.quantity),
    discount_requested: Number(f.discount_requested),
    claimed_tier: f.claimed_tier || null,
    competitor_price: f.competitor_price === "" ? null : Number(f.competitor_price),
    competitor_verified: Boolean(f.competitor_verified),
  };
}

/** Button text: in omega mode decisions run inside the Omega agent (still MeTTa rules). */
export function evaluateLabel(engine) {
  return engine === "omega" ? "Evaluate with Omega · MeTTa" : "Evaluate with MeTTa";
}

/** The friendly message under a field (never an internal field name). */
function FieldError({ field, errors }) {
  const message = errors?.[field];
  return message ? <span className="field-error" id={`${fieldId(field)}-error`}>{message}</span> : null;
}

export default function DealForm({ sellers, products, form, onChange, onSubmit, onPreset, busy, engine,
                                   errors = {}, errorFocus = 0, onClearError, listsError = null }) {
  const reduced = useReducedMotion();
  const set = (key) => (e) => {
    onChange({ ...form, [key]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
    if (errors[key]) onClearError?.(key);                 // editing a field clears its message
  };
  const field = (key) => ({
    id: fieldId(key),
    ...(errors[key] ? { "aria-invalid": true, "aria-describedby": `${fieldId(key)}-error` } : {}),
  });

  // After a failed Evaluate: scroll to the first field with a problem and put the cursor there.
  useEffect(() => {
    const first = errorFocus ? firstErrorField(errors) : null;
    if (first) focusField(document, fieldId(first), reduced);
  }, [errorFocus]);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <form id="deal-form" className="card form form-compact" noValidate aria-labelledby="form-title"
          onSubmit={(e) => { e.preventDefault(); onSubmit(form); }}>
      <div className="card-head" style={{ marginBottom: 0 }}>
        <h3 className="card-title" id="form-title"><FileText size={17} aria-hidden="true" />Discount request</h3>
      </div>

      <div className="field">
        <span className="label">Load a test deal</span>
        <div className="presets">
          {PRESETS.map((p) => (
            <button type="button" key={p.label} className="preset" onClick={() => onPreset(p.deal)}>{p.label}</button>
          ))}
        </div>
      </div>

      <div className="field">
        <span className="label">Category demos</span>
        <div className="presets">
          {CATEGORY_PRESETS.map((p) => (
            <button type="button" key={p.label} className="preset preset-accent" title={p.hint}
                    onClick={() => onPreset(p.deal)}>{p.label}</button>
          ))}
        </div>
      </div>

      <label className="field">
        <span className="label">Seller</span>
        <select className="select" required disabled={Boolean(listsError)} value={form.seller_id} onChange={set("seller_id")} {...field("seller_id")}>
          <option value="" disabled>Choose a seller…</option>
          {sellers.map((s) => (
            <option key={s.seller_id} value={s.seller_id}>{s.name} · {s.tier} on record</option>
          ))}
        </select>
        <FieldError field="seller_id" errors={errors} />
      </label>

      <label className="field">
        <span className="label">Product</span>
        <select className="select" required disabled={Boolean(listsError)} value={form.product_id} onChange={set("product_id")} {...field("product_id")}>
          {groupByCategory(products).map(({ category, items }) => (
            <optgroup key={category} label={CATEGORY_LABELS[category]}>
              {items.map((p) => (
                <option key={p.product_id} value={p.product_id}>
                  {p.name} · list {formatINR(p.list_price)} · cost {formatINR(p.cost_price)}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <FieldError field="product_id" errors={errors} />
      </label>

      <div className="field-row">
        <label className="field">
          <span className="label">Quantity</span>
          <input className="input" type="number" min="1" step="1" required
                 value={form.quantity} onChange={set("quantity")} {...field("quantity")} />
          <FieldError field="quantity" errors={errors} />
        </label>
        <label className="field">
          <span className="label">Discount</span>
          <div className="input-suffix">
            <input className="input" type="number" min="0" max="100" step="0.1" required
                   value={form.discount_requested} onChange={set("discount_requested")} {...field("discount_requested")} />
            <span>%</span>
          </div>
          <FieldError field="discount_requested" errors={errors} />
        </label>
      </div>

      <label className="field">
        <span className="label">Tier the seller claims</span>
        <select className="select" value={form.claimed_tier} onChange={set("claimed_tier")} {...field("claimed_tier")}>
          <option value="">Not stated</option>
          <option>Gold</option><option>Silver</option><option>New</option>
        </select>
        <span className="hint">A different claim is logged; the record tier is used.</span>
        <FieldError field="claimed_tier" errors={errors} />
      </label>

      <div className="field-row" style={{ alignItems: "end" }}>
        <label className="field">
          <span className="label">Competitor price</span>
          <input className="input" type="number" min="0" step="0.01" placeholder="Optional"
                 value={form.competitor_price} onChange={set("competitor_price")} {...field("competitor_price")} />
          <FieldError field="competitor_price" errors={errors} />
        </label>
        <label className="toggle toggle-field">
          <input type="checkbox" checked={form.competitor_verified} onChange={set("competitor_verified")}
                 disabled={form.competitor_price === ""} />
          <span className="toggle-track" aria-hidden="true" />
          Quote verified
        </label>
      </div>

      <button className="btn btn-primary btn-lg" disabled={busy}>
        {busy ? <LoaderCircle size={18} className="spin" aria-hidden="true" /> : <Calculator size={18} aria-hidden="true" />}
        {busy ? "Evaluating…" : evaluateLabel(engine)}
      </button>
    </form>
  );
}
