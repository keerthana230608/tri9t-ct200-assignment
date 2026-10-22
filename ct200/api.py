from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import select

from .db import Generation, NodeRecord, Selection, SessionLocal, Version, get_node, get_version, store_version
from .llm import generate as live_generate
from .parser import parse_pdf
from .schemas import GenerateRequest, IngestRequest, SelectionCreate, TestCaseSet
from .services import compare_versions, latest_version, record_dict, search_nodes, selection_staleness, tree_node

app = FastAPI(title="CT-200 Traceability API", version="1.0.0", description="PDF hierarchy extraction, version diffing, pinned selections, and test-case idea traceability.")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/versions/ingest")
def ingest(request: IngestRequest):
    path = Path(request.pdf_path)
    if not path.is_file():
        raise HTTPException(404, "PDF file not found")
    try:
        parsed = parse_pdf(path, request.version_id)
        store_version(request.version_id, path.name, parsed)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, f"PDF extraction failed: {exc}") from exc
    return {"version_id": request.version_id, "source_sha256": parsed["source_sha256"], "page_count": parsed["page_count"], "warnings": parsed["warnings"]}


@app.get("/versions")
def versions():
    with SessionLocal() as session:
        rows = session.scalars(select(Version).order_by(Version.created_at)).all()
        return [{"version_id": r.version_id, "source_name": r.source_name, "source_sha256": r.source_sha256,
                 "page_count": r.page_count, "parser": r.parser_name, "created_at": r.created_at,
                 "warnings": json.loads(r.warnings_json)} for r in rows]


@app.get("/versions/{version_id}/sections")
def sections(version_id: str):
    with SessionLocal() as session:
        version = get_version(session, version_id)
        if not version:
            raise HTTPException(404, "version not found")
        parsed = json.loads(version.tree_json)
        return {"version_id": version_id, "page_count": version.page_count, "tree": parsed["root"], "warnings": json.loads(version.warnings_json)}


@app.get("/nodes/{node_key:path}")
def node(node_key: str, version: str = Query(...)):
    with SessionLocal() as session:
        result = tree_node(session, version, node_key)
        if result is None:
            raise HTTPException(404, "node not found for version")
        return result


@app.get("/search")
def search(q: str, version: str, kind: str | None = None):
    with SessionLocal() as session:
        if not get_version(session, version):
            raise HTTPException(404, "version not found")
        return {"version_id": version, "query": q, "results": search_nodes(session, version, q, kind)}


@app.get("/changes")
def changes(from_version: str, to_version: str):
    with SessionLocal() as session:
        if not get_version(session, from_version) or not get_version(session, to_version):
            raise HTTPException(404, "one or both versions not found")
        return compare_versions(session, from_version, to_version)


@app.post("/selections")
def create_selection(request: SelectionCreate):
    with SessionLocal.begin() as session:
        if not get_version(session, request.version_id):
            raise HTTPException(404, "version not found")
        keys = list(dict.fromkeys(request.node_keys))
        hashes = {}
        for key in keys:
            record = get_node(session, request.version_id, key)
            if not record:
                raise HTTPException(404, f"node {key!r} not found in version {request.version_id}")
            hashes[key] = record.content_hash
        selection_id = str(uuid.uuid4())
        selection = Selection(selection_id=selection_id, version_id=request.version_id,
                              node_keys_json=json.dumps(keys), hashes_json=json.dumps(hashes))
        session.add(selection)
    return {"selection_id": selection_id, "version_id": request.version_id, "node_keys": keys, "hashes": hashes}


@app.get("/selections/{selection_id}")
def get_selection(selection_id: str, current_version: str | None = None):
    with SessionLocal() as session:
        selection = session.get(Selection, selection_id)
        if not selection:
            raise HTTPException(404, "selection not found")
        return selection_staleness(session, selection, current_version)


