import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamPost } from "../api.js";
import { Markdown } from "../md.jsx";
import { can, Diff, Empty, ErrorBox, fmtTime, LlmBadge, relTime, Spinner, Trace, useLoad } from "../ui.jsx";

// ---------- file tree ----------
function buildTree(files) {
  const root = { dirs: {}, files: [] };
  for (const f of files) {
    const parts = f.path.split("/");
    let n = root;
    for (let i = 0; i < parts.length - 1; i++) n = n.dirs[parts[i]] ||= { dirs: {}, files: [] };
    n.files.push({ name: parts[parts.length - 1], path: f.path, size: f.size });
  }
  return root;
}

function Node({ node, base, depth, open, toggle, sel, pick, refs }) {
  const dirs = Object.keys(node.dirs).sort();
  return (
    <>
      {dirs.map((d) => {
        const p = base + d + "/";
        const isOpen = open.has(p);
        return (
          <div key={p}>
            <button className="tn dir" style={{ paddingLeft: 8 + depth * 14 }} onClick={() => toggle(p)} aria-expanded={isOpen}>
              <span className="chev">{isOpen ? "▾" : "▸"}</span>{d}
            </button>
            {isOpen && <Node node={node.dirs[d]} base={p} depth={depth + 1} open={open} toggle={toggle} sel={sel} pick={pick} refs={refs} />}
          </div>
        );
      })}
      {node.files.sort((a, b) => a.name.localeCompare(b.name)).map((f) => (
        <button key={f.path} className={`tn file ${sel === f.path ? "on" : ""}`} style={{ paddingLeft: 22 + depth * 14 }} onClick={() => pick(f.path)} title={f.path}>
          {f.name}{refs.includes(f.path) && <span className="refdot" title="In the chat context">&#9679;</span>}
        </button>
      ))}
    </>
  );
}

function Tree({ files, sel, pick, refs }) {
  const [filter, setFilter] = useState("");
  const tree = useMemo(() => buildTree(files), [files]);
  const [open, setOpen] = useState(() => new Set());
  const toggle = (p) => setOpen((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n; });
  const q = filter.trim().toLowerCase();
  const hits = q ? files.filter((f) => f.path.toLowerCase().includes(q)).slice(0, 200) : null;
  return (
    <div className="ws-tree">
      <input className="ws-filter" placeholder="Filter files" aria-label="Filter files" value={filter} onChange={(e) => setFilter(e.target.value)} />
      <div className="ws-tree-list" role="tree">
        {hits ? (hits.length ? hits.map((f) => <button key={f.path} className={`tn file ${sel === f.path ? "on" : ""}`} style={{ paddingLeft: 10 }} onClick={() => pick(f.path)} title={f.path}>{f.path}</button>) : <div className="muted small" style={{ padding: 10 }}>No match.</div>)
          : <Node node={tree} base="" depth={0} open={open} toggle={toggle} sel={sel} pick={pick} refs={refs} />}
      </div>
    </div>
  );
}

// ---------- viewer ----------
const MAX_RENDER = 3000;
function FileView({ repoId, path, inChat, addRef, askAbout }) {
  const { data, error, loading } = useLoad(() => api(`/api/repos/${repoId}/file?path=${encodeURIComponent(path)}`), [repoId, path]);
  const [all, setAll] = useState(false);
  useEffect(() => setAll(false), [path]);
  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const lines = data.content.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  const shown = all ? lines : lines.slice(0, MAX_RENDER);
  return (
    <div className="ws-file">
      <div className="ws-pathbar">
        <span className="mono grow" title={path}>{path}</span>
        <span className="muted small">{data.binary ? "binary" : `${data.lines} lines, ${(data.size / 1024).toFixed(1)} KB`}</span>
        <button className="btn sm" onClick={() => addRef(path)} disabled={inChat}>{inChat ? "In chat" : "Add to chat"}</button>
        <button className="btn sm primary" onClick={() => askAbout(path)}>Explain this file</button>
      </div>
      {data.binary ? <Empty>Binary file: not shown.</Empty> : (
        <div className="code" tabIndex={0} aria-label={`Contents of ${path}`}>
          {shown.map((l, i) => <div className="cl" key={i}><span className="ln">{i + 1}</span><span className="lt">{l || " "}</span></div>)}
          {!all && lines.length > MAX_RENDER && <button className="btn sm" onClick={() => setAll(true)}>Show the remaining {lines.length - MAX_RENDER} lines</button>}
          {data.truncated && <div className="hint" style={{ padding: 8 }}>File is large: only the first 400 KB are shown.</div>}
        </div>
      )}
      {data.note && <div className="hint" style={{ padding: "4px 10px" }}>{data.note}</div>}
    </div>
  );
}

// ---------- proposals ----------
export function ProposalCard({ p, repo, admin, analyst, onChange, setErr }) {
  const isGh = repo && repo.kind === "github";
  const act = async (fn) => { setErr(""); try { await fn(); } catch (e) { setErr(e.message); } onChange(); };
  return (
    <div className="prop">
      <div className="row"><b className="mono">{p.path}</b><span className={`badge ${p.status === "applied" ? "ok" : p.status === "pending" ? "warn" : "bad"}`}>{p.status}</span><span className="muted small">#{p.id}</span></div>
      {p.rationale && <div className="small" style={{ margin: "4px 0" }}>{p.rationale}</div>}
      <Diff diff={p.diff} />
      {p.status === "pending" && (
        <div className="row">
          {!isGh && <button className="btn sm primary" disabled={!admin} title={admin ? "Edits the file on the server" : "Admin only"}
            onClick={() => window.confirm(`Apply this change to ${p.path}? It edits the file on the server.`) && act(() => api(`/api/proposals/${p.id}/apply`, { method: "POST" }))}>Apply</button>}
          {isGh && <button className="btn sm primary" disabled={!admin || !repo.allow_pr} title={repo.allow_pr ? "Creates a branch and a pull request" : "Enable pull requests for this repository first"}
            onClick={() => window.confirm(`Create a branch and open a pull request for ${p.path}?`) && act(async () => { const r = await api(`/api/proposals/${p.id}/pr`, { method: "POST" }); window.alert(r.url ? `Pull request: ${r.url}` : `Pushed branch ${r.branch}`); })}>Open pull request</button>}
          <button className="btn sm" disabled={!analyst} onClick={() => act(() => api(`/api/proposals/${p.id}/reject`, { method: "POST" }))}>Reject</button>
        </div>
      )}
    </div>
  );
}

// ---------- changes ----------
function Changes({ repo, admin, analyst }) {
  const [sub, setSub] = useState("pending");
  const [err, setErr] = useState("");
  const props = useLoad(() => api("/api/proposals?status=pending"), []);
  const status = useLoad(() => api(`/api/repos/${repo.id}/status`), [repo.id]);
  const commits = useLoad(() => api(`/api/repos/${repo.id}/commits`), [repo.id]);
  const [sha, setSha] = useState(null);
  const [diff, setDiff] = useState(null);
  const mine = (props.data || []).filter((p) => p.repo_id === repo.id);
  async function show(s) {
    setSha(s); setDiff(null); setErr("");
    try { setDiff(await api(`/api/repos/${repo.id}/commits/${s}`)); } catch (e) { setErr(e.message); }
  }
  return (
    <div className="ws-changes">
      <div className="tabs" role="tablist" style={{ marginBottom: 8 }}>
        {[["pending", `Proposed by AI (${mine.length})`], ["work", `Uncommitted${status.data && status.data.files ? ` (${status.data.files.length})` : ""}`], ["history", "History"]].map(([id, l]) => (
          <button key={id} role="tab" className="tab" aria-selected={sub === id} onClick={() => setSub(id)}>{l}</button>))}
      </div>
      <ErrorBox error={err} />
      {sub === "pending" && (props.loading && !props.data ? <Spinner /> : mine.length === 0 ? <Empty>No pending AI proposals. Ask the chat to fix something and the proposed change shows up here as a diff.</Empty> :
        mine.map((p) => <ProposalCard key={p.id} p={p} repo={repo} admin={admin} analyst={analyst} setErr={setErr} onChange={() => props.reload(true)} />))}
      {sub === "work" && (status.loading && !status.data ? <Spinner /> : !status.data || !status.data.is_git ? <Empty>This directory is not a git repository, so there is no change tracking.</Empty> :
        status.data.clean ? <Empty>No uncommitted changes.{repo.kind === "github" ? " A linked GitHub clone is always reset to the remote, so changes appear as commits under History." : ""}</Empty> : (
          <>
            <div className="row" style={{ marginBottom: 6 }}>{status.data.files.map((f) => <span className="chip" key={f.path}><b>{f.status}</b> {f.path}</span>)}</div>
            <Diff diff={status.data.diff} />
          </>))}
      {sub === "history" && (commits.loading && !commits.data ? <Spinner /> : !commits.data || !commits.data.is_git ? <Empty>Not a git repository.</Empty> : (
        <div className="ws-hist">
          <div className="ws-commits">{commits.data.commits.map((c) => (
            <button key={c.sha} className={`commit ${sha === c.sha ? "on" : ""}`} onClick={() => show(c.sha)}>
              <div className="cs">{c.subject}</div><div className="muted small">{c.author} &middot; {relTime(c.date)} &middot; <code>{c.sha.slice(0, 7)}</code></div>
            </button>))}</div>
          <div className="ws-commit-diff">{!sha ? <Empty>Select a commit to see what changed.</Empty> : !diff ? <Spinner /> : <><Diff diff={diff.diff} />{diff.truncated && <div className="hint">Diff truncated.</div>}</>}</div>
        </div>))}
    </div>
  );
}

// ---------- repo chat ----------
const QUICK = [["Explain this repository", "repo", "Give me a short overview of what this repository does and how it is organised."],
  ["Review the whole codebase", "repo", "Review the whole repository for bugs, security risks and reliability problems. Give the most important findings first."]];

function RepoChat({ repo, refs, removeRef, clearRefs, admin, analyst, onProposals, injected, clearInjected }) {
  const [msgs, setMsgs] = useState([]);
  const [text, setText] = useState("");
  const [live, setLive] = useState(null);
  const [scope, setScope] = useState("files");
  const [err, setErr] = useState("");
  const [provider, setProvider] = useState(null);
  const endRef = useRef(null), abortRef = useRef(null);
  const busy = !!live;
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [msgs, live]);
  useEffect(() => { setMsgs([]); setErr(""); }, [repo.id]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const send = useCallback(async (q, sc, files) => {
    const t = (q ?? text).trim();
    if (!t || busy) return;
    setText(""); setErr("");
    const history = msgs.filter((m) => !m.error).slice(-10).map((m) => ({ role: m.role, content: m.content }));
    const useFiles = files ?? refs;
    setMsgs((m) => [...m, { role: "user", content: t, files: sc === "repo" ? [] : useFiles, scope: sc }]);
    setLive({ text: "", steps: [] });
    const ac = new AbortController();
    abortRef.current = ac;
    let final = null;
    try {
      await streamPost(`/api/repos/${repo.id}/chat`, { text: t, files: sc === "repo" ? [] : useFiles, scope: sc, history }, (ev, d) => {
        if (ev === "delta") setLive((l) => l && { ...l, text: l.text + d.text });
        else if (ev === "discard_text") setLive((l) => l && { ...l, text: "" });
        else if (ev === "step") setLive((l) => {
          if (!l) return l;
          const steps = l.steps.slice();
          if (d.status === "running") steps.push({ name: d.name, args: d.args, status: "run" });
          else { const j = steps.findLastIndex((s) => s.name === d.name && s.status === "run"); if (j >= 0) steps[j] = { ...steps[j], status: d.status === "ok" ? "done" : "err" }; }
          return { ...l, steps };
        });
        else if (ev === "answer") { final = d; setProvider(d.provider); }
        else if (ev === "error") setErr(d.message);
      }, ac.signal);
    } catch (e) { setErr(e.message); }
    abortRef.current = null;
    setLive(null);
    if (final) { setMsgs((m) => [...m, { role: "assistant", content: final.text, trace: final.trace, proposals: final.proposals, ctxFiles: final.files, error: !!final.error }]); if (final.proposals?.length) onProposals(); }
  }, [text, busy, msgs, refs, repo.id, onProposals]);

  useEffect(() => { if (injected) { send(injected.text, injected.scope, injected.files); clearInjected(); } /* eslint-disable-next-line */ }, [injected]);

  const label = { list_files: "Listing files", read_file: "Reading", grep: "Searching", git_log: "Reading history", propose_edit: "Drafting a change" };
  return (
    <div className="ws-chat">
      <div className="ws-chat-h"><b>Ask about this code</b><span className="right"><LlmBadge llm={provider} /></span></div>
      <div className="ws-chat-body">
        <ErrorBox error={err} />
        {msgs.length === 0 && !busy && (
          <div className="ws-hint">
            <p>Pick files in the tree and press <b>Add to chat</b>, then ask. Or use the whole-repository buttons.</p>
            <div className="stack">{QUICK.map(([l, sc, q]) => <button key={l} className="btn" disabled={!analyst} onClick={() => send(q, sc)}>{l}</button>)}</div>
            <p className="muted small">The AI can read the repo and propose changes. It cannot run code or tests, and it never changes a file without your approval. Whole-repository reviews sample the code within a step limit, and the answer lists what was read.</p>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`wmsg ${m.role}`}>
            <div className="who">{m.role === "user" ? "You" : "LogFix AI"}</div>
            {m.role === "user" ? <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div> : <Markdown text={m.content} />}
            {m.role === "user" && (m.scope === "repo" ? <div className="muted small">scope: whole repository</div> : m.files.length > 0 && <div className="row">{m.files.map((f) => <span className="chip" key={f}>{f}</span>)}</div>)}
            {m.ctxFiles && m.ctxFiles.some((f) => !f.status.startsWith("included")) && <div className="hint">{m.ctxFiles.filter((f) => !f.status.startsWith("included")).map((f) => `${f.path}: ${f.status}`).join("; ")}</div>}
            {m.proposals && m.proposals.map((p) => <ProposalCard key={p.id} p={p} repo={repo} admin={admin} analyst={analyst} setErr={setErr} onChange={onProposals} />)}
            {m.trace && <Trace trace={m.trace} label="What it looked at" />}
          </div>
        ))}
        {live && (
          <div className="wmsg assistant">
            <div className="who">LogFix AI</div>
            {live.steps.length > 0 && <div className="toolchips">{live.steps.map((s, i) => <span key={i} className={`toolchip ${s.status}`}>{label[s.name] || s.name}{s.args && s.args.path ? ` ${s.args.path}` : ""}</span>)}</div>}
            {live.text ? <div className="cursor"><Markdown text={live.text} /></div> : <span className="muted cursor">Reading the code</span>}
          </div>
        )}
        <div ref={endRef} />
      </div>
      <div className="ws-chat-foot">
        <div className="row" style={{ marginBottom: 6 }}>
          <div className="seg" role="group" aria-label="Scope">
            <button className={scope === "files" ? "on" : ""} onClick={() => setScope("files")}>Selected files</button>
            <button className={scope === "repo" ? "on" : ""} onClick={() => setScope("repo")}>Whole repository</button>
          </div>
          {scope === "files" && refs.length > 0 && <button className="btn link small" onClick={clearRefs}>clear</button>}
        </div>
        {scope === "files" && <div className="row" style={{ marginBottom: 6 }}>{refs.length === 0 ? <span className="muted small">No files selected. The AI will explore on its own.</span> : refs.map((f) => (
          <span className="chip refchip" key={f}>{f.split("/").pop()}<button aria-label={`Remove ${f}`} onClick={() => removeRef(f)}>&times;</button></span>))}</div>}
        <div className="gpt-box">
          <textarea aria-label="Ask about the code" rows={2} value={text} onChange={(e) => setText(e.target.value)} disabled={!analyst} maxLength={6000}
            placeholder={scope === "repo" ? "Ask about the whole repository" : "Ask about the selected files"}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(undefined, scope); } }} />
          {busy ? <button className="btn send" onClick={() => abortRef.current?.abort()} aria-label="Stop" title="Stop">&#9632;</button>
            : <button className="btn primary send" disabled={!text.trim() || !analyst} onClick={() => send(undefined, scope)} aria-label="Send">&#10148;</button>}
        </div>
      </div>
    </div>
  );
}

