// Verify quote helpers (no business logic: the backend decides whether a quote is genuine).
// Codes are 12 consonants, shown as KXMB-PQRT-HNDZ (backend/services/quote_seal.py).
export const CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ";
const CODE_RE = new RegExp(`^[${CODE_ALPHABET}]{12}$`);

/** "kxmb pqrt-hndz" -> "KXMBPQRTHNDZ" (spaces and dashes ignored, any case). */
export const compactCode = (text) => String(text ?? "").replace(/[\s-]/g, "").toUpperCase();

export const isCode = (text) => CODE_RE.test(compactCode(text));

/** "KXMBPQRTHNDZ" -> "KXMB-PQRT-HNDZ"; partial input is grouped as typed. */
export const formatCode = (text) => (compactCode(text).match(/.{1,4}/g) ?? []).join("-");

export const normalizeRef = (text) => String(text ?? "").trim().toUpperCase();

/** "#verify?ref=Q-00001&code=KXMBPQRTHNDZ" -> { ref: "Q-00001", code: "KXMB-PQRT-HNDZ" }. */
export function parseVerifyHash(hash) {
  const query = String(hash ?? "").split("?")[1] ?? "";
  const params = new URLSearchParams(query);
  return { ref: normalizeRef(params.get("ref") ?? ""), code: formatCode(params.get("code") ?? "") };
}

/** The Verify quote page link for a quote (same form as the QR code on the PDF). */
export const verifyHash = (ref, code) =>
  `#verify?ref=${encodeURIComponent(normalizeRef(ref))}&code=${encodeURIComponent(compactCode(code))}`;
