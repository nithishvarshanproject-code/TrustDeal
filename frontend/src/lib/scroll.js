// Scrolling helpers (display only). Under prefers-reduced-motion the page jumps instead of animating.
export const scrollBehavior = (reduced) => (reduced ? "auto" : "smooth");

/** Should the Decision card be brought into view? Stacked (narrow screens, the card is below the form):
 *  always. Side by side: only when the card's top is off screen or barely visible. */
export function shouldScrollToDecision(cardRect, formRect, viewportHeight, minVisible = 120) {
  if (!cardRect) return false;
  const stacked = formRect != null && cardRect.top >= formRect.bottom - 1;
  if (stacked) return true;
  return cardRect.top < 0 || cardRect.top > viewportHeight - minVisible;
}

/** Scroll so the top of the Decision card (#decision-card) is visible. Returns true if it scrolled. */
export function scrollToDecision(doc, win, reduced) {
  const card = doc.getElementById("decision-card");
  if (!card) return false;
  const form = doc.getElementById("deal-form");
  const viewport = win.innerHeight || doc.documentElement?.clientHeight || 0;
  if (!shouldScrollToDecision(card.getBoundingClientRect(), form?.getBoundingClientRect() ?? null, viewport)) {
    return false;
  }
  card.scrollIntoView({ behavior: scrollBehavior(reduced), block: "start" });
  return true;
}

/** Bring a form field into view and put the cursor in it. Returns true if the field exists. */
export function focusField(doc, id, reduced) {
  const el = doc.getElementById(id);
  if (!el) return false;
  el.scrollIntoView({ behavior: scrollBehavior(reduced), block: "center" });
  el.focus({ preventScroll: true });
  return true;
}
