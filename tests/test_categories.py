"""Category-aware products: each category has its own margin floor and max discount (policy.metta).
All expected results come from the MeTTa engine."""
import pytest
from sqlalchemy import create_engine, inspect, text

from backend.database import ensure_schema
from engine.bridge import category_profile
from engine.policy_admin import PolicyChangeError, apply_policy_proposal

AURORA, MERIDIAN = 1, 3                      # Gold / Silver sellers (clean payment history)
SMARTPHONE_A, LAPTOP_14, LAPTOP_PRO, EARBUDS = 2, 4, 5, 7


def evaluate(client, seller, product, discount, quantity=20, claimed=None):
    resp = client.post("/deals/evaluate", json={
        "seller_id": seller, "product_id": product, "quantity": quantity,
        "discount_requested": discount, "claimed_tier": claimed})
    assert resp.status_code == 200, resp.text
    return resp.json()


def line(body, rule_id):
    return next(e for e in body["trail"] if e["rule_id"] == rule_id)


def add_product(client, name, category, cost, list_price):
    resp = client.post("/products", json={"name": name, "category": category,
                                          "cost_price": cost, "list_price": list_price})
    assert resp.status_code == 201, resp.text
    return resp.json()["product_id"]


# ---------- category facts ----------

@pytest.mark.parametrize("category, floor, cat_max", [
    ("mobiles", 8.0, 12.0), ("laptops", 10.0, 15.0), ("accessories", 30.0, 40.0), ("general", 15.0, 100.0)])
def test_category_floors_and_maxes(category, floor, cat_max):
    assert category_profile(category) == {"category": category, "margin_floor": floor, "category_max": cat_max}


def test_missing_category_means_general():
    assert category_profile(None)["category"] == "general"


# ---------- same request, different category ----------

def test_same_15_percent_phone_vs_earbuds(stack):
    client = stack["client"]
    phone = evaluate(client, AURORA, SMARTPHONE_A, 15.0)
    earbuds = evaluate(client, AURORA, EARBUDS, 15.0)

    assert (phone["result"], phone["approved_discount"]) == ("COUNTER", 12.0)
    assert "category max 12.0" in line(phone, "ALLOWED")["check"]          # the category max is the limit
    assert line(phone, "R1")["check"].endswith("< 8.0% (max discount 13.0%)")
    assert phone["category"] == {"category": "mobiles", "margin_floor": 8.0, "category_max": 12.0}

    assert (earbuds["result"], earbuds["approved_discount"]) == ("APPROVE", 15.0)
    assert "category max" not in line(earbuds, "ALLOWED")["check"]
    assert earbuds["category"]["margin_floor"] == 30.0


def test_laptop_below_cost_is_rejected(stack):
    body = evaluate(stack["client"], MERIDIAN, LAPTOP_14, 22.0, quantity=5)
    assert body["result"] == "REJECT" and body["approved_discount"] is None
    assert line(body, "DECISION")["check"] == "step 1: price 42900.0 < cost 44000.0 -> REJECT"
    assert line(body, "R1")["status"] == "fail"


def test_category_floor_changes_the_margin_limit(stack):
    # Laptop Pro 16 (85,000 / 110,000): 10% laptop floor -> max discount 14.1%
    # (a 15% general floor would give ~9.1%). At 13% off: price 95,700, margin 11.2%.
    body = evaluate(stack["client"], MERIDIAN, LAPTOP_PRO, 13.0)
    assert line(body, "R1")["check"] == "margin at 13.0% off is 11.2% >= 10.0% (max discount 14.1%)"
    assert (body["result"], body["approved_discount"]) == ("COUNTER", 12.0)   # Silver 10 + R3 2


def test_category_max_limits_a_high_margin_laptop(stack):
    client = stack["client"]
    refurb = add_product(client, "Refurbished Laptop", "laptops", 30000.0, 50000.0)   # margin limit 33.3%
    body = evaluate(client, AURORA, refurb, 20.0)
    assert (body["result"], body["approved_discount"]) == ("COUNTER", 15.0)
    assert line(body, "ALLOWED")["check"] == \
        "min(max(cap 20.0 + R3 2.0 + R4 0.0 = 22.0, competitor 0.0), margin limit 33.3, category max 15.0) = 15.0"


