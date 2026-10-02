// Voice mode helpers (browser Web Speech API only). No business logic: spoken replies are built
// from the customer-safe view (/customer/* allowlists), and voice commands only *propose* one of
// the existing buttons, which the customer must confirm with a click.
import { formatPrice } from "./format.js";

export const VOICE_LANG = "en-IN";
export const PRIVACY_NOTE = "Voice is processed by your browser's speech service; TrustDeal stores only the text you send.";
export const UNSUPPORTED_NOTE = "Voice works in Chrome or Edge";

/** What this browser offers: a SpeechRecognition constructor and speechSynthesis (or null). */
export function speechSupport(win = globalThis) {
  const recognition = win?.SpeechRecognition || win?.webkitSpeechRecognition || null;
  const synthesis = win?.speechSynthesis && win?.SpeechSynthesisUtterance ? win.speechSynthesis : null;
  return { recognition, synthesis };
}

/** An en-IN voice, else any English voice, else null (the browser default). */
export function pickVoice(voices = []) {
  const norm = (v) => (v.lang || "").replace("_", "-").toLowerCase();
  return voices.find((v) => norm(v) === "en-in") || voices.find((v) => norm(v).startsWith("en")) || null;
}

const WHOLE = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** 50160 -> "50,160 rupees"; 2099.3 -> "2,099 rupees and 30 paise". */
export function speakPrice(value) {
  const paiseTotal = Math.round(Number(value) * 100);
  const rupees = Math.floor(paiseTotal / 100);
  const paise = paiseTotal % 100;
  return `${WHOLE.format(rupees)} rupees${paise ? ` and ${paise} paise` : ""}`;
}

const speakPct = (value) => `${Number(Number(value).toFixed(1))} percent`;

/** Plain text for speech: "₹50,160" -> "50,160 rupees", "%" -> "percent", no links,
 *  markdown, emojis or symbols. */
