// Voice controller for the Customer chat: browser speech-to-text and text-to-speech, no React.
// It never sends or acts on its own except: auto-send (off by default) sends the recognised text
// like a typed message; a voice command only becomes a pending confirmation, and the action runs
// only when confirm() is called from an on-screen click.
import { VOICE_LANG, parseVoiceCommand, pickVoice, speechSupport, voiceErrorMessage } from "./voice.js";

/** States: idle | listening | processing | speaking.
 *  handlers: onState(state), onTranscript(text, final), onSend(text), onConfirmRequest(action|null),
 *  onAction(action), onError(message|null), getActions() -> the view's current actions. */
export function createVoiceController(win, handlers = {}) {
  const { recognition: Recognition, synthesis } = speechSupport(win);
  const call = (name, ...args) => handlers[name]?.(...args);
  let state = "idle";
  let rec = null;
  let autoSend = false;
  let pending = null;

  function setState(next) {
    if (next !== state) { state = next; call("onState", state); }
  }

  function stopSpeaking() {
    if (synthesis && (synthesis.speaking || synthesis.pending || state === "speaking")) synthesis.cancel();
    if (state === "speaking") setState("idle");
  }

  function finalText(text) {
    const action = parseVoiceCommand(text, call("getActions") ?? []);
    if (action) {
      pending = action;
      call("onTranscript", "", true);
      call("onConfirmRequest", action);
      return;
    }
    call("onTranscript", text, true);
    if (autoSend && text.trim()) call("onSend", text.trim());
  }

  function start() {
    if (!Recognition || state === "listening") return;
    stopSpeaking();                                 // the customer talks: stop reading out loud
    call("onError", null);
    rec = new Recognition();
    rec.lang = VOICE_LANG;
    rec.interimResults = true;
    rec.continuous = false;                         // the browser stops after a silence
    rec.maxAlternatives = 1;
    let heard = "";
    rec.onresult = (event) => {
      let text = "";
      let final = false;
      for (let i = 0; i < event.results.length; i += 1) {
        text += event.results[i][0].transcript;
        if (event.results[i].isFinal) final = true;
      }
      heard = text.trim();
      if (!final) call("onTranscript", heard, false);
    };
    rec.onerror = (event) => {
      const message = voiceErrorMessage(event.error);
      if (message) call("onError", message);
    };
    rec.onend = () => {
      rec = null;
      if (state === "listening") setState("idle");
      if (heard) finalText(heard);
      heard = "";
    };
    setState("listening");
    try {
      rec.start();
    } catch {
      rec = null;
      setState("idle");
      call("onError", voiceErrorMessage("start-failed"));
    }
  }

  function stop() {
    if (rec) rec.stop();                            // onend delivers the final transcript
  }

  return {
    supported: Boolean(Recognition),
    canSpeak: Boolean(synthesis),
    get state() { return state; },
    get autoSend() { return autoSend; },
    get pending() { return pending; },
    setAutoSend(on) { autoSend = Boolean(on); },
    start,
    stop,
    toggle() { if (state === "listening") stop(); else start(); },
    processing(on) {
      if (on) setState("processing");
      else if (state === "processing") setState("idle");
    },
    speak(text) {
      if (!synthesis || !text || state === "listening") return false;
      synthesis.cancel();
      const utterance = new win.SpeechSynthesisUtterance(text);
      utterance.lang = VOICE_LANG;
      const voice = pickVoice(synthesis.getVoices?.() ?? []);
      if (voice) { utterance.voice = voice; }
      utterance.onend = () => { if (state === "speaking") setState("idle"); };
      utterance.onerror = utterance.onend;
      setState("speaking");
      synthesis.speak(utterance);
      return true;
    },
    stopSpeaking,
    /** The on-screen "Yes": run the confirmed button action, once. */
    confirm() {
      const action = pending;
      pending = null;
      call("onConfirmRequest", null);
      if (action && (call("getActions") ?? []).includes(action)) call("onAction", action);
    },
    cancel() {
      pending = null;
      call("onConfirmRequest", null);
    },
    destroy() {
      if (rec) { rec.onend = null; rec.abort?.(); rec = null; }
      if (synthesis) synthesis.cancel();
      pending = null;
    },
  };
}
