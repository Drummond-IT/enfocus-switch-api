"""Async client for the Enfocus Switch Web Services REST API (``/api/v1``).

Endpoint reference: https://www.enfocus.com/manuals/DeveloperGuide/WebServices/24/index.html
"""

from __future__ import annotations

import base64
import json
import mimetypes
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .config import Settings


class SwitchError(RuntimeError):
    """Raised when Switch rejects a call or returns ``status: false``."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def encrypt_password(password: str, public_key_pem: bytes) -> str:
    """RSA/PKCS#1 v1.5 encrypt, base64 encode and prefix with ``!@$`` as Switch requires."""
    key = serialization.load_pem_public_key(public_key_pem)
    encrypted = key.encrypt(password.encode("utf-8"), padding.PKCS1v15())  # type: ignore[union-attr]
    return "!@$" + base64.b64encode(encrypted).decode("ascii")


def _load_public_key(path: str) -> bytes:
    if path:
        return Path(path).expanduser().read_bytes()
    return resources.files(__package__).joinpath("switch_public_key.pem").read_bytes()


class SwitchClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._token: str | None = None
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        # Created lazily so the client can be reused after aclose() (one MCP session ends, another starts).
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.settings.url,
                timeout=self.settings.timeout,
                verify=self.settings.verify_tls,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is None or self._client.is_closed:
            return
        if self._token:
            try:
                await self._client.get("/logout", headers=self._auth_headers())
            except httpx.HTTPError:
                pass
            self._token = None
        await self._client.aclose()

    # ------------------------------------------------------------------ auth

    async def login(self) -> dict[str, Any]:
        if not self.settings.username:
            raise SwitchError("SWITCH_USERNAME is not configured.")
        body = {
            "username": self.settings.username,
            "password": encrypt_password(
                self.settings.password, _load_public_key(self.settings.public_key_path)
            ),
        }
        resp = await self._http.post("/login", params={"lang": self.settings.lang}, json=body)
        data = self._decode(resp)
        if not data.get("success") or not data.get("token"):
            raise SwitchError(data.get("error") or "Login to Switch failed.", resp.status_code)
        self._token = data["token"]
        return data

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _request(
        self, method: str, path: str, *, raw: bool = False, add_lang: bool = True, **kwargs: Any
    ) -> Any:
        """Send an authenticated request, logging in (again) when needed."""
        params = dict(kwargs.pop("params", None) or {})
        if add_lang:
            params.setdefault("lang", self.settings.lang)
        headers = dict(kwargs.pop("headers", None) or {})
        for attempt in range(2):
            if self._token is None:
                await self.login()
            resp = await self._http.request(
                method, path, params=params, headers={**headers, **self._auth_headers()}, **kwargs
            )
            if resp.status_code == 401 and attempt == 0:
                self._token = None
                continue
            break
        if raw:
            if resp.status_code >= 400:
                raise SwitchError(f"Switch returned HTTP {resp.status_code}", resp.status_code)
            return resp
        return self._decode(resp)

    @staticmethod
    def _decode(resp: httpx.Response) -> Any:
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code >= 400:
            msg = data.get("error") if isinstance(data, dict) else None
            raise SwitchError(msg or f"Switch returned HTTP {resp.status_code}: {resp.text[:300]}", resp.status_code)
        if data is None:
            raise SwitchError(f"Switch returned a non-JSON response: {resp.text[:300]}", resp.status_code)
        if isinstance(data, dict) and data.get("status") in (False, "error"):
            raise SwitchError(data.get("error") or "Switch reported a failure.", resp.status_code)
        return data

    # ---------------------------------------------------------------- server

    async def ping(self, refresh: bool = True) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/ping", params={"refresh": str(refresh).lower()})

    # ----------------------------------------------------------------- flows

    async def list_flows(self, fields: list[str] | None = None, ids: list[str] | None = None) -> list[dict]:
        params: dict[str, str] = {}
        if fields:
            params["fields"] = ",".join(fields)
        if ids:
            params["ids"] = ",".join(ids)
        data = await self._request("GET", "/api/v1/flows", params=params)
        return data if isinstance(data, list) else data.get("data", [])

    async def set_flow_state(self, flow_id: str, action: str) -> dict[str, Any]:
        if action not in ("start", "stop"):
            raise ValueError("action must be 'start' or 'stop'")
        return await self._request("PUT", f"/api/v1/flows/{flow_id}", params={"action": action})

    # --------------------------------------------------------- submit points

    async def list_submit_points(self, submit_point_id: str | None = None) -> list[dict]:
        path = "/api/v1/submitpoints" + (f"/{submit_point_id}" if submit_point_id else "")
        data = await self._request("GET", path)
        if isinstance(data, dict):
            data = data.get("data", [data])
        return data

    async def submit_job(
        self,
        flow_id: str,
        object_id: str,
        file_path: Path,
        job_name: str | None = None,
        metadata: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Submit a single file to a Submit point."""
        stat = file_path.stat()
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        form = {
            "flowId": str(flow_id),
            "objectId": str(object_id),
            "jobName": job_name or file_path.name,
            "filePath": file_path.name,
            "origin": str(file_path),
            "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "metadata": json.dumps(metadata or []),
        }
        # Bytes rather than a file handle so the body can be re-sent after a re-login.
        files = {"file": (file_path.name, file_path.read_bytes(), mime)}
        return await self._request("POST", "/api/v1/job", data=form, files=files)

    # ------------------------------------------------------------------ jobs

    async def list_jobs(
        self,
        filter_query: dict | str | None = None,
        fields: list[str] | None = None,
        sort: str | None = None,
        limit: int | None = None,
        last_updated: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {}
        if filter_query:
            params["filter"] = filter_query if isinstance(filter_query, str) else json.dumps(filter_query)
        if fields:
            params["fields"] = ",".join(fields)
        if sort:
            params["sort"] = sort
        if limit:
            params["limit"] = str(limit)
        if last_updated:
            params["lastUpdated"] = last_updated
        return await self._request("GET", "/api/v1/jobs", params=params)

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        data = await self.list_jobs(filter_query={"and": [{"id": {"is": job_id}}]}, limit=1)
        jobs = data.get("data") or []
        return jobs[0] if jobs else None

    async def job_metadata(self, job_ids: list[str], readonly: bool | None = None) -> dict[str, Any]:
        params = {"ids": ",".join(job_ids)}
        if readonly is not None:
            params["readonly"] = str(readonly).lower()
        data = await self._request("GET", "/api/v1/job/metadata", params=params)
        return data.get("metadata", {})

    async def route_job(
        self,
        job_id: str,
        connection_ids: list[str],
        metadata: list[dict[str, str]] | None = None,
        updated: str | None = None,
    ) -> dict[str, Any]:
        # Switch expects form fields whose values are JSON-encoded.
        form = {"connections": json.dumps([str(c) for c in connection_ids]), "metadata": json.dumps(metadata or [])}
        if updated:
            form["updated"] = updated
        return await self._request("PUT", f"/api/v1/job/{job_id}", params={"action": "route"}, data=form)

    async def replace_job(self, job_id: str, file_path: Path, updated: str | None = None) -> dict[str, Any]:
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        form = {"updated": updated} if updated else {}
        files = {"file[0][file]": (file_path.name, file_path.read_bytes(), mime)}
        return await self._request(
            "PUT", f"/api/v1/job/{job_id}", params={"action": "replace"}, data=form, files=files
        )

    async def set_job_lock(self, job_id: str, locked: bool) -> dict[str, Any]:
        action = "lock" if locked else "unlock"
        return await self._request("PUT", f"/api/v1/job/{job_id}", params={"action": action})

    async def set_rush(self, processing_id: str, rush: bool) -> dict[str, Any]:
        action = "rush" if rush else "unrush"
        return await self._request("PUT", f"/api/v1/processingjob/{processing_id}", params={"action": action})

    async def thumbnails(self, job_ids: list[str]) -> list[dict[str, str]]:
        data = await self._request("GET", "/api/v1/thumbnails", params={"jobIds": ",".join(job_ids)})
        return data.get("data", [])

    # ----------------------------------------------------- downloads/reports

    async def _download_link(self, path: str) -> str:
        data = await self._request("GET", path)
        link = data.get("data")
        if not link:
            raise SwitchError("Switch did not return a download link.")
        return link

    async def fetch_link(self, link: str) -> httpx.Response:
        """Download a link returned by Switch.

        Switch builds links with its own idea of its address (often 127.0.0.1),
        so only the path and query are kept and sent to the configured URL.
        """
        parts = urlsplit(link)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        return await self._request("GET", target, raw=True, add_lang=False)

    async def download_job(self, job_id: str) -> httpx.Response:
        return await self.fetch_link(await self._download_link(f"/api/v1/job/{job_id}"))

    async def download_report(self, job_id: str) -> httpx.Response:
        return await self.fetch_link(await self._download_link(f"/api/v1/job/report/{job_id}"))

    # -------------------------------------------------------------- messages

    async def messages(self, **filters: Any) -> dict[str, Any]:
        params = {k: str(v) for k, v in filters.items() if v not in (None, "")}
        return await self._request("GET", "/api/v1/messages", params=params)

    # --------------------------------------------------------------- graphql

    async def graphql(self, query: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/graphql", content=query.encode("utf-8"), headers={"Content-Type": "application/graphql"}
        )
