"""Parity through the REAL Omega side: every request must give exactly the same result
through Omega (PeTTa, inside the container) as through LocalMettaRunner (hyperon).

Needs a running Omega container (omega\\start-omega.bat [agent]) and a free port 8000
(stop the app first with stop.bat). Run:
    set OMEGA_PARITY=1 && .venv\\Scripts\\python -m pytest tests/test_omega_parity.py -s
Writes samples/omega_parity_report.md with the results and latencies.
OMEGA_PARITY_PORT / OMEGA_PARITY_TOKEN run it against an Omega side connected to another
port (e.g. a second harness container while the app keeps port 8000).
"""
import json
import os
import socket
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.getenv("OMEGA_PARITY") != "1",
                                reason="needs a running Omega container; set OMEGA_PARITY=1")

ROOT = Path(__file__).resolve().parent.parent
RUNS = 3


PORT = int(os.getenv("OMEGA_PARITY_PORT", "8000"))


def _read_token() -> str:
    if os.getenv("OMEGA_PARITY_TOKEN"):
        return os.environ["OMEGA_PARITY_TOKEN"]
    env_file = ROOT / "omega" / "omega.env"
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("DEALDESK_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("DEALDESK_TOKEN missing in omega/omega.env - run omega\\start-omega.bat first")


def _queries():
    from engine.bridge import to_metta
    from tests.test_engine import TEST_DEALS, build_deal

    queries = []
    for case in sorted(TEST_DEALS):
        expr = to_metta(build_deal(case))
        queries.append((f"deal {case} evaluate-core", f"(evaluate-core {expr})"))
        queries.append((f"deal {case} what-if-for", f"(what-if-for {expr})"))
    # category deals (same catalog numbers as data/seed.py)
    aurora = dict(build_deal(1), quantity=20, claimed_tier="Gold")
    meridian = dict(build_deal(3), claimed_tier="Silver")
    category_deals = {
        "phone 15% (category max)": dict(aurora, category="mobiles", cost_price=16000.0, list_price=20000.0,
                                         discount_requested=15.0),
        "earbuds 15%": dict(aurora, category="accessories", cost_price=1200.0, list_price=2999.0,
                            discount_requested=15.0),
        "laptop 22% below cost": dict(meridian, category="laptops", cost_price=44000.0, list_price=55000.0,
                                      quantity=5, discount_requested=22.0),
        "refurb laptop 20% (category max 15)": dict(aurora, category="laptops", cost_price=30000.0,
                                                   list_price=50000.0, discount_requested=20.0),
        "thin phone (no discount room)": dict(aurora, category="mobiles", cost_price=19000.0,
                                              list_price=20000.0, discount_requested=5.0),
    }
    for label, deal in category_deals.items():
        expr = to_metta(deal)
        queries.append((f"{label} evaluate-core", f"(evaluate-core {expr})"))
        queries.append((f"{label} what-if-for", f"(what-if-for {expr})"))
    for cat in ("mobiles", "laptops", "accessories", "general", "None"):
        queries.append((f"category profile {cat}", f"(category-profile-of {cat})"))

    silver = dict(build_deal(3), claimed_tier="Silver", discount_requested=14.0)
    gold = dict(build_deal(1), quantity=60, discount_requested=20.0)
    ov = lambda i, d, r: f'(override {i} COUNTER APPROVE "{r}" {to_metta(d)})'  # noqa: E731
    for label, expr in [
        ("trust Aurora", "(history-stv 0 40)"), ("trust Blank Slate", "(history-stv None None)"),
        ("trust Echo", "(history-stv 1 15)"), ("trust Nova", "(history-stv 0 2)"),
        ("update-trust on-time", "(update-trust (history-stv 1 15) on-time)"),
        ("update-trust late", "(update-trust (history-stv 1 15) late)"),
        ("update-trust no history", "(update-trust (stv 0.5 0.0) on-time)"),
        ("proposals: 3 Silver", "(propose-policy-changes (" + " ".join(ov(i, silver, f"r{i}") for i in (1, 2, 3)) + "))"),
        ("proposals: 2 Silver", "(propose-policy-changes (" + " ".join(ov(i, silver, f"r{i}") for i in (1, 2)) + "))"),
        ("proposals: Gold margin-bound", "(propose-policy-changes (" + " ".join(ov(i, gold, f"g{i}") for i in (4, 5, 6)) + "))"),
    ]:
        queries.append((label, expr))
    return queries


@pytest.fixture(scope="module")
def live_backend():
    import uvicorn

    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            pytest.fail(f"port {PORT} is in use - run stop.bat first (the test serves the backend itself)")

    os.environ["ENGINE_RUNNER"] = "omega"
    os.environ["OMEGA_TOKEN"] = _read_token()
    os.environ.setdefault("OMEGA_TIMEOUT_S", "10")
    from backend.app import create_app
    from engine import bridge
    from engine.omega_link import hub, rules_sha256

    bridge.reset_runner()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    expected = rules_sha256(bridge.ENGINE_DIR)
    deadline = time.time() + 90
    while time.time() < deadline:
        status = hub.status(expected)
        if status["connected"] and status["rules_match"]:
            break
        time.sleep(0.5)
    else:
        server.should_exit = True
        pytest.fail(f"Omega did not connect with matching rules within 90 s: {hub.status(expected)}")
    yield
    server.should_exit = True
    thread.join(10)
    bridge.reset_runner()


def test_omega_matches_local_exactly_three_runs(live_backend):
    from engine.bridge import LocalMettaRunner, OmegaRunner, _to_py
    from engine.omega_link import hub

    local = LocalMettaRunner()
    omega = OmegaRunner()
    queries = _queries()
    expected = {expr: str(local.run(expr)) for _, expr in queries}

    timings = {label: [] for label, _ in queries}
    plugin_ms = {label: [] for label, _ in queries}
    mismatches = []
    for run in range(1, RUNS + 1):
        for label, expr in queries:
            text, ms = hub.request(expr, omega.expected_sha256, omega.timeout)
            atom = omega._parser.parse_single(text)
            timings[label].append(ms)
            same_text = text == expected[expr]
            same_value = json.dumps(_to_py(atom)) == json.dumps(_to_py(local.run(expr)))
            if not (same_text and same_value):
                mismatches.append((run, label, expected[expr][:200], text[:200]))

    lines = [
        "# Omega parity report",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by tests/test_omega_parity.py.",
        "",
        f"Omega side: {hub.status(omega.expected_sha256).get('runtime')} "
        f"(rules sha256 {omega.expected_sha256[:16]}..., same files as local).",
        f"Queries: {len(queries)} x {RUNS} runs = {len(queries) * RUNS} comparisons, "
        f"each compared as exact result text AND parsed value (incl. number types).",
        "",
        f"**Result: {'ALL IDENTICAL' if not mismatches else f'{len(mismatches)} MISMATCHES'}**",
        "",
        "| Query | Round trip through Omega, avg of 3 (ms) |",
        "|---|---|",
    ]
    for label, _ in queries:
        lines.append(f"| {label} | {statistics.mean(timings[label]):.1f} |")
    (ROOT / "samples" / "omega_parity_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))

    assert not mismatches, mismatches


# ---------- the agent's rules (engine/agent.metta) ----------

STATES = ("NEW", "WAITING_CUSTOMER", "WAITING_VERIFICATION", "ESCALATED", "QUOTED", "DECLINED", "ORDERED",
          "CLOSED")
EVENTS = (("new-request", 1), ("customer-ask", 2), ("customer-ask", 4), ("customer-accept", 2),
          ("customer-decline", 2), ("verified", 1), ("not-verified", 1), ("manager-approved", 1),
          ("manager-rejected", 1), ("order-placed", 2), ("tick", 2))


def _customer_deal(tier, claimed, cost, lst, qty, disc, category, late=0, orders=40):
    from engine.bridge import to_metta
    return to_metta({"record_tier": tier, "claimed_tier": claimed, "cost_price": cost, "list_price": lst,
                     "quantity": qty, "discount_requested": disc, "late_payments": late, "total_orders": orders,
                     "competitor_price": None, "competitor_verified": False, "requests_this_month": 0,
                     "seller_name": "Parity Customer", "category": category})


def _agent_queries():
    phone20 = _customer_deal("Gold", None, 16000.0, 20000.0, 1, 20.0, "mobiles")
    claim15 = _customer_deal("Silver", "Gold", 70.0, 100.0, 60, 15.0, "general", 0, 25)
    verified15 = _customer_deal("Gold", "Gold", 70.0, 100.0, 60, 15.0, "general", 0, 25)
    buds30 = _customer_deal("Gold", None, 1200.0, 2999.0, 1, 30.0, "accessories")
    buds45 = _customer_deal("Gold", None, 1200.0, 2999.0, 1, 45.0, "accessories")
    new35 = _customer_deal("New", None, 70.0, 100.0, 10, 35.0, "general", 0, 2)
    verdicts = [("COUNTER 12 phone", f"COUNTER 12.0 {phone20}"), ("COUNTER tier claim", f"COUNTER 12.0 {claim15}"),
                ("APPROVE verified", f"APPROVE 15.0 {verified15}"), ("ESCALATE 30", f"ESCALATE None {buds30}"),
                ("ESCALATE 45 over limits", f"ESCALATE None {buds45}"), ("REJECT below cost", f"REJECT None {new35}")]
    queries = [(f"next-action {s} / {v} / {e} r{r}", f"(next-action {s} (verdict {vx}) (event {e} {r}))")
               for s in STATES for v, vx in verdicts for e, r in EVENTS]
    lite = _customer_deal("Gold", None, 9500.0, 11999.0, 1, 20.0, "mobiles")
    charger = _customer_deal("Gold", None, 700.0, 1500.0, 60, 25.0, "accessories")
    buds25_60 = _customer_deal("Gold", None, 1200.0, 2999.0, 60, 25.0, "accessories")
    queries += [
        ("quote-terms phone 12", f"(quote-terms {phone20} 12.0)"),
        ("quote-terms phone 15 refused", f"(quote-terms {phone20} 15.0)"),
        ("quote-terms buds 30", f"(quote-terms {buds30} 30.0)"),
        ("quote-terms widget 60 x 15", f"(quote-terms {verified15} 15.0)"),
        ("quote-terms 0%", f"(quote-terms {phone20} 0.0)"),
        ("alternatives phone -> cheaper model", f"(alternatives {phone20} 2 ((candidate cheaper-model 10 {lite})))"),
        ("alternatives buds 60 units -> volume + cheaper",
         f"(alternatives {buds25_60} 7 ((candidate cheaper-model 6 {charger})))"),
        ("alternatives none", f"(alternatives {phone20} 2 ())"),
    ]
    return queries


def test_agent_rules_match_local_exactly_three_runs(live_backend):
    """next-action for every state x event x kind of decision, quote-terms and alternatives."""
    from engine.bridge import LocalMettaRunner, OmegaRunner, _to_py
    from engine.omega_link import hub

    local = LocalMettaRunner()
    omega = OmegaRunner()
    queries = _agent_queries()
    expected = {}
    for _, expr in queries:
        atom = local.run(expr)
        expected[expr] = (str(atom), json.dumps(_to_py(atom)))
    # Values must be identical (incl. int vs float). Text may differ ONLY in how PeTTa prints
    # floats: SWI-Prolog writes 20000.0 as 2.0e+04; those are counted and reported, not hidden.
    timings, mismatches, float_format_only = [], [], set()
    for run in range(1, RUNS + 1):
        for label, expr in queries:
            text, ms = hub.request(expr, omega.expected_sha256, omega.timeout)
            timings.append(ms)
            value = json.dumps(_to_py(omega._parser.parse_single(text)))
            if value != expected[expr][1]:
                mismatches.append((run, label, expected[expr][0][:200], text[:200]))
            elif text != expected[expr][0]:
                float_format_only.add(label)

    report = ROOT / "samples" / "omega_parity_report.md"
    old = report.read_text(encoding="utf-8") if report.exists() else "# Omega parity report\n"
    lines = [old.split("\n## Agent rules")[0].rstrip(), "", "## Agent rules (engine/agent.metta)", "",
             f"next-action for {len(STATES)} states x 6 kinds of decision x {len(EVENTS)} events, plus "
             f"quote-terms and alternatives: {len(queries)} queries x {RUNS} runs = "
             f"{len(queries) * RUNS} comparisons of the parsed value (see the note below on text).", "",
             f"**Result: {'ALL IDENTICAL' if not mismatches else f'{len(mismatches)} MISMATCHES'}**", "",
             f"Round trip through Omega: median {statistics.median(timings):.1f} ms, "
             f"max {max(timings):.1f} ms.", "",
             f"Values are compared exactly (incl. int vs float). {len(float_format_only)} queries print a float "
             "differently (PeTTa/SWI-Prolog writes 20000.0 as 2.0e+04; same value): "
             + (", ".join(sorted(float_format_only)) or "none") + ".", ""]
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nagent parity: {len(queries)} queries x {RUNS} runs, {len(mismatches)} mismatches")
    assert not mismatches, mismatches[:5]
