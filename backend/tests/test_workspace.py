import json
import subprocess

import pytest

from test_github import sh
from test_chat_stream import parse_sse


@pytest.fixture
def repo(env, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    sh("git", "init", "-q", "-b", "main", cwd=d)
    (d / "app.py").write_text("def pay(x):\n    return x * 2\n")
    (d / "lib").mkdir()
    (d / "lib" / "db.py").write_text("POOL = 5\n")
    (d / ".env").write_text("SECRET=abc")
    (d / "key.pem").write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----")
    sh("git", "add", "-A", "-f", cwd=d)
    sh("git", "commit", "-q", "-m", "initial", cwd=d)
    (d / "app.py").write_text("def pay(x):\n    return x * 3\n")
    sh("git", "commit", "-qam", "triple it", cwd=d)
    (d / "app.py").write_text("def pay(x):\n    return x * 4  # wip\n")
    (d / ".env").write_text("SECRET=changed")
    rid = env.client.post("/api/repos", json={"name": "proj", "root_path": str(d)}).json()["id"]
    return {"id": rid, "dir": d}


def test_tree_hides_secrets_and_git(env, repo):
    t = env.client.get(f"/api/repos/{repo['id']}/tree").json()
    paths = {f["path"] for f in t["files"]}
    assert {"app.py", "lib/db.py"} <= paths
    assert not any(p.startswith(".git/") or p in (".env", "key.pem") for p in paths)
    assert t["is_git"]


def test_file_view_and_guards(env, repo):
    r = env.client.get(f"/api/repos/{repo['id']}/file", params={"path": "lib/db.py"}).json()
    assert r["content"] == "POOL = 5\n" and r["lines"] == 2
    for bad in (".env", "../x", "/etc/passwd", ".git/config", "key.pem", "nope.py"):
        assert env.client.get(f"/api/repos/{repo['id']}/file", params={"path": bad}).status_code == 400, bad


def test_file_secret_values_masked(env, repo):
    (repo["dir"] / "cfg.py").write_text('password = "hunter2hunter2"\n')
    r = env.client.get(f"/api/repos/{repo['id']}/file", params={"path": "cfg.py"}).json()
    assert "hunter2" not in r["content"]


def test_binary_file(env, repo):
    (repo["dir"] / "a.bin").write_bytes(b"\x00\x01\x02")
    assert env.client.get(f"/api/repos/{repo['id']}/file", params={"path": "a.bin"}).json()["binary"] is True


def test_commits_and_diff(env, repo):
    c = env.client.get(f"/api/repos/{repo['id']}/commits").json()
    assert [x["subject"] for x in c["commits"]] == ["triple it", "initial"]
    d = env.client.get(f"/api/repos/{repo['id']}/commits/{c['commits'][0]['sha']}").json()["diff"]
    assert "-    return x * 2" in d and "+    return x * 3" in d
    first = env.client.get(f"/api/repos/{repo['id']}/commits/{c['commits'][1]['sha']}").json()["diff"]
    assert "BEGIN PRIVATE KEY" not in first and "SECRET=abc" not in first       # secret files excluded from diffs
    assert env.client.get(f"/api/repos/{repo['id']}/commits/--output=x").status_code == 400
    assert env.client.get(f"/api/repos/{repo['id']}/commits/zzzzzzz").status_code == 400


def test_uncommitted_changes_exclude_secret_files(env, repo):
    s = env.client.get(f"/api/repos/{repo['id']}/status").json()
    assert [f["path"] for f in s["files"]] == ["app.py"]
    assert "x * 4" in s["diff"] and "changed" not in s["diff"]


def test_workspace_needs_analyst_and_known_repo(env, repo):
    from conftest import make_user
    h = make_user(env, "viewer1", "viewer")
    assert env.client.get(f"/api/repos/{repo['id']}/tree", headers=h).status_code == 403
    assert env.client.get("/api/repos/999/tree").status_code == 404


def test_non_git_dir_is_handled(env, tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "a.txt").write_text("hi")
    rid = env.client.post("/api/repos", json={"name": "plain", "root_path": str(d)}).json()["id"]
    assert env.client.get(f"/api/repos/{rid}/commits").json() == {"is_git": False, "commits": []}
    assert env.client.get(f"/api/repos/{rid}/status").json()["is_git"] is False


def chat(env, rid, **body):
    r = env.client.post(f"/api/repos/{rid}/chat", json=body)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)


