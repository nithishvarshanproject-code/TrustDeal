import { useEffect, useRef } from "react";
import { LoaderCircle, TriangleAlert, X } from "lucide-react";

/** In-page confirmation dialog (Escape or Cancel closes it). */
export default function ConfirmModal({ title, children, confirmLabel, busy, error, onConfirm, onClose }) {
  const cancelRef = useRef(null);
  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="card modal form" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
        <div className="card-head" style={{ marginBottom: 0 }}>
          <h3 className="card-title" id="confirm-title">
            <TriangleAlert size={17} style={{ color: "var(--counter)" }} aria-hidden="true" />{title}
          </h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy} aria-label="Close">
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <div className="muted" style={{ fontSize: 13.5 }}>{children}</div>
        {error && <div className="error">{error}</div>}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button ref={cancelRef} type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="button" className="btn btn-bad" onClick={onConfirm} disabled={busy}>
            {busy && <LoaderCircle size={16} className="spin" aria-hidden="true" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
