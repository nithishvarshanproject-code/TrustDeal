import { useEffect, useState } from "react";
import { useReducedMotion } from "../hooks/useReducedMotion.js";

/** Counts from 0 to `value` (ease-out). Renders the final value at once for reduced motion. */
export default function CountUp({ value, decimals = 1, duration = 700 }) {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(reduced ? value : 0);

  useEffect(() => {
    if (reduced) { setShown(value); return undefined; }
    let frame;
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      setShown(value * (1 - Math.pow(1 - t, 3)));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, duration, reduced]);

  return <>{shown.toFixed(decimals)}</>;
}