// ---------- page ----------
export default function Workspace({ user, repos, reposReload }) {
  const admin = can(user, "admin"), analyst = can(user, "analyst");
  const ready = (repos || []).filter((r) => r.kind !== "github" || r.sync_status === "ready");
  const [rid, setRid] = useState(null);
  const repo = ready.find((r) => r.id === rid) || ready[0];
  const [sel, setSel] = useState(null);
  const [view, setView] = useState("file");
  const [refs, setRefs] = useState([]);
  const [injected, setInjected] = useState(null);
  const tree = useLoad(() => (repo ? api(`/api/repos/${repo.id}/tree`) : Promise.resolve(null)), [repo?.id]);
  const propsTick = useRef(0);
  const [, force] = useState(0);
  useEffect(() => { setSel(null); setRefs([]); setView("file"); }, [repo?.id]);

  if (!analyst) return <Empty>The code workspace needs the analyst role.</Empty>;
  if (!repo) return <Empty>No repository is ready yet. Link a GitHub repository or register a server directory on the “Repositories” tab.</Empty>;
  const addRef = (p) => setRefs((r) => (r.includes(p) || r.length >= 8 ? r : [...r, p]));
  const pick = (p) => { setSel(p); setView("file"); };
  const ask = (path) => { addRef(path); setInjected({ text: `Explain what ${path} does, and point out any bugs or risks.`, scope: "files", files: [path] }); };

  return (
    <>
      <div className="row" style={{ marginBottom: 8 }}>
        <select aria-label="Repository" value={repo.id} onChange={(e) => setRid(Number(e.target.value))}>{ready.map((r) => <option key={r.id} value={r.id}>{r.name}{r.kind === "github" ? " (GitHub)" : ""}</option>)}</select>
        {repo.kind === "github" && <span className="muted small">{repo.branch} @ {(repo.last_commit || "").slice(0, 7)} &middot; synced {relTime(repo.last_synced_at)}</span>}
        {repo.kind === "github" && <button className="btn sm" onClick={async () => { await api(`/api/repos/${repo.id}/sync`, { method: "POST" }).catch(() => {}); reposReload(); }}>Sync now</button>}
        <button className="btn sm" onClick={() => tree.reload(true)}>Refresh files</button>
      </div>
      <div className="ws">
        <aside className="ws-left" aria-label="Files">
          {tree.loading && !tree.data ? <Spinner /> : tree.error ? <ErrorBox error={tree.error} /> : tree.data && <Tree files={tree.data.files} sel={sel} pick={pick} refs={refs} />}
          {tree.data?.truncated && <div className="hint" style={{ padding: 8 }}>Showing the first 5000 files.</div>}
        </aside>
        <section className="ws-mid">
          <div className="tabs" role="tablist" style={{ marginBottom: 0 }}>
            <button role="tab" className="tab" aria-selected={view === "file"} onClick={() => setView("file")}>File</button>
            <button role="tab" className="tab" aria-selected={view === "changes"} onClick={() => setView("changes")}>Changes</button>
          </div>
          <div className="ws-mid-body">
            {view === "file" ? (sel ? <FileView repoId={repo.id} path={sel} inChat={refs.includes(sel)} addRef={addRef} askAbout={ask} /> : <Empty>Choose a file from the tree.</Empty>)
              : <Changes key={repo.id + ":" + propsTick.current} repo={repo} admin={admin} analyst={analyst} />}
          </div>
        </section>
        <RepoChat repo={repo} refs={refs} removeRef={(f) => setRefs((r) => r.filter((x) => x !== f))} clearRefs={() => setRefs([])} admin={admin} analyst={analyst}
          onProposals={() => { propsTick.current += 1; force((n) => n + 1); }} injected={injected} clearInjected={() => setInjected(null)} />
      </div>
    </>
  );
}
