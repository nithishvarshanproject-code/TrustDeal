import { useEffect, useState } from "react";
import { CircleCheck, CircleX, LoaderCircle, ShieldCheck, Store } from "lucide-react";
import { api } from "../api.js";
import { formatPct, formatPrice, formatQuoteValidity } from "../lib/format.js";
import { formatCode, isCode, normalizeRef, parseVerifyHash, verifyHash } from "../lib/verify.js";

const STATUS = { open: "Open", ordered: "Ordered", expired: "Expired" };

/** Public, customer-safe page: is this quote genuine? Opened from the QR code on the quote PDF, from
 *  the link on the quote card, or by typing the quote number and code. Only /verify/quote is called. */
export default function VerifyQuote() {
  const initial = parseVerifyHash(window.location.hash);
  const [ref, setRef] = useState(initial.ref);
  const [code, setCode] = useState(initial.code);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function check(r, c) {
    setBusy(true); setError(null); setResult(null);
    try {
      setResult(await api.verifyQuote(normalizeRef(r), formatCode(c)));
      window.history.replaceState(null, "", verifyHash(r, c));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (initial.ref && initial.code) check(initial.ref, initial.code);
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const ready = normalizeRef(ref) !== "" && isCode(code);

  return (
    <div className="shop verify-page">
      <header className="shop-head">
        <div className="shop-brand">
          <div className="brand-mark"><Store size={17} color="#fff" aria-hidden="true" /></div>
          <div>
            <div className="brand-name">BASIX Store</div>
            <div className="brand-sub">TrustDeal · Every discount, explained and proven.</div>
          </div>
        </div>
      </header>

      <section className="card verify-card" aria-labelledby="verify-title">
        <h1 className="card-title" id="verify-title"><ShieldCheck size={18} aria-hidden="true" /> Verify a quote</h1>
        <p className="muted">Enter the quote number and the code printed on the quote PDF, or scan its QR code.</p>
        <form className="verify-form" onSubmit={(e) => { e.preventDefault(); if (ready) check(ref, code); }}>
          <label>
            <span className="label">Quote number</span>
            <input className="input mono" value={ref} maxLength={20} placeholder="Q-00001"
                   onChange={(e) => setRef(e.target.value)} />
          </label>
          <label>
            <span className="label">Code</span>
            <input className="input mono" value={code} maxLength={16} placeholder="KXMB-PQRT-HNDZ"
                   autoCapitalize="characters" spellCheck={false}
                   onChange={(e) => setCode(formatCode(e.target.value))} />
          </label>
          <button className="btn btn-primary" disabled={!ready || busy}>
            {busy ? <LoaderCircle size={16} className="spin" /> : <ShieldCheck size={16} aria-hidden="true" />} Verify
          </button>
        </form>

        {error && <div className="error" role="alert">{error}</div>}
        <div aria-live="polite">{result && (result.genuine ? <Genuine quote={result} /> : <NotGenuine message={result.message} />)}</div>
      </section>
    </div>
  );
}

function Genuine({ quote }) {
  const rows = [
    ["Quote", quote.quote_ref],
    ["Prepared for", quote.customer],
    ["Product", `${quote.product_name}${quote.quantity > 1 ? ` × ${quote.quantity}` : ""}`],
    ["List price (each)", formatPrice(quote.list_price)],
    ["Discount", quote.discount > 0 ? formatPct(quote.discount) : "None"],
    ["Price each", formatPrice(quote.unit_price)],
    ["Total", formatPrice(quote.total)],
    ["You save", formatPrice(quote.savings)],
    ["Valid until", formatQuoteValidity(quote.valid_until)],
    ["Status", quote.order_ref ? `${STATUS[quote.status]} · ${quote.order_ref}` : STATUS[quote.status]],
  ];
  return (
    <div className="verify-result genuine">
      <div className="verify-head"><CircleCheck size={22} aria-hidden="true" /> Genuine quote ✓</div>
      <p className="muted">These are the details the store issued. They should match your PDF exactly.</p>
      <dl className="verify-details">
        {rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
    </div>
  );
}

function NotGenuine({ message }) {
  return (
    <div className="verify-result not-genuine">
      <div className="verify-head"><CircleX size={22} aria-hidden="true" /> Not genuine ✗</div>
      <p>{message}</p>
      <p className="muted">If the number and code are typed correctly, the quote may have been changed after the
        store issued it. Do not rely on it.</p>
    </div>
  );
}
