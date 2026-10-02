# Audit trail: test deal 3 (COUNTER)

## The request

**Meridian Supply** asks for **20% off** 60 units of Industrial Widget
(list price 100.00, cost 70.00). The seller says they are a **Gold** customer.

## The decision

| Result | Discount offered | Confidence |
|---|---|---|
| **COUNTER** | **12.0%** instead of 20% | **90%** |

**In one sentence:** the seller is Silver on record, not Gold. For a Silver seller with a
clean payment history the most we can give is 12%, so we counter at 12%.

## Every rule the engine checked

| # | Rule | Result | What it found |
|---|------|--------|---------------|
| 1 | CONFLICT | ⚠ warning | The seller claims **Gold**, but the record says **Silver**. The record is used, and the conflict is logged. |
| 2 | R1 margin floor | ✗ fail | At 20% off the price is 80.00, a margin of **12.5%**, below the required **15%**. The largest discount that keeps a 15% margin is **17.6%**. |
| 3 | R2 tier cap | ✓ pass | The cap for Silver is **10%**. |
| 4 | R3 payment bonus | ✓ pass | **0 late payments in 25 orders** earns **+2%**. |
| 5 | R4 volume bonus | ✗ fail | **60 units** is below the 100-unit threshold, so there is no volume bonus. |
| 6 | R5 competitor match | – skipped | No competitor quote was given. |
| 7 | R6 authority | ✓ pass | 20% is within the agent's 25% authority, so no human is needed. |
| 8 | R7 repeat requests | ✓ pass | 0 earlier requests this month (the flag starts at 3). |
| 9 | TRUST | ✓ pass | Trust is `(stv 1.0 0.71)`: always on time, with enough evidence (25 orders), so confidence isn't lowered for thin history. |
| 10 | ALLOWED | ✓ pass | min(cap 10 + R3 2 + R4 0 = **12**, margin limit **17.6**) = **12.0%** |
| 11 | DECISION | ⚠ counter | **Step 4** of the decision order: the request of 20% is above the allowed 12%, so it's a **COUNTER at 12%**. |

## How the 12% was calculated

```
tier cap (Silver)                 10.0
+ payment bonus (R3)              +2.0
+ volume bonus  (R4)              +0.0
                                  ----
tier allowance                    12.0
margin limit (keeps 15% margin)   17.6
allowed max = min(12.0, 17.6)  =  12.0%
```

Decision order: 1. price below cost -> REJECT (no, 80 > 70) · 2. request above 25% ->
ESCALATE (no) · 3. request within the allowed max -> APPROVE (no, 20 > 12) ·
**4. otherwise -> COUNTER at the allowed max = 12%**.

## Why confidence is 90%, not 100%

Confidence starts at 100% and loses **10 points for the tier conflict** (claimed Gold, record
Silver). There are no other penalties: the payment history is solid, there's no unverified
quote and there aren't too many requests.

## Override hint for a human reviewer

> The seller claims **Gold**. If that tier is verified, the allowed max would be **17.6%**
> (Gold 20 + 2 bonus = 22, capped by the 17.6% margin limit).

A reviewer can record an override. It's stored next to this decision, and the original
decision is never changed.

## What would it take? (re-checked by the engine)

Each option was re-run through the same rules and gets approved. None breaks the margin floor.

| Option | Change | New allowed max |
|---|---|---|
| Verify the claimed tier | Confirm Meridian Supply really is Gold | **17.6%** |
| Raise the quantity | Order at least **100 units** (+5% volume bonus) | **17.0%** |
| Lower the discount | Ask for **12.0%** instead | **12.0%**, approvable as is |

---
*Generated from the TrustDeal MeTTa engine (`engine/rules.metta`, engine: local-hyperon) on
the seeded demo data. Every line above maps to a rule ID in the engine's trail.*