export function cleanForSpeech(text) {
  return String(text ?? "")
    .replace(/https?:\/\/\S+/g, " ")
    .replace(/(?:₹|\bRs\.?|\bINR)\s?(\d[\d,]*)(?:\.(\d{1,2}))?/g, (_, whole, dec) => {
      const paise = dec ? Number(dec.padEnd(2, "0")) : 0;
      return `${whole} rupees${paise ? ` and ${paise} paise` : ""}`;
    })
    .replace(/(\d)\s?%/g, "$1 percent")
    .replace(/[\p{Extended_Pictographic}\u{FE0F}\u{200D}]/gu, "")
    .replace(/[*_`#>~|[\]{}<>^\\]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** The first `max` sentences of a cleaned text. */
function firstSentences(text, max = 2) {
  const parts = text.match(/[^.!?]+[.!?]*/g) || [];
  return parts.slice(0, max).join("").trim();
}

/** Id of the newest agent message the customer can see (sent messages only), or null. */
export function latestAgentMessageId(view) {
  const ids = (view?.messages ?? []).filter((m) => m.sender === "agent").map((m) => m.message_id);
  return ids.length ? Math.max(...ids) : null;
}

/** A short spoken reply built only from named customer-safe fields of the view: the offer or
 *  quote (discount, total, savings), at most one alternative, else the agent's last message. */
export function buildSpokenReply(view) {
  if (!view) return "";
  const quote = view.quote;
  if (quote && quote.status === "ordered") {
    return `Thank you! Your order is placed. Total ${speakPrice(quote.total)}.`;
  }
  if (quote) {
    const parts = [`Your quote is ready: ${speakPrice(quote.total)}`
      + (quote.discount > 0 ? `, ${speakPct(quote.discount)} off` : "") + "."];
    if (quote.savings > 0) parts.push(`You save ${speakPrice(quote.savings)}.`);
    if ((view.actions ?? []).includes("order")) parts.push("Tap Place order when you are ready.");
    return parts.join(" ");
  }
  const offer = view.offer;
  if (offer && (view.actions ?? []).includes("accept")) {
    const qty = offer.quantity > 1 ? ` for ${offer.quantity}` : "";
    const parts = [`Our best offer is ${speakPct(offer.discount)} off: ${speakPrice(offer.total)}${qty}.`];
    if (offer.savings > 0) parts.push(`You save ${speakPrice(offer.savings)}.`);
    const alts = (view.actions ?? []).includes("switch") ? offer.alternatives ?? [] : [];
    if (alts.length) {
      const a = alts[0];
      parts.push(`Or ${cleanForSpeech(a.product_name)}${a.quantity > 1 ? `, ${a.quantity} units,` : ""}`
        + ` for ${speakPrice(a.total)}.`);
    }
    parts.push(alts.length > 1 ? "See the screen for more options." : "Say accept, or no thanks.");
    return parts.join(" ");
  }
  if (view.pending_review) return "A manager is reviewing your request. I will tell you when it is ready.";
  const last = [...(view.messages ?? [])].reverse().find((m) => m.sender === "agent");
  return last ? firstSentences(cleanForSpeech(last.text)) : "";
}

// ---------- voice commands -> existing buttons (always confirmed on screen) ----------

const NEGATION = /\b(not|don'?t|won'?t|never|can'?t|cannot|no way|unless|if|less|more|percent|%)\b/;
const COMMANDS = [
  ["order", /^(yes,? |ok(ay)?,? |please )*(place (the |my )?order|order (it|now)|buy (it|now)|confirm (the |my )?order)( please| now)?$/],
  ["decline", /^(no,? )?(no thanks?|no thank you|not interested)( for now)?$/],
  ["accept", /^(yes,? |ok(ay)?,? |sure,? )*(i )?(accept|i'?ll take it|deal)( (it|the|this|that))?( offer)?( please)?$/],
];

/** "yes, accept" -> "accept" when that button is available now; anything else -> null (the
 *  words go to the chat like typed text). Only short, unambiguous phrases count. */
export function parseVoiceCommand(text, actions = []) {
  const said = String(text ?? "").toLowerCase().replace(/[.!?]+$/g, "").replace(/\s+/g, " ").trim();
  if (!said || said.split(" ").length > 6) return null;
  const hit = COMMANDS.find(([, re]) => re.test(said));
  if (!hit || (hit[0] !== "decline" && NEGATION.test(said))) return null;
  return actions.includes(hit[0]) ? hit[0] : null;
}

/** The on-screen confirmation for a voice command, e.g. "Accept offer of ₹50,160?". */
export function confirmationText(action, view) {
  if (action === "accept") return view?.offer ? `Accept offer of ${formatPrice(view.offer.total)}?` : "Accept the offer?";
  if (action === "order") return view?.quote ? `Place order for ${formatPrice(view.quote.total)}?` : "Place the order?";
  if (action === "decline") return "Say no thanks to this offer?";
  return null;
}

const ERRORS = {
  "not-allowed": "Microphone access is blocked. Allow the microphone for this site, or type your message.",
  "service-not-allowed": "Microphone access is blocked. Allow the microphone for this site, or type your message.",
  "no-speech": "I didn't hear anything. Press the mic and try again, or type your message.",
  "audio-capture": "No microphone was found. You can type your message instead.",
  network: "The browser's speech service is unreachable. Check your connection, or type your message.",
};

/** A friendly message for a SpeechRecognition error code; null when there is nothing to say. */
export function voiceErrorMessage(code) {
  if (code === "aborted") return null;
  return ERRORS[code] ?? "Voice input stopped. You can try again or type your message.";
}

// ---------- the chat input draft (edit before send) ----------

export const emptyDraft = { text: "", voice: false };

export const MAX_MESSAGE = 500;                 // the same limit as typed messages (backend MessageIn)

/** The recognised words replace the input; the draft remembers it came from voice. */
export const draftFromTranscript = (text) => ({ text: String(text).slice(0, MAX_MESSAGE), voice: true });

/** Typing edits the draft; it stays a voice message unless the customer clears it. */
export const editDraft = (draft, text) => ({ text, voice: draft.voice && text.trim() !== "" });

/** What is sent: exactly the (edited) text in the input, with the voice label. */
export const outgoing = (draft) => ({ text: draft.text.trim(), voice: Boolean(draft.voice) });
