export const FALLBACK_PRODUCT_IMAGE = "/products/generic.svg";

const RULES = [
  [/industrial|widget/, "industrial-widget.svg"],
  [/smartphone|phone/, "phone.svg"],
  [/laptop/, "laptop.svg"],
  [/charger|adapter/, "charger.svg"],
  [/earbud|headphone/, "earbuds.svg"],
  [/case|cover/, "phone-case.svg"],
  [/sleeve|laptop bag/, "laptop-sleeve.svg"],
];

export function productImage(product) {
  const name = String(product?.name ?? "").toLowerCase();
  const category = String(product?.category ?? "").toLowerCase();
  const file = RULES.find(([pattern]) => pattern.test(name))?.[1]
    ?? (category === "mobiles" ? "phone.svg" : category === "laptops" ? "laptop.svg" : null);
  return `/products/${file ?? FALLBACK_PRODUCT_IMAGE.split("/").at(-1)}`;
}
