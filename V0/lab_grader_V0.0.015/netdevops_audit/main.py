"""FastAPI application exposing async device inspection and an HTML report visualizer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from audit_engine import NetworkAuditRuleEngine
from collector import AsyncNetworkCollector, DeviceTarget
from parser_layer import Finding, parse_interface_status

app = FastAPI(title="NetDevOps Audit API")

# In-memory job tracker: hostname -> job state. Not persisted; resets on process restart.
JOB_TRACKER: Dict[str, Dict[str, Any]] = {}

JOB_STATUS_PENDING = "PENDING"
JOB_STATUS_RUNNING = "RUNNING"
JOB_STATUS_DONE = "DONE"
JOB_STATUS_FAILED = "FAILED"

_rule_engine = NetworkAuditRuleEngine()


class InspectRequest(BaseModel):
    hostname: str
    host: str
    device_type: str
    username: str
    password: str
    command: str = "show interfaces status"
    port: int = 22


async def _run_inspection(job_key: str, target: DeviceTarget, command: str) -> None:
    JOB_TRACKER[job_key]["status"] = JOB_STATUS_RUNNING

    collector = AsyncNetworkCollector(max_concurrency=15)
    results = await collector.collect_all([target], command)
    result = results[0]

    if not result["success"]:
        JOB_TRACKER[job_key].update(
            {
                "status": JOB_STATUS_FAILED,
                "error": result["result"],
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return

    findings = parse_interface_status(result["result"], target.hostname)
    score = _rule_engine.calculate_device_score(findings)

    JOB_TRACKER[job_key].update(
        {
            "status": JOB_STATUS_DONE,
            "findings": [f.serialize() for f in findings],
            "health_score": score,
            "raw_result": result,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.post("/api/v1/inspect")
async def inspect_device(request: InspectRequest, background_tasks: BackgroundTasks) -> Dict[str, str]:
    """Register an async CLI inspection job for a single device."""
    target = DeviceTarget(
        hostname=request.hostname,
        host=request.host,
        device_type=request.device_type,
        username=request.username,
        password=request.password,
        port=request.port,
    )

    JOB_TRACKER[request.hostname] = {
        "status": JOB_STATUS_PENDING,
        "hostname": request.hostname,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "findings": [],
        "health_score": None,
        "error": None,
    }

    background_tasks.add_task(_run_inspection, request.hostname, target, request.command)

    return {"hostname": request.hostname, "status": JOB_STATUS_PENDING}


@app.get("/api/v1/inspect/{hostname}/findings")
async def get_findings(hostname: str) -> List[Dict[str, Any]]:
    """Return the findings for a completed job, or 404 if the hostname is unknown."""
    job = JOB_TRACKER.get(hostname)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No inspection job found for hostname '{hostname}'")
    return job["findings"]


def _severity_counts(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"CRITICAL": 0, "WARNING": 0, "INFO": 0}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    return counts


def _severity_class(severity: str) -> str:
    return {"CRITICAL": "sev-critical", "WARNING": "sev-warning", "INFO": "sev-info"}.get(severity, "sev-info")


def _score_class(score: float) -> str:
    if score >= 90:
        return "score-good"
    if score >= 70:
        return "score-warn"
    return "score-bad"


def render_report_html(hostname: str, job: Dict[str, Any]) -> str:
    status = job["status"]
    findings = job.get("findings", []) or []
    health_score = job.get("health_score") or {}
    h_d = health_score.get("H_d", 0.0)
    counts = _severity_counts(findings)

    findings_rows = "\n".join(
        f"""<tr class="{_severity_class(f['severity'])}">
            <td>{f['node_id']}</td>
            <td>{f['category']}</td>
            <td><span class="badge {_severity_class(f['severity'])}">{f['severity']}</span></td>
            <td>{f['message']}</td>
            <td>{f['measured_value']}</td>
        </tr>"""
        for f in findings
    )

    if not findings_rows:
        findings_rows = '<tr><td colspan="5" class="empty-row">No findings recorded</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Audit Report — {hostname}</title>
<style>
  :root {{
    color-scheme: dark;
  }}
  body {{
    background: #0d1117;
    color: #c9d1d9;
    font-family: 'Segoe UI', Consolas, monospace;
    margin: 0;
    padding: 2rem;
  }}
  h1 {{
    font-size: 1.4rem;
    color: #f0f6fc;
  }}
  .status-line {{
    color: #8b949e;
    margin-bottom: 1.5rem;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
  }}
  .card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 1rem;
    text-align: center;
  }}
  .card .label {{
    color: #8b949e;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  .card .value {{
    font-size: 2rem;
    font-weight: 600;
    margin-top: 0.4rem;
  }}
  .score-good .value {{ color: #3fb950; }}
  .score-warn .value {{ color: #d29922; }}
  .score-bad .value {{ color: #f85149; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    overflow: hidden;
  }}
  th, td {{
    padding: 0.6rem 0.8rem;
    text-align: left;
    border-bottom: 1px solid #21262d;
  }}
  th {{
    background: #21262d;
    color: #f0f6fc;
    font-size: 0.85rem;
    text-transform: uppercase;
  }}
  .badge {{
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
  }}
  .sev-critical {{ color: #f85149; }}
  .badge.sev-critical {{ background: rgba(248, 81, 73, 0.15); }}
  .sev-warning {{ color: #d29922; }}
  .badge.sev-warning {{ background: rgba(210, 153, 34, 0.15); }}
  .sev-info {{ color: #58a6ff; }}
  .badge.sev-info {{ background: rgba(88, 166, 255, 0.15); }}
  .empty-row {{
    text-align: center;
    color: #8b949e;
  }}
</style>
</head>
<body>
  <h1>Audit Report — {hostname}</h1>
  <div class="status-line">Job status: {status}</div>

  <div class="grid">
    <div class="card {_score_class(h_d)}">
      <div class="label">Health Score (H_d)</div>
      <div class="value">{h_d}</div>
    </div>
    <div class="card">
      <div class="label">Critical</div>
      <div class="value sev-critical">{counts['CRITICAL']}</div>
    </div>
    <div class="card">
      <div class="label">Warning</div>
      <div class="value sev-warning">{counts['WARNING']}</div>
    </div>
    <div class="card">
      <div class="label">Info</div>
      <div class="value sev-info">{counts['INFO']}</div>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Node</th>
        <th>Category</th>
        <th>Severity</th>
        <th>Message</th>
        <th>Measured Value</th>
      </tr>
    </thead>
    <tbody>
      {findings_rows}
    </tbody>
  </table>
</body>
</html>"""


