import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight, BadgePercent, Check, CircleCheck, Clock, Download, FileText, LoaderCircle, MessageSquare, Mic, PackageCheck,
  Send,
  ShieldCheck, ShoppingBag, Sparkles, Store, UserRound, X,
} from "lucide-react";
import { api } from "../api.js";
import ModeSwitch from "../components/ModeSwitch.jsx";
import TelegramConnect from "../components/TelegramConnect.jsx";
import { MicButton, VoiceBar, useVoice } from "../components/VoiceControls.jsx";
import { CATEGORY_LABELS, CATEGORY_ORDER, formatPct, formatPrice, formatQuoteValidity, groupByCategory } from "../lib/format.js";
import { LOAD_FAILED, loadState } from "../lib/loadState.js";
import { verifyHash } from "../lib/verify.js";
import { productImage } from "../lib/productImages.js";
import {
  buildSpokenReply, confirmationText, draftFromTranscript, editDraft, emptyDraft, latestAgentMessageId, outgoing,
} from "../lib/voice.js";

const POLL_MS = 3000;
const SUGGESTIONS = ["Can I get 10% off?", "What does it cost?"];

/** The shopper's view: catalog, a chat with the store's agent, offers, quotes and orders.
 *  It only talks to /customer/* endpoints, which never return costs, margins or rules. */
