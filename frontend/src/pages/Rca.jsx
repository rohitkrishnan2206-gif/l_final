import { Fragment, useState } from "react";
import { api, download } from "../api.js";
import { Card, Empty, ErrorBox, Field, fmtTime, nav, SevBadge, Spinner, useLoad } from "../ui.jsx";

const PAGE = 25;
const PERIODS = [[0, "All time"], [1, "Last 24 hours"], [7, "Last 7 days"], [30, "Last 30 days"], [90, "Last 90 days"]];
const SEVS = ["", "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];
const EMPTY = { q: "", severity: "", confidence: "", service: "", status: "", days: 0, all_versions: false };

function qs(f, extra = {}) {
  const p = new URLSearchParams();
  Object.entries({ ...f, ...extra }).forEach(([k, v]) => { if (v !== "" && v !== 0 && v !== false && v != null) p.set(k, v); });
  return p.toString();
}

export default function Rca() {
  const [form, setForm] = useState(EMPTY);
  const [applied, setApplied] = useState(EMPTY);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState({});
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const { data, error, loading, reload } = useLoad(() => api("/api/rca?" + qs(applied, { limit: PAGE, offset })), [applied, offset]);
  const set = (k) => (e) => setForm({ ...form, [k]: k === "days" ? Number(e.target.value) : e.target.value });

  async function exportAs(kind) {
    setBusy(kind); setErr("");
    try { await download(`/api/rca/export.${kind}?` + qs(applied), `rca-reports.${kind}`); } catch (e) { setErr(e.message); } finally { setBusy(""); }
  }
  const items = data?.items || [];
  return (
    <>
      <div className="page-h"><h1>RCA reports</h1><span className="sub">every root-cause analysis the agents have produced</span></div>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); setOffset(0); setApplied(form); }}>
        <Field label="Search"><input value={form.q} onChange={set("q")} placeholder="pool, timeout, payment-service" /></Field>
        <Field label="Severity"><select value={form.severity} onChange={set("severity")}>{SEVS.map((s) => <option key={s} value={s}>{s || "All severities"}</option>)}</select></Field>
        <Field label="Confidence"><select value={form.confidence} onChange={set("confidence")}><option value="">Any</option><option>high</option><option>medium</option><option>low</option></select></Field>
        <Field label="Service"><select value={form.service} onChange={set("service")}><option value="">All services</option>{(data?.services || []).map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Incident status"><select value={form.status} onChange={set("status")}><option value="">Any</option><option>open</option><option>acknowledged</option><option>resolved</option></select></Field>
        <Field label="Period"><select value={form.days} onChange={set("days")}>{PERIODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
        <button className="btn primary">Apply</button>
        <button type="button" className="btn" onClick={() => { setForm(EMPTY); setApplied(EMPTY); setOffset(0); }}>Clear</button>
        <label className="check"><input type="checkbox" checked={form.all_versions} onChange={(e) => setForm({ ...form, all_versions: e.target.checked })} /> Include older RCA runs of the same incident</label>
      </form>
      <ErrorBox error={error || err} onRetry={reload} />
      <Card title={data ? `${data.total} RCA report${data.total === 1 ? "" : "s"}` : "RCA reports"}
        actions={<>
          <button className="btn sm" disabled={!!busy || !data?.total} onClick={() => exportAs("csv")}>{busy === "csv" ? "Preparing..." : "Export CSV"}</button>
          <button className="btn sm" disabled={!!busy || !data?.total} onClick={() => exportAs("pdf")}>{busy === "pdf" ? "Preparing..." : "Export PDF"}</button>
        </>}>
        {loading && !data ? <Spinner /> : items.length === 0 ? (
          <Empty>No RCA reports match. An RCA is created when an incident is investigated (auto-investigate, the pipeline, or the Investigate button on an incident). Exports cover the current filters, up to 500 reports.</Empty>
        ) : (
          <>
            <div className="tbl-wrap"><table className="tbl">
              <thead><tr><th>Incident</th><th>Severity</th><th>Service</th><th>Root cause</th><th>Confidence</th><th>Generated</th><th></th></tr></thead>
              <tbody>{items.map((r) => (
                <Fragment key={r.rca_id}>
                  <tr>
                    <td><a href={`#/incidents/${r.incident_id}`} onClick={(e) => { e.preventDefault(); nav("incidents", r.incident_id); }}>#{r.incident_id}</a> {r.title}</td>
                    <td><SevBadge sev={r.severity} /></td><td>{r.service || "-"}</td>
                    <td className="msg">{(r.root_cause || "Not stated").slice(0, 140)}{(r.root_cause || "").length > 140 ? "..." : ""}</td>
                    <td><span className={`badge ${r.confidence === "high" ? "ok" : r.confidence === "medium" ? "warn" : "bad"}`}>{r.confidence}</span></td>
                    <td className="nowrap">{fmtTime(r.created_at)}</td>
                    <td><button className="btn sm" onClick={() => setOpen({ ...open, [r.rca_id]: !open[r.rca_id] })}>{open[r.rca_id] ? "Hide" : "Details"}</button></td>
                  </tr>
                  {open[r.rca_id] && (
                    <tr><td colSpan={7}>
                      <dl className="kv">
                        <dt>Root cause</dt><dd>{r.root_cause || "-"}</dd>
                        <dt>Summary</dt><dd>{r.summary || "-"}</dd>
                        <dt>Evidence</dt><dd>{r.evidence_verified} of {r.evidence_total} cited logs verified</dd>
                        <dt>Model</dt><dd>{r.provider} / {r.model}</dd>
                      </dl>
                      {r.recommendations.length > 0 && <><h3>Recommendations</h3><ul>{r.recommendations.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
                    </td></tr>
                  )}
                </Fragment>
              ))}</tbody>
            </table></div>
            <div className="row" style={{ marginTop: 12 }}>
              <button className="btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
              <span className="muted">{offset + 1}-{Math.min(offset + PAGE, data.total)} of {data.total}</span>
              <button className="btn sm" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
