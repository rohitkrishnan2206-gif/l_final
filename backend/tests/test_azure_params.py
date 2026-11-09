import json

import httpx

from app.llm.azure_openai import AzureOpenAIProvider


def _prov(dep, handler):
    return AzureOpenAIProvider("https://x.openai.azure.com", dep, "2024-08-01-preview", "k",
                               transport=httpx.MockTransport(handler), sleep=lambda s: None)


OK = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": {}}


def test_gpt5_uses_new_params():
    seen = []

    def h(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json=OK)

    _prov("gpt-5-6-terra", h).complete("s", [{"role": "user", "content": "x"}])
    assert "max_completion_tokens" in seen[0] and "max_tokens" not in seen[0] and "temperature" not in seen[0]


def test_old_model_learns_from_400():
    seen = []

    def h(req):
        b = json.loads(req.content)
        seen.append(b)
        if "max_tokens" in b:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens'"}})
        return httpx.Response(200, json=OK)

    p = _prov("my-deploy", h)
    assert p.complete("s", [{"role": "user", "content": "x"}]).text == "hi"
    assert "max_completion_tokens" in seen[-1]


def test_azure_test_reports_empty_workspace():
    from app.connectors.azure import AzureConnector

    def h(req):
        if "login.microsoftonline" in str(req.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        q = json.loads(req.content)["query"]
        if q.startswith("print"):
            return httpx.Response(200, json={"tables": [{"columns": [{"name": "ok"}], "rows": [[1]]}]})
        return httpx.Response(200, json={"tables": [{"columns": [{"name": "T"}, {"name": "n"}], "rows": [["AzureDiagnostics", 50]]}]})

    c = AzureConnector({"workspace_id": "w", "tenant_id": "t", "client_id": "c"}, {"client_secret": "s"}, transport=httpx.MockTransport(h), sleep=lambda s: None)
    r = c.test()
    assert r["tables"] == {"AzureDiagnostics": 50} and "AzureDiagnostics" in r["note"]