@app.get("/report/{hostname}", response_class=HTMLResponse)
async def get_report(hostname: str) -> HTMLResponse:
    """Render a dark-mode CSS grid HTML report for a given device's inspection job."""
    job = JOB_TRACKER.get(hostname)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No inspection job found for hostname '{hostname}'")
    return HTMLResponse(content=render_report_html(hostname, job))


import asyncio

import pytest
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def clear_job_tracker():
    JOB_TRACKER.clear()
    yield
    JOB_TRACKER.clear()

@pytest.fixture
def client():
    return TestClient(app)

def test_inspect_registers_job_and_returns_pending(client, monkeypatch):
    async def fake_run_inspection(job_key, target, command):
        return None

    monkeypatch.setattr("main._run_inspection", fake_run_inspection)

    response = client.post(
        "/api/v1/inspect",
        json={
            "hostname": "sw1",
            "host": "192.0.2.1",
            "device_type": "arista_eos",
            "username": "admin",
            "password": "admin",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"
    assert "sw1" in JOB_TRACKER

def test_get_findings_404_for_unknown_hostname(client):
    response = client.get("/api/v1/inspect/ghost-host/findings")
    assert response.status_code == 404

def test_get_findings_returns_serialized_list(client):
    JOB_TRACKER["sw2"] = {
        "status": "DONE",
        "findings": [
            Finding(
                node_id="sw2",
                category="Physical",
                severity="WARNING",
                message="Interface Et2 is not connected",
                measured_value="notconnect",
            ).serialize()
        ],
        "health_score": {"H_d": 94.0, "breakdown": {}},
    }

    response = client.get("/api/v1/inspect/sw2/findings")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["severity"] == "WARNING"

def test_get_report_404_for_unknown_hostname(client):
    response = client.get("/report/ghost-host")
    assert response.status_code == 404

def test_get_report_renders_html_with_score_and_findings(client):
    JOB_TRACKER["sw3"] = {
        "status": "DONE",
        "findings": [
            Finding(
                node_id="sw3",
                category="Physical",
                severity="CRITICAL",
                message="Interface Et3 is running half duplex",
                measured_value="half",
            ).serialize()
        ],
        "health_score": {"H_d": 94.0, "breakdown": {}},
    }

    response = client.get("/report/sw3")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "sw3" in body
    assert "94.0" in body
    assert "half duplex" in body

def test_render_report_html_handles_no_findings():
    job = {"status": "DONE", "findings": [], "health_score": {"H_d": 100.0}}
    html = render_report_html("sw4", job)
    assert "No findings recorded" in html
    assert "100.0" in html

def test_run_inspection_updates_tracker_on_success(monkeypatch):
    JOB_TRACKER["sw5"] = {"status": "PENDING", "hostname": "sw5", "findings": [], "health_score": None}

    async def fake_collect_all(self, devices, command):
        return [{"hostname": "sw5", "success": True, "result": "Et1 up", "execution_time_sec": 0.1}]

    def fake_parse_interface_status(raw_cli, hostname):
        return []

    monkeypatch.setattr(AsyncNetworkCollector, "collect_all", fake_collect_all)
    monkeypatch.setattr("main.parse_interface_status", fake_parse_interface_status)

    target = DeviceTarget(hostname="sw5", host="192.0.2.5", device_type="arista_eos", username="a", password="b")
    asyncio.run(_run_inspection("sw5", target, "show interfaces status"))

    assert JOB_TRACKER["sw5"]["status"] == "DONE"
    assert JOB_TRACKER["sw5"]["health_score"]["H_d"] == 100.0

def test_run_inspection_marks_failed_on_collector_error(monkeypatch):
    JOB_TRACKER["sw6"] = {"status": "PENDING", "hostname": "sw6", "findings": [], "health_score": None}

    async def fake_collect_all(self, devices, command):
        return [{"hostname": "sw6", "success": False, "result": "ConnectionError: refused", "execution_time_sec": 0.1}]

    monkeypatch.setattr(AsyncNetworkCollector, "collect_all", fake_collect_all)

    target = DeviceTarget(hostname="sw6", host="192.0.2.6", device_type="arista_eos", username="a", password="b")
    asyncio.run(_run_inspection("sw6", target, "show interfaces status"))

    assert JOB_TRACKER["sw6"]["status"] == "FAILED"
    assert "ConnectionError" in JOB_TRACKER["sw6"]["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