def test_no_discount_room_counters_at_zero(stack):
    client = stack["client"]
    thin = add_product(client, "Thin-margin Phone", "mobiles", 19000.0, 20000.0)   # 5% margin at list < 8%
    body = evaluate(client, AURORA, thin, 5.0)
    assert (body["result"], body["approved_discount"]) == ("COUNTER", 0.0)
    assert "no discount room in this category" in line(body, "DECISION")["check"]
    assert line(body, "DECISION")["check"] == \
        "step 4: request 5.0% but no discount room in this category -> COUNTER at 0.0% (list price)"
    assert line(body, "ALLOWED")["check"].endswith("= 0.0 (below 0: no discount room in this category)")
    assert line(body, "ALLOWED")["value"] == 0.0


def test_decision_detail_keeps_the_category(stack):
    client = stack["client"]
    body = evaluate(client, AURORA, SMARTPHONE_A, 15.0)
    stored = client.get(f"/deals/{body['deal_id']}").json()["decision"]
    assert stored["category"] == body["category"]


# ---------- D2 never touches category facts ----------

def _counter_then_override(client, seller, product, discount, n=3):
    for i in range(n):
        body = evaluate(client, seller, product, discount)
        assert body["result"] == "COUNTER"
        client.post(f"/deals/{body['deal_id']}/override", json={
            "reviewer": "Sales lead", "new_result": "APPROVE", "reason": f"override {i}"})


def test_d2_does_not_learn_from_category_limited_overrides(stack):
    client = stack["client"]
    # phones countered at the category max: a tier-cap change cannot fix them -> no proposal at all
    _counter_then_override(client, AURORA, SMARTPHONE_A, 15.0)
    assert client.get("/policy/proposals").json() == []


def test_d2_only_ever_proposes_tier_caps(stack):
    client = stack["client"]
    # Silver on Laptop Pro at 13%: tier cap is the limit (12 < margin 14.1 < category 15)
    _counter_then_override(client, MERIDIAN, LAPTOP_PRO, 13.0)
    proposals = client.get("/policy/proposals").json()
    assert proposals and all(p["fact"] == "tier-cap" for p in proposals)
    assert (proposals[0]["key"], proposals[0]["old_value"], proposals[0]["new_value"]) == ("Silver", 10.0, 11.0)


@pytest.mark.parametrize("fact, key", [("margin-floor-for", "mobiles"), ("category-max", "accessories"),
                                       ("margin-floor", "x")])
def test_category_facts_can_never_be_rewritten(stack, fact, key):
    with pytest.raises(PolicyChangeError):
        apply_policy_proposal({"fact": fact, "key": key, "old_value": 8.0, "new_value": 5.0},
                              approved_by="someone", engine_dir=stack["engine_dir"])
    resp = stack["client"].post("/policy/proposals/apply", json={
        "fact": fact, "key": key, "old_value": 8.0, "new_value": 5.0, "approved_by": "someone"})
    assert resp.status_code == 409


# ---------- products: migration and validation ----------

def test_migration_adds_category_without_losing_data(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:   # a products table from before categories existed
        conn.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, "
                          "cost_price FLOAT NOT NULL, list_price FLOAT NOT NULL)"))
        conn.execute(text("INSERT INTO products VALUES (1, 'Industrial Widget', 70.0, 100.0)"))
    ensure_schema(engine)
    ensure_schema(engine)          # idempotent
    assert "category" in {c["name"] for c in inspect(engine).get_columns("products")}
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id, name, cost_price, list_price, category FROM products")).one()
    assert tuple(row) == (1, "Industrial Widget", 70.0, 100.0, "general")
    engine.dispose()


def test_add_product_validation(stack):
    client = stack["client"]
    bad = [
        {"name": "Cheap", "category": "mobiles", "cost_price": 100.0, "list_price": 100.0},    # list == cost
        {"name": "Loss", "category": "mobiles", "cost_price": 120.0, "list_price": 100.0},     # list < cost
        {"name": "Odd", "category": "furniture", "cost_price": 10.0, "list_price": 20.0},      # unknown category
        {"name": "  ", "category": "general", "cost_price": 10.0, "list_price": 20.0},         # blank name
    ]
    for body in bad:
        assert client.post("/products", json=body).status_code == 422, body
    new_id = add_product(client, "USB-C Cable", "accessories", 90.0, 299.0)
    assert any(p["product_id"] == new_id and p["category"] == "accessories"
               for p in client.get("/products").json())
