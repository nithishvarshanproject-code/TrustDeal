// Display helpers (no business logic).
const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

export const formatINR = (value) => INR.format(value);

// Prices shown to customers keep paise when there are any (₹2,099.30), like the agent's messages.
const INR_PAISE = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2,
                                                   maximumFractionDigits: 2 });
export const formatPrice = (value) =>
  (Math.round(value * 100) % 100 === 0 ? INR.format(value) : INR_PAISE.format(value));
export const formatPct = (value) => `${Number(value.toFixed(1))}%`;

/** Format a UTC quote expiry in India Standard Time, matching the quote PDF. */
export function formatQuoteValidity(value) {
  const iso = value.endsWith("Z") || value.includes("+") ? value : `${value}Z`;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const part = (type) => parts.find((item) => item.type === type)?.value;
  return `${part("day")} ${part("month")} ${part("year")}, ${part("hour")}:${part("minute")} IST`;
}

/** Any stored UTC timestamp, the app's way: "03 Oct 2026, 09:46 IST" (whatever the browser's locale). */
export const formatISTDateTime = (value) => formatQuoteValidity(value);

/** Format stored UTC timestamps as India Standard Time, regardless of the browser's locale. */
export function formatISTTime(value) {
  const iso = value.endsWith("Z") || value.includes("+") ? value : `${value}Z`;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const part = (type) => parts.find((item) => item.type === type)?.value;
  return `${part("hour")}:${part("minute")} IST`;
}

/** Format the daily summary's already-local timestamp using the same fixed display style. */
export function formatISTSummaryDateTime(value) {
  if (!value) return value;
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!match) return value;
  const [, year, month, day, hour, minute] = match;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  const monthName = new Intl.DateTimeFormat("en-GB", { month: "short", timeZone: "UTC" }).format(date);
  return `${day} ${monthName} ${year}, ${hour}:${minute} IST`;
}

export const CATEGORY_LABELS = {
  mobiles: "Mobiles",
  laptops: "Laptops",
  accessories: "Accessories",
  general: "General",
};

export const CATEGORY_ORDER = ["mobiles", "laptops", "accessories", "general"];

/** Group products by category, in CATEGORY_ORDER, keeping each group's order. */
export function groupByCategory(products) {
  return CATEGORY_ORDER
    .map((category) => ({ category, items: products.filter((p) => (p.category || "general") === category) }))
    .filter((group) => group.items.length > 0);
}
