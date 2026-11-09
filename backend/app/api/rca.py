"""RCA Reports: every finished root-cause analysis, searchable, with CSV / PDF export."""
from __future__ import annotations

import csv
import io
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response

from ..context import AppContext
from ..db import to_iso, utcnow
from ..services import guide
from .deps import get_ctx, require

router = APIRouter(prefix="/api/rca")
EXPORT_CAP = 500


def _rows(ctx: AppContext, q: str = "", severity: str = "", confidence: str = "", service: str = "",
          status: str = "", days: int = 0, all_versions: bool = False, scan: int = 3000) -> list[dict]:
    sql = ("SELECT i.id, i.incident_id, i.result, i.provider, i.model, i.created_at, i.created_by, "
           "n.title, n.severity, n.service, n.status AS incident_status, n.event_count "
           "FROM investigations i JOIN incidents n ON n.id=i.incident_id "
           "WHERE i.status='done' AND i.kind='incident' AND i.result IS NOT NULL")
    p: list = []
    if days:
        sql += " AND i.created_at>=?"
        p.append(to_iso(utcnow() - timedelta(days=days)))
    if severity:
        sql += " AND n.severity=?"
        p.append(severity.upper())
    if status:
        sql += " AND n.status=?"
        p.append(status)
    if service:
        sql += " AND n.service=?"
        p.append(service)
    sql += " ORDER BY i.id DESC LIMIT ?"
    p.append(scan)
    out, seen = [], set()
    needle = q.strip().lower()
    for r in ctx.db.query(sql, tuple(p)):
        if not all_versions:
            if r["incident_id"] in seen:
                continue
            seen.add(r["incident_id"])
        try:
            res = json.loads(r["result"])
        except (TypeError, json.JSONDecodeError):
            continue
        conf = str(res.get("confidence") or "n/a").lower()
        if confidence and conf != confidence.lower():
            continue
        g = res.get("grounding") or {}
        item = {
            "rca_id": r["id"], "incident_id": r["incident_id"], "title": r["title"], "severity": r["severity"],
            "service": r["service"], "incident_status": r["incident_status"], "confidence": conf,
            "root_cause": str(res.get("root_cause") or ""), "summary": str(res.get("summary") or ""),
            "recommendations": [str(x) for x in (res.get("recommendations") or [])],
            "evidence_verified": g.get("verified", 0), "evidence_total": g.get("total", 0),
            "provider": r["provider"], "model": r["model"], "created_at": r["created_at"], "created_by": r["created_by"],
        }
        if needle:
            hay = " ".join([item["title"] or "", item["service"] or "", item["root_cause"], item["summary"],
                            " ".join(item["recommendations"])]).lower()
            if needle not in hay:
                continue
        out.append(item)
    return out


def _filters(q: str, severity: str, confidence: str, service: str, status: str, days: int, all_versions: bool) -> dict:
    return {"q": q, "severity": severity, "confidence": confidence, "service": service, "status": status,
            "days": max(0, min(days, 3650)), "all_versions": all_versions}


@router.get("")
def list_rca(q: str = "", severity: str = "", confidence: str = "", service: str = "", status: str = "",
             days: int = 0, all_versions: bool = False, limit: int = 50, offset: int = 0,
             _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    rows = _rows(ctx, **_filters(q, severity, confidence, service, status, days, all_versions))
    limit = max(1, min(limit, 200))
    svc = sorted({r["service"] for r in _rows(ctx, all_versions=False) if r["service"]})
    return {"total": len(rows), "items": rows[max(0, offset):max(0, offset) + limit], "services": svc}


def _csv_safe(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # block spreadsheet formula injection


@router.get("/export.csv")
def export_csv(q: str = "", severity: str = "", confidence: str = "", service: str = "", status: str = "",
               days: int = 0, all_versions: bool = False,
               _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    rows = _rows(ctx, **_filters(q, severity, confidence, service, status, days, all_versions))[:EXPORT_CAP]
    cols = ["rca_id", "incident_id", "title", "severity", "service", "incident_status", "confidence", "root_cause",
            "summary", "recommendations", "evidence_verified", "evidence_total", "model", "created_at"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([_csv_safe("; ".join(r[c]) if c == "recommendations" else r[c]) for c in cols])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="rca-reports.csv"'})


def _markdown(rows: list[dict], total: int) -> str:
    md = ["# RCA Reports", f"{len(rows)} report(s)" + (f" shown of {total} (export is capped at {EXPORT_CAP})" if total > len(rows) else ""), ""]
    for r in rows:
        md += ["---", f"## Incident #{r['incident_id']}: {r['title']}",
               f"**Severity:** {r['severity']} | **Service:** {r['service'] or '-'} | **Status:** {r['incident_status']} | **Confidence:** {r['confidence']}",
               f"**Generated:** {r['created_at']} | **Model:** {r['model'] or '-'} | **Evidence verified:** {r['evidence_verified']}/{r['evidence_total']}",
               "", "### Root cause", r["root_cause"] or "Not stated.", "", "### Summary", r["summary"] or "-", ""]
        if r["recommendations"]:
            md += ["### Recommendations"] + [f"- {x}" for x in r["recommendations"]] + [""]
    return "\n".join(md)


@router.get("/export.pdf")
def export_pdf(q: str = "", severity: str = "", confidence: str = "", service: str = "", status: str = "",
               days: int = 0, all_versions: bool = False,
               _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    rows = _rows(ctx, **_filters(q, severity, confidence, service, status, days, all_versions))
    if not rows:
        raise HTTPException(404, "No RCA reports match these filters")
    return Response(guide.to_pdf(_markdown(rows[:EXPORT_CAP], len(rows)), "RCA Reports"), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="rca-reports.pdf"'})
