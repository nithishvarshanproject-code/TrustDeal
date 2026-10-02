"""Input limits shared by the API models (data validation only; no business rules).
The tier caps, category floors and every other rule stay in policy.metta."""

MAX_QUANTITY = 100_000
MAX_NAME = 80        # product names, reviewers, approvers
MAX_REASON = 500     # override reasons (also the MeTTa string limit in engine/metta_safe.py)

TIERS = ("Gold", "Silver", "New")
_TIER_BY_KEY = {t.casefold(): t for t in TIERS}


def normalize_tier(value: str | None) -> str | None:
    """Any case -> Gold / Silver / New; blank -> None; anything else is refused."""
    if value is None or not value.strip():
        return None
    tier = _TIER_BY_KEY.get(value.strip().casefold())
    if tier is None:
        raise ValueError(f"claimed_tier must be one of {', '.join(TIERS)}")
    return tier
