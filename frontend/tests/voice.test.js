import test from "node:test";
import assert from "node:assert/strict";
import {
  buildSpokenReply, cleanForSpeech, confirmationText, draftFromTranscript, editDraft, outgoing, parseVoiceCommand,
  pickVoice, speakPrice, speechSupport, voiceErrorMessage,
} from "../src/lib/voice.js";
import { createVoiceController } from "../src/lib/voiceController.js";

// ---------- mocks of the Web Speech API ----------

function mockWindow({ recognition = true, synthesis = true, voices = [{ name: "Rishi", lang: "en-IN" }] } = {}) {
  const win = { recognitions: [], spoken: [], cancelled: 0 };
  class FakeRecognition {
    constructor() { win.recognitions.push(this); this.started = false; }
    start() { this.started = true; }
    stop() { this.onend?.(); }
    abort() { this.onend?.(); }
    // test helpers
    say(text, isFinal) {
      this.onresult?.({ results: [Object.assign([{ transcript: text }], { isFinal })] });
    }
    fail(code) { this.onerror?.({ error: code }); this.onend?.(); }
  }
  if (recognition) win.webkitSpeechRecognition = FakeRecognition;
  if (synthesis) {
    win.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
    win.speechSynthesis = {
      speaking: false, pending: false,
      getVoices: () => voices,
      speak(u) { this.speaking = true; win.spoken.push(u); },
      cancel() { this.speaking = false; win.cancelled += 1; },
    };
  }
  return win;
}

function setup(opts = {}, actions = []) {
  const win = mockWindow(opts);
  const log = { states: [], transcripts: [], sent: [], actions: [], confirms: [], errors: [] };
  const ctl = createVoiceController(win, {
    onState: (s) => log.states.push(s),
    onTranscript: (t, final) => log.transcripts.push([t, final]),
    onSend: (t) => log.sent.push(t),
    onAction: (a) => log.actions.push(a),
    onConfirmRequest: (a) => log.confirms.push(a),
    onError: (e) => log.errors.push(e),
    getActions: () => actions,
  });
  return { win, ctl, log, rec: () => win.recognitions.at(-1) };
}

// A customer-safe view, plus internal fields that must never be spoken even if present.
const VIEW = {
  request_id: 7, status: "WAITING_CUSTOMER", actions: ["accept", "ask", "decline", "switch"],
  messages: [{ message_id: 3, sender: "agent", text: "Our best price is ₹50,160 for 2 units." }],
  offer: {
    discount: 12, total: 50160, unit_price: 25080, quantity: 2, list_price: 28500, savings: 6840,
    reason: "This is the best price we can offer on this item.",
    alternatives: [
      { product_name: "Smartphone Lite", quantity: 1, total: 12999, discount: 5, savings: 684 },
      { product_name: "Smartphone B", quantity: 1, total: 15999, discount: 0, savings: 0 },
    ],
  },
  quote: null,
  cost_price: 19876, margin: 0.21, rule_id: "R1", confidence: 0.93, trust: { strength: 0.9 },
  audit_trail: ["R1 margin floor 15%"],
};

// ---------- support and fallbacks ----------

test("unsupported browser: no recognition, so the mic is hidden", () => {
  const { ctl } = setup({ recognition: false, synthesis: false });
  assert.equal(ctl.supported, false);
  assert.equal(ctl.canSpeak, false);
  ctl.start();                                          // a no-op, never throws
  assert.equal(ctl.state, "idle");
  assert.equal(speechSupport({}).recognition, null);
});

test("webkit and standard constructors are both detected", () => {
  class R {}
  assert.equal(speechSupport({ SpeechRecognition: R }).recognition, R);
  assert.equal(speechSupport({ webkitSpeechRecognition: R }).recognition, R);
});

