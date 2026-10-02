"""Turn the MeTTa trail into numbered plain-English lines (fixed templates, no LLM).

One line per trail entry, same order, same rule ID, same wording from MeTTa.
Nothing is added, removed or changed.
"""

_STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "warn": "WARNING", "skip": "SKIPPED"}


def explain_trail(trail: list[dict]) -> list[str]:
    return [
        f"{n}. [{e['rule_id']}] {_STATUS_LABEL.get(e['status'], e['status'].upper())}: {e['check']}"
        for n, e in enumerate(trail, start=1)
    ]


def explain_override(hint: dict | None) -> str | None:
    if hint is None:
        return None
    return (
        f"If the seller really is {hint['claimed_tier']}, the allowed max would be "
        f"{hint['allowed_max']}%; verify the tier before overriding."
    )


_WHAT_IF = {
    "verify-tier": "Verify the claimed {detail} tier: allowed max becomes {allowed_max}%.",
    "verify-competitor": "Verify the competitor quote of {detail}: allowed max becomes {allowed_max}%.",
    "raise-quantity": "Order at least {detail} units: allowed max becomes {allowed_max}%.",
    "lower-discount": "Request {detail}% instead: approvable as is.",
}


def explain_what_if(options: list[dict]) -> list[str]:
    return [_WHAT_IF[o["change"]].format(**o) for o in options]


def explain(trail: list[dict], override_hint: dict | None, what_if: list[dict] | None = None) -> dict:
    return {
        "lines": explain_trail(trail),
        "override": explain_override(override_hint),
        "what_if": explain_what_if(what_if or []),
    }
