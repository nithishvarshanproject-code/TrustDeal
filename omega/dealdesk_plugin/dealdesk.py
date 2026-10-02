"""Deal Desk plugin for OmegaClaw (Omega agent).

Loaded by the agent at startup (config/plugins.yaml, loader: python). It:
  1. loads compat_petta.pl, policy.metta, rules.metta and learning.metta into the agent's
     own space (&self), from the read-only mount DEALDESK_ENGINE_DIR;
  2. connects OUT to the Deal Desk backend WebSocket (DEALDESK_WS_URL, bearer DEALDESK_TOKEN
     from the environment, or from a token file that is deleted right after reading);
  3. checks each request (engine/metta_safe.py: exactly one call to a Deal Desk entry point)
     and the live rules (fingerprint of the compiled rule clauses and policy facts actually
     in the runtime), then evaluates it EXACTLY in the agent's PeTTa runtime;
  4. after an approved policy change, reloads policy.metta live when the backend asks.

Deterministic by design: requests never pass through the agent's LLM loop. The plugin
contains no business logic; every decision comes from the .metta files.
"""
import hashlib
import importlib.util
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

try:
    import janus_swi as janus
except ImportError:  # name used by the SWI-Prolog build inside the Omega image
    import janus

logger = logging.getLogger("dealdesk")

# Mounted under /PeTTa: the agent's Landlock policy (profile/policy.yaml) only allows reads there.
ENGINE_DIR = Path(os.environ.get("DEALDESK_ENGINE_DIR", "/PeTTa/dealdesk/engine"))
WS_URL = os.environ.get("DEALDESK_WS_URL", "ws://host.docker.internal:8000/omega/engine")
# The agent's entrypoint scrubs the environment (only an allowlist survives), so in agent
# mode the token comes from a file in a writable mount (under /tmp, which Landlock allows).
# The plugin deletes it after reading, before the agent loop starts.
TOKEN_FILE = Path(os.environ.get("DEALDESK_TOKEN_FILE", "/tmp/dealdesk-secret/token"))
MAX_FRAME = 1024 * 1024          # bytes, both directions (the backend enforces the same)

# Same files, same order as engine/omega_link.py OMEGA_RULE_FILES (the hash must match).
RULE_FILES = ("compat_petta.pl", "policy.metta", "rules.metta", "learning.metta", "agent.metta")

TOKEN = ""
_eval_lock = threading.Lock()
_state = {"loaded": False, "error": None, "sha256": None, "live": None}
_loaded_text: dict = {}   # file name -> text as loaded into the agent's space
_fp_names: dict = {"functions": [], "facts": []}


