import json

import httpx

from app.db import now_iso
from conftest import inject, make_user


def seed(env):
    inject(env)
    inc = env.ctx.db.one("SELECT * FROM incidents ORDER BY id LIMIT 1")

    def add(root, conf="high", status="done", rec=("restart it",)):
        res = json.dumps({"summary": "sum " + root, "root_cause": root, "confidence": conf,
                          "recommendations": list(rec), "grounding": {"verified": 2, "total": 3}})
        return env.ctx.db.execute(
            "INSERT INTO investigations(kind,incident_id,status,result,provider,model,created_by,created_at) VALUES('incident',?,?,?,?,?,?,?)",
            (inc["id"], status, res, "mock", "m", "admin", now_iso()))
    add("old cause", "low")
    add("=cmd|' /C calc'!A0 pool exhausted", "high")
    add("never finished", "high", status="failed")
    return inc


def test_list_filters_and_latest_only(env):
    inc = seed(env)
    r = env.client.get("/api/rca").json()
    assert r["total"] == 1 and r["items"][0]["confidence"] == "high"  # newest per incident, failed ignored
    assert env.client.get("/api/rca?all_versions=true").json()["total"] == 2
    assert env.client.get("/api/rca?q=pool").json()["total"] == 1
    assert env.client.get("/api/rca?q=nomatch").json()["total"] == 0
    assert env.client.get("/api/rca?confidence=low").json()["total"] == 0
    assert env.client.get(f"/api/rca?severity={inc['severity']}").json()["total"] == 1
    assert env.client.get("/api/rca?severity=LOW" if inc["severity"] != "LOW" else "/api/rca?severity=CRITICAL").json()["total"] == 0


def test_csv_blocks_formula_injection_and_pdf_works(env):
    seed(env)
    csvtxt = env.client.get("/api/rca/export.csv")
    assert csvtxt.status_code == 200 and "text/csv" in csvtxt.headers["content-type"]
    assert "'=cmd" in csvtxt.text and ",=cmd" not in csvtxt.text
    pdf = env.client.get("/api/rca/export.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert env.client.get("/api/rca/export.pdf?q=nomatch").status_code == 404


def test_viewer_can_read_and_requires_login(env):
    seed(env)
    h = make_user(env, "viewer1", "viewer")
    assert env.client.get("/api/rca", headers=h).status_code == 200
    assert env.client.get("/api/rca", headers={"Authorization": ""}).status_code == 401


def test_catalog_admin_only_and_discover(env):
    h = make_user(env, "viewer2", "viewer")
    assert env.client.get("/api/settings/llm/catalog", headers=h).status_code == 403
    cat = env.client.get("/api/settings/llm/catalog").json()
    assert cat["azure_deployments"] and cat["azure_api_versions"] and cat["bedrock_models"]
    r = env.client.post("/api/settings/llm/discover").json()
    assert r["ok"] is False  # provider is mock


def test_list_deployments_parsing():
    from app.llm.azure_openai import AzureOpenAIProvider
    h = lambda req: httpx.Response(200, json={"data": [{"id": "my-gpt", "model": "gpt-4o", "status": "succeeded"}]})
    p = AzureOpenAIProvider("https://x.openai.azure.com", "d", "v", "k", transport=httpx.MockTransport(h))
    assert p.list_deployments() == [{"name": "my-gpt", "model": "gpt-4o", "status": "succeeded"}]
    p2 = AzureOpenAIProvider("https://x.openai.azure.com", "d", "v", "k", transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    try:
        p2.list_deployments()
        assert False
    except Exception as e:
        assert "Foundry" in str(e)
