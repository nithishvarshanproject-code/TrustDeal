import { useEffect, useState } from "react";
import Sidebar, { NAV } from "./components/Sidebar.jsx";
import AgentInbox from "./pages/AgentInbox.jsx";
import Customer from "./pages/Customer.jsx";
import DealDesk from "./pages/DealDesk.jsx";
import Decisions from "./pages/Decisions.jsx";
import Policy from "./pages/Policy.jsx";
import Products from "./pages/Products.jsx";
import Overview from "./pages/Overview.jsx";
import Sellers from "./pages/Sellers.jsx";
import VerifyQuote from "./pages/VerifyQuote.jsx";

const PAGES = { overview: Overview, desk: DealDesk, decisions: Decisions, agent: AgentInbox, policy: Policy, sellers: Sellers,
                products: Products };

export default function App() {
  const [page, setPage] = useState(() => {
    const fromHash = window.location.hash.slice(1).split("?")[0];   // #verify?ref=...&code=...
    return PAGES[fromHash] || fromHash === "customer" || fromHash === "verify" ? fromHash : "desk";
  });

  function select(key) {
    setPage(key);
    window.history.replaceState(null, "", `#${key}`);
  }

  const switchMode = (mode) => select(mode === "customer" ? "customer" : "desk");

  useEffect(() => {
    const label = page === "customer" ? "BASIX Store" : page === "verify" ? "Verify quote"
      : NAV.find((n) => n.key === page)?.label;
    document.title = label ? `${label} · TrustDeal` : "TrustDeal";
  }, [page]);

  // The customer side is its own shop layout, without the seller's navigation.
  if (page === "customer") return <Customer onSwitchMode={switchMode} />;
  // Public, customer-safe quote check (QR code on the quote PDF).
  if (page === "verify") return <VerifyQuote />;

  const Page = PAGES[page];
  return (
    <div className="shell">
      <Sidebar current={page} onSelect={select} onSwitchMode={switchMode} />
      <main className="main">
        <Page onNavigate={select} />
      </main>
    </div>
  );
}