export default function Customer({ onSwitchMode }) {
  const [customers, setCustomers] = useState([]);
  const [customerId, setCustomerId] = useState(null);
  const [me, setMe] = useState(null);
  const [catalog, setCatalog] = useState(null);
  const [catalogFailed, setCatalogFailed] = useState(false);
  const [requests, setRequests] = useState([]);
  const [focus, setFocus] = useState(null);           // product the customer is asking about
  const [view, setView] = useState(null);             // the active request (customer-safe view)
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.customers().then((list) => { setCustomers(list); setCustomerId(list[0]?.customer_id ?? null); })
      .catch((e) => setError(e.message));
    api.customerCatalog().then(setCatalog).catch((e) => { setError(e.message); setCatalogFailed(true); });
  }, []);

  const loadRequests = useCallback(() => {
    if (customerId == null) return;
    api.myRequests(customerId).then(setRequests).catch(() => {});
  }, [customerId]);

  useEffect(() => {
    if (customerId == null) return;
    setView(null); setFocus(null); setError(null);
    api.customerMe(customerId).then(setMe).catch(() => setMe(null));
    loadRequests();
  }, [customerId, loadRequests]);

  // While a manager reviews the request (or a reply awaits approval), check for updates.
  useEffect(() => {
    if (!view || !(view.pending_review || view.pending_reply)) return undefined;
    const id = setInterval(() => {
      api.customerRequest(view.request_id, customerId).then((v) => {
        setView(v);
        if (!v.pending_review && !v.pending_reply) loadRequests();
      }).catch(() => {});
    }, POLL_MS);
    return () => clearInterval(id);
  }, [view, customerId, loadRequests]);

  async function act(fn) {
    setBusy(true); setError(null);
    try {
      const v = await fn();
      setView(v);
      setFocus(v.product);
      loadRequests();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const open = view && !["CLOSED", "ORDERED", "DECLINED"].includes(view.status);
  const send = (text, voice = false) => act(() => api.customerMessage({
    customer_id: customerId, text, voice, product_id: focus?.product_id ?? null,
    request_id: open && view.product.product_id === focus?.product_id ? view.request_id : null,
  }));
  const button = (action, extra = {}) => act(() => api.customerAction(view.request_id, action,
                                                                      { customer_id: customerId, ...extra }));

  function startAsking(product) {
    setFocus(product);
    const existing = requests.find((r) => r.product_name === product.name
      && !["CLOSED", "ORDERED", "DECLINED"].includes(r.status));
    if (existing) {
      api.customerRequest(existing.request_id, customerId).then(setView).catch(() => setView(null));
    } else {
      setView(null);
    }
  }

  function openRequest(r) {
    api.customerRequest(r.request_id, customerId).then((v) => { setView(v); setFocus(v.product); })
      .catch((e) => setError(e.message));
  }

  return (
    <div className="shop">
      <header className="shop-head">
        <div className="shop-brand">
          <div className="brand-mark"><Store size={17} color="#fff" aria-hidden="true" /></div>
          <div>
            <div className="brand-name">BASIX Store</div>
            <div className="brand-sub">Ask for a discount · answered in seconds by TrustDeal</div>
          </div>
        </div>
        <div className="shop-head-right">
          <label className="shopping-as">
            <UserRound size={15} aria-hidden="true" />
            <span className="muted">Shopping as</span>
            <select className="select select-sm" value={customerId ?? ""} aria-label="Shopping as"
                    onChange={(e) => setCustomerId(Number(e.target.value))}>
              {customers.map((c) => <option key={c.customer_id} value={c.customer_id}>{c.name}</option>)}
            </select>
            {me && <span className={`loyalty loyalty-${me.loyalty_tier.toLowerCase()}`}>{me.loyalty_tier} member</span>}
          </label>
          <TelegramConnect kind="customer" customerId={customerId} />
          <ModeSwitch mode="customer" onSwitch={onSwitchMode} />
        </div>
      </header>

      {error && <div className="error" role="alert">{error}</div>}

      <div className="shop-grid">
        <div className="shop-left">
          <Catalog catalog={catalog} failed={catalogFailed} focus={focus} onAsk={startAsking} onRetry={() => {
            setCatalogFailed(false); api.customerCatalog().then(setCatalog).catch((e) => { setError(e.message); setCatalogFailed(true); });
          }} />
          <MyRequests requests={requests} activeId={view?.request_id} onOpen={openRequest} />
        </div>
        <Chat view={view} focus={focus} busy={busy} onSend={send} onButton={button} catalog={catalog ?? []}
              onChooseProduct={(p) => { setFocus(p); setView(null); }}
              pdfUrl={view?.quote ? api.quotePdfUrl(view.request_id, customerId) : null}
              invoiceUrl={view?.quote?.invoice_no ? api.invoicePdfUrl(view.request_id, customerId) : null}
              onNew={() => { setView(null); }} />
      </div>
    </div>
  );
}

function Catalog({ catalog, failed, focus, onAsk, onRetry }) {
  const state = loadState(catalog, failed);
  if (state === "failed") return <section className="card"><p className="muted" style={{ margin: 0 }}>{LOAD_FAILED} <button className="btn btn-sm" onClick={onRetry}>Retry</button></p></section>;
  if (state === "loading") return <section className="card"><LoaderCircle className="spin" size={18} /> Loading products…</section>;
  return (
    <section className="card" aria-labelledby="catalog-title">
      <div className="card-head">
        <h2 className="card-title" id="catalog-title"><ShoppingBag size={17} aria-hidden="true" />Products</h2>
        <span className="label">Pick one and ask for a discount</span>
      </div>
      {groupByCategory(catalog).sort((a, b) => CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category))
        .map(({ category, items }) => (
          <div key={category} className="shop-category">
            <div className="label">{CATEGORY_LABELS[category]}</div>
            <div className="product-grid">
              {items.map((p) => (
                <div key={p.product_id} className={`product-card${focus?.product_id === p.product_id ? " selected" : ""}`}>
                  <img className="product-image" src={productImage(p)} alt={`${p.name} illustration`} loading="lazy" />
                  <div className="product-name">{p.name}</div>
                  <div className="product-price">{formatPrice(p.list_price)}</div>
                  <button className="btn btn-sm" onClick={() => onAsk(p)}>
                    <BadgePercent size={14} aria-hidden="true" /> Ask for a discount
                  </button>
                </div>
              ))}
            </div>
          </div>
        ))}
    </section>
  );
}

