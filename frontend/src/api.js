// Thin wrapper around the FastAPI backend (proxied at /api by Vite).
async function request(method, path, body) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ")
      : detail || `${res.status} ${res.statusText}`;
    throw Object.assign(new Error(message), { status: res.status, detail });   // pages may map field errors
  }
  return data;
}

export const api = {
  overviewStats: () => request("GET", "/stats/overview"),
  sellers: () => request("GET", "/sellers"),
  products: () => request("GET", "/products"),
  addProduct: (product) => request("POST", "/products", product),
  previewProductCsv: (csv) => request("POST", "/products/import/preview", { csv }),
  importProductCsv: (csv) => request("POST", "/products/import", { csv }),
  engine: () => request("GET", "/engine"),
  omegaStatus: () => request("GET", "/omega/status"),
  resetDemo: () => request("POST", "/demo/reset", { confirm: "RESET" }),
  evaluate: (deal) => request("POST", "/deals/evaluate", deal),
  deal: (id) => request("GET", `/deals/${id}`),
  history: () => request("GET", "/deals/history"),
  whatIf: (id) => request("GET", `/deals/${id}/what-if`),
  override: (id, body) => request("POST", `/deals/${id}/override`, body),
  outcome: (id, paidOnTime) => request("POST", `/deals/${id}/outcome`, { paid_on_time: paidOnTime }),
  proposals: () => request("GET", "/policy/proposals"),
  applyProposal: (body) => request("POST", "/policy/proposals/apply", body),
  policyHistory: () => request("GET", "/policy/history"),
  policyCurrent: () => request("GET", "/policy/current"),
  ledger: (limit = 25) => request("GET", `/seller/ledger?limit=${limit}`),
  verifyLedger: () => request("POST", "/seller/ledger/verify"),

  // Customer page (customer-safe endpoints only)
  customers: () => request("GET", "/customer/customers"),
  customerMe: (customerId) => request("GET", `/customer/me?customer_id=${customerId}`),
  customerCatalog: () => request("GET", "/customer/catalog"),
  myRequests: (customerId) => request("GET", `/customer/requests?customer_id=${customerId}`),
  customerRequest: (id, customerId) => request("GET", `/customer/requests/${id}?customer_id=${customerId}`),
  customerMessage: (body) => request("POST", "/customer/messages", body),
  verifyQuote: (ref, code) => request("POST", "/verify/quote", { ref, code }),
  customerAction: (id, action, body) => request("POST", `/customer/requests/${id}/${action}`, body),
  quotePdfUrl: (id, customerId) => `/api/customer/requests/${id}/quote.pdf?customer_id=${customerId}`,
  invoicePdfUrl: (id, customerId) => `/api/customer/requests/${id}/invoice.pdf?customer_id=${customerId}`,
  customerTelegram: (customerId) => request("GET", `/customer/telegram?customer_id=${customerId}`),
  customerTelegramCode: (customerId) => request("POST", "/customer/telegram/code", { customer_id: customerId }),

  // Seller: agent inbox
  agentInbox: () => request("GET", "/seller/agent/deals"),
  agentDeal: (id) => request("GET", `/seller/agent/deals/${id}`),
  agentTasks: (status = "open") => request("GET", `/seller/agent/tasks?status=${status}`),
  resolveTask: (id, body) => request("POST", `/seller/agent/tasks/${id}/resolve`, body),
  approveMessage: (id, body) => request("POST", `/seller/agent/messages/${id}/approve`, body),
  agentSettings: () => request("GET", "/seller/agent/settings"),
  setAgentSettings: (body) => request("POST", "/seller/agent/settings", body),
  sellerTelegram: () => request("GET", "/seller/agent/telegram"),
  sellerTelegramCode: () => request("POST", "/seller/agent/telegram/code"),
  dailySummary: () => request("GET", "/seller/agent/daily-summary"),
  sendDailySummary: () => request("POST", "/seller/agent/daily-summary/send-now"),
  runFollowups: () => request("POST", "/seller/agent/followups/run"),

  // Seller: "Ask why" about one stored decision (source "deal" | "agent"); read-only
  askWhy: (decisionId, body) => request("POST", `/seller/decisions/${decisionId}/ask`, body),
};
