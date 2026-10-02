import { useRef, useState } from "react";
import { CircleCheck, CircleX, FileUp, LoaderCircle, Upload } from "lucide-react";
import { api } from "../api.js";
import { CATEGORY_LABELS, formatINR } from "../lib/format.js";

const MAX_BYTES = 1024 * 1024; // same 1 MB limit as the server

const price = (value) => (value == null ? "—" : formatINR(value));

/** CSV import: pick a file -> server-side preview with errors per row -> import valid rows only. */
export default function CsvImport({ onImported }) {
  const fileRef = useRef(null);
  const [fileName, setFileName] = useState(null);
  const [text, setText] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(null);

  function reset() {
    setFileName(null); setText(null); setPreview(null); setError(null);
    if (fileRef.current) fileRef.current.value = "";
  }

  async function onFile(e) {
    const file = e.target.files?.[0];
    setDone(null); setError(null); setPreview(null);
    if (!file) return;
    if (file.size > MAX_BYTES) { setError("The file is larger than 1 MB."); return; }
    setBusy(true);
    try {
      let content;
      try {
        // strict: invalid UTF-8 is an error, not silently replaced characters
        content = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      } catch {
        setError("The file is not UTF-8 text. Save it as CSV UTF-8 and try again.");
        return;
      }
      setFileName(file.name);
      setText(content);
      setPreview(await api.previewProductCsv(content));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function doImport() {
    setBusy(true); setError(null);
    try {
      const result = await api.importProductCsv(text);
      setDone(result);
      reset();
      onImported?.(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const fileErrors = preview?.file_errors ?? [];
  const canImport = preview && fileErrors.length === 0 && preview.valid > 0 && !busy;

  return (
    <section className="card" aria-labelledby="csv-title">
      <div className="card-head">
        <h2 className="card-title" id="csv-title"><FileUp size={17} aria-hidden="true" />Import products from CSV</h2>
        <span className="label">Columns: name, category, cost_price, list_price</span>
      </div>

      <div className="csv-picker">
        <input ref={fileRef} id="csv-file" type="file" accept=".csv,text/csv" className="sr-only"
               onChange={onFile} disabled={busy} />
        <label htmlFor="csv-file" className="btn" aria-disabled={busy}>
          {busy && !preview
            ? <LoaderCircle size={16} className="spin" aria-hidden="true" />
            : <Upload size={16} aria-hidden="true" />}
          Choose CSV file
        </label>
        <span className="muted csv-file-name">{fileName ?? "No file chosen (UTF-8, max 1 MB, 500 rows)"}</span>
      </div>

      {error && <div className="error" role="alert" style={{ marginTop: 12 }}>{error}</div>}

      {done && (
        <div className="success" role="status" style={{ marginTop: 12 }}>
          Imported <b>{done.imported}</b> product{done.imported === 1 ? "" : "s"}
          {done.invalid > 0 && <> · skipped {done.invalid} row{done.invalid === 1 ? "" : "s"} with errors</>}.
          They are now in the Deal check product list.
        </div>
      )}

      {preview && (
        <div className="csv-preview">
          {fileErrors.length > 0
            ? fileErrors.map((msg) => <div key={msg} className="error" role="alert">{msg}</div>)
            : (
              <>
                <div className="csv-summary" role="status">
                  <span className="csv-count ok"><CircleCheck size={15} aria-hidden="true" />{preview.valid} valid</span>
                  <span className={`csv-count ${preview.invalid ? "bad" : ""}`}>
                    <CircleX size={15} aria-hidden="true" />{preview.invalid} with errors
                  </span>
                  <span className="muted">Rows with errors are shown here and will not be imported.</span>
                </div>
                <div className="table-wrap csv-table">
                  <table className="table">
                    <thead>
                      <tr><th className="num">Line</th><th>Name</th><th>Category</th><th className="num">Cost</th>
                        <th className="num">List</th><th>Status</th></tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((r) => {
                        const ok = r.errors.length === 0;
                        return (
                          <tr key={r.line} className={ok ? "" : "csv-row-bad"} style={{ cursor: "default" }}>
                            <td className="num muted">{r.line}</td>
                            <td>{r.name || <span className="muted">(empty)</span>}</td>
                            <td>
                              {CATEGORY_LABELS[r.category]
                                ? <span className={`cat-badge cat-${r.category}`}>{CATEGORY_LABELS[r.category]}</span>
                                : <span className="muted">{r.category}</span>}
                            </td>
                            <td className="num">{price(r.cost_price)}</td>
                            <td className="num">{price(r.list_price)}</td>
                            <td className="csv-status">
                              {ok
                                ? <span className="csv-ok"><CircleCheck size={14} aria-hidden="true" />Ready</span>
                                : (
                                  <ul className="csv-errors">
                                    {r.errors.map((msg) => (
                                      <li key={msg}><CircleX size={14} aria-hidden="true" />{msg}</li>
                                    ))}
                                  </ul>
                                )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}

          <div className="csv-actions">
            <button type="button" className="btn btn-ghost" onClick={reset} disabled={busy}>Cancel</button>
            <button type="button" className="btn btn-primary" onClick={doImport} disabled={!canImport}>
              {busy ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <Upload size={16} aria-hidden="true" />}
              Import {preview.valid} valid row{preview.valid === 1 ? "" : "s"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