function MyRequests({ requests, activeId, onOpen }) {
  return (
    <section className="card" aria-labelledby="myreq-title">
      <div className="card-head">
        <h2 className="card-title" id="myreq-title"><MessageSquare size={17} aria-hidden="true" />My requests</h2>
        <span className="label">{requests.length}</span>
      </div>
      {requests.length === 0
        ? <p className="muted" style={{ margin: 0 }}>No requests yet.</p>
        : (
          <ul className="myreq-list">
            {requests.map((r) => (
              <li key={r.request_id}>
                <button className={`myreq${r.request_id === activeId ? " active" : ""}`} onClick={() => onOpen(r)}>
                  <span className="myreq-name">{r.product_name}{r.quantity > 1 ? ` × ${r.quantity}` : ""}</span>
                  <span className={`status-pill st-${r.status.toLowerCase()}`}>{r.status_label}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
    </section>
  );
}

function Chat({ view, focus, busy, onSend, onButton, onNew, pdfUrl, invoiceUrl, catalog = [], onChooseProduct }) {
  const [draft, setDraft] = useState(emptyDraft);       // the input text, and whether it came from voice
  const [askPct, setAskPct] = useState("");
  const [asking, setAsking] = useState(false);
  const endRef = useRef(null);
  const voiceTurn = useRef(false);                      // the request in flight was spoken
  const heard = useRef({ request: null, id: null });    // newest agent message already seen / spoken

  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [view, busy]);
  useEffect(() => { setAsking(false); setAskPct(""); }, [view?.request_id]);

  const actions = view?.actions ?? [];
  const product = view?.product ?? focus;
  const canType = Boolean(product) && !busy && !view?.pending_review && !view?.pending_reply;

  function sendText(text, voice) {
    if (!text || !canType) return;
    voiceTurn.current = voice;
    onSend(text, voice);
    setDraft(emptyDraft);
  }

  // Voice adds no new path: the words fill the input (editable), a voice command only asks for an
  // on-screen confirmation, and a confirmed command presses the same button as a click.
  const voice = useVoice({
    onTranscript: (t) => setDraft(t ? draftFromTranscript(t) : emptyDraft),
    onSend: (t) => sendText(t, true),
    onAction: (action) => { voiceTurn.current = true; onButton(action); },
    getActions: () => actions,
  });

  useEffect(() => { voice.processing(busy && voiceTurn.current); }, [busy]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {                                     // a confirmation that is no longer possible goes away
    if (voice.pending && !actions.includes(voice.pending)) voice.cancel();
  }, [actions.join(","), voice.pending]);               // eslint-disable-line react-hooks/exhaustive-deps

  // Read new agent replies aloud (customer-safe view fields only), when "Speak replies" is on.
  useEffect(() => {
    if (!view) { heard.current = { request: null, id: null }; return; }
    const id = latestAgentMessageId(view);
    const prev = heard.current;
    heard.current = { request: view.request_id, id };
    const fresh = id != null && (prev.request === view.request_id ? prev.id == null || id > prev.id : voiceTurn.current);
    if (!busy) voiceTurn.current = false;
    if (fresh && voice.speakReplies && !view.pending_reply) voice.speak(buildSpokenReply(view));
  }, [view]);                                           // eslint-disable-line react-hooks/exhaustive-deps

  function submit(e) {
    e.preventDefault();
    const { text, voice: spoken } = outgoing(draft);
    sendText(text, spoken);
  }

  return (
    <section className="card chat" aria-labelledby="chat-title">
      <div className="card-head" style={{ marginBottom: 12 }}>
        <h2 className="card-title" id="chat-title">
          <Sparkles size={17} aria-hidden="true" />
          {product ? product.name : "Store assistant"}
        </h2>
        {view && <button className="btn btn-ghost btn-sm" onClick={onNew}>New question</button>}
      </div>

      <div className="chat-body" aria-live="polite">
        {!product && (
          <div className="chat-empty">
            <Store size={26} aria-hidden="true" />
            <p>Choose a product and ask for a discount, for example <i>“Can I get 20% off this phone?”</i></p>
          </div>
        )}
        {product && !view && (
          <div className="bubble agent">
            Hi! You're looking at <b>{product.name}</b> ({formatPrice(product.list_price)}). How can I help?
          </div>
        )}
        {view?.messages.map((m) => (
          <div key={m.message_id} className={`bubble ${m.sender}`}>{m.text}</div>
        ))}
        {(view?.product_choices ?? []).length > 0 && <><div className="bubble agent">I found a few matches. Choose one to check its deal.</div><div className="choice-grid">{view.product_choices.map((p) => (
          <article className="choice-card" key={p.product_id}>
            <img src={productImage(p)} alt={`${p.name} illustration`} loading="lazy" />
            <b>{p.name}</b><span>{formatPrice(p.list_price)}</span>
            <button className="btn btn-sm" disabled={busy} onClick={() => onChooseProduct(p)}>Check deal</button>
          </article>
        ))}</div></>}
        {view?.show_products && !(view?.product_choices ?? []).length && <><div className="bubble agent">Here are the products available in our catalog.</div><div className="choice-grid">{catalog.map((p) => (
          <article className="choice-card" key={p.product_id}>
            <img src={productImage(p)} alt={`${p.name} illustration`} loading="lazy" />
            <b>{p.name}</b><span>{formatPrice(p.list_price)}</span>
            <button className="btn btn-sm" disabled={busy} onClick={() => onChooseProduct(p)}>Check deal</button>
          </article>
        ))}</div></>}
        {busy && <div className="bubble agent typing"><LoaderCircle size={14} className="spin" /> Checking with our pricing rules…</div>}
        {view?.pending_reply && !busy && (
          <div className="bubble agent typing"><LoaderCircle size={14} className="spin" /> The store is preparing a reply…</div>
        )}

        {view?.pending_review && (
          <div className="notice notice-wait"><Clock size={16} aria-hidden="true" />
            A manager is reviewing your request. This chat updates automatically.</div>
        )}
        {view?.offer && <OfferCard offer={view.offer} />}
        {view?.offer?.alternatives?.length > 0 && actions.includes("switch") && (
          <Alternatives options={view.offer.alternatives} busy={busy}
                        onPick={(i) => onButton("switch", { index: i })} />
        )}
        {view?.quote && <QuoteCard quote={view.quote} product={view.product} canOrder={actions.includes("order")}
                                   busy={busy} onOrder={() => onButton("order")} pdfUrl={pdfUrl}
                                   invoiceUrl={invoiceUrl} />}
        {voice.pending && (
          <div className="voice-confirm" role="alertdialog" aria-label="Confirm voice command">
            <Mic size={15} aria-hidden="true" />
            <span>{confirmationText(voice.pending, view)}</span>
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={voice.confirm}>Yes</button>
            <button className="btn btn-ghost btn-sm" onClick={voice.cancel}>No</button>
          </div>
        )}
        {view && ["CLOSED", "DECLINED"].includes(view.status) && !view.quote && (
          <div className="notice"><X size={16} aria-hidden="true" /> This request is closed.</div>
        )}
        <div ref={endRef} />
      </div>

      {actions.includes("accept") && (
        <div className="chat-actions">
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => onButton("accept")}>
            <Check size={15} aria-hidden="true" /> Accept offer
          </button>
          {asking ? (
            <form className="ask-again" onSubmit={(e) => {
              e.preventDefault();
              if (askPct !== "") onButton("ask", { discount: Number(askPct) });
            }}>
              <div className="input-suffix">
                <input className="input input-sm" type="number" min="0" max="100" step="0.5" autoFocus
                       aria-label="Discount to ask for" value={askPct} onChange={(e) => setAskPct(e.target.value)} />
                <span>%</span>
              </div>
              <button className="btn btn-sm" disabled={busy || askPct === ""}>Ask</button>
            </form>
          ) : (
            <button className="btn btn-sm" disabled={busy} onClick={() => setAsking(true)}>Ask again</button>
          )}
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onButton("decline")}>No thanks</button>
        </div>
      )}

      {product && !view && (
        <div className="chips">
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="preset" disabled={busy} onClick={() => onSend(s)}>{s}</button>
          ))}
        </div>
      )}
      <form className="chat-input" onSubmit={submit}>
        <input className="input" maxLength={500} value={draft.text} disabled={!canType}
               placeholder={voice.state === "listening" ? "Listening…"
                 : product ? `Message about ${product.name}…` : "Choose a product first"}
               aria-label="Message" onChange={(e) => setDraft(editDraft(draft, e.target.value))} />
        <MicButton voice={voice} disabled={!canType} />
        <button className="btn btn-primary" disabled={!canType || !draft.text.trim()} aria-label="Send">
          {busy ? <LoaderCircle size={16} className="spin" /> : <Send size={16} />}
        </button>
      </form>
      {product && <VoiceBar voice={voice} />}
    </section>
  );
}

