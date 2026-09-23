import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from switch_mcp.client import SwitchClient, SwitchError, encrypt_password


def test_encrypt_password_roundtrip():
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    token = encrypt_password("s3cret", pem)
    assert token.startswith("!@$")
    assert key.decrypt(base64.b64decode(token[3:]), padding.PKCS1v15()) == b"s3cret"


def test_bundled_key_is_used_by_default(settings):
    from switch_mcp.client import _load_public_key

    assert b"BEGIN PUBLIC KEY" in _load_public_key("")


async def test_login_and_relogin_on_expired_token(client, fake):
    flows = await client.list_flows()
    assert [f["name"] for f in flows] == ["Customer PDFs", "Imposition"]
    assert fake.tokens_issued == 1
    fake.expire_next = True
    await client.list_flows()
    assert fake.tokens_issued == 2
    assert all(r.url.params.get("lang") == "enUS" for r in fake.requests if r.url.path.startswith("/api/"))


async def test_bad_credentials(settings, fake):
    settings.username = "nobody"
    c = SwitchClient(settings, transport=httpx.MockTransport(fake))
    with pytest.raises(SwitchError, match="Wrong user name or password"):
        await c.list_flows()


async def test_status_false_raises(client, fake):
    with pytest.raises(SwitchError, match="no fake"):
        await client._request("GET", "/api/v1/unknown")


async def test_route_sends_json_encoded_form_fields(client, fake):
    await client.route_job("job-1", ["11"], [{"id": "cpMF_1", "name": "Approved by", "value": "Sam"}])
    routed = fake.routed[0]
    assert json.loads(routed["connections"]) == ["11"]
    assert json.loads(routed["metadata"])[0]["value"] == "Sam"


async def test_report_link_is_rewritten_to_configured_host(client, fake):
    content = await client.download_report("job-1", max_bytes=10_000_000)
    assert content.startswith(b"<?xml")
    fetched = fake.requests[-1]
    assert fetched.url.host == "switch.test" and fetched.url.path == "/job/report/abcjob-1"
    assert "lang" not in fetched.url.params


async def test_submit_job_multipart(client, fake, settings):
    pdf = settings.upload_dirs[0] / "art.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    result = await client.submit_job("1", "3", pdf.name, pdf.read_bytes(), "ORD1001.pdf",
                                     [{"id": "spMF_1", "name": "Order", "value": "1"}])
    assert result["jobId"] == "job-new"
    body = fake.submitted[0]["body"]
    assert fake.submitted[0]["content_type"].startswith("multipart/form-data")
    for part in (b'name="flowId"', b'name="objectId"', b'name="jobName"', b"ORD1001.pdf", b"%PDF-1.4 test"):
        assert part in body


async def test_download_size_cap(client, fake):
    fake.report = b"<x>" + b"a" * 5000 + b"</x>"
    with pytest.raises(SwitchError, match="larger than"):
        await client.download_report("job-1", max_bytes=1000)


async def test_hostile_link_paths_stay_on_configured_host(client, fake):
    assert client._link_path("https://evil.example//other.example/x?a=1") == "/other.example/x?a=1"
    assert client._link_path("http://127.0.0.1:51088/job/abc") == "/job/abc"
