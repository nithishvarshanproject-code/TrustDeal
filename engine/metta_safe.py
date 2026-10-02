"""Safe MeTTa text: the one place that turns values into MeTTa and checks MeTTa requests.

Shared by engine/bridge.py (building every request), both runners (checking each request
before it runs), engine/policy_admin.py (rewriting a policy fact) and the Omega plugin
(checking each request it receives; it loads this file from the mounted engine folder).
Standard library only, so it also runs inside the Omega container.

No business logic: this only guarantees that a value becomes exactly ONE MeTTa atom and
that a request is exactly ONE call to a Deal Desk entry point, never extra code.
"""
import math
import re

MAX_SYMBOL = 32
MAX_STRING = 500
MAX_REQUEST = 512 * 1024          # a request with many overrides stays far below this

# The only functions a request may call (the same list for local hyperon and Omega).
ENTRY_POINTS = frozenset({
    "evaluate", "evaluate-core", "what-if-for", "propose-policy-changes", "history-stv",
    "update-trust", "category-profile-of",
    "next-action", "quote-terms", "alternatives",          # engine/agent.metta
})

_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
# Outside string literals a request may contain only what the builders below produce.
_PLAIN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ()._+-")


class MettaInputError(ValueError):
    """A value or request that must not be placed into MeTTa."""


# ---------- values -> MeTTa ----------

def symbol(value, allowed=None) -> str:
    """A bare symbol (tier, category, result...). None -> None."""
    if value is None:
        return "None"
    text = value if isinstance(value, str) else str(value)
    if len(text) > MAX_SYMBOL or not _SYMBOL.fullmatch(text):
        raise MettaInputError(f"invalid symbol: {text[:40]!r}")
    if allowed is not None and text not in allowed:
        raise MettaInputError(f"{text!r} is not one of {', '.join(sorted(allowed))}")
    return text


def string(value, max_len: int = MAX_STRING) -> str:
    """A string literal. Whitespace (incl. newlines) collapses to single spaces; control
    characters are refused; backslash and double quote are escaped."""
    text = " ".join(str(value).split())
    if len(text) > max_len:
        raise MettaInputError(f"text is longer than {max_len} characters")
    if not text.isprintable():
        raise MettaInputError("text contains control characters")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def number(value, kind) -> str:
    """An int or a finite float literal. None -> None."""
    if value is None:
        return "None"
    if isinstance(value, bool) or kind not in (int, float):
        raise MettaInputError(f"not a number: {value!r}")
    try:
        converted = kind(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MettaInputError(f"not a number: {value!r}") from exc
    if kind is float and not math.isfinite(converted):
        raise MettaInputError(f"not a finite number: {value!r}")
    return repr(converted)


# ---------- checking MeTTa text ----------

def _forms(text: str, allow_comments: bool):
    """Split MeTTa text into top-level forms: yields (start, end, runnable).
    String aware; raises MettaInputError on unbalanced parentheses or strings."""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == ";" and allow_comments:
            while i < n and text[i] != "\n":
                i += 1
            continue
        runnable = ch == "!"
        start = i
        if runnable:
            i += 1
        if i >= n or text[i] != "(":
            # a bare top-level token (not produced by Deal Desk)
            while i < n and not text[i].isspace() and text[i] not in "()":
                i += 1
            yield start, i, runnable
            continue
        depth, in_string = 0, False
        while i < n:
            c = text[i]
            if in_string:
                if c == "\\":
                    i += 1
                elif c == '"':
                    in_string = False
            elif c == '"':
                in_string = True
            elif c == ";" and allow_comments:
                while i < n and text[i] != "\n":
                    i += 1
                continue
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
        else:
            raise MettaInputError("unbalanced parentheses or string")
        yield start, i, runnable


def check_request(expr: str, entry_points=ENTRY_POINTS, max_len: int = MAX_REQUEST) -> str:
    """Accept only ONE parenthesised call to an allowed entry point, built from symbols,
    numbers and escaped strings. Refuses a second expression, `!`, `&space`, `$var`,
    comments, newlines and unknown escapes. Returns expr unchanged."""
    if not isinstance(expr, str) or not expr:
        raise MettaInputError("empty request")
    if len(expr) > max_len:
        raise MettaInputError(f"request is longer than {max_len} characters")
    if expr[0] != "(":
        raise MettaInputError("request must be one parenthesised call")
    depth, in_string, i, n = 0, False, 0, len(expr)
    while i < n:
        c = expr[i]
        if in_string:
            if c == "\\":
                if i + 1 >= n or expr[i + 1] not in '\\"':
                    raise MettaInputError("unknown escape in string")
                i += 2
                continue
            if c == '"':
                in_string = False
            elif not c.isprintable():
                raise MettaInputError("control character in string")
        elif c == '"':
            in_string = True
        elif c not in _PLAIN_CHARS:
            raise MettaInputError(f"character {c!r} is not allowed outside strings")
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0 and i != n - 1:
                raise MettaInputError("only one expression per request")
        i += 1
    if in_string or depth != 0:
        raise MettaInputError("unbalanced parentheses or string")
    head = expr[1:].split(" ", 1)[0].rstrip(")")
    if head not in entry_points:
        raise MettaInputError(f"{head[:40]!r} is not a Deal Desk entry point")
    return expr


def top_level_forms(text: str) -> list[str]:
    """The top-level forms of a .metta file (comments removed), e.g. for listing its facts."""
    return [text[s:e] for s, e, _ in _forms(text, allow_comments=True)]


def defined_functions(text: str) -> set[str]:
    """Names defined by (= (name ...) ...) at the top level of a .metta file."""
    names = set()
    for form in top_level_forms(text):
        m = re.match(r"\(=\s*\(([^\s()]+)", form)
        if m:
            names.add(m.group(1))
    return names


def fact_relations(text: str) -> set[str]:
    """Heads of the top-level facts of a .metta file, e.g. tier-cap for (tier-cap Gold 20.0)."""
    heads = set()
    for form in top_level_forms(text):
        m = re.match(r"\(([^\s()]+)", form)
        if m and m.group(1) not in ("=", ":"):
            heads.add(m.group(1))
    return heads
