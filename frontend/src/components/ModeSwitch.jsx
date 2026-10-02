import { Store, Users } from "lucide-react";

/** [Customer | Seller] switch shown on both sides of the app. */
export default function ModeSwitch({ mode, onSwitch }) {
  return (
    <div className="mode-switch" role="group" aria-label="View">
      <button className={mode === "customer" ? "active" : ""} aria-pressed={mode === "customer"}
              onClick={() => onSwitch("customer")}>
        <Store size={14} aria-hidden="true" /> Customer
      </button>
      <button className={mode === "seller" ? "active" : ""} aria-pressed={mode === "seller"}
              onClick={() => onSwitch("seller")}>
        <Users size={14} aria-hidden="true" /> Seller
      </button>
    </div>
  );
}
