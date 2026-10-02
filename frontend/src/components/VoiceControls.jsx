import { useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, Mic, MicOff, Square, Volume2 } from "lucide-react";
import { PRIVACY_NOTE, UNSUPPORTED_NOTE } from "../lib/voice.js";
import { createVoiceController } from "../lib/voiceController.js";

/** React wrapper around the voice controller. `handlers` may change on every render:
 *  onTranscript(text, final), onSend(text), onAction(action), getActions(). */
export function useVoice(handlers) {
  const latest = useRef(handlers);
  latest.current = handlers;
  const [state, setState] = useState("idle");
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(null);
  const [autoSend, setAutoSendState] = useState(false);          // off by default (safety)
  const [speakReplies, setSpeakRepliesState] = useState(false);  // switches on when voice is first used
  const touched = useRef(false);

  const ctl = useMemo(() => createVoiceController(typeof window === "undefined" ? undefined : window, {
    onState: setState,
    onError: setError,
    onConfirmRequest: setPending,
    onTranscript: (text, final) => latest.current.onTranscript?.(text, final),
    onSend: (text) => latest.current.onSend?.(text),
    onAction: (action) => latest.current.onAction?.(action),
    getActions: () => latest.current.getActions?.() ?? [],
  }), []);
  useEffect(() => () => ctl.destroy(), [ctl]);

  return {
    supported: ctl.supported, canSpeak: ctl.canSpeak, state, error, pending, autoSend, speakReplies,
    toggle() {
      if (!touched.current && ctl.state !== "listening") { touched.current = true; setSpeakRepliesState(ctl.canSpeak); }
      ctl.toggle();
    },
    setAutoSend(on) { ctl.setAutoSend(on); setAutoSendState(on); },
    setSpeakReplies(on) { touched.current = true; setSpeakRepliesState(on); if (!on) ctl.stopSpeaking(); },
    speak: (text) => ctl.speak(text),
    stopSpeaking: () => ctl.stopSpeaking(),
    processing: (on) => ctl.processing(on),
    confirm: () => ctl.confirm(),
    cancel: () => ctl.cancel(),
  };
}

const STATE_LABELS = { listening: "Listening… press again to stop", processing: "Checking…", speaking: "Speaking…" };

/** The mic button that sits next to the chat input. */
export function MicButton({ voice, disabled }) {
  if (!voice.supported) return null;
  const listening = voice.state === "listening";
  return (
    <button type="button" className={`btn mic-btn${listening ? " listening" : ""}`} disabled={disabled && !listening}
            aria-pressed={listening} aria-label={listening ? "Stop listening" : "Speak your message"}
            title={listening ? "Stop listening" : "Speak your message"} onClick={voice.toggle}>
      {voice.state === "processing" ? <LoaderCircle size={16} className="spin" />
        : listening ? <MicOff size={16} /> : <Mic size={16} />}
    </button>
  );
}

/** Status, options, errors and the privacy note under the chat input. */
export function VoiceBar({ voice }) {
  if (!voice.supported) {
    return <p className="voice-note"><MicOff size={13} aria-hidden="true" /> {UNSUPPORTED_NOTE}</p>;
  }
  return (
    <div className="voice-bar">
      <div className="voice-row">
        <span className={`voice-state vs-${voice.state}`} role="status" aria-live="polite">
          {voice.state !== "idle" && <span className="voice-dot" aria-hidden="true" />}
          {STATE_LABELS[voice.state] ?? "Voice ready (English)"}
        </span>
        {voice.state === "speaking" && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={voice.stopSpeaking}>
            <Square size={13} aria-hidden="true" /> Stop speaking
          </button>
        )}
        <label className="voice-opt">
          <input type="checkbox" checked={voice.autoSend} onChange={(e) => voice.setAutoSend(e.target.checked)} />
          Auto-send after speaking
        </label>
        {voice.canSpeak && (
          <label className="voice-opt">
            <input type="checkbox" checked={voice.speakReplies}
                   onChange={(e) => voice.setSpeakReplies(e.target.checked)} />
            <Volume2 size={13} aria-hidden="true" /> Speak replies
          </label>
        )}
      </div>
      {voice.error && <div className="voice-error" role="alert">{voice.error}</div>}
      <p className="voice-note">{PRIVACY_NOTE}</p>
    </div>
  );
}
