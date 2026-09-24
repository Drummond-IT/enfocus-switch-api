"""RFQ completeness check (C2)."""

from __future__ import annotations

from datetime import date

from mcp.client import Client

from switch_mcp.automation.rfq import validate_rfq


def test_complete_business_card_request():
    r = validate_rfq({"product": "Business cards", "quantity": "500, 1000 and 2.5k", "trim": "3.5 x 2",
                      "colors": "4/4", "stock": "14 pt gloss", "due_date": "2030-01-31",
                      "delivery": "pickup", "artwork": "PDF supplied"})
    assert r["ready_to_quote"] and r["product_family"] == "business card"
    assert r["spec"]["quantities"] == [500, 1000, 2500] and r["questions_for_customer"] == []


def test_booklet_missing_details_and_saddle_stitch_pages():
    r = validate_rfq({"product": "booklet", "quantity": 250, "pages": 22, "binding": "saddle stitch",
                      "colors": "4/4"}, today=date(2026, 1, 1))
    assert not r["ready_to_quote"]
    assert r["missing"] == ["trim", "stock"]
    assert any("multiple of 4" in p["message"] and "24" in p["message"] for p in r["problems"])
    assert any("finished size" in q for q in r["questions_for_customer"])


def test_bad_values_become_problems_not_crashes():
    r = validate_rfq({"trim": "big", "quantity": "0", "due_date": "2020-01-01", "wibble": 1})
    fields = {p["field"] for p in r["problems"]}
    assert {"spec", "quantity", "due_date"} <= fields and r["ignored_fields"] == ["wibble"]
    assert validate_rfq({})["missing"] == ["quantity", "trim", "colors", "stock"]


def test_perfect_bound_thin_and_many_pages_need_binding():
    r = validate_rfq({"pages": 16, "binding": "perfect bound"})
    assert any("thin for perfect binding" in p["message"] for p in r["problems"])
    assert "binding" in validate_rfq({"pages": 12})["missing"]


async def test_tool_validate_job_spec(make_server):
    async with Client(make_server()) as c:
        r = await c.call_tool("validate_job_spec", {"product": "flyer", "quantity": "1000", "trim": "8.5x11",
                                                    "colors": "4/0"})
    assert r.structured_content["missing"] == ["stock"]


def test_huge_numbers_do_not_crash():
    r = validate_rfq({"quantity": "9" * 400, "pages": 8})
    assert r["spec"].get("quantity") is None or r["spec"]["quantity"] < 10**9
