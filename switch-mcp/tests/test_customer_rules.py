import json
from pathlib import Path

import pytest
from mcp.client import Client

from switch_mcp import preflight
from switch_mcp.automation.customer_rules import CustomerRules, apply_to_analysis

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLE = Path(__file__).resolve().parents[1] / "customer_rules.example.json"


def _rules(tmp_path, data):
    f = tmp_path / "rules.json"
    f.write_text(json.dumps(data))
    return CustomerRules.load(f)


def test_example_file_loads_and_aliases_match():
    rules = CustomerRules.load(EXAMPLE)
    assert rules.find("example customer inc.").customer == "Example Customer Inc"
    assert rules.find("EXCUST").customer == "Example Customer Inc"
    assert rules.find("Someone Else") is None and rules.find(None) is None


@pytest.mark.parametrize("data,error", [
    ([1], "JSON object"),
    ({"A": {"accept": ["low_res"]}}, "unknown categories"),
    ({"A": {"colour": "red"}}, "unknown keys"),
    ({"A": {"ticket_defaults": {"pages": 4}}}, "ticket_defaults may only set"),
])
def test_invalid_rules_are_explained(tmp_path, data, error):
    with pytest.raises(ValueError, match=error):
        _rules(tmp_path, data)


def test_accepting_low_res_and_fixing_fonts_changes_the_verdict(tmp_path):
    analysis = preflight.analyze(preflight.parse_report((FIXTURES / "pitstop_report.xml").read_bytes()))
    assert analysis["verdict"] == "needs_customer"
    rule = _rules(tmp_path, {"ACME": {"accept": ["low_resolution"], "prepress_fixes": ["fonts_not_embedded"],
                                      "notes": "Waiver on file"}}).find("acme")
    adjusted = apply_to_analysis(analysis, rule)
    assert adjusted["verdict"] == "prepress_can_fix"
    assert analysis["verdict"] == "needs_customer", "the original analysis is not modified"
    low_res = next(i for i in adjusted["issues"] if i["category"] == "low_resolution")
    assert (low_res["severity"], low_res["original_severity"]) == ("accepted", "error")
    csr = preflight.render(adjusted, "csr")
    assert "Accepted per customer agreement (ACME): Low-resolution images" in csr and "Waiver on file" in csr
    assert "Low-resolution" not in preflight.render(adjusted, "customer")


async def test_tools_apply_customer_rules(make_server, settings, fake, tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"ACME": {"accept": ["low_resolution"], "prepress_fixes": ["fonts_not_embedded"],
                                          "ticket_defaults": {"bleed_in": 0.0625}}}))
    server = make_server(customer_rules=str(rules))
    fake.jobs[0]["customFields"] = [{"name": "Customer name", "value": "ACME"}]
    async with Client(server) as c:
        r = await c.call_tool("explain_job_report", {"job_id": "job-1", "audience": "csr"})
        assert r.structured_content["customer"] == "ACME"
        assert r.structured_content["verdict"] == "prepress_can_fix"
        r = await c.call_tool("explain_preflight_report", {"report": "Error: image resolution 72 ppi",
                                                           "customer": "acme"})
        assert r.structured_content["verdict"] == "ready"


async def test_broken_rules_file_is_a_config_error(make_server, tmp_path):
    bad = tmp_path / "rules.json"
    bad.write_text("{not json")
    async with Client(make_server(customer_rules=str(bad))) as c:
        r = await c.call_tool("list_flows", {})
        assert r.is_error and "CUSTOMER_RULES" in r.content[0].text


async def test_ticket_defaults_from_customer_rule(make_server, settings, tmp_path):
    from test_automation import CMYK_BOX, make_pdf

    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"ACME": {"ticket_defaults": {"bleed_in": 0.0625}}}))
    pdf = settings.upload_dirs[0] / "a.pdf"
    pdf.write_bytes(make_pdf([CMYK_BOX], bleed_in=0.0625))
    server = make_server(customer_rules=str(rules))
    async with Client(server) as c:
        plain = await c.call_tool("compare_file_to_ticket", {"file_path": str(pdf), "trim": "8.5x11"})
        acme = await c.call_tool("compare_file_to_ticket", {"file_path": str(pdf), "trim": "8.5x11",
                                                            "customer": "ACME"})
    assert [i["check"] for i in plain.structured_content["issues"]] == ["bleed"]  # house default 0.125"
    assert acme.structured_content["verdict"] == "matches_ticket"  # ACME's 1/16" bleed is fine
