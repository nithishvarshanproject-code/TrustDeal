"""OmegaRunner and the /omega/engine protocol, tested with a fake plugin (no Docker).

The fake plugin speaks the same protocol as omega/dealdesk_plugin/dealdesk.py but evaluates
with hyperon, so these tests prove the transport, the parsing and every failure mode.
The real Omega parity test is tests/test_omega_parity.py.
"""
import asyncio
import json
import threading
import time
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from engine import bridge
from engine.bridge import LocalMettaRunner, evaluate_deal, from_metta, to_metta, what_if_for
from engine.omega_link import hub, rules_sha256
from tests.test_engine import TEST_DEALS, build_deal

TOKEN = "test-token"


LIVE_FP = "fake-live-fingerprint-0001"


class FakePlugin:
    """mode: ok | silent (never answers) | error (reports an evaluation error)
    | tampered (the plugin's live-space check fails) | drift (results carry another live
    fingerprint than the hello). reload_ok=False makes the hot reload fail.
    live_fp=None sends a hello without a live fingerprint (an old plugin)."""

    def __init__(self, client, engine_dir, mode="ok", token=TOKEN, rules_hash=None, reload_ok=True,
                 live_fp=LIVE_FP, transform=None):
        self.client, self.mode, self.token, self.reload_ok = client, mode, token, reload_ok
        self.engine_dir = engine_dir
        self.live_fp = live_fp
        self.transform = transform or (lambda text: text)   # e.g. print floats the way PeTTa does
        self.rules_hash = rules_hash or rules_sha256(engine_dir)
        self.local = LocalMettaRunner(engine_dir)
        self.plugin_id = str(uuid.uuid4())   # lets start()/stop() wait for THIS connection
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            with self.client.websocket_connect(
                    "/omega/engine", headers={"Authorization": f"Bearer {self.token}"}) as ws:
                ws.send_json({"type": "hello", "engine": "omega", "runtime": "fake",
                              "rules_sha256": self.rules_hash, "live_fingerprint": self.live_fp,
                              "loaded": True, "error": None, "plugin_id": self.plugin_id})
                while True:
                    msg = ws.receive_json()
                    if msg.get("type") == "shutdown":
                        return
                    if msg.get("type") == "reload":
                        # like the real plugin: re-read engine/ and report the new hash
                        if not self.reload_ok:
                            ws.send_json({"type": "reloaded", "id": msg["id"], "ok": False,
                                          "error": "simulated reload failure"})
                            continue
                        self.local = LocalMettaRunner(self.engine_dir)
                        self.rules_hash = rules_sha256(self.engine_dir)
                        self.live_fp = LIVE_FP + "-reloaded"
                        ws.send_json({"type": "reloaded", "id": msg["id"], "ok": True,
                                      "rules_sha256": self.rules_hash, "live_fingerprint": self.live_fp,
                                      "removed": [], "added": []})
                        continue
                    if self.mode == "silent":
                        continue
                    if self.mode == "error":
                        ws.send_json({"type": "result", "id": msg["id"], "ok": False, "error": "boom"})
                        continue
                    if self.mode == "tampered":
                        ws.send_json({"type": "result", "id": msg["id"], "ok": False,
                                      "integrity_error": True, "live_fingerprint": "changed",
                                      "error": "the Deal Desk rules in Omega's live space changed"})
                        continue
                    text = self.transform(str(self.local.run(msg["expr"])))
                    live = "changed-in-memory" if self.mode == "drift" else self.live_fp
                    ws.send_json({"type": "result", "id": msg["id"], "ok": True, "text": text, "ms": 1.0,
                                  "live_fingerprint": live})
        except Exception as exc:  # surfaced by the tests
            self.error = exc

    def _attached(self) -> bool:
        return (hub._hello or {}).get("plugin_id") == self.plugin_id

    def start(self):
        # wait for THIS plugin's hello (not a previous test's connection that is closing)
        self.thread.start()
        for _ in range(200):
            if self._attached() or self.error:
                return self
            time.sleep(0.05)
        raise AssertionError(f"fake plugin did not connect: {self.error}")

    def stop(self):
        if self._attached():
            asyncio.run_coroutine_threadsafe(hub._send({"type": "shutdown"}), hub._loop).result(5)
        self.thread.join(5)
        for _ in range(100):   # the hub must really have detached before the next test
            if not self._attached():
                return
            time.sleep(0.05)


