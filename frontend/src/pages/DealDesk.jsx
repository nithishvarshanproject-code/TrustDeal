import { useCallback, useEffect, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { api } from "../api.js";
import ConfirmModal from "../components/ConfirmModal.jsx";
import DealForm, { EMPTY_FORM, formToRequest, presetToForm } from "../components/DealForm.jsx";
import DecisionCard from "../components/DecisionCard.jsx";
import OverrideModal from "../components/OverrideModal.jsx";
import PipelineStrip, { PIPELINE } from "../components/PipelineStrip.jsx";
import WhatIfCards from "../components/WhatIfCards.jsx";
import { useReducedMotion } from "../hooks/useReducedMotion.js";
import { apiFieldErrors, validateDeal } from "../lib/dealValidation.js";
import { scrollToDecision } from "../lib/scroll.js";

const STEP_MS = 220;
const RULES_STEP = PIPELINE.findIndex((s) => s.name === "MeTTa rules");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

export default function DealDesk() {
  const reduced = useReducedMotion();
  const [sellers, setSellers] = useState([]);
  const [products, setProducts] = useState([]);
  const [listsError, setListsError] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [stage, setStage] = useState(-1);
  const [busy, setBusy] = useState(false);
  const [decision, setDecision] = useState(null);
  const [decisionSeller, setDecisionSeller] = useState(null);
  const [error, setError] = useState(null);
  const [fieldErrors, setFieldErrors] = useState({});   // friendly messages next to the form fields
  const [errorFocus, setErrorFocus] = useState(0);      // bumped on each failed Evaluate: focus the first one
  const [whatIf, setWhatIf] = useState(null);
  const [whatIfError, setWhatIfError] = useState(null);
  const [overrideDefaults, setOverrideDefaults] = useState(null);
  const [notice, setNotice] = useState(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState(null);
  const runId = useRef(0);

  const [engine, setEngine] = useState(null);   // "local" | "omega" (for the button label)

  const loadLists = useCallback(() => {
    setListsError(null);
    Promise.all([api.sellers(), api.products()])
      .then(([s, p]) => { setSellers(s); setProducts(p); })
      .catch((err) => setListsError(`Could not load sellers/products: ${err.message}`));
  }, []);
  useEffect(loadLists, [loadLists]);
  useEffect(() => { api.engine().then((e) => setEngine(e.engine_runner)).catch(() => setEngine(null)); }, []);

  async function resetDemo() {
    setResetBusy(true);
    setResetError(null);
    try {
      await api.resetDemo();
      runId.current += 1;          // ignore any evaluation still in flight
      setResetOpen(false);
      setDecision(null); setWhatIf(null); setWhatIfError(null); setError(null);
      setForm(EMPTY_FORM); setStage(-1); setBusy(false); setFieldErrors({});
      setNotice("Demo reset: fresh demo data, original policy restored, policy history archived.");
      loadLists();
    } catch (err) {
      setResetError(err.message);
    } finally {
      setResetBusy(false);
    }
  }

  const showFieldErrors = (errors) => { setFieldErrors(errors); setErrorFocus((n) => n + 1); };

  const evaluate = useCallback(async (values) => {
    const problems = validateDeal(values);
    if (Object.keys(problems).length) { setError(null); showFieldErrors(problems); return; }
    setFieldErrors({});
    const id = ++runId.current;
    setBusy(true); setError(null); setNotice(null);
    setDecision(null); setWhatIf(null); setWhatIfError(null);

    // Light the pipeline up to "MeTTa rules" while the request runs; hold there until it returns.
    let step = 0;
    setStage(0);
    const timer = reduced ? null : setInterval(() => {
      if (step < RULES_STEP) setStage(++step);
    }, STEP_MS);
    if (reduced) setStage(RULES_STEP);

    try {
      const [result] = await Promise.all([api.evaluate(formToRequest(values)), reduced ? null : wait(STEP_MS * RULES_STEP)]);
      clearInterval(timer);
      if (id !== runId.current) return;
      for (let s = RULES_STEP + 1; s <= PIPELINE.length && !reduced; s++) {
        setStage(s);
        await wait(STEP_MS);
      }
      setStage(PIPELINE.length);
      setDecisionSeller(sellers.find((s) => String(s.seller_id) === String(values.seller_id)) ?? null);
      setDecision(result);
      if (result.result !== "APPROVE") {
        api.whatIf(result.deal_id)
          .then((w) => { if (id === runId.current) setWhatIf(w); })
          .catch((err) => { if (id === runId.current) setWhatIfError(err.message); });
      }
    } catch (err) {
      clearInterval(timer);
      if (id === runId.current) {
        setStage(-1);
        if (err.status === 422) {                       // never raw API text or internal field names
          const { _form: general, ...fields } = apiFieldErrors(err);
          if (Object.keys(fields).length) showFieldErrors(fields);
          if (general) setError(general);
        } else {
          setError(err.message);
        }
      }
    } finally {
      if (id === runId.current) setBusy(false);
    }
  }, [reduced, sellers]);

  // A new decision: bring the top of the Decision card into view (side by side only when it is off screen;
  // stacked on narrow screens always). Reduced motion: jump instead of animating.
  useEffect(() => {
    if (!decision) return undefined;
    const frame = requestAnimationFrame(() => scrollToDecision(document, window, reduced));
    return () => cancelAnimationFrame(frame);
  }, [decision]);   // eslint-disable-line react-hooks/exhaustive-deps

  function pickWhatIf(option) {
    if (option.change === "verify-tier") {
      setOverrideDefaults({
        new_result: "COUNTER",
        reason: `Verified ${option.detail} tier: engine what-if says allowed max ${option.allowed_max.toFixed(1)}%.`,
      });
      return;
    }
    const next = { ...form };
    if (option.change === "verify-competitor") next.competitor_verified = true;
    if (option.change === "raise-quantity") next.quantity = String(option.detail);
    if (option.change === "lower-discount") next.discount_requested = String(option.detail);
    setForm(next);
    window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
    evaluate(next);
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Deal check</h1>
          <p className="page-sub">Every discount, explained and proven: requests decided by MeTTa rules, line by line.</p>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => { setResetError(null); setResetOpen(true); }}>
          <RotateCcw size={14} aria-hidden="true" /> Reset demo
        </button>
      </header>

      <PipelineStrip stage={stage} />

      {listsError && <div className="error" role="alert">{listsError} <button className="btn btn-sm" onClick={loadLists}>Retry</button></div>}
      {error && <div className="error" role="alert">{error}</div>}
      {notice && <div className="success" role="status">{notice}</div>}

      <div className="desk-grid">
        <DealForm
          sellers={sellers}
          products={products}
          listsError={listsError}
          form={form}
          onChange={setForm}
          onSubmit={evaluate}
          onPreset={(deal) => { setForm(presetToForm(deal)); setStage(-1); setFieldErrors({}); }}
          busy={busy}
          engine={engine}
          errors={fieldErrors}
          errorFocus={errorFocus}
          onClearError={(key) => setFieldErrors(({ [key]: _, ...rest }) => rest)}
        />
        <DecisionCard
          decision={decision}
          seller={decisionSeller}
          loading={busy}
          engine={engine}
          onOverride={setOverrideDefaults}
          onOutcome={() => api.sellers().then(setSellers).catch(() => {})}
        />
      </div>

      <WhatIfCards
        result={decision?.result}
        loading={!whatIf && !whatIfError}
        options={whatIf?.what_if}
        sentences={whatIf?.explanation}
        error={whatIfError}
        onPick={pickWhatIf}
      />

      {resetOpen && (
        <ConfirmModal
          title="Reset the demo?"
          confirmLabel="Reset demo"
          busy={resetBusy}
          error={resetError}
          onConfirm={resetDemo}
          onClose={() => setResetOpen(false)}
        >
          This re-seeds all sellers, products and deals, restores <span className="mono">policy.metta</span> to
          the original and archives the policy change history (nothing is deleted). Decisions and overrides made
          since the last reset will be gone from the app.
        </ConfirmModal>
      )}

      {overrideDefaults && decision && (
        <OverrideModal
          dealId={decision.deal_id}
          defaults={overrideDefaults}
          onClose={() => setOverrideDefaults(null)}
          onDone={(saved) => {
            setOverrideDefaults(null);
            setNotice(`Override #${saved.override_id} recorded: ${saved.original_result} → ${saved.new_result}. `
              + "The original MeTTa decision is unchanged.");
          }}
        />
      )}
    </div>
  );
}