def test_repo_chat_with_selected_file_streams(env, repo):
    ev = chat(env, repo["id"], text="pay", files=["app.py"], scope="files")
    kinds = [e for e, _ in ev]
    assert "answer" in kinds and kinds[-1] == "done"
    ans = next(d for e, d in ev if e == "answer")
    assert ans["files"] == [{"path": "app.py", "status": "included"}]
    assert "app.py" in ans["text"] or "app.py" in json.dumps(ans["trace"])


def test_selected_files_reach_model_as_untrusted_data(env, repo):
    from app.llm.base import LLMProvider, LLMResponse
    seen = {}

    class Spy(LLMProvider):
        name, model, is_real = "spy", "s", True
        def complete(self, system, messages, tools=None, *a, **k):
            seen["system"], seen["messages"] = system, messages
            return LLMResponse(text="done")

    env.ctx.llm.safe_get = lambda: Spy()
    (repo["dir"] / "cfg.py").write_text('password = "hunter2hunter2"\n')
    ev = chat(env, repo["id"], text="review", files=["app.py", ".env", "../x", "cfg.py"], scope="files",
              history=[{"role": "assistant", "content": "stray"}, {"role": "user", "content": "earlier q"}, {"role": "assistant", "content": "earlier a"}])
    last = seen["messages"][-1]["content"]
    assert '<untrusted_file path="app.py">' in last and "1: def pay(x):" in last
    assert "hunter2" not in last and "SECRET=" not in last
    assert seen["messages"][0]["role"] == "user" and len(seen["messages"]) == 3     # leading assistant turn dropped
    ans = next(d for e, d in ev if e == "answer")
    st = {f["path"]: f["status"] for f in ans["files"]}
    assert st[".env"].startswith("refused") and st["../x"].startswith("refused") and st["app.py"] == "included"
    assert "WHOLE repository" not in seen["system"]
    chat(env, repo["id"], text="review all", scope="repo")
    assert "WHOLE repository" in seen["system"]


def test_repo_chat_returns_new_proposals(env, repo):
    from app.llm.base import LLMProvider, LLMResponse, ToolCall

    class Proposer(LLMProvider):
        name, model, is_real = "p", "p", True
        n = 0
        def complete(self, system, messages, tools=None, *a, **k):
            Proposer.n += 1
            if Proposer.n == 1:
                return LLMResponse(tool_calls=[ToolCall("1", "propose_edit", {"path": "lib/db.py", "old_text": "POOL = 5", "new_text": "POOL = 20", "rationale": "bigger pool"})])
            return LLMResponse(text="Proposed.")

    env.ctx.llm.safe_get = lambda: Proposer()
    ev = chat(env, repo["id"], text="raise the pool", files=["lib/db.py"])
    ans = next(d for e, d in ev if e == "answer")
    assert len(ans["proposals"]) == 1 and "+POOL = 20" in ans["proposals"][0]["diff"]
    assert (repo["dir"] / "lib" / "db.py").read_text() == "POOL = 5\n"     # nothing applied


def test_repo_chat_validation_and_activity(env, repo):
    assert env.client.post(f"/api/repos/{repo['id']}/chat", json={"text": "x", "scope": "bogus"}).status_code == 422
    assert env.client.post("/api/repos/999/chat", json={"text": "x"}).status_code == 404
    chat(env, repo["id"], text="pay")
    assert env.ctx.db.one("SELECT COUNT(*) AS n FROM agent_activity WHERE agent='code_assistant'")["n"] >= 1
