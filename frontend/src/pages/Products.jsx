import { useCallback, useEffect, useState } from "react";
import { LoaderCircle, Package, Plus } from "lucide-react";
import { api } from "../api.js";
import CsvImport from "../components/CsvImport.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { Skeleton } from "../components/Skeleton.jsx";
import { LOAD_FAILED, loadState } from "../lib/loadState.js";
import { CATEGORY_LABELS, CATEGORY_ORDER, formatINR, groupByCategory } from "../lib/format.js";

const EMPTY = { name: "", category: "mobiles", cost_price: "", list_price: "" };

function AddProductForm({ onAdded }) {
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  const cost = Number(form.cost_price);
  const list = Number(form.list_price);
  const priceError = form.cost_price !== "" && form.list_price !== "" && !(list > cost)
    ? "List price must be higher than cost price." : null;

  async function submit(e) {
    e.preventDefault();
    if (priceError) return;
    setBusy(true);
    setError(null);
    try {
      const added = await api.addProduct({ ...form, cost_price: cost, list_price: list });
      setForm(EMPTY);
      onAdded(added);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit} aria-labelledby="add-title">
      <div className="card-head">
        <h2 className="card-title" id="add-title"><Plus size={17} aria-hidden="true" />Add a product</h2>
        <span className="label">Category rules come from policy.metta</span>
      </div>
      <div className="product-form">
        <label className="field">
          <span className="label">Name</span>
          <input className="input" required maxLength={80} value={form.name} onChange={set("name")}
                 placeholder="e.g. USB-C Cable 1m" />
        </label>
        <label className="field">
          <span className="label">Category</span>
          <select className="select" value={form.category} onChange={set("category")}>
            {CATEGORY_ORDER.map((c) => <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>)}
          </select>
        </label>
        <label className="field">
          <span className="label">Cost (₹)</span>
          <input className="input" type="number" min="1" step="1" required value={form.cost_price}
                 onChange={set("cost_price")} aria-invalid={Boolean(priceError)} />
        </label>
        <label className="field">
          <span className="label">List (₹)</span>
          <input className="input" type="number" min="1" step="1" required value={form.list_price}
                 onChange={set("list_price")} aria-invalid={Boolean(priceError)} />
        </label>
        <button className="btn btn-primary" disabled={busy || Boolean(priceError)} style={{ height: 42 }}>
          {busy ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <Plus size={16} aria-hidden="true" />}
          Add
        </button>
      </div>
      {(priceError || error) && <div className="error" style={{ marginTop: 12 }}>{priceError || error}</div>}
    </form>
  );
}

export default function Products() {
  const [products, setProducts] = useState(null);
  const [error, setError] = useState(null);
  const [added, setAdded] = useState(null);

  const load = useCallback(() => {
    api.products().then(setProducts).catch((err) => setError(err.message));
  }, []);
  useEffect(load, [load]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Products</h1>
          <p className="page-sub">Each category has its own margin floor and maximum discount, applied by MeTTa.</p>
        </div>
      </header>

      <AddProductForm onAdded={(p) => { setAdded(p); load(); }} />
      {added && (
        <div className="success" role="status">
          Added <b>{added.name}</b> to {CATEGORY_LABELS[added.category]}. It is now in the Deal check product list.
        </div>
      )}
      <CsvImport onImported={() => { setAdded(null); load(); }} />
      {error && <div className="error" role="alert">{error}</div>}

      <section className="card" aria-label="Catalog" style={{ padding: 12 }}>
        {products == null
          ? (loadState(products, error) === "failed" ? <p className="muted" style={{ margin: 0 }}>{LOAD_FAILED}</p>
            : Array.from({ length: 6 }, (_, i) => <Skeleton key={i} height={40} style={{ margin: "8px 0" }} />))
          : products.length === 0
            ? <EmptyState icon={Package} title="No products yet">Add one above, or run start.bat reset.</EmptyState>
            : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr><th>Product</th><th>Category</th><th className="num">Cost</th>
                      <th className="num">List</th><th className="num">Margin at list</th></tr>
                  </thead>
                  <tbody>
                    {groupByCategory(products).flatMap(({ items }) => items).map((p) => (
                      <tr key={p.product_id} style={{ cursor: "default" }}>
                        <td>{p.name}</td>
                        <td><span className={`cat-badge cat-${p.category}`}>{CATEGORY_LABELS[p.category]}</span></td>
                        <td className="num">{formatINR(p.cost_price)}</td>
                        <td className="num">{formatINR(p.list_price)}</td>
                        <td className="num">{(((p.list_price - p.cost_price) / p.list_price) * 100).toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
      </section>
    </div>
  );
}
