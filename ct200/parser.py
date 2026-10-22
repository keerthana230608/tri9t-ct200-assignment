from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader

SECTION_RE = re.compile(r"^(\d+(?:\.\d+)*\.?)\s+(.+?)\s*$")
LIST_RE = re.compile(r"^(?:[•●▪‣]|\d+[.)]|[a-zA-Z][.)])\s+(.+)$")
TABLE_HEADERS = {"parameter value", "code meaning device behavior"}
CAPTION_RE = re.compile(r"^(?:Figure|Fig\.|Image|Table)\s+[A-Z0-9.-]+\s*[:.—-]", re.I)


def canonical(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    return re.sub(r"\s+", " ", text).strip()


def digest(text: str) -> str:
    return hashlib.sha256(canonical(text).encode("utf-8")).hexdigest()


@dataclass
class Line:
    text: str
    size: float = 0.0
    bold: bool = False
    y: float = 0.0


@dataclass
class Node:
    node_key: str
    version_id: str
    kind: str
    heading: str
    level: int
    section_no: str | None
    parent_key: str | None
    page_start: int
    page_end: int
    body: str = ""
    content_hash: str = ""
    match_method: str = "section_number"
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)

    def finalize(self) -> None:
        for child in self.children:
            child.finalize()
        own = canonical(self.heading + " " + self.body)
        self.content_hash = digest(own + "|" + "|".join(child.content_hash for child in self.children))

    def as_dict(self, include_children: bool = True) -> dict[str, Any]:
        out = {"node_key": self.node_key, "version_id": self.version_id, "kind": self.kind,
               "heading": self.heading, "level": self.level, "section_no": self.section_no,
               "parent_key": self.parent_key, "page_start": self.page_start, "page_end": self.page_end,
               "body": self.body, "content_hash": self.content_hash, "match_method": self.match_method,
               "metadata": self.metadata}
        if include_children:
            out["children"] = [child.as_dict() for child in self.children]
        return out


def _page_lines(page) -> tuple[list[Line], str]:
    fragments: list[tuple[float, float, str, float, bool]] = []
    def visitor(text, cm, tm, font_dict, font_size):
        value = str(text or "")
        if value.strip():
            basefont = str((font_dict or {}).get("/BaseFont", "")).casefold()
            for segment in value.splitlines():
                if segment.strip():
                    fragments.append((float(tm[5]), float(tm[4]), segment, float(font_size or 0), "bold" in basefont))
    try:
        text = page.extract_text(visitor_text=visitor) or ""
    except Exception:
        text = page.extract_text() or ""
    if not fragments:
        return [Line(canonical(line)) for line in text.splitlines() if canonical(line)], text

    # Merge text fragments sharing a baseline; sort top-to-bottom, then left-to-right.
    # In pypdf's text matrix the supplied PDFs use an increasing y origin.
    fragments.sort(key=lambda row: (round(row[0], 1), row[1]))
    groups: list[list[tuple[float, float, str, float, bool]]] = []
    for item in fragments:
        target = next((g for g in groups if abs(g[0][0] - item[0]) <= 1.5), None)
        if target is None:
            groups.append([item])
        else:
            target.append(item)
    lines = []
    for group in groups:
        group.sort(key=lambda row: row[1])
        value = canonical(" ".join(row[2] for row in group))
        if value:
            lines.append(Line(value, max(row[3] for row in group), any(row[4] for row in group), group[0][0]))
    # Visitor order can be unreliable in some generators. Use line extraction as a fallback
    # when grouping loses a material amount of visible text.
    if len(" ".join(line.text for line in lines)) < 0.65 * len(canonical(text)):
        lines = [Line(canonical(line)) for line in text.splitlines() if canonical(line)]
    return lines, text


def _heading_candidate(line: Line, body_size: float) -> tuple[str, str, int] | None:
    match = SECTION_RE.match(line.text)
    if not match:
        return None
    number, title = match.groups()
    number = number.rstrip(".")
    parts = number.split(".")
    list_like = len(parts) == 1 and (":" in title or title.lower().startswith(("normal", "elevated", "hypertension", "hypertensive")))
    is_heading_style = line.bold or (line.size >= body_size * 1.12 and len(title.split()) <= 13)
    if list_like or not is_heading_style:
        return None
    return number, title, len(parts)


def _image_count(page) -> int:
    try:
        return len(page.images)
    except Exception:
        try:
            resources = page.get("/Resources").get_object()
            xobjects = resources.get("/XObject", {}).get_object()
            return sum(1 for ref in xobjects.values() if ref.get_object().get("/Subtype") == "/Image")
        except Exception:
            return 0


def _ocr_pdf(path: Path):
    """Optional scan fallback: call OCRmyPDF only when installed; never install tools."""
    executable = shutil.which("ocrmypdf")
    if not executable:
        return None
    tempdir = tempfile.TemporaryDirectory(prefix="ct200_ocr_")
    output = Path(tempdir.name) / "ocr.pdf"
    try:
        result = subprocess.run([executable, "--skip-text", str(path), str(output)], capture_output=True, text=True, timeout=180)
        if result.returncode == 0 and output.is_file():
            return output, tempdir
    except Exception:
        pass
    tempdir.cleanup()
    return None


