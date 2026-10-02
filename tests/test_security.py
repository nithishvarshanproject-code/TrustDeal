"""Security hardening tests: MeTTa injection, input validation, CSV limits, CORS / hosts /
headers, the Omega WebSocket and the plugin's checks, and log redaction."""
import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from engine import log_redaction, metta_safe
from engine.bridge import ENGINE_DIR, LocalMettaRunner, evaluate_deal, from_metta, to_metta
from tests.test_engine import TEST_DEALS, build_deal

ROOT = Path(__file__).resolve().parent.parent
BS = chr(92)

# Crafted values that would break out of a naive "..." literal.
STRING_PAYLOADS = [
    'x") (injected-canary) ("',
    "x" + BS,
    "x" + BS + '") (injected-canary) ("',
    "x" + BS + BS + '") (injected-canary) ("',
    "x ; (injected-canary)",
    'x\n") (injected-canary) ("',
    "$x &self",
    'x") !(add-atom &self (injected-canary)) ("',
    'x") (= (confidence $d) 0.0) ("',
    "Caf\u00e9 \u20b9 \U0001F600",
]


# ---------- engine/metta_safe.py ----------

@pytest.mark.parametrize("value", ["Gold\n", 'Gold") (x', "Gold x", "", "1Gold", "$x", "&self",
                                   "Gold;", "a" * 33, "Gold\u00e9"])
def test_symbol_refuses_anything_but_one_plain_symbol(value):
    with pytest.raises(metta_safe.MettaInputError):
        metta_safe.symbol(value)


def test_symbol_allowed_list_and_none():
    assert metta_safe.symbol("Gold", allowed={"Gold", "Silver"}) == "Gold"
    assert metta_safe.symbol(None) == "None"
    with pytest.raises(metta_safe.MettaInputError):
        metta_safe.symbol("Platinum", allowed={"Gold", "Silver"})


@pytest.mark.parametrize("payload", STRING_PAYLOADS)
def test_string_payload_becomes_one_escaped_literal(payload):
    literal = metta_safe.string(payload)
    # exactly one string literal: the only unescaped quotes are the outer ones
    inner = literal[1:-1]
    assert literal[0] == literal[-1] == '"'
    i = 0
    while i < len(inner):
        if inner[i] == BS:
            assert inner[i + 1] in (BS, '"')
            i += 2
            continue
        assert inner[i] != '"' and inner[i] != "\n"
        i += 1
    metta_safe.check_request(f"(evaluate-core (deal Gold Gold 70.0 100.0 1 1.0 0 1 None False 0 {literal} None))")


@pytest.mark.parametrize("value", ["a\x00b", "a\x1bb", "a\u202eb"])
def test_string_refuses_control_characters(value):
    with pytest.raises(metta_safe.MettaInputError):
        metta_safe.string(value)


def test_string_length_limit():
    metta_safe.string("x" * 500)
    with pytest.raises(metta_safe.MettaInputError):
        metta_safe.string("x" * 501)


@pytest.mark.parametrize("value, kind", [(float("inf"), float), (float("nan"), float), ("1e400", float),
                                         (True, int), ("12abc", int), (object(), float)])
def test_number_refuses_non_finite_and_non_numbers(value, kind):
    with pytest.raises(metta_safe.MettaInputError):
        metta_safe.number(value, kind)


def test_number_output_is_unchanged_repr():
    assert metta_safe.number(150, int) == "150"
    assert metta_safe.number(10, float) == "10.0"
    assert metta_safe.number(None, int) == "None"


@pytest.mark.parametrize("expr, why", [
    ("(evaluate-core (deal Gold)) (injected-canary)", "only one expression"),
    ("(evaluate-core (deal Gold)) !(add-atom &self (x))", "only one expression"),
    ("(evaluate-core !(x))", "not allowed outside strings"),
    ("(evaluate-core &self)", "not allowed outside strings"),
    ("(evaluate-core $x)", "not allowed outside strings"),
    ("(evaluate-core ; comment\n (x))", "not allowed outside strings"),
    ("(evaluate-core\n(x))", "not allowed outside strings"),
    ("(add-atom self (x))", "not a Deal Desk entry point"),
    ("(import! self x)", "not allowed outside strings"),
    ("(evaluate-core (x)", "unbalanced"),
    ('(evaluate-core "abc)', "unbalanced"),
    ('(evaluate-core "a' + BS + 'n")', "unknown escape"),
    ('(evaluate-core "a\x01")', "control character"),
    (" (evaluate-core (x))", "one parenthesised call"),
    ("", "empty"),
])
def test_check_request_refuses(expr, why):
    with pytest.raises(metta_safe.MettaInputError, match=why):
        metta_safe.check_request(expr)