function OfferCard({ offer }) {
  return (
    <div className="offer-card">
      <div className="offer-top">
        <span className="offer-label">Our best offer</span>
        <span className="offer-discount">{formatPct(offer.discount)} off</span>
      </div>
      <div className="offer-price">
        {formatPrice(offer.total)}
        {offer.quantity > 1 && <small> for {offer.quantity} · {formatPrice(offer.unit_price)} each</small>}
      </div>
      <div className="offer-meta">
        <span className="strike">{formatPrice(offer.list_price * offer.quantity)}</span>
        <span className="save">You save {formatPrice(offer.savings)}</span>
      </div>
      <p className="offer-reason">{offer.reason}</p>
    </div>
  );
}

function Alternatives({ options, busy, onPick }) {
  return (
    <div className="alts">
      <div className="label">Other options within your budget</div>
      <div className="alt-grid">
        {options.map((o, i) => (
          <button key={`${o.product_id}-${o.quantity}`} className="alt-card" disabled={busy} onClick={() => onPick(i)}>
            <span className="alt-kind">{o.kind === "bigger-quantity" ? "Buy more, pay less each" : "Cheaper model"}</span>
            <span className="alt-name">{o.product_name}{o.quantity > 1 ? ` × ${o.quantity}` : ""}</span>
            <span className="alt-price">{formatPrice(o.total)}
              {o.discount > 0 && <small> · {formatPct(o.discount)} off</small>}</span>
            {o.price_difference != null
              ? <span className="save">{formatPrice(o.price_difference)} less than {o.compared_to}</span>
              : <span className="save">You save {formatPrice(o.savings)}</span>}
            <span className="alt-cta">Switch to this <ArrowRight size={14} aria-hidden="true" /></span>
          </button>
        ))}
      </div>
    </div>
  );
}

