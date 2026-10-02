import { useEffect, useState } from "react";

const ROWS = [["Strength", "strength"], ["Confidence", "confidence"]];
const ZERO = { strength: 0, confidence: 0 };

/** Animates each bar from `before` to `after`.
 *  With `before`: the faint "ghost" bar keeps showing the old value and the numbers show the change.
 *  Without `before`: bars grow from zero and only the current value is shown. */
export default function TrustBars({ before, after, title, compact = false }) {
  const start = before ?? ZERO;
  const [current, setCurrent] = useState(start);
  useEffect(() => {
    setCurrent(start);
    let inner;
    const outer = requestAnimationFrame(() => { inner = requestAnimationFrame(() => setCurrent(after)); });
    return () => { cancelAnimationFrame(outer); cancelAnimationFrame(inner); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [before?.strength, before?.confidence, after.strength, after.confidence]);

  return (
    <div className={`trust${compact ? " compact" : ""}`}>
      {title && <div className="label">{title}</div>}
      {ROWS.map(([label, key]) => (
        <div className="trust-row" key={key}>
          <span className="muted">{label}</span>
          <div className="trust-track" role="meter" aria-label={label}
               aria-valuemin={0} aria-valuemax={1} aria-valuenow={after[key]}>
            {before && <div className="trust-fill ghost" style={{ width: `${before[key] * 100}%` }} />}
            <div className="trust-fill" style={{ width: `${current[key] * 100}%` }} />
          </div>
          {before ? (
            <span className="trust-nums">
              {before[key].toFixed(3)} → <b>{after[key].toFixed(3)}</b>{" "}
              <span className={after[key] >= before[key] ? "st-pass" : "st-fail"}>
                ({after[key] >= before[key] ? "+" : "−"}{Math.abs(after[key] - before[key]).toFixed(3)})
              </span>
            </span>
          ) : (
            <span className="trust-nums"><b>{after[key].toFixed(2)}</b></span>
          )}
        </div>
      ))}
    </div>
  );
}