def parse_pdf(path: str | Path, version_id: str) -> dict[str, Any]:
    path = Path(path)
    reader = PdfReader(str(path), strict=False)
    pages = list(reader.pages)
    extracted = [_page_lines(page) for page in pages]
    ocr_used = False
    warnings: list[dict[str, Any]] = []
    empty_pages = {i for i, (_, text) in enumerate(extracted, start=1) if not text.strip()}
    ocr_temp = None
    if empty_pages:
        ocr_result = _ocr_pdf(path)
        if ocr_result:
            ocr_path, ocr_temp = ocr_result
            reader = PdfReader(str(ocr_path), strict=False)
            pages = list(reader.pages)
            extracted = [_page_lines(page) for page in pages]
            ocr_used = True
        else:
            for page_no, (_, text) in enumerate(extracted, start=1):
                if not text.strip():
                    warnings.append({"page": page_no, "code": "ocr_unavailable", "message": "Page has no extractable text; install OCRmyPDF with Tesseract/Ghostscript to enable scan fallback."})

    all_sizes = [line.size for lines, _ in extracted for line in lines if line.size > 0]
    body_size = sorted(all_sizes)[len(all_sizes) // 2] if all_sizes else 10.0
    root = Node("document", version_id, "document", path.stem, 0, None, None, 1, len(pages), match_method="root")
    sections_by_number: dict[str, Node] = {}
    active = root
    page_stats = []
    title_seen = False
    order_counter = 0

    for page_no, (lines, raw_text) in enumerate(extracted, start=1):
        page = pages[page_no - 1]
        image_count = _image_count(page)
        page_stats.append({"page": page_no, "text_chars": len(raw_text), "ocr_used": ocr_used and page_no in empty_pages, "image_count": image_count})
        i = 0
        while i < len(lines):
            line = lines[i]
            text = line.text
            if not title_seen and page_no == 1 and line.size >= body_size * 1.35 and len(text.split()) <= 14:
                title_parts = [text]
                j = i + 1
                while j < len(lines) and lines[j].size >= body_size * 1.35 and not _heading_candidate(lines[j], body_size):
                    title_parts.append(lines[j].text)
                    j += 1
                title = canonical(" ".join(title_parts))
                root.children.append(Node("title", version_id, "title", title, 1, None, root.node_key, page_no, page_no, title, metadata={"font_size": line.size}))
                title_seen = True
                i = j
                continue

            # Borderless tables in the supplied manual have explicit column labels.
            if text.casefold() in TABLE_HEADERS:
                rows = [text.split()]
                raw_rows = []
                j = i + 1
                while j < len(lines):
                    if _heading_candidate(lines[j], body_size):
                        break
                    if j > i + 1 and lines[j].size >= body_size * 1.12 and len(lines[j].text.split()) <= 13:
                        break
                    raw_rows.append(lines[j].text)
                    rows.append(lines[j].text.split())
                    j += 1
                table = Node(f"{active.node_key}/table-{page_no}-{len(active.children)+1}", version_id, "table", f"Table on page {page_no}", active.level + 1, None, active.node_key,
                             page_no, page_no, "\n".join(" | ".join(row) for row in rows), metadata={"rows": rows, "raw_rows": raw_rows, "detection": "borderless_header_fallback", "alignment_review_required": True})
                active.children.append(table)
                warnings.append({"page": page_no, "code": "table_fallback", "message": f"Reconstructed {text!r} table from positioned text; verify column alignment."})
                i = j
                continue

            candidate = _heading_candidate(line, body_size)
            if candidate:
                number, heading, level = candidate
                key = f"sec-{number}"
                if number in sections_by_number:
                    key = f"{key}-dup{sum(1 for n in sections_by_number.values() if n.section_no == number)+1}"
                    warnings.append({"page": page_no, "code": "duplicate_section_number", "section_no": number})
                components = number.split(".")
                parent = next((sections_by_number.get(".".join(components[:depth])) for depth in range(len(components)-1, 0, -1) if ".".join(components[:depth]) in sections_by_number), root)
                order_counter += 1
                node = Node(key, version_id, "section", heading, level, number, parent.node_key, page_no, page_no,
                            metadata={"font_size": line.size, "bold": line.bold, "source_order": order_counter, "bbox_y": line.y})
                parent.children.append(node)
                sections_by_number[number] = node
                active = node
                i += 1
                continue

            if CAPTION_RE.match(text):
                _append_content(active, version_id, text, page_no, "figure_caption")
            elif (match := LIST_RE.match(text)):
                _append_content(active, version_id, match.group(1), page_no, "list_item", marker=text[:len(text)-len(match.group(1))].strip())
            elif text:
                _append_content(active, version_id, text, page_no)
            i += 1

        if image_count:
            _append_content(active, version_id, f"{image_count} embedded image(s); visual review required", page_no, "figure_reference", image_count=image_count)

    if not title_seen and extracted and extracted[0][0]:
        first = extracted[0][0][0].text
        root.children.insert(0, Node("title", version_id, "title", first, 1, None, root.node_key, 1, 1, first, metadata={"fallback": True}))
        warnings.append({"page": 1, "code": "title_fallback", "message": "Title inferred from the first extracted text line."})
    root.finalize()
    if ocr_temp:
        ocr_temp.cleanup()
    return {"root": root.as_dict(), "page_count": len(pages), "pages": page_stats, "warnings": warnings,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "parser": "pypdf-positioned-text-v1"}


def _append_content(parent: Node, version_id: str, text: str, page: int, kind: str = "paragraph", **metadata: Any) -> None:
    key = f"{parent.node_key}/{kind}-{page}-{len(parent.children)+1}"
    parent.children.append(Node(key, version_id, kind, kind.replace("_", " ").title(), parent.level + 1, None, parent.node_key,
                                page, page, text, metadata=metadata))


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)