@pytest.fixture
def omega(stack, monkeypatch):
    monkeypatch.setenv("ENGINE_RUNNER", "omega")
    monkeypatch.setenv("OMEGA_TOKEN", TOKEN)
    # The fake plugin evaluates with hyperon, which takes seconds for propose-policy-changes
    # (real Omega/PeTTa: ~5-10 ms). A generous timeout here; the timeout test sets its own.
    monkeypatch.setenv("OMEGA_TIMEOUT_S", "30")
    bridge.reset_runner(stack["engine_dir"])
    plugins = []

    def connect(**kwargs):
        plugin = FakePlugin(stack["client"], stack["engine_dir"], **kwargs).start()
        plugins.append(plugin)
        return plugin

    yield {**stack, "connect": connect}
    for p in plugins:
        p.stop()


def _canon(x) -> str:
    """Exact comparison including number types (0 vs 0.0)."""
    return json.dumps(x, sort_keys=True)


def test_omega_runner_matches_local_for_all_six_deals(omega):
    omega["connect"]()
    local = LocalMettaRunner(omega["engine_dir"])
    for case in sorted(TEST_DEALS):
        deal = build_deal(case)
        through_omega = evaluate_deal(deal, include_what_if=False)
        direct = from_metta(local.run(f"(evaluate-core {to_metta(deal)})"))
        assert _canon(through_omega) == _canon(direct), case
        assert what_if_for(deal) == from_metta(local.run(f"(evaluate {to_metta(deal)})"))["what_if"]
    assert bridge.get_runner().name == "omega"


def test_api_footer_says_omega(omega):
    omega["connect"]()
    body = omega["client"].post("/deals/evaluate", json={
        "seller_id": 3, "product_id": 1, "quantity": 60, "discount_requested": 20, "claimed_tier": "Gold"}).json()
    assert (body["result"], body["approved_discount"]) == ("COUNTER", 12.0)
    assert body["engine"]["runner"] == "omega"
    status = omega["client"].get("/omega/status").json()
    assert status["connected"] and status["rules_match"] and status["engine_runner"] == "omega"


def test_not_connected_is_a_clear_503(omega):
    resp = omega["client"].post("/deals/evaluate", json={
        "seller_id": 1, "product_id": 1, "quantity": 150, "discount_requested": 10})
    assert resp.status_code == 503
    assert "Omega agent is not connected" in resp.json()["detail"]


def test_wrong_token_is_rejected(omega):
    with pytest.raises(WebSocketDisconnect):
        with omega["client"].websocket_connect("/omega/engine", headers={"Authorization": "Bearer nope"}) as ws:
            ws.receive_json()
    assert not hub.status("")["connected"]


def test_different_rules_are_refused(omega):
    omega["connect"](rules_hash="0" * 64)
    resp = omega["client"].post("/deals/evaluate", json={
        "seller_id": 1, "product_id": 1, "quantity": 150, "discount_requested": 10})
    assert resp.status_code == 503
    assert "different rule files" in resp.json()["detail"]
    assert omega["client"].get("/omega/status").json()["rules_match"] is False


def test_timeout_is_a_clear_503(omega, monkeypatch):
    monkeypatch.setenv("OMEGA_TIMEOUT_S", "0.5")
    bridge.reset_runner(omega["engine_dir"])
    omega["connect"](mode="silent")
    started = time.perf_counter()
    resp = omega["client"].post("/deals/evaluate", json={
        "seller_id": 1, "product_id": 1, "quantity": 150, "discount_requested": 10})
    assert resp.status_code == 503
    assert "did not answer within 0.5 s" in resp.json()["detail"]
    assert time.perf_counter() - started < 5


def test_omega_evaluation_error_is_a_502(omega):
    omega["connect"](mode="error")
    resp = omega["client"].post("/deals/evaluate", json={
        "seller_id": 1, "product_id": 1, "quantity": 150, "discount_requested": 10})
    assert resp.status_code == 502
    assert "boom" in resp.json()["detail"]


# ---------- live-space integrity (rules changed in Omega's memory, not in the files) ----------

DEAL = {"seller_id": 1, "product_id": 1, "quantity": 150, "discount_requested": 10}


