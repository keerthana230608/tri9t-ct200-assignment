from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Generation, NodeRecord, Selection, Version, child_records, get_node


def tree_node(session: Session, version_id: str, node_key: str) -> dict | None:
    record = get_node(session, version_id, node_key)
    if not record:
        return None
    item = record_dict(record)
    item["children"] = [tree_node(session, version_id, child.node_key) for child in child_records(session, version_id, node_key)]
    return item


def record_dict(row: NodeRecord) -> dict:
    return {"node_key": row.node_key, "version_id": row.version_id, "kind": row.kind, "heading": row.heading,
            "section_no": row.section_no, "parent_key": row.parent_key, "level": row.level,
            "page_start": row.page_start, "page_end": row.page_end, "body": row.body,
            "content_hash": row.content_hash, "match_method": row.match_method,
            "metadata": json.loads(row.metadata_json)}


def _records(session: Session, version_id: str) -> list[NodeRecord]:
    return list(session.scalars(select(NodeRecord).where(NodeRecord.version_id == version_id)))


def _heading_path(session: Session, row: NodeRecord) -> str:
    parts = [re.sub(r"\s+", " ", row.heading).strip().casefold()]
    parent_key = row.parent_key
    while parent_key:
        parent = get_node(session, row.version_id, parent_key)
        if not parent:
            break
        if parent.kind != "document":
            parts.append(re.sub(r"\s+", " ", parent.heading).strip().casefold())
        parent_key = parent.parent_key
    return "path:" + " > ".join(reversed(parts))


def _logical_key(session: Session, row: NodeRecord) -> str:
    return f"section:{row.section_no}" if row.section_no else _heading_path(session, row)


def compare_versions(session: Session, from_id: str, to_id: str) -> dict:
    old = _records(session, from_id)
    new = _records(session, to_id)
    # Numbered sections are matched by their explicit number. Other nodes only
    # match when their stable key is identical; no fuzzy claim of equivalence.
    old_map, new_map = {}, {}
    for row in old:
        if row.kind in {"section", "title"}:
            old_map[_logical_key(session, row)] = row
    for row in new:
        if row.kind in {"section", "title"}:
            new_map[_logical_key(session, row)] = row
    changes = []
    for key in sorted(old_map.keys() | new_map.keys()):
        a, b = old_map.get(key), new_map.get(key)
        if a is None:
            changes.append({"status": "added", "logical_key": key, "to_node_key": b.node_key, "heading": b.heading, "match_method": b.match_method})
        elif b is None:
            changes.append({"status": "removed", "logical_key": key, "from_node_key": a.node_key, "heading": a.heading, "match_method": a.match_method})
        elif a.content_hash != b.content_hash or a.heading != b.heading:
            changes.append({"status": "changed", "logical_key": key, "from_node_key": a.node_key, "to_node_key": b.node_key,
                            "from_hash": a.content_hash, "to_hash": b.content_hash, "from_heading": a.heading,
                            "to_heading": b.heading, "match_method": "section_number" if a.section_no and b.section_no else "heading_path",
                            "change_scope": "heading" if a.heading != b.heading else "subtree"})
        else:
            changes.append({"status": "unchanged", "logical_key": key, "from_node_key": a.node_key, "to_node_key": b.node_key,
                            "content_hash": a.content_hash, "match_method": "section_number" if a.section_no and b.section_no else "heading_path"})
    return {"from_version": from_id, "to_version": to_id, "changes": changes}


def latest_version(session: Session) -> Version | None:
    return session.scalars(select(Version).order_by(Version.created_at.desc())).first()