def _load_engine_module(name: str):
    """Shared helpers from the mounted engine folder (the same file the backend uses)."""
    spec = importlib.util.spec_from_file_location(f"dealdesk_{name}", ENGINE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metta_safe = _load_engine_module("metta_safe")
log_redaction = _load_engine_module("log_redaction")
log_redaction.install()


def _read_token() -> str:
    """DEALDESK_TOKEN from the environment (harness mode), else the token file, which is
    deleted right away so the agent's own tools cannot read it later."""
    token = os.environ.get("DEALDESK_TOKEN", "")
    if not token and TOKEN_FILE.is_file():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        try:
            TOKEN_FILE.unlink()
        except OSError as exc:
            logger.warning("Deal Desk token file could not be deleted after reading (%s)",
                           exc.__class__.__name__)
    log_redaction.register_secret(token)
    return token


def _metta(program: str) -> list:
    """Run MeTTa source in the agent's runtime; return the results as text, one per result."""
    result = janus.query_once(
        "process_metta_string(S, Results), maplist(swrite, Results, Texts)", {"S": program})
    return list(result["Texts"])


def _read_files() -> dict:
    return {name: (ENGINE_DIR / name).read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
            for name in RULE_FILES}


def rules_sha256(texts: dict) -> str:
    digest = hashlib.sha256()
    for name in RULE_FILES:
        digest.update(name.encode())
        digest.update(texts[name].encode("utf-8"))
    return digest.hexdigest()


def _fact_lines(policy_text: str) -> list:
    """The facts of policy.metta, one per line, exactly as written (comments removed)."""
    facts = []
    for line in policy_text.splitlines():
        line = line.split(";", 1)[0].strip()
        if line.startswith("(") and line.endswith(")") and not line.startswith("(="):
            facts.append(line)
    return facts


# ---------- live-space integrity ----------
# PeTTa compiles every (= (f ...) ...) into a global Prolog clause and stores facts as
# '&self'(Rel, Args...) clauses. The fingerprint hashes the clauses of every Deal Desk
# function and every Deal Desk fact exactly as they are in the runtime now, so a rule or
# fact added, removed or replaced after loading (from any space) changes it.
_FP_PROLOG = r"""
dd_fp_clause(Names, S) :- member(N, Names), current_predicate(N/A), functor(H, N, A),
    catch(clause(H, B), _, fail), copy_term((H :- B), C), numbervars(C, 0, _),
    format(string(S), "~q", [C]).
dd_fp_fact(Rels, S) :- current_predicate('&self'/A), A >= 1, functor(H, '&self', A),
    member(R, Rels), arg(1, H, R), clause(H, true), copy_term(H, C), numbervars(C, 0, _),
    format(string(S), "~q", [C]).
dd_fingerprint(NameStrs, RelStrs, Sorted) :-
    maplist([X, Y]>>atom_string(Y, X), NameStrs, Names),
    maplist([X, Y]>>atom_string(Y, X), RelStrs, Rels),
    findall(S, dd_fp_clause(Names, S), L1), findall(S, dd_fp_fact(Rels, S), L2),
    append(L1, L2, L0), msort(L0, Sorted).
"""


def _fingerprint_names(texts: dict) -> dict:
    functions, facts = set(), set()
    for name in ("policy.metta", "rules.metta", "learning.metta", "agent.metta"):
        functions |= metta_safe.defined_functions(texts[name])
        facts |= metta_safe.fact_relations(texts[name])
    functions |= set(re.findall(r"(?m)^'?([a-z_][A-Za-z0-9_-]*)'?\(", texts["compat_petta.pl"]))
    return {"functions": sorted(functions), "facts": sorted(facts)}


def live_fingerprint() -> str:
    items = janus.query_once("dd_fingerprint(N, R, L)",
                             {"N": _fp_names["functions"], "R": _fp_names["facts"]})["L"]
    return hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()


def _integrity_error() -> dict | None:
    """None if the live rules still match what was loaded; else an error reply."""
    try:
        current = live_fingerprint()
    except Exception as exc:
        return {"ok": False, "integrity_error": True, "live_fingerprint": None,
                "error": f"live rule fingerprint unavailable: {exc!r}"}
    if current != _state["live"]:
        logger.error("Deal Desk live rules changed since loading (fingerprint %s != %s)",
                     current[:12], str(_state["live"])[:12])
        return {"ok": False, "integrity_error": True, "live_fingerprint": current,
                "error": "the Deal Desk rules in Omega's live space changed since they were "
                         "loaded - restart Omega"}
    return None


def load_rules() -> None:
    """Load the shim and the three rule files into the agent's &self (once, at startup)."""
    try:
        texts = _read_files()
        # lib_import: the agent's run.metta already imported it, and PeTTa returns no result
        # for a repeat import. If it were really missing, the next step would fail.
        lib_import = "!(import! &self (library lib_import))"
        out = _metta(lib_import)
        if any(o != "true" for o in out):
            raise RuntimeError(f"{lib_import} returned {out}")
        steps = [
            f"!(import_prolog_functions_from_file {ENGINE_DIR}/compat_petta.pl (format-args))",
            f"!(import! &self {ENGINE_DIR}/policy.metta)",
            f"!(import! &self {ENGINE_DIR}/rules.metta)",
            f"!(import! &self {ENGINE_DIR}/learning.metta)",
            f"!(import! &self {ENGINE_DIR}/agent.metta)",
        ]
        for step in steps:
            out = _metta(step)
            # In the agent, lib_import's functions are defined twice (loaded by run.metta and
            # by other libraries), so a call can succeed more than once: ["true", "true"].
            if not out or any(o != "true" for o in out):
                raise RuntimeError(f"{step} returned {out}")
        janus.consult("dealdesk_fingerprint", data=_FP_PROLOG)
        _fp_names.update(_fingerprint_names(texts))
        _loaded_text.clear()
        _loaded_text.update(texts)
        _state.update(loaded=True, error=None, sha256=rules_sha256(texts), live=live_fingerprint())
        logger.info("Deal Desk rules loaded from %s (sha256 %s, live fingerprint %s)",
                    ENGINE_DIR, _state["sha256"][:12], _state["live"][:12])
    except Exception as exc:  # reported to the backend in the hello frame; never hidden
        _state.update(loaded=False, error=repr(exc))
        logger.exception("Deal Desk rules could not be loaded")


def reload_policy() -> dict:
    """Hot reload after an approved policy change: swap the changed policy.metta facts in the
    agent's space (remove the old ones, add the new ones). Re-importing the file would
    duplicate every fact, so only the difference is applied. rules.metta, learning.metta and
    compat_petta.pl cannot change live: that needs an Omega restart."""
    if not _state["loaded"]:
        return {"ok": False, "error": f"rules were never loaded: {_state['error']}"}
    try:
        with _eval_lock:
            # never bless a tampered space: it must still match before it is changed
            problem = _integrity_error()
            if problem:
                return problem
            texts = _read_files()
            changed = [n for n in RULE_FILES if texts[n] != _loaded_text.get(n)]
            not_live = [n for n in changed if n != "policy.metta"]
            if not_live:
                return {"ok": False, "error": f"{', '.join(not_live)} changed; only policy.metta can be "
                                              "reloaded live - restart Omega (omega\\start-omega.bat)"}
            removed, added = [], []
            if "policy.metta" in changed:
                old_facts = _fact_lines(_loaded_text["policy.metta"])
                new_facts = _fact_lines(texts["policy.metta"])
                removed = [f for f in old_facts if f not in new_facts]
                added = [f for f in new_facts if f not in old_facts]
                for fact in removed:
                    if _metta(f"!(remove-atom &self {fact})") != ["true"]:
                        raise RuntimeError(f"could not remove {fact}")
                for fact in added:
                    if _metta(f"!(add-atom &self {fact})") != ["true"]:
                        raise RuntimeError(f"could not add {fact}")
                _loaded_text["policy.metta"] = texts["policy.metta"]
            _state["sha256"] = rules_sha256(_loaded_text)
            _state["live"] = live_fingerprint()
        logger.info("Deal Desk policy reloaded: -%d +%d facts (sha256 %s)",
                    len(removed), len(added), _state["sha256"][:12])
        return {"ok": True, "rules_sha256": _state["sha256"], "live_fingerprint": _state["live"],
                "removed": removed, "added": added}
    except Exception as exc:
        logger.exception("Deal Desk policy reload failed")
        return {"ok": False, "error": repr(exc)}


def evaluate(expr: str) -> dict:
    """Evaluate one checked expression exactly, on verified live rules. Exactly one result."""
    try:
        metta_safe.check_request(expr)
    except metta_safe.MettaInputError as exc:
        return {"ok": False, "error": f"request refused: {exc}"}
    started = time.perf_counter()
    try:
        with _eval_lock:
            problem = _integrity_error()
            if problem:
                return problem
            results = _metta("!" + expr)
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}
    ms = round((time.perf_counter() - started) * 1000, 2)
    if len(results) != 1:
        return {"ok": False, "error": f"expected exactly 1 result, got {len(results)}"}
    return {"ok": True, "text": results[0], "ms": ms, "live_fingerprint": _state["live"]}


