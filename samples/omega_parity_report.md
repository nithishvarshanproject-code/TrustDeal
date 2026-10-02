# Omega parity report

Generated 2026-10-01T03:30:02+00:00 by tests/test_omega_parity.py.

Omega side: petta (rules sha256 6736516869e3c443..., same files as local).
Queries: 37 x 3 runs = 111 comparisons, each compared as exact result text AND parsed value (incl. number types).

**Result: ALL IDENTICAL**

| Query | Round trip through Omega, avg of 3 (ms) |
|---|---|
| deal 1 evaluate-core | 10.5 |
| deal 1 what-if-for | 8.5 |
| deal 2 evaluate-core | 8.7 |
| deal 2 what-if-for | 9.0 |
| deal 3 evaluate-core | 9.8 |
| deal 3 what-if-for | 9.7 |
| deal 4 evaluate-core | 8.7 |
| deal 4 what-if-for | 8.9 |
| deal 5 evaluate-core | 9.4 |
| deal 5 what-if-for | 10.0 |
| deal 6 evaluate-core | 8.7 |
| deal 6 what-if-for | 8.4 |
| phone 15% (category max) evaluate-core | 9.0 |
| phone 15% (category max) what-if-for | 9.0 |
| earbuds 15% evaluate-core | 8.5 |
| earbuds 15% what-if-for | 8.3 |
| laptop 22% below cost evaluate-core | 8.9 |
| laptop 22% below cost what-if-for | 12.5 |
| refurb laptop 20% (category max 15) evaluate-core | 9.4 |
| refurb laptop 20% (category max 15) what-if-for | 9.3 |
| thin phone (no discount room) evaluate-core | 8.7 |
| thin phone (no discount room) what-if-for | 8.1 |
| category profile mobiles | 8.0 |
| category profile laptops | 7.5 |
| category profile accessories | 8.3 |
| category profile general | 7.6 |
| category profile None | 7.6 |
| trust Aurora | 7.3 |
| trust Blank Slate | 7.8 |
| trust Echo | 7.0 |
| trust Nova | 7.4 |
| update-trust on-time | 7.4 |
| update-trust late | 8.2 |
| update-trust no history | 8.1 |
| proposals: 3 Silver | 9.5 |
| proposals: 2 Silver | 9.1 |
| proposals: Gold margin-bound | 8.8 |

## Agent rules (engine/agent.metta)

next-action for 8 states x 6 kinds of decision x 11 events, plus quote-terms and alternatives: 536 queries x 3 runs = 1608 comparisons of the parsed value (see the note below on text).

**Result: ALL IDENTICAL**

Round trip through Omega: median 8.1 ms, max 37.6 ms.

Values are compared exactly (incl. int vs float). 2 queries print a float differently (PeTTa/SWI-Prolog writes 20000.0 as 2.0e+04; same value): alternatives buds 60 units -> volume + cheaper, quote-terms 0%.