def test_check_request_size_limit():
    big = '(evaluate-core "' + "x" * metta_safe.MAX_REQUEST + '")'
    with pytest.raises(metta_safe.MettaInputError, match="longer than"):
        metta_safe.check_request(big)


def test_check_request_accepts_every_real_request():
    for case in TEST_DEALS:
        expr = to_metta(build_deal(case))
        for head in ("evaluate", "evaluate-core", "what-if-for"):
            metta_safe.check_request(f"({head} {expr})")
    metta_safe.check_request("(history-stv None None)")
    metta_safe.check_request("(update-trust (stv 0.5 0.0) on-time)")
    metta_safe.check_request("(category-profile-of None)")


# ---------- injection through the real engine (hyperon) ----------

@pytest.fixture(scope="module")
def runner():
    return LocalMettaRunner(ENGINE_DIR)   # read-only use of the rule files


def _canary(runner) -> list:
    return runner._metta.run("!(match &self (injected-canary) yes)")[0]


@pytest.mark.parametrize("payload", STRING_PAYLOADS)
def test_seller_name_payload_cannot_inject(runner, payload):
    clean = from_metta(runner.run(f"(evaluate-core {to_metta(build_deal(1))})"))
    crafted = from_metta(runner.run(f"(evaluate-core {to_metta(dict(build_deal(1), seller_name=payload))})"))
    assert (crafted["result"], crafted["approved_discount"], crafted["confidence"]) == \
        (clean["result"], clean["approved_discount"], clean["confidence"])
    assert [e for e in crafted["trail"] if e["rule_id"] != "TRUST"] == \
        [e for e in clean["trail"] if e["rule_id"] != "TRUST"]
    assert _canary(runner) == []
    confidence = runner._metta.run(f"!(confidence {to_metta(build_deal(1))})")[0]
    assert [str(c) for c in confidence] == ["1.0"]


def test_runner_refuses_a_second_expression(runner):
    with pytest.raises(metta_safe.MettaInputError):
        runner.run(f"(evaluate-core {to_metta(build_deal(1))}) (injected-canary)")
    assert _canary(runner) == []


@pytest.mark.parametrize("tier", ['Gold") (injected-canary', "Gold\n", "Gold x"])
def test_crafted_tier_never_reaches_the_engine(tier):
    with pytest.raises(ValueError):
        evaluate_deal(dict(build_deal(1), claimed_tier=tier))


def test_override_reason_payload_in_policy_proposals(stack):
    client = stack["client"]
    deal = {"seller_id": 3, "product_id": 1, "quantity": 60, "discount_requested": 14.0, "claimed_tier": "Silver"}
    for payload in STRING_PAYLOADS[:4]:
        deal_id = client.post("/deals/evaluate", json=deal).json()["deal_id"]
        resp = client.post(f"/deals/{deal_id}/override", json={
            "reviewer": "r", "new_result": "APPROVE", "reason": payload})
        assert resp.status_code == 201
    proposals = client.get("/policy/proposals")
    assert proposals.status_code == 200
    reasons = [e["reason"] for p in proposals.json() for e in p["evidence"]]
    assert reasons and all(r.startswith("x") for r in reasons)   # stored as data, returned as data


# ---------- the Omega plugin's own checks (real plugin code, stub Prolog bridge) ----------

class _FakeJanus:
    def __init__(self):
        self.queries, self.fp_items = [], ["clause-a", "fact-b"]

    def query_once(self, query, bindings=None):
        self.queries.append(query)
        if query.startswith("dd_fingerprint"):
            return {"L": list(self.fp_items)}
        return {"Texts": ["(decision APPROVE 10.0 1.0 () (override-hint none) (what-if not-computed))"]}

    def consult(self, *args, **kwargs):
        pass


