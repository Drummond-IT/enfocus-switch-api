"""A small in-memory stand-in for the Switch Web Services API."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from switch_mcp.client import SwitchClient
from switch_mcp.config import Settings
from switch_mcp.server import build_server

FIXTURES = Path(__file__).parent / "fixtures"


def _match(job: dict, cond: dict) -> bool:
    if "and" in cond:
        return all(_match(job, c) for c in cond["and"])
    if "or" in cond:
        return any(_match(job, c) for c in cond["or"])
    (field, ops), = cond.items()
    (op, value), = ops.items()
    actual = str(job.get(field, ""))
    if op == "is":
        return actual == value
    if op == "contains":
        return value.lower() in actual.lower()
    return True  # date operators etc. are not simulated


class FakeSwitch:
    def __init__(self) -> None:
        self.tokens_issued = 0
        self.valid_token: str | None = None
        self.expire_next = False
        self.requests: list[httpx.Request] = []
        self.submitted: list[dict] = []
        self.routed: list[dict] = []
        self.report = (FIXTURES / "pitstop_report.xml").read_bytes()
        self.jobs = [
            {
                "id": "job-1", "name": "ORD1001_Brochure.pdf", "status": "alert", "flowName": "Customer PDFs",
                "flowId": "1", "checkpointName": "Preflight review", "checkpointId": "7",
                "onAlertSince": "2026-09-22T08:00:00", "updated": "2026-09-22T08:00:00", "userName": "web",
                "pages": 8, "type": "pdf", "isFile": True, "processingId": "000A2",
                "allowReplacing": True, "allowReportViewing": True, "hasMetadata": True,
                "outConnections": [{"id": "11", "name": "Approve"}, {"id": "12", "name": "Reject"}],
            },
            {
                "id": "job-2", "name": "ORD1002_Cards.pdf", "status": "processing", "flowName": "Customer PDFs",
                "flowId": "1", "updated": "2026-09-22T09:00:00", "userName": "web", "processingId": "000A3",
            },
        ]
        self.submit_points = [{
            "flowId": "1", "objectId": "3", "name": "Upload PDF", "flowName": "Customer PDFs",
            "flowState": "Running", "accept": "filesOnly", "acceptFileTypes": ["pdf"], "jobRequired": True,
            "metadata": [
                {"id": "spMF_1", "name": "Order number", "type": "string", "valueIsRequired": True,
                 "displayField": True, "format": "ORD\\d+", "value": ""},
                {"id": "spMF_2", "name": "Paper", "type": "enum:Gloss;Matte;Uncoated;", "valueIsRequired": False,
                 "displayField": True, "value": "Gloss"},
            ],
        }]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        params = dict(request.url.params)

        if path == "/login":
            body = json.loads(request.content)
            if body["username"] != "demo" or not body["password"].startswith("!@$"):
                return httpx.Response(401, json={"success": False, "error": "Bad credentials"})
            self.tokens_issued += 1
            self.valid_token = f"token-{self.tokens_issued}"
            return httpx.Response(200, json={"success": True, "user": "demo", "token": self.valid_token,
                                             "jobClientAccess": True, "rushJobsAccess": True})
        if path == "/logout":
            return httpx.Response(200, json={"status": True})

        if request.headers.get("Authorization") != f"Bearer {self.valid_token}" or self.expire_next:
            self.expire_next = False
            return httpx.Response(401, json={"status": False, "error": "Not authorized"})

        if path == "/api/v1/ping":
            return httpx.Response(200, json={"status": True, "data": 0})
        if path == "/api/v1/flows":
            return httpx.Response(200, json=[{"id": "1", "name": "Customer PDFs", "status": "running"},
                                             {"id": "2", "name": "Imposition", "status": "stopped"}])
        if path == "/api/v1/submitpoints":
            return httpx.Response(200, json=self.submit_points)
        if path == "/api/v1/jobs":
            jobs = self.jobs
            if "filter" in params:
                jobs = [j for j in jobs if _match(j, json.loads(params["filter"]))]
            return httpx.Response(200, json={"status": True, "data": jobs})
        if path == "/api/v1/job/metadata":
            return httpx.Response(200, json={"status": True, "metadata": {"job-1": [
                {"id": "cpMF_1", "name": "Approved by", "type": "string", "valueIsRequired": True,
                 "displayField": True, "value": ""},
            ]}})
        if path == "/api/v1/job" and request.method == "POST":
            self.submitted.append({"content_type": request.headers["content-type"], "body": request.content})
            return httpx.Response(200, json={"status": True, "jobId": "job-new"})
        m = re.fullmatch(r"/api/v1/job/([\w-]+)", path)
        if m and request.method == "PUT" and params.get("action") == "route":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.routed.append({"job": m.group(1), **form})
            return httpx.Response(200, json={"status": True})
        m = re.fullmatch(r"/api/v1/job/report/([\w-]+)", path)
        if m:
            return httpx.Response(200, json={"status": True, "data": f"http://127.0.0.1:51088/job/report/abc{m.group(1)}"})
        if path.startswith("/job/report/"):
            return httpx.Response(200, content=self.report, headers={"content-type": "application/xml"})
        if path == "/api/v1/messages":
            return httpx.Response(200, json={"status": "success", "messages": [
                {"id": 1, "type": "error", "flow": "Customer PDFs", "element": "PitStop Server",
                 "job": "ORD1001_Brochure.pdf", "message": "Preflight failed with 3 errors"},
                {"id": 2, "type": "error", "flow": "Customer PDFs", "element": "PitStop Server",
                 "job": "ORD1003.pdf", "message": "Preflight failed with 5 errors"},
            ]})
        return httpx.Response(404, json={"status": False, "error": f"no fake for {request.method} {path}"})


@pytest.fixture
def fake() -> FakeSwitch:
    return FakeSwitch()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    return Settings(url="http://switch.test:51088", username="demo", password="demo",
                    upload_dirs=[uploads], download_dir=tmp_path / "downloads")


@pytest.fixture
def client(settings: Settings, fake: FakeSwitch) -> SwitchClient:
    return SwitchClient(settings, transport=httpx.MockTransport(fake))


@pytest.fixture
def make_server(settings: Settings, fake: FakeSwitch):
    def _make(**overrides):
        s = Settings(**{**settings.__dict__, **overrides})
        return build_server(s, SwitchClient(s, transport=httpx.MockTransport(fake)))
    return _make