def test_plugin_detected_live_tampering_is_a_503(omega):
    omega["connect"](mode="tampered")
    resp = omega["client"].post("/deals/evaluate", json=DEAL)
    assert resp.status_code == 503
    assert "live space changed" in resp.json()["detail"]


def test_live_fingerprint_drift_is_a_503(omega):
    """Even if the plugin says ok, a result from other live rules than the hello's is refused."""
    omega["connect"](mode="drift")
    resp = omega["client"].post("/deals/evaluate", json=DEAL)
    assert resp.status_code == 503
    assert "live Deal Desk rules differ" in resp.json()["detail"]


def test_plugin_without_live_fingerprint_is_refused(omega):
    omega["connect"](live_fp=None)
    resp = omega["client"].post("/deals/evaluate", json=DEAL)
    assert resp.status_code == 503
    assert "live rule fingerprint" in resp.json()["detail"]


def test_status_shows_the_live_fingerprint(omega):
    omega["connect"]()
    assert omega["client"].get("/omega/status").json()["live_fingerprint"] == LIVE_FP


# ---------- hot reload after an approved policy change ----------

# Meridian Supply (Silver), 60 units, 14%: allowed 10 + 2 = 12 -> COUNTER 12
SILVER_DEAL = {"seller_id": 3, "product_id": 1, "quantity": 60, "discount_requested": 14.0,
               "claimed_tier": "Silver"}
APPLY = {"fact": "tier-cap", "key": "Silver", "old_value": 10.0, "new_value": 12.0,
         "approved_by": "Head of Sales"}


def _three_silver_overrides(client):
    for i in range(3):
        body = client.post("/deals/evaluate", json=SILVER_DEAL).json()
        assert (body["result"], body["approved_discount"], body["engine"]["runner"]) == ("COUNTER", 12.0, "omega")
        client.post(f"/deals/{body['deal_id']}/override", json={
            "reviewer": "Sales lead", "new_result": "APPROVE", "reason": f"key account {i}"})


def test_apply_hot_reloads_omega_and_new_result_follows(omega):
    omega["connect"]()
    client = omega["client"]
    _three_silver_overrides(client)
    before = client.get("/omega/status").json()

    resp = client.post("/policy/proposals/apply", json=APPLY)
    assert resp.status_code == 200, resp.text
    assert resp.json()["omega_reload"]["reloaded"] is True

    after = client.get("/omega/status").json()
    assert after["rules_match"] is True
    assert after["omega_rules_sha256"] != before["omega_rules_sha256"]   # Omega now has the new policy
    assert after["live_fingerprint"] == LIVE_FP + "-reloaded"           # new live baseline adopted

    body = client.post("/deals/evaluate", json=SILVER_DEAL).json()
    assert (body["result"], body["approved_discount"], body["engine"]["runner"]) == ("APPROVE", 14.0, "omega")


def test_failed_hot_reload_is_a_clear_503(omega):
    omega["connect"](reload_ok=False)
    client = omega["client"]
    _three_silver_overrides(client)

    resp = client.post("/policy/proposals/apply", json=APPLY)
    assert resp.status_code == 503
    assert "Policy saved, but Omega could not reload it" in resp.json()["detail"]
    assert client.get("/omega/status").json()["rules_match"] is False
    # decisions are refused (different rules), never computed on stale rules or locally
    resp = client.post("/deals/evaluate", json=SILVER_DEAL)
    assert resp.status_code == 503 and "different rule files" in resp.json()["detail"]


def test_demo_reset_hot_reloads_omega_back_to_the_original_policy(omega):
    omega["connect"]()
    client = omega["client"]
    _three_silver_overrides(client)
    assert client.post("/policy/proposals/apply", json=APPLY).status_code == 200
    assert client.post("/deals/evaluate", json=SILVER_DEAL).json()["result"] == "APPROVE"

    resp = client.post("/demo/reset", json={"confirm": "RESET"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["omega_reload"]["reloaded"] is True
    assert client.get("/omega/status").json()["rules_match"] is True
    body = client.post("/deals/evaluate", json=SILVER_DEAL).json()
    assert (body["result"], body["approved_discount"], body["engine"]["runner"]) == ("COUNTER", 12.0, "omega")