@pytest.fixture
def plugin(monkeypatch):
    original_factory = logging.getLogRecordFactory()   # the plugin installs its own redaction
    fake = _FakeJanus()
    monkeypatch.setitem(sys.modules, "janus_swi", fake)
    monkeypatch.setenv("DEALDESK_ENGINE_DIR", str(ENGINE_DIR))
    spec = importlib.util.spec_from_file_location(
        "dealdesk_plugin_under_test", ROOT / "omega" / "dealdesk_plugin" / "dealdesk.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    texts = module._read_files()
    module._fp_names.update(module._fingerprint_names(texts))
    module._loaded_text.update(texts)
    module._state.update(loaded=True, sha256=module.rules_sha256(texts), live=module.live_fingerprint())
    yield module, fake
    logging.setLogRecordFactory(original_factory)


def _evals(fake) -> int:
    return sum(1 for q in fake.queries if q.startswith("process_metta_string"))


def test_plugin_fingerprints_every_rule_function_and_policy_fact(plugin):
    module, _ = plugin
    assert {"decide", "evaluate-core", "allowed-max", "format-args"} <= set(module._fp_names["functions"])
    assert {"tier-cap", "margin-floor-for", "category-max", "penalty"} <= set(module._fp_names["facts"])


def test_plugin_refuses_injected_request_without_running_it(plugin):
    module, fake = plugin
    reply = module.evaluate(f"(evaluate-core {to_metta(build_deal(1))}) (injected-canary)")
    assert reply["ok"] is False and "request refused" in reply["error"]
    reply = module.evaluate("(add-atom self (x))")
    assert reply["ok"] is False
    assert _evals(fake) == 0


def test_plugin_evaluates_a_valid_request_with_its_live_fingerprint(plugin):
    module, fake = plugin
    reply = module.evaluate(f"(evaluate-core {to_metta(build_deal(1))})")
    assert reply["ok"] is True and reply["live_fingerprint"] == module._state["live"]
    assert _evals(fake) == 1


def test_plugin_refuses_to_decide_on_tampered_live_rules(plugin):
    module, fake = plugin
    fake.fp_items.append("(= (decide $d) (APPROVE 99.0 x))")   # a rule added in memory
    reply = module.evaluate(f"(evaluate-core {to_metta(build_deal(1))})")
    assert reply["ok"] is False and reply["integrity_error"] is True
    assert _evals(fake) == 0
    assert module.reload_policy()["integrity_error"] is True   # a reload never blesses it


def test_plugin_reads_token_file_once_and_deletes_it(plugin, tmp_path, monkeypatch):
    module, _ = plugin
    token_file = tmp_path / "token"
    token_file.write_text("tok-3f9a1c2b7d", encoding="utf-8")
    monkeypatch.setattr(module, "TOKEN_FILE", token_file)
    monkeypatch.delenv("DEALDESK_TOKEN", raising=False)
    assert module._read_token() == "tok-3f9a1c2b7d"
    assert not token_file.exists()
    assert "tok-3f9a1c2b7d" not in module.log_redaction.redact("connected with tok-3f9a1c2b7d")


# ---------- input validation (clear 422s, never 500, never bad data) ----------

BASE = {"seller_id": 1, "product_id": 1, "quantity": 150, "discount_requested": 10, "claimed_tier": "Gold"}


@pytest.mark.parametrize("change", [
    {"claimed_tier": "Platinum"}, {"claimed_tier": "decide"}, {"claimed_tier": 'Gold") (x'},
    {"quantity": 0}, {"quantity": 100_001}, {"quantity": 10 ** 30},
    {"discount_requested": -1}, {"discount_requested": 100.5}, {"competitor_price": 0},
    {"claimed_tier": "x" * 40},
])
def test_evaluate_refuses_invalid_input_with_422(stack, change):
    resp = stack["client"].post("/deals/evaluate", json={**BASE, **change})
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("body", [
    '{"seller_id":1,"product_id":1,"quantity":5,"discount_requested":NaN}',
    '{"seller_id":1,"product_id":1,"quantity":5,"discount_requested":10,"competitor_price":Infinity}',
])
def test_evaluate_refuses_nan_and_infinity(stack, body):
    resp = stack["client"].post("/deals/evaluate", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 422


@pytest.mark.parametrize("given, stored", [("gold", "Gold"), (" SILVER ", "Silver"), ("nEw", "New"), ("", None)])
def test_claimed_tier_any_case_is_normalized(stack, given, stored):
    resp = stack["client"].post("/deals/evaluate", json={**BASE, "claimed_tier": given})
    assert resp.status_code == 200, resp.text
    deal = stack["client"].get(f"/deals/{resp.json()['deal_id']}").json()
    assert deal["claimed_tier"] == stored


def test_claimed_tier_error_message_is_clear(stack):
    resp = stack["client"].post("/deals/evaluate", json={**BASE, "claimed_tier": "Platinum"})
    assert "claimed_tier must be one of Gold, Silver, New" in resp.text


@pytest.mark.parametrize("body", [
    {"name": "X", "category": "general", "cost_price": 1, "list_price": 1},
    {"name": "X", "category": "tablets", "cost_price": 1, "list_price": 2},
    {"name": "x" * 81, "category": "general", "cost_price": 1, "list_price": 2},
    {"name": "   ", "category": "general", "cost_price": 1, "list_price": 2},
    {"name": "X", "category": "general", "cost_price": 0, "list_price": 2},
])
def test_add_product_refuses_invalid_input(stack, body):
    assert stack["client"].post("/products", json=body).status_code == 422


def test_add_product_refuses_infinity(stack):
    resp = stack["client"].post("/products", content='{"name":"Inf","category":"general","cost_price":1,'
                                                     '"list_price":Infinity}',
                                headers={"Content-Type": "application/json"})
    assert resp.status_code == 422
    assert all(p["name"] != "Inf" for p in stack["client"].get("/products").json())


def test_override_and_approval_length_limits(stack):
    client = stack["client"]
    deal_id = client.post("/deals/evaluate", json=BASE).json()["deal_id"]
    ok = {"reviewer": "r", "new_result": "APPROVE", "reason": "y" * 500}
    assert client.post(f"/deals/{deal_id}/override", json=ok).status_code == 201
    assert client.post(f"/deals/{deal_id}/override", json={**ok, "reason": "y" * 501}).status_code == 422
    assert client.post(f"/deals/{deal_id}/override", json={**ok, "reviewer": "r" * 81}).status_code == 422
    apply = {"fact": "tier-cap", "key": "Silver", "old_value": 10, "new_value": 12, "approved_by": "a" * 81}
    assert client.post("/policy/proposals/apply", json=apply).status_code == 422


# ---------- CSV upload limits ----------

HEADER = "name,category,cost_price,list_price\n"


def test_csv_over_1_mb_is_refused(stack):
    text = HEADER + "\u20b9" * 400_000          # 400k characters but 1.2 MB of UTF-8
    resp = stack["client"].post("/products/import/preview",
                                content=json.dumps({"csv": text}, ensure_ascii=False).encode("utf-8"),
                                headers={"Content-Type": "application/json"})
    assert resp.status_code == 422 and "larger than 1 MB" in resp.text


def test_request_body_over_2_mb_is_refused(stack):
    resp = stack["client"].post("/products/import/preview",
                                content=b'{"csv": "' + b"x" * (2 * 1024 * 1024 + 10) + b'"}',
                                headers={"Content-Type": "application/json"})
    assert resp.status_code == 413


@pytest.mark.parametrize("bad", ["\ufffd", "\x00"])
def test_csv_must_be_utf8_text(stack, bad):
    body = stack["client"].post("/products/import/preview",
                                json={"csv": HEADER + f"Cable{bad},general,1,2\n"}).json()
    assert body["file_errors"] and "not UTF-8" in body["file_errors"][0]


def test_csv_huge_field_is_a_file_error_not_a_500(stack):
    resp = stack["client"].post("/products/import/preview", json={"csv": HEADER + "x" * 200_000})
    assert resp.status_code == 200
    assert "not a valid CSV" in resp.json()["file_errors"][0]


def test_csv_row_limit_kept(stack):
    rows = "".join(f"P{i},general,1,2\n" for i in range(501))
    body = stack["client"].post("/products/import/preview", json={"csv": HEADER + rows}).json()
    assert "Too many rows" in body["file_errors"][0]


def test_csv_infinite_price_is_a_row_error(stack):
    body = stack["client"].post("/products/import/preview",
                                json={"csv": HEADER + "X,general,1,inf\nY,general,1,1e400\n"}).json()
    assert body["valid"] == 0 and body["invalid"] == 2


# ---------- CORS, hosts, headers ----------

@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_cors_allows_the_two_local_origins(stack, origin):
    resp = stack["client"].options("/sellers", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize("origin", ["http://evil.example", "http://localhost:3000"])
def test_cors_refuses_other_origins(stack, origin):
    resp = stack["client"].options("/sellers", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    assert resp.status_code == 400
    assert resp.headers.get("access-control-allow-origin") != origin


def test_cors_refuses_other_methods(stack):
    resp = stack["client"].options("/sellers", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE"})
    assert resp.status_code == 400


def test_unknown_host_header_is_refused(stack):
    assert stack["client"].get("/health", headers={"Host": "evil.example"}).status_code == 400
    for host in ("localhost:8000", "127.0.0.1:8000", "host.docker.internal:8000"):
        assert stack["client"].get("/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("method, path, body, status", [
    ("get", "/health", None, 200), ("get", "/deals/999999", None, 404),
    ("post", "/deals/evaluate", {"quantity": -1}, 422)])
def test_security_headers_on_every_response(stack, method, path, body, status):
    resp = getattr(stack["client"], method)(path, **({"json": body} if body is not None else {}))
    assert resp.status_code == status
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


# ---------- Omega WebSocket ----------

TOKEN = "ws-token-7c1e9b2a44"


def test_ws_non_ascii_authorization_is_refused_cleanly(stack, monkeypatch):
    monkeypatch.setenv("OMEGA_TOKEN", TOKEN)
    with pytest.raises(WebSocketDisconnect) as exc:
        with stack["client"].websocket_connect("/omega/engine",
                                               headers={"Authorization": "Bearer t\u00e9st".encode("latin-1")}) as ws:
            ws.receive_json()
    assert exc.value.code == 1008


def test_ws_oversized_frame_is_refused(stack, monkeypatch):
    monkeypatch.setenv("OMEGA_TOKEN", TOKEN)
    with pytest.raises(WebSocketDisconnect) as exc:
        with stack["client"].websocket_connect("/omega/engine",
                                               headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
            ws.send_text('{"type": "hello", "pad": "' + "x" * (1024 * 1024) + '"}')
            ws.receive_json()
    assert exc.value.code == 1009


def test_ws_token_is_never_logged_or_returned(stack, monkeypatch, caplog):
    monkeypatch.setenv("OMEGA_TOKEN", TOKEN)
    caplog.set_level(logging.DEBUG)
    for token in (TOKEN, "wrong-token-123"):
        try:
            with stack["client"].websocket_connect("/omega/engine",
                                                   headers={"Authorization": f"Bearer {token}"}) as ws:
                ws.send_json({"type": "hello", "rules_sha256": "x", "loaded": False})
                ws.close()
        except WebSocketDisconnect:
            pass
        logging.getLogger("test").info("header was Bearer %s", token)
    assert TOKEN not in caplog.text
    assert TOKEN not in stack["client"].get("/omega/status").text


# ---------- log redaction ----------

@pytest.mark.parametrize("text, secret", [
    ("Authorization: Bearer abcdef123456", "abcdef123456"),
    ("api_key=s3cr3tvalue", "s3cr3tvalue"),
    ('{"token": "abcd1234efgh"}', "abcd1234efgh"),
    ("OMEGACLAW_AUTH_SECRET=493817", "493817"),
    ("password: hunter2hunter2", "hunter2hunter2"),
    ("using key sk-proj-ABCDEFGHIJKLMNOPQRST", "sk-proj-ABCDEFGHIJKLMNOPQRST"),
])
def test_redact_masks_key_and_token_patterns(text, secret):
    masked = log_redaction.redact(text)
    assert secret not in masked and log_redaction.MASK in masked


def test_redact_masks_known_secret_values(monkeypatch):
    monkeypatch.setenv("ASIONE_API_KEY", "plainlookingvalue42")
    monkeypatch.setenv("OMEGACLAW_AUTH_SECRET", "271828")
    masked = log_redaction.redact("key plainlookingvalue42 and code 271828 in a sentence")
    assert "plainlookingvalue42" not in masked and "271828" not in masked


def test_redact_keeps_ordinary_text():
    text = "Deal Desk rules loaded (sha256 ddfb8412108b), 150 units at 10%"
    assert log_redaction.redact(text) == text


def test_log_records_are_redacted_everywhere(caplog, monkeypatch):
    log_redaction.install()
    monkeypatch.setenv("DEALDESK_TOKEN", "0123456789abcdef0123")
    caplog.set_level(logging.INFO)
    log = logging.getLogger("uvicorn.error")
    log.info("connect with %s", "Bearer 0123456789abcdef0123")
    try:
        raise RuntimeError("token=0123456789abcdef0123")
    except RuntimeError:
        log.exception("failed")
    assert "0123456789abcdef0123" not in caplog.text
    # uvicorn's access log unpacks record.args: the tuple structure and numbers are kept
    record = logging.getLogger("uvicorn.access").makeRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/x?token=0123456789abcdef0123", "1.1", 200), None)
    assert isinstance(record.args, tuple) and record.args[4] == 200
    assert "0123456789abcdef0123" not in record.getMessage()
