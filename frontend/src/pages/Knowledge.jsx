import { useRef, useState } from "react";
import { api, fileToB64 } from "../api.js";
import { useEffect } from "react";
import { can, Card, Empty, ErrorBox, Field, fmtTime, Spinner, takeHandoff, useLoad } from "../ui.jsx";

export default function Knowledge({ user }) {
  const docs = useLoad(() => api("/api/kb/docs"));
  const [q, setQ] = useState(() => takeHandoff("kbQ"));
  const [hits, setHits] = useState(null);
  const [doc, setDoc] = useState(null);
  const [form, setForm] = useState({ title: "", content: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const analyst = can(user, "analyst");
  const fileRef = useRef(null);
  const [over, setOver] = useState(false);
  const [results, setResults] = useState([]);
  async function upload(list) {
    const files = Array.from(list || []).slice(0, 10);
    if (!files.length) return;
    setErr(""); setBusy(true); setResults([]);
    const out = [];
    for (const f of files) {
      try {
        if (f.size > 10 * 1024 * 1024) throw new Error("larger than 10 MB");
        const r = await api("/api/kb/upload", { method: "POST", body: { name: f.name, data: await fileToB64(f) } });
        out.push({ name: f.name, ok: true, note: `added as "${r.title}" (${r.chars.toLocaleString()} characters${r.truncated ? ", truncated" : ""})` });
      } catch (e) { out.push({ name: f.name, ok: false, note: e.message.replace(/^[^:]*: /, "") }); }
      setResults([...out]);
    }
    setBusy(false); docs.reload(true);
    if (fileRef.current) fileRef.current.value = "";
  }
  useEffect(() => { if (q.trim()) api("/api/kb/search?q=" + encodeURIComponent(q)).then(setHits).catch((e) => setErr(e.message)); /* eslint-disable-next-line */ }, []);

  const guard = async (fn) => { setErr(""); setBusy(true); try { await fn(); } catch (e) { setErr(e.message); } finally { setBusy(false); } };
  const search = (e) => { e.preventDefault(); if (q.trim()) guard(async () => setHits(await api("/api/kb/search?q=" + encodeURIComponent(q)))); };
  const view = (id) => guard(async () => setDoc(await api(`/api/kb/docs/${id}`)));
  const add = (e) => { e.preventDefault(); guard(async () => { await api("/api/kb/docs", { method: "POST", body: form }); setForm({ title: "", content: "" }); docs.reload(true); }); };
  const del = (d) => { if (window.confirm(`Delete "${d.title}"?`)) guard(async () => { await api(`/api/kb/docs/${d.id}`, { method: "DELETE" }); if (doc?.id === d.id) setDoc(null); docs.reload(true); }); };

  return (
    <>
      <div className="page-h"><h1>Knowledge base</h1><span className="sub">runbooks and documentation the agents can search</span></div>
      <ErrorBox error={err || docs.error} />
      <Card title="Search">
        <form className="row" onSubmit={search}>
          <input className="grow" aria-label="Search the knowledge base" value={q} onChange={(e) => setQ(e.target.value)} placeholder="connection pool exhausted" />
          <button className="btn primary" disabled={busy}>Search</button>
        </form>
        <div className="hint" style={{ marginTop: 6 }}>BM25 keyword search, not embeddings. Wording matters: use words that appear in the runbook.</div>
        {hits && (hits.results.length === 0 ? <Empty>No matching runbooks.</Empty> : (
          <div className="stack" style={{ marginTop: 12 }}>{hits.results.map((h) => (
            <div key={h.doc_id}>
              <div className="row"><strong>{h.title}</strong><span className="chip">score {h.score}</span></div>
              <pre className="tprev" style={{ maxHeight: 160 }}>{h.snippet}</pre>
            </div>
          ))}</div>
        ))}
      </Card>
      <div style={{ height: 14 }} />
      <div className="grid g2">
        <Card title="Documents">
          {docs.loading && !docs.data ? <Spinner /> : !docs.data || docs.data.length === 0 ? <Empty>No documents.</Empty> : (
            <div className="tbl-wrap"><table className="tbl">
              <thead><tr><th>Title</th><th>Source</th><th className="num">Chunks</th><th /></tr></thead>
              <tbody>{docs.data.map((d) => (
                <tr key={d.id}>
                  <td><button className="btn link" onClick={() => view(d.id)}>{d.title}</button></td>
                  <td className="muted">{d.source}</td><td className="num">{d.chunks}</td>
                  <td><button className="btn sm danger" disabled={!analyst} onClick={() => del(d)}>Delete</button></td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
          {doc && (<><h3 style={{ marginTop: 14 }}>{doc.title}</h3><div className="hint">added {fmtTime(doc.created_at)}</div><div className="doc-body">{doc.content}</div></>)}
        </Card>
        <Card title="Add a runbook or document">
          <div className={`drop ${over ? "over" : ""}`} role="button" tabIndex={0} aria-label="Upload files" aria-disabled={!analyst}
            onClick={() => analyst && fileRef.current?.click()} onKeyDown={(e) => { if (analyst && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); fileRef.current?.click(); } }}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); if (analyst) upload(e.dataTransfer.files); }}>
            <b>{busy ? "Uploading..." : "Upload files"}</b><div className="small">Drop files here or click. PDF, Word (.docx), Markdown, text, JSON, YAML, CSV, logs. Up to 10 files, 10 MB each.</div>
            {!analyst && <div className="small bad-t">Requires the analyst role.</div>}
          </div>
          <input ref={fileRef} type="file" multiple hidden accept=".pdf,.docx,.md,.markdown,.txt,.log,.json,.yaml,.yml,.csv,.tsv,.xml,.html,.ini,.toml,.conf,.rst" onChange={(e) => upload(e.target.files)} />
          {results.length > 0 && <div className="stack" style={{ marginTop: 8 }}>{results.map((r, i) => <div key={i} className={`alert ${r.ok ? "alert-ok" : "alert-err"}`}><span><b>{r.name}</b>: {r.note}</span></div>)}</div>}
          <h3 style={{ marginTop: 16 }}>Or paste text</h3>
          <form className="stack" onSubmit={add}>
            <Field label="Title"><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required maxLength={200} disabled={!analyst} /></Field>
            <Field label="Content (Markdown; use ## headings per section)" hint="Secrets such as keys and passwords are redacted before storage.">
              <textarea rows={10} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} required minLength={10} disabled={!analyst} />
            </Field>
            <div><button className="btn primary" disabled={!analyst || busy} title={analyst ? "" : "Requires analyst role"}>Add document</button></div>
          </form>
        </Card>
      </div>
    </>
  );
}
