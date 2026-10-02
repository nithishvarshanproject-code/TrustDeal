def parse_deal_text(raw_text: str) -> dict:
    """Use the LLM to turn a messy seller request into deal fields.

    Returns a dict with keys: seller_id, product_id, quantity, discount_requested,
    claimed_tier, competitor_price. Unclear fields stay None. competitor_verified is
    never taken from free text. Extraction only; the LLM never decides anything.

    TODO: implement once an LLM API key is available (read LLM_API_KEY from .env;
    raise a clear "parser unavailable" error if missing). Until then,
    POST /deals/evaluate answers raw_text requests with 501.
    """
    raise NotImplementedError
