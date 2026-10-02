import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { FALLBACK_PRODUCT_IMAGE, productImage } from "../src/lib/productImages.js";

const publicDir = resolve("public");
const catalog = [
  ["Industrial Widget", "general"], ["Smartphone A 128GB", "mobiles"],
  ["Smartphone Pro 256GB", "mobiles"], ["Laptop 14 i5 16GB", "laptops"],
  ["Laptop Pro 16", "laptops"], ["Fast Charger 65W", "accessories"],
  ["Wireless Earbuds", "accessories"], ["Phone Case", "accessories"],
  ["Laptop Sleeve", "accessories"], ["Smartphone Lite 64GB", "mobiles"],
];

test("every current catalog product maps to an existing original illustration", () => {
  for (const [name, category] of catalog) {
    const image = productImage({ name, category });
    assert.ok(existsSync(resolve(publicDir, `.${image}`)), `${name} -> ${image}`);
  }
});

test("unknown and newly imported products use the existing fallback", () => {
  const image = productImage({ name: "Unlisted CSV item", category: "general" });
  assert.equal(image, FALLBACK_PRODUCT_IMAGE);
  assert.ok(existsSync(resolve(publicDir, `.${image}`)));
});
