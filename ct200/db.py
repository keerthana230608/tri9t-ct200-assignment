from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Version(Base):
    __tablename__ = "versions"
    version_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_name: Mapped[str] = mapped_column(String)
    source_sha256: Mapped[str] = mapped_column(String)
    page_count: Mapped[int] = mapped_column(Integer)
    parser_name: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    tree_json: Mapped[str] = mapped_column(Text)


class NodeRecord(Base):
    __tablename__ = "nodes"
    version_id: Mapped[str] = mapped_column(String, primary_key=True)
    node_key: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    heading: Mapped[str] = mapped_column(String)
    section_no: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_key: Mapped[str | None] = mapped_column(String, nullable=True)
    level: Mapped[int] = mapped_column(Integer)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String)
    match_method: Mapped[str] = mapped_column(String)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class Selection(Base):
    __tablename__ = "selections"
    selection_id: Mapped[str] = mapped_column(String, primary_key=True)
    version_id: Mapped[str] = mapped_column(String)
    node_keys_json: Mapped[str] = mapped_column(Text)
    hashes_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Generation(Base):
    __tablename__ = "generations"
    generation_id: Mapped[str] = mapped_column(String, primary_key=True)
    selection_id: Mapped[str] = mapped_column(String)
    version_id: Mapped[str] = mapped_column(String)
    node_keys_json: Mapped[str] = mapped_column(Text)
    hashes_json: Mapped[str] = mapped_column(Text)
    tests_json: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    prompt_hash: Mapped[str] = mapped_column(String)
    validation_status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def _engine():
    url = os.getenv("CT200_DB_URL", "sqlite:///./ct200.db")
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url)


engine = _engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base.metadata.create_all(engine)


def flatten_tree(node: dict[str, Any]):
    yield node
    for child in node.get("children", []):
        yield from flatten_tree(child)


def store_version(version_id: str, source_name: str, parsed: dict[str, Any]) -> None:
    with SessionLocal.begin() as session:
        previous = session.get(Version, version_id)
        if previous:
            if previous.source_sha256 == parsed["source_sha256"]:
                return
            raise ValueError(f"Version {version_id!r} already exists with a different source; use a new version id.")
        root = parsed["root"]
        version = Version(version_id=version_id, source_name=source_name, source_sha256=parsed["source_sha256"], page_count=parsed["page_count"], parser_name=parsed["parser"], warnings_json=json.dumps(parsed["warnings"], ensure_ascii=False), tree_json=json.dumps(parsed, ensure_ascii=False))
        session.add(version)
        for item in flatten_tree(root):
            session.add(NodeRecord(
                version_id=version_id, node_key=item["node_key"], kind=item["kind"], heading=item["heading"],
                section_no=item.get("section_no"), parent_key=item.get("parent_key"), level=item["level"],
                page_start=item["page_start"], page_end=item["page_end"], body=item.get("body", ""),
                content_hash=item["content_hash"], match_method=item.get("match_method", "heuristic"),
                metadata_json=json.dumps(item.get("metadata", {}), ensure_ascii=False),
            ))


def get_version(session: Session, version_id: str) -> Version | None:
    return session.get(Version, version_id)


def get_node(session: Session, version_id: str, node_key: str) -> NodeRecord | None:
    return session.get(NodeRecord, (version_id, node_key))


def child_records(session: Session, version_id: str, parent_key: str | None):
    stmt = select(NodeRecord).where(NodeRecord.version_id == version_id, NodeRecord.parent_key == parent_key).order_by(NodeRecord.page_start, NodeRecord.node_key)
    return list(session.scalars(stmt))