@app.post("/selections/{selection_id}/generations")
def create_generation(selection_id: str, request: GenerateRequest):
    with SessionLocal() as session:
        selection = session.get(Selection, selection_id)
        if not selection:
            raise HTTPException(404, "selection not found")
        keys = json.loads(selection.node_keys_json)
        nodes = []
        for key in keys:
            record = get_node(session, selection.version_id, key)
            nodes.append({"node_key": key, "heading": record.heading, "hash": record.content_hash,
                          "source_text": _node_source(session, selection.version_id, key)})
        prompt = (f"Draft exactly {request.count} test case ideas as JSON for the selected CT-200 requirements. "
                  "Use only the source nodes, list concrete steps and expected results, and label the source node. "
                  "These are unverified suggestions, not approved medical-device tests.\n" + json.dumps(nodes, ensure_ascii=False))
        provider, model = "offline-demo", "template-v1"
        if request.use_live_llm:
            try:
                data, provider, model = live_generate(prompt, request.count, set(keys))
            except Exception as exc:
                raise HTTPException(502, str(exc)) from exc
        else:
            data = _offline_data(selection_id, keys, nodes, request.count)
        try:
            validated = TestCaseSet.model_validate(data)
            if len(validated.test_cases) != request.count:
                raise ValueError(f"expected exactly {request.count} cases")
            if any(test.source_node_key not in keys for test in validated.test_cases):
                raise ValueError("generated case points outside the pinned selection")
        except Exception as exc:
            raise HTTPException(502, f"structured output validation failed: {exc}") from exc
        generation_id = str(uuid.uuid4())
        hashes = json.loads(selection.hashes_json)
        row = Generation(generation_id=generation_id, selection_id=selection_id, version_id=selection.version_id,
                         node_keys_json=json.dumps(keys), hashes_json=json.dumps(hashes), tests_json=validated.model_dump_json(),
                         provider=provider, model=model, prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(), validation_status="passed")
        session.add(row)
        session.commit()
        return {"generation_id": generation_id, "selection_id": selection_id, "version_id": selection.version_id,
                "provider": provider, "model": model, "validation_status": "passed", "staleness": selection_staleness(session, selection),
                "test_cases": validated.model_dump()["test_cases"], "review_notice": "Draft ideas only; verify every requirement and procedure before use."}


@app.get("/generations")
def generations(selection_id: str | None = None, node_key: str | None = None):
    if not selection_id and not node_key:
        raise HTTPException(400, "provide selection_id or node_key")
    with SessionLocal() as session:
        rows = session.scalars(select(Generation).order_by(Generation.created_at.desc())).all()
        results = []
        for row in rows:
            keys = json.loads(row.node_keys_json)
            if selection_id and row.selection_id != selection_id:
                continue
            if node_key and node_key not in keys:
                continue
            selection = session.get(Selection, row.selection_id)
            results.append({"generation_id": row.generation_id, "selection_id": row.selection_id,
                            "version_id": row.version_id, "node_keys": keys, "source_hashes": json.loads(row.hashes_json),
                            "provider": row.provider, "model": row.model, "prompt_hash": row.prompt_hash,
                            "validation_status": row.validation_status, "created_at": row.created_at,
                            "staleness": selection_staleness(session, selection), "test_cases": json.loads(row.tests_json)})
        return {"count": len(results), "results": results}


def _node_source(session, version_id: str, key: str) -> str:
    node = tree_node(session, version_id, key)
    if not node:
        return ""
    pieces = [node.get("heading", ""), node.get("body", "")]
    def visit(item):
        for child in item.get("children", []):
            pieces.extend([child.get("heading", ""), child.get("body", "")])
            visit(child)
    visit(node)
    return "\n".join(p for p in pieces if p)


def _offline_data(selection_id, keys, nodes, count):
    cases = []
    for index in range(count):
        source = nodes[index % len(nodes)]
        snippet = " ".join(source["source_text"].split())[:150]
        cases.append({"title": f"Review requirement {index+1}: {source['heading'][:80]}", "source_node_key": source["node_key"],
                      "rationale": f"Offline demo placeholder grounded in selected text: {snippet}. A reviewer must define a validated procedure.",
                      "preconditions": ["Controlled test configuration is documented.", "Source version and node are verified by a reviewer."],
                      "steps": ["Set up the controlled test configuration described by the requirement.", "Apply the documented condition and record the device response."],
                      "expected_result": "The observed response is checked against the cited requirement and any deviation is recorded.",
                      "risk": "safety" if any(w in source["source_text"].lower() for w in ("pressure", "alarm", "deflat", "error")) else "functional"})
    return {"test_cases": cases}
