"""A stand-in for the Switch Web Services API, for tests and safe demos.

It implements the endpoints the connector uses, with the same request and
response shapes as the Switch 24 API docs. It can decrypt the login password
with its own RSA key pair, so the real encryption path is exercised.

Run it as a real HTTP server to try the connector without touching production:

    python tests/fake_switch.py --port 51188

It prints the settings to point the connector at it.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

FIXTURES = Path(__file__).parent / "fixtures"
# A tiny valid PNG, used as the job thumbnail.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _match(job: dict, cond: dict) -> bool:
    if "and" in cond:
        return all(_match(job, c) for c in cond["and"])
    if "or" in cond:
        return any(_match(job, c) for c in cond["or"])
    ((field, ops),) = cond.items()
    ((op, value),) = ops.items()
    actual = str(job.get(field, ""))
    if op == "is":
        return actual == value
    if op == "contains":
        return value.lower() in actual.lower()
    return True  # date operators are not simulated


def make_keypair() -> tuple[rsa.RSAPrivateKey, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, pem


class FakeSwitch:
    def __init__(self, username: str = "demo", password: str = "demo", private_key: rsa.RSAPrivateKey | None = None):
        self.username = username
        self.password = password
        self.private_key = private_key  # when set, the encrypted password is really checked
        self.tokens_issued = 0
        self.valid_token: str | None = None
        self.open_sessions: set[str] = set()
        self.expire_next = False
        self.requests: list[httpx.Request] = []
        self.submitted: list[dict] = []
        self.routed: list[dict] = []
        self.actions: list[tuple[str, str]] = []
        self.report = (FIXTURES / "pitstop_report.xml").read_bytes()
        self.job_file = b"%PDF-1.4\n% fake job content\n"
        self.flows = [{"id": "1", "name": "Customer PDFs", "status": "running"},
                      {"id": "2", "name": "Imposition", "status": "stopped"}]
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
                "isFile": True,
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

    def _password_ok(self, sent: str) -> bool:
        if not sent.startswith("!@$"):
            return False
        if self.private_key is None:
            return True
        try:
            plain = self.private_key.decrypt(base64.b64decode(sent[3:]), padding.PKCS1v15())
        except ValueError:
            return False
        return plain.decode("utf-8") == self.password

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        params = dict(request.url.params)
        auth = request.headers.get("Authorization", "")

        if path == "/login" and request.method == "POST":
            body = json.loads(request.content)
            if body.get("username") != self.username or not self._password_ok(body.get("password", "")):
                return httpx.Response(401, json={"success": False, "error": "Wrong user name or password"})
            self.tokens_issued += 1
            self.valid_token = f"token-{self.tokens_issued}"
            self.open_sessions.add(self.valid_token)
            return httpx.Response(200, json={"success": True, "user": self.username, "token": self.valid_token,
                                             "jobClientAccess": True, "messagesAccess": True, "rushJobsAccess": True})
        if path == "/logout":
            self.open_sessions.discard(auth.removeprefix("Bearer "))
            return httpx.Response(200, json={"status": True})

        if auth != f"Bearer {self.valid_token}" or self.expire_next:
            self.expire_next = False
            return httpx.Response(401, json={"status": False, "error": "Not authorized"})

        if path == "/api/v1/ping":
            return httpx.Response(200, json={"status": True, "data": 0})
        if path == "/api/v1/flows":
            return httpx.Response(200, json=self.flows)
        m = re.fullmatch(r"/api/v1/flows/([\w-]+)", path)
        if m and request.method == "PUT":
            self.actions.append((params.get("action", ""), m.group(1)))
            return httpx.Response(200, json={"status": True, "flowStatus": "running" if params.get("action") == "start"
                                             else "stopped"})
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
        m = re.fullmatch(r"/api/v1/job/report/([\w-]+)", path)
        if m:
            return httpx.Response(200, json={"status": True,
                                             "data": f"http://127.0.0.1:51088/job/report/abc{m.group(1)}"})
        m = re.fullmatch(r"/api/v1/job/([\w-]+)", path)
        if m and request.method == "PUT":
            action = params.get("action", "")
            if action == "route":
                form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
                self.routed.append({"job": m.group(1), **form})
            else:
                self.actions.append((action, m.group(1)))
            return httpx.Response(200, json={"status": True})
        if m and request.method == "GET":
            return httpx.Response(200, json={"status": True, "data": f"http://127.0.0.1:51088/job/dl{m.group(1)}"})
        if path.startswith("/job/report/"):
            return httpx.Response(200, content=self.report, headers={"content-type": "application/xml"})
        if path.startswith("/job/dl"):
            return httpx.Response(200, content=self.job_file, headers={"content-type": "application/pdf"})
        m = re.fullmatch(r"/api/v1/processingjob/([\w-]+)", path)
        if m and request.method == "PUT":
            self.actions.append((params.get("action", ""), m.group(1)))
            return httpx.Response(200, json={"status": True})
        if path == "/api/v1/thumbnails":
            ids = params.get("jobIds", "").split(",")
            return httpx.Response(200, json={"status": True, "data": [
                {"id": i, "thumbnail": base64.b64encode(PNG_1PX).decode()} for i in ids]})
        if path == "/api/v1/messages":
            return httpx.Response(200, json={"status": "success", "messages": [
                {"id": 1, "type": "error", "flow": "Customer PDFs", "element": "PitStop Server",
                 "job": "ORD1001_Brochure.pdf", "message": "Preflight failed with 3 errors"},
                {"id": 2, "type": "error", "flow": "Customer PDFs", "element": "PitStop Server",
                 "job": "ORD1003.pdf", "message": "Preflight failed with 5 errors"},
            ]})
        if path == "/api/v1/graphql":
            return httpx.Response(200, json={"data": {"jobs": {"count": 2}}})
        return httpx.Response(404, json={"status": False, "error": f"no fake for {request.method} {path}"})


def serve(fake: FakeSwitch, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Serve ``fake`` over real HTTP in a background thread. ``port=0`` picks a free port."""
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            request = httpx.Request(self.command, f"http://{host}:{self.server.server_port}{self.path}",
                                    headers=dict(self.headers.items()), content=body)
            with lock:
                response = fake(request)
            payload = response.content
            self.send_response(response.status_code)
            self.send_header("Content-Type", response.headers.get("content-type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_DELETE = _handle

        def log_message(self, *args) -> None:  # keep test output quiet
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a fake Switch Web Services server for trying the connector.")
    parser.add_argument("--port", type=int, default=51188)
    parser.add_argument("--key-out", default="fake-switch-public.pem",
                        help="where to write the fake server's public key")
    args = parser.parse_args()
    key, pem = make_keypair()
    Path(args.key_out).write_bytes(pem)
    server = serve(FakeSwitch(private_key=key), port=args.port)
    print(f"Fake Switch listening on http://127.0.0.1:{server.server_port}  (user demo / password demo)")
    print("Point the connector at it with:")
    print(f"  SWITCH_URL=http://127.0.0.1:{server.server_port}")
    print("  SWITCH_USERNAME=demo")
    print("  SWITCH_PASSWORD=demo")
    print(f"  SWITCH_PUBLIC_KEY_PATH={Path(args.key_out).resolve()}")
    print("Press Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