test("errors become friendly messages and voice stops", () => {
  const { ctl, log, rec } = setup();
  ctl.start();
  rec().fail("not-allowed");
  assert.match(log.errors.at(-1), /Microphone access is blocked/);
  assert.equal(ctl.state, "idle");
  assert.match(voiceErrorMessage("no-speech"), /didn't hear anything/);
  assert.match(voiceErrorMessage("network"), /speech service is unreachable/);
  assert.equal(voiceErrorMessage("aborted"), null);
});

// ---------- listening and the transcript ----------

test("recognition uses en-IN; the transcript fills the input live, auto-send is off", () => {
  const { ctl, log, rec } = setup();
  assert.equal(ctl.autoSend, false);
  ctl.start();
  assert.equal(rec().lang, "en-IN");
  assert.equal(rec().started, true);
  assert.equal(ctl.state, "listening");
  rec().say("can I get 10", false);
  rec().say("can I get 10% off", true);
  ctl.stop();
  assert.deepEqual(log.transcripts, [["can I get 10", false], ["can I get 10% off", true]]);
  assert.deepEqual(log.sent, []);                       // nothing is sent without a click
  assert.deepEqual(log.states, ["listening", "idle"]);
});

test("pressing the mic again stops listening (toggle)", () => {
  const { ctl, rec } = setup();
  ctl.toggle();
  assert.equal(ctl.state, "listening");
  rec().say("hello", true);
  ctl.toggle();
  assert.equal(ctl.state, "idle");
});

test("auto-send, when switched on, sends the recognised text once", () => {
  const { ctl, log, rec } = setup();
  ctl.setAutoSend(true);
  ctl.start();
  rec().say("Can I get 15% off?", true);
  rec().onend();
  assert.deepEqual(log.sent, ["Can I get 15% off?"]);
});

test("edit before send: the edited text is what is sent, still labelled voice", () => {
  let draft = draftFromTranscript("can I get 50 percent of");
  draft = editDraft(draft, "Can I get 15% off?");
  assert.deepEqual(outgoing(draft), { text: "Can I get 15% off?", voice: true });
  assert.deepEqual(outgoing(editDraft(draft, "")), { text: "", voice: false });
  assert.equal(draftFromTranscript("x".repeat(600)).text.length, 500);   // same limit as typing
});

// ---------- voice commands need an on-screen confirmation ----------

test("voice 'accept' only asks for confirmation; the action runs after the click", () => {
  const actions = ["accept", "ask", "decline"];
  const { ctl, log, rec } = setup({}, actions);
  ctl.start();
  rec().say("Yes, accept", true);
  ctl.stop();
  assert.deepEqual(log.actions, []);                    // nothing pressed yet
  assert.deepEqual(log.sent, []);                       // and not sent as chat text
  assert.equal(ctl.pending, "accept");
  assert.deepEqual(log.confirms, ["accept"]);
  ctl.confirm();
  assert.deepEqual(log.actions, ["accept"]);
  ctl.confirm();                                        // works once
  assert.deepEqual(log.actions, ["accept"]);
});

test("voice 'place order' is never placed without the confirmation click; No cancels", () => {
  const { ctl, log, rec } = setup({}, ["order"]);
  ctl.setAutoSend(true);                                // even with auto-send on
  ctl.start();
  rec().say("place order", true);
  ctl.stop();
  assert.equal(ctl.pending, "order");
  assert.deepEqual(log.actions, []);
  assert.deepEqual(log.sent, []);
  ctl.cancel();
  ctl.confirm();
  assert.deepEqual(log.actions, []);
});

test("commands map only to buttons available now, and only short clear phrases", () => {
  const all = ["accept", "decline", "order"];
  assert.equal(parseVoiceCommand("accept", all), "accept");
  assert.equal(parseVoiceCommand("Yes, accept.", all), "accept");
  assert.equal(parseVoiceCommand("I accept the offer", all), "accept");
  assert.equal(parseVoiceCommand("no thanks", all), "decline");
  assert.equal(parseVoiceCommand("Not interested", all), "decline");
  assert.equal(parseVoiceCommand("place the order", all), "order");
  assert.equal(parseVoiceCommand("accept", ["order"]), null);                 // button not offered
  assert.equal(parseVoiceCommand("I won't accept less than 20%", all), null); // normal chat text
  assert.equal(parseVoiceCommand("can I get 15% off", all), null);
});

test("the confirmation shows the customer-safe offer price", () => {
  assert.equal(confirmationText("accept", VIEW).replace(/\s/g, " "), "Accept offer of ₹50,160?");
  assert.match(confirmationText("order", { quote: { total: 17600 } }), /^Place order for ₹17,600\?$/);
});

// ---------- spoken replies ----------

test("spoken reply: prices as rupees, one alternative, no internal data", () => {
  const spoken = buildSpokenReply(VIEW);
  assert.match(spoken, /12 percent off: 50,160 rupees for 2\./);
  assert.match(spoken, /You save 6,840 rupees\./);
  assert.match(spoken, /Or Smartphone Lite for 12,999 rupees\./);
  assert.doesNotMatch(spoken, /Smartphone B/);                     // up to 1 alternative
  assert.match(spoken, /See the screen for more options\./);
  assert.doesNotMatch(spoken, /₹|%|\*|#/);
  for (const secret of ["19876", "19,876", "cost", "margin", "R1", "rule", "confidence", "trust", "0.93", "audit"]) {
    assert.ok(!spoken.toLowerCase().includes(secret.toLowerCase()), `spoke ${secret}`);
  }
});

test("spoken quote and order replies", () => {
  const quote = { total: 17600, discount: 12, savings: 2400, status: "open" };
  assert.equal(buildSpokenReply({ ...VIEW, quote, actions: ["order"] }),
    "Your quote is ready: 17,600 rupees, 12 percent off. You save 2,400 rupees. Tap Place order when you are ready.");
  assert.match(buildSpokenReply({ ...VIEW, quote: { ...quote, status: "ordered" } }), /order is placed\. Total 17,600 rupees/);
});

test("other replies: the agent's message, cleaned and short", () => {
  const view = { messages: [{ message_id: 1, sender: "agent",
    text: "**Sorry** 😊 we can't go below ₹2,099.30 on this one. Visit https://x.y now. Anything else? More text." }] };
  assert.equal(buildSpokenReply(view), "Sorry we can't go below 2,099 rupees and 30 paise on this one. Visit now.");
  assert.equal(speakPrice(50160), "50,160 rupees");
  assert.equal(cleanForSpeech("₹1,00,000 and 5%"), "1,00,000 rupees and 5 percent");
});

test("speaking uses an en-IN voice, falls back to English, and stops when the customer talks", () => {
  assert.equal(pickVoice([{ lang: "hi-IN" }, { lang: "en-GB", name: "UK" }]).name, "UK");
  assert.equal(pickVoice([{ lang: "hi-IN" }]), null);
  const { ctl, win } = setup({ voices: [{ name: "US", lang: "en-US" }, { name: "Rishi", lang: "en_IN" }] });
  assert.equal(ctl.speak("Hello"), true);
  assert.equal(ctl.state, "speaking");
  assert.equal(win.spoken[0].lang, "en-IN");
  assert.equal(win.spoken[0].voice.name, "Rishi");
  const cancels = win.cancelled;
  ctl.start();                                          // talking again stops the speech
  assert.ok(win.cancelled > cancels);
  assert.equal(ctl.state, "listening");
});

test("stop-speaking button and spoken-end return to idle", () => {
  const { ctl, win } = setup();
  ctl.speak("One");
  ctl.stopSpeaking();
  assert.equal(ctl.state, "idle");
  ctl.speak("Two");
  win.spoken.at(-1).onend();
  assert.equal(ctl.state, "idle");
  ctl.processing(true);
  assert.equal(ctl.state, "processing");
  ctl.processing(false);
  assert.equal(ctl.state, "idle");
});