function QuoteCard({ quote, product, canOrder, busy, onOrder, pdfUrl, invoiceUrl }) {
  const ordered = quote.status === "ordered";
  return (
    <div className={`quote-card${ordered ? " ordered" : ""}`}>
      <div className="offer-top">
        <span className="offer-label">{ordered ? "Order confirmed" : "Your quote"}</span>
        <span className="mono">{ordered ? quote.order_ref : quote.quote_ref}</span>
      </div>
      <div className="offer-price">{formatPrice(quote.total)}
        <small> · {product.name}{quote.quantity > 1 ? ` × ${quote.quantity}` : ""}</small></div>
      <div className="offer-meta">
        {quote.discount > 0 && <span>{formatPct(quote.discount)} off</span>}
        {quote.savings > 0 && <span className="save">You save {formatPrice(quote.savings)}</span>}
        {!ordered && <span className="muted"><Clock size={13} aria-hidden="true" /> valid until{" "}
          {formatQuoteValidity(quote.valid_until)}</span>}
      </div>
      {ordered && <div className="order-ok"><PackageCheck size={18} aria-hidden="true" /> Thank you! Your order {quote.order_ref} is placed.</div>}
      <div className="quote-actions">
        {!ordered && canOrder && (
          <button className="btn btn-primary" disabled={busy} onClick={onOrder}>
            <CircleCheck size={16} aria-hidden="true" /> Place order
          </button>
        )}
        {ordered && invoiceUrl && (
          <a className="btn btn-primary" href={invoiceUrl} download={`TrustDeal-${quote.invoice_no}.pdf`}>
            <FileText size={16} aria-hidden="true" /> Download invoice
          </a>
        )}
        {pdfUrl && (
          <a className="btn" href={pdfUrl} download={`TrustDeal-${quote.quote_ref}.pdf`}>
            <Download size={16} aria-hidden="true" /> Download PDF
          </a>
        )}
        {quote.verify_code && (
          <a className="btn btn-ghost" href={verifyHash(quote.quote_ref, quote.verify_code)} target="_blank"
             rel="noopener noreferrer" title={`Code ${quote.verify_code}`}>
            <ShieldCheck size={16} aria-hidden="true" /> Verify this quote
          </a>
        )}
      </div>
    </div>
  );
}