def selection_staleness(session: Session, selection: Selection, current_version: str | None = None) -> dict:
    current = current_version or (latest_version(session).version_id if latest_version(session) else selection.version_id)
    pinned_keys = json.loads(selection.node_keys_json)
    pinned_hashes = json.loads(selection.hashes_json)
    report = []
    for key in pinned_keys:
        old_hash = pinned_hashes.get(key)
        old_record = get_node(session, selection.version_id, key)
        # section number based mapping is exact for this sample; fallback keys are
        # not silently promoted to exact if an item cannot be identified.
        current_record = get_node(session, current, key)
        if not current_record and old_record and old_record.section_no:
            current_record = session.scalars(select(NodeRecord).where(NodeRecord.version_id == current, NodeRecord.section_no == old_record.section_no, NodeRecord.kind == "section")).first()
        elif not current_record and old_record:
            old_path = _heading_path(session, old_record)
            candidates = session.scalars(select(NodeRecord).where(NodeRecord.version_id == current, NodeRecord.kind == old_record.kind)).all()
            current_record = next((candidate for candidate in candidates if _heading_path(session, candidate) == old_path), None)
        if not current_record:
            report.append({"node_key": key, "status": "unknown_or_missing", "pinned_hash": old_hash})
        elif current_record.content_hash != old_hash:
            report.append({"node_key": key, "current_node_key": current_record.node_key, "status": "stale", "pinned_hash": old_hash, "current_hash": current_record.content_hash})
        else:
            report.append({"node_key": key, "current_node_key": current_record.node_key, "status": "current", "pinned_hash": old_hash, "current_hash": current_record.content_hash})
    return {"selection_id": selection.selection_id, "pinned_version": selection.version_id, "current_version": current, "sources": report,
            "is_stale": any(item["status"] != "current" for item in report)}


def search_nodes(session: Session, version_id: str, q: str, kind: str | None = None) -> list[dict]:
    stmt = select(NodeRecord).where(NodeRecord.version_id == version_id)
    if kind:
        stmt = stmt.where(NodeRecord.kind == kind)
    needle = q.casefold()
    return [record_dict(row) for row in session.scalars(stmt) if needle in f"{row.heading} {row.body}".casefold()]


def subtree_source(node: dict) -> str:
    parts = [f"{node['heading']}: {node.get('body','')}".strip()]
    for child in node.get("children", []):
        parts.append(subtree_source(child))
    return "\n".join(part for part in parts if part)


def content_prompt(selection: Selection, nodes: list[dict], count: int) -> str:
    return (f"Create {count} test-case ideas from this selected CT-200 manual content. "
            "Return only JSON with key test_cases; each item must have title, source_node_key, rationale, "
            "preconditions (array), steps (array), expected_result, and risk (safety|functional|data|usability). "
            "Do not invent requirements; if a requirement is unclear, say so in rationale. These are draft ideas, not approved tests.\n\n" +
            json.dumps({"selection_id": selection.selection_id, "version_id": selection.version_id, "sources": nodes}, ensure_ascii=False))


def make_offline_cases(selection_id: str, node_keys: list[str], nodes: list[dict], count: int) -> dict:
    # Explicitly labelled local fixture, not an LLM claim. It exercises storage,
    # validation, and source pinning without needing a key.
    cases = []
    for i in range(count):
        source = nodes[i % len(nodes)]
        snippet = re.sub(r"\s+", " ", source["source_text"]).strip()[:180]
        cases.append({
            "title": f"Review source requirement {i + 1}: {source['node_key']}",
            "source_node_key": source["node_key"],
            "rationale": f"Offline demo placeholder based on the selected manual text: {snippet or source['heading']}. A reviewer must turn this into a verified procedure.",
            "preconditions": ["Use a controlled test setup and the specified CT-200 configuration."],
            "steps": ["Review the cited source text and identify its measurable acceptance condition.", "Execute the condition in a controlled environment and record observed behavior."],
            "expected_result": "Observed behavior is compared with the source requirement; deviations are documented for engineering review.",
            "risk": "safety" if re.search(r"pressure|alarm|safety|deflat|error", snippet, re.I) else "functional",
        })
    return {"test_cases": cases}
