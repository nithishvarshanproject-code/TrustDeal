"""CSV product import: preview (no writes) and import of valid rows only."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = "name,category,cost_price,list_price\n"


def preview(client, text):
    resp = client.post("/products/import/preview", json={"csv": text})
    assert resp.status_code == 200, resp.text
    return resp.json()


def do_import(client, text):
    resp = client.post("/products/import", json={"csv": text})
    assert resp.status_code == 200, resp.text
    return resp.json()


def names(client):
    return [p["name"] for p in client.get("/products").json()]


def test_valid_file_imports_every_row(stack):
    client = stack["client"]
    text = HEADER + "Tablet 10,general,9000,12999\nUSB Hub,accessories,300,899\n"
    before = len(names(client))
    result = do_import(client, text)
    assert (result["valid"], result["invalid"], result["imported"]) == (2, 0, 2)
    assert names(client)[-2:] == ["Tablet 10", "USB Hub"]
    assert len(names(client)) == before + 2


def test_invalid_file_imports_nothing_and_explains_each_row(stack):
    client = stack["client"]
    text = HEADER + "\n".join([
        ",mobiles,100,200",                      # line 2: empty name
        "Sofa,furniture,100,200",                # line 3: unknown category
        "Cheap Phone,mobiles,500,500",           # line 4: list == cost
        "Broken Price,laptops,abc,1000",         # line 5: not a number
        "Negative,accessories,-5,10",            # line 6: not positive
        "Zero List,accessories,5,0",             # line 7: list not positive
    ]) + "\n"
    before = names(client)
    result = do_import(client, text)
    assert (result["valid"], result["invalid"], result["imported"]) == (0, 6, 0)
    errors = {r["line"]: r["errors"] for r in result["rows"]}
    assert errors[2] == ["name is empty"]
    assert errors[3] == ["unknown category 'furniture' (use mobiles, laptops, accessories, general)"]
    assert errors[4] == ["list_price must be higher than cost_price"]
    assert errors[5] == ["cost_price is not a number"]
    assert errors[6] == ["cost_price must be positive"]
    assert errors[7] == ["list_price must be positive"]
    assert names(client) == before


def test_mixed_file_imports_only_the_valid_rows(stack):
    client = stack["client"]
    text = HEADER + "\n".join([
        "Good Phone,mobiles,10000,12500",
        "Bad Phone,mobiles,12500,10000",          # list < cost
        "Good Case,,50,199",                      # blank category -> general
        "Good Phone,mobiles,10000,12500",         # duplicate in the file
        'Big Laptop,laptops,"₹1,00,000","₹1,25,000"',  # formatted prices are fine
    ]) + "\n"
    shown = preview(client, text)
    assert (shown["valid"], shown["invalid"]) == (3, 2)
    assert "Good Phone" not in names(client)                     # preview writes nothing

    result = do_import(client, text)
    assert result["imported"] == 3
    imported = {p["name"]: p for p in result["products"]}
    assert set(imported) == {"Good Phone", "Good Case", "Big Laptop"}
    assert imported["Good Case"]["category"] == "general"
    assert (imported["Big Laptop"]["cost_price"], imported["Big Laptop"]["list_price"]) == (100000.0, 125000.0)
    bad = {r["line"]: r["errors"] for r in result["rows"] if r["errors"]}
    assert bad == {3: ["list_price must be higher than cost_price"], 5: ["duplicate name in this file"]}


def test_existing_names_are_not_imported_twice(stack):
    client = stack["client"]
    text = HEADER + "Wireless Earbuds,accessories,1200,2999\n"     # already in the seed catalog
    result = do_import(client, text)
    assert result["imported"] == 0
    assert result["rows"][0]["errors"] == ["a product with this name already exists"]


def test_file_level_errors(stack):
    client = stack["client"]
    assert preview(client, "")["file_errors"] == ["The file is empty."]
    missing = preview(client, "name,cost_price\nX,1\n")["file_errors"][0]
    assert missing.startswith("Missing column(s): category, list_price")
    assert preview(client, HEADER)["file_errors"] == ["The file has a header but no product rows."]
    # Excel BOM, different column order, extra column and blank lines are all fine
    ok = preview(client, "﻿LIST_PRICE,name,notes,category,cost_price\n\n999,Desk Lamp,x,general,400\n\n")
    assert (ok["file_errors"], ok["valid"], ok["rows"][0]["name"]) == ([], 1, "Desk Lamp")


def test_sample_file_is_valid(stack):
    text = (ROOT / "samples" / "products_sample.csv").read_text(encoding="utf-8")
    result = preview(stack["client"], text)
    assert (result["file_errors"], result["valid"], result["invalid"]) == ([], 10, 0)
    assert {r["category"] for r in result["rows"]} == {"mobiles", "laptops", "accessories", "general"}
