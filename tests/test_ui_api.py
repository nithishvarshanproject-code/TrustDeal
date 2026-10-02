"""Endpoints added for the frontend: seller/product lists and engine info."""
import pytest


def test_list_sellers_with_trust(stack):
    sellers = stack["client"].get("/sellers").json()
    assert [s["name"] for s in sellers][:2] == ["Aurora Traders", "Nova Startups"]
    blank = next(s for s in sellers if s["name"] == "Blank Slate Co")
    assert blank["trust"] == {"strength": 0.5, "confidence": 0.0}
    aurora = sellers[0]
    assert aurora["trust"]["confidence"] == pytest.approx(0.8)


def test_list_products(stack):
    products = stack["client"].get("/products").json()
    # product 1 (used by the 6 test deals) is unchanged, now with its category
    assert products[0] == {"product_id": 1, "name": "Industrial Widget", "category": "general",
                           "cost_price": 70.0, "list_price": 100.0}
    # the seeded INR catalog: 2 mobiles, 2 laptops, 4 accessories, then product 10 (a cheaper
    # phone added for the agent's "cheaper model" alternative)
    categories = [p["category"] for p in products[1:]]
    assert categories == ["mobiles"] * 2 + ["laptops"] * 2 + ["accessories"] * 4 + ["mobiles"]


def test_evaluate_reports_engine_and_time(stack):
    client = stack["client"]
    body = client.post("/deals/evaluate", json={"seller_id": 1, "product_id": 1, "quantity": 150,
                                                "discount_requested": 10.0}).json()
    assert body["engine"]["runner"] == "local-hyperon"
    assert 0 < body["engine"]["elapsed_s"] < 30
    assert client.get(f"/deals/{body['deal_id']}").json()["decision"]["engine"] == body["engine"]
