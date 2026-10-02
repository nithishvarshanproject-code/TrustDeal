import test from "node:test";
import assert from "node:assert/strict";
import { compactCode, formatCode, isCode, parseVerifyHash, verifyHash } from "../src/lib/verify.js";

test("the QR / quote-card link is parsed into a quote number and a grouped code", () => {
  assert.deepEqual(parseVerifyHash("#verify?ref=Q-00001&code=KXMBPQRTHNDZ"), { ref: "Q-00001", code: "KXMB-PQRT-HNDZ" });
  assert.deepEqual(parseVerifyHash("#verify?ref=q-00001&code=kxmb-pqrt-hndz"), { ref: "Q-00001", code: "KXMB-PQRT-HNDZ" });
  assert.deepEqual(parseVerifyHash("#verify"), { ref: "", code: "" });
});

test("verify links round-trip and match the PDF's QR link form", () => {
  assert.equal(verifyHash("Q-00001", "KXMB-PQRT-HNDZ"), "#verify?ref=Q-00001&code=KXMBPQRTHNDZ");
  assert.deepEqual(parseVerifyHash(verifyHash(" q-00042 ", "kxmb pqrt hndz")), { ref: "Q-00042", code: "KXMB-PQRT-HNDZ" });
});

test("codes: any case, spaces or dashes; 12 consonants only", () => {
  assert.equal(compactCode(" kxmb-pqrt hndz "), "KXMBPQRTHNDZ");
  assert.equal(formatCode("kxmbpq"), "KXMB-PQ");
  assert.ok(isCode("kxmb pqrt-hndz"));
  assert.ok(!isCode("KXMB-PQRT-HND"));          // too short
  assert.ok(!isCode("AEIO-PQRT-HNDZ"));         // vowels are never in a code
  assert.ok(!isCode("K70M-PQRT-HNDZ"));         // nor digits
});