def _serve_connection(connect) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with connect(WS_URL, additional_headers=headers, open_timeout=15, max_size=MAX_FRAME) as ws:
        ws.send(json.dumps({"type": "hello", "engine": "omega", "runtime": "petta",
                            "rules_sha256": _state["sha256"], "live_fingerprint": _state["live"],
                            "loaded": _state["loaded"], "error": _state["error"]}))
        logger.info("Deal Desk connected to %s", WS_URL)
        for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "eval":
                reply = evaluate(msg.get("expr", ""))
                ws.send(json.dumps({"type": "result", "id": msg.get("id"), **reply}))
            elif msg.get("type") == "reload":
                reply = reload_policy()
                ws.send(json.dumps({"type": "reloaded", "id": msg.get("id"), **reply}))


def _connection_loop() -> None:
    from websockets.sync.client import connect

    delay = 1.0
    while True:
        try:
            _serve_connection(connect)
            delay = 1.0
        except Exception as exc:
            logger.warning("Deal Desk link to %s lost or unavailable (%s); retrying in %.0f s",
                           WS_URL, exc.__class__.__name__, delay)
        time.sleep(delay)
        delay = min(delay * 2, 30.0)


def loadOmegaClawPlugin():
    """OmegaClaw plugin entry point: read the token, load the rules, start the link thread,
    return at once (before the agent loop starts)."""
    global TOKEN
    TOKEN = _read_token()
    if not TOKEN:
        logger.error("DEALDESK_TOKEN is not set; the Deal Desk link is disabled")
        return
    load_rules()
    threading.Thread(target=_connection_loop, name="dealdesk-link", daemon=True).start()


def serve_forever():
    """Harness mode (no agent loop, no LLM): load, link, and keep the process alive."""
    loadOmegaClawPlugin()
    while True:
        time.sleep(3600)
