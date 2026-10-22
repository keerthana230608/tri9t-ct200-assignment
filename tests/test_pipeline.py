from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from ct200 import db
import ct200.api as api
from ct200.api import app
from ct200.parser import parse_pdf
from ct200.schemas import TestCaseSet as ValidatedCaseSet
from ct200.services import compare_versions, selection_staleness

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "data" / "ct200_manual.pdf"
V2 = ROOT / "data" / "ct200_manual_v2.pdf"


def walk(node):
    yield node
    for child in node.get("children", []):
        yield from walk(child)


def find_section(tree, section_no):
    return next(node for node in walk(tree["root"]) if node.get("section_no") == section_no)


def test_actual_manual_preserves_skipped_level_and_source_order():
    parsed = parse_pdf(V1, "v1")
    root = parsed["root"]
    battery = find_section(parsed, "2.1.1.1")
    assert battery["level"] == 4
    assert battery["parent_key"] == "sec-2.1"
    intended_use = find_section(parsed, "1.1")
    assert intended_use["parent_key"] == "sec-1"
    operation = find_section(parsed, "3")
    headings = [child["section_no"] for child in operation["children"] if child["kind"] == "section"]
    assert headings.index("3.4") < headings.index("3.3")  # source order is retained
    assert parsed["page_count"] == 6
    tables = [n for n in walk(root) if n["kind"] == "table"]
    assert len(tables) >= 2
    assert tables[0]["metadata"]["rows"][0] == ["Parameter", "Value"]


def test_classification_numbered_list_is_not_misclassified_as_section():
    parsed = parse_pdf(V1, "v1")
    all_nodes = list(walk(parsed["root"]))
    assert not any(n.get("section_no") in {"1", "2", "3", "4", "5"} and "Normal:" in n.get("heading", "") for n in all_nodes)
    assert any(n["kind"] == "list_item" and "Normal" in n.get("body", "") for n in all_nodes)


def test_v2_changes_are_detected_and_selection_becomes_stale():
    old_factory = db.SessionLocal
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    db.SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        old = parse_pdf(V1, "v1")
        new = parse_pdf(V2, "v2")
        alarm_v1 = " ".join(n.get("body", "") for n in find_section(old, "4.3")["children"])
        alarm_v2 = " ".join(n.get("body", "") for n in find_section(new, "4.3")["children"])
        assert "E1–E5" in alarm_v1
        assert "E1–E6" in alarm_v2
        db.store_version("v1", V1.name, old)
        db.store_version("v2", V2.name, new)
        with db.SessionLocal.begin() as session:
            delta = compare_versions(session, "v1", "v2")
            changed = {item["logical_key"] for item in delta["changes"] if item["status"] == "changed"}
            assert "section:2.1.1.1" in changed
            assert "section:3.2" in changed
            old_node = db.get_node(session, "v1", "sec-4.1")
            from uuid import uuid4
            selection = db.Selection(selection_id=str(uuid4()), version_id="v1", node_keys_json='["sec-4.1"]',
                                     hashes_json=json.dumps({"sec-4.1": old_node.content_hash}))
            session.add(selection)
        with db.SessionLocal() as session:
            report = selection_staleness(session, selection, "v2")
            assert report["is_stale"] is False  # safety section itself is unchanged
            changed_node = db.get_node(session, "v1", "sec-2.1.1.1")
            selection2 = db.Selection(selection_id=str(uuid4()), version_id="v1", node_keys_json='["sec-2.1.1.1"]',
                                      hashes_json=json.dumps({"sec-2.1.1.1": changed_node.content_hash}))
            report2 = selection_staleness(session, selection2, "v2")
            assert report2["is_stale"] is True
            assert report2["sources"][0]["status"] == "stale"
    finally:
        db.SessionLocal = old_factory
        engine.dispose()


def test_structured_llm_schema_rejects_out_of_contract_case():
    valid = {
        "test_cases": [
            {"title": f"Case {i} title", "source_node_key": "sec-4.1", "rationale": "Verify the stated safety requirement under controlled conditions.",
             "preconditions": ["Controlled setup is ready."], "steps": ["Set the initial state.", "Trigger the source condition."],
             "expected_result": "Observed behavior matches the requirement.", "risk": "safety"}
            for i in range(3)
        ]
    }
    assert len(ValidatedCaseSet.model_validate(valid).test_cases) == 3
    valid["test_cases"].pop()
    try:
        ValidatedCaseSet.model_validate(valid)
    except Exception:
        pass
    else:
        raise AssertionError("schema must reject fewer than three cases")


def test_api_selection_generation_staleness_and_retrieval():
    old_factory = db.SessionLocal
    old_api_factory = api.SessionLocal
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    db.SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    api.SessionLocal = db.SessionLocal
    try:
        db.store_version("v1", V1.name, parse_pdf(V1, "v1"))
        db.store_version("v2", V2.name, parse_pdf(V2, "v2"))
        with TestClient(app) as client:
            selection = client.post("/selections", json={"version_id": "v1", "node_keys": ["sec-2.1.1.1"]})
            assert selection.status_code == 200
            selection_id = selection.json()["selection_id"]
            generated = client.post(f"/selections/{selection_id}/generations", json={"count": 3, "use_live_llm": False})
            assert generated.status_code == 200
            assert generated.json()["validation_status"] == "passed"
            assert len(generated.json()["test_cases"]) == 3
            assert generated.json()["staleness"]["is_stale"] is True
            history = client.get(f"/generations?selection_id={selection_id}")
            assert history.status_code == 200
            assert history.json()["count"] == 1
            assert client.get("/changes?from_version=v1&to_version=v2").status_code == 200
    finally:
        db.SessionLocal = old_factory
        api.SessionLocal = old_api_factory
        engine.dispose()
