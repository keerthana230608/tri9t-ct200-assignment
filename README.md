# CT-200 document extraction and traceability demo

Small FastAPI service for extracting the two supplied CT-200 PDF versions, preserving document hierarchy and page provenance, comparing revisions, pinning selections, and retrieving test-case ideas with stale-source warnings.

## What is included

- Positioned PDF text extraction with pypdf. It uses the embedded text layer when available and attempts OCRmyPDF (with Tesseract/Ghostscript installed) only when a page has no extractable text.
- Heading tree, paragraphs, lists, borderless tables reconstructed from explicit column headers, figure/image references when available, page numbers, and extraction warnings.
- SQLite persistence for immutable document versions, nodes, selections, and generated outputs.
- Stable node keys based on section numbers, with normalized heading-path fallback for unnumbered headings. Content SHA-256 hashes support version comparison and staleness checks.
- FastAPI endpoints and an offline demo generator. Configure an OpenAI-compatible API key to use a live model.
- Example extraction outputs for both revisions and a section-level diff in `artifacts/`.

This is a focused assignment prototype, not a validated medical-device document-control system. OCR/table outputs and semantic node matching require human review.

## Setup

Requires Python 3.11+. OCRmyPDF, Tesseract, and Ghostscript are optional and are only needed for image-only scanned pages. From this directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The two input manuals are in `data/`. To create a local SQLite database from scratch:

```powershell
python -m ct200.cli ingest data/ct200_manual.pdf --version v1
python -m ct200.cli ingest data/ct200_manual_v2.pdf --version v2
python -m ct200.cli demo
```

Start the API:

```powershell
uvicorn ct200.api:app --reload
```

Open `http://127.0.0.1:8000/docs` to explore the API. `python scripts/demo.py` exercises the complete flow in-process and does not need a running server. The default database is `ct200.db`; set `CT200_DB_URL` to use another SQLite URL. For a live LLM call set `LLM_API_KEY`, optionally `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_PROVIDER`. Never commit secrets.

The PDF upload copy of the approach note is in `output/pdf/Assignment_Approach.pdf`. To regenerate it, install `requirements-docs.txt` and run `python scripts/create_approach_pdf.py`.

## v1 to v2 flow

1. Ingest each PDF under distinct version labels (`v1`, `v2`). Ingestion is idempotent by version label plus source SHA-256.
2. Call `GET /versions/v1/sections` or `GET /nodes/{node_key}?version=v1` to inspect the extracted hierarchy and hashes.
3. Call `GET /changes?from_version=v1&to_version=v2` for added, removed, and changed nodes. Match first by section number; heading-path fallback is explicitly marked as heuristic.
4. Pin exact node keys and content hashes with `POST /selections`. Selections retain the source version.
5. Call `POST /selections/{selection_id}/generations`. Each output is saved with selection ID, source version, source hash, model/provider, validation status, and prompt hash. The default offline path is labelled as a fixture; set `use_live_llm=true` to use the configured provider.
6. Retrieve past outputs using `GET /generations?selection_id=...` or `GET /generations?node_key=...`. If the same logical node is changed in a later version, the API marks that output stale; it never silently regenerates it.

## Test

```powershell
pytest -q
python scripts/demo.py
```

The tests cover the supplied PDFs' irregular nested numbering and out-of-order sections, preservation of page/table/list content, stale traceability after edits, and structured-output validation. They are software checks only, not clinical validation.

## Known limitations / human review

- Layout extraction depends on positioned text/font cues. Unusual scans need OCR language packs and may need deskew or manual correction.
- Table reconstruction is best effort; merged cells, multi-page tables, and tables without known headers can be flattened or missed. Every inferred table carries page provenance and a warning to review alignment.
- `2.1.1.1` is retained as level 4 even though `2.1.1` is absent. The tree attaches it to the nearest existing numeric ancestor instead of inventing a missing heading.
- Section `3.4` occurs before `3.3` in reading order in both PDFs. The parser preserves source order and does not silently reorder the manual.
- The diff reports a parent section as changed when a descendant changes; inspect `change_scope` and node hashes to see whether a heading changed or the subtree changed.
- Exact section numbers are good matching keys for this sample. The unnumbered fallback is path-based and can mis-match renamed or duplicated headings; review changes flagged with `match_method=heading_path`.
- OCR text is uncertain and can change hashes; extraction records its page and confidence is not inferred. A human must review safety-critical values.
- LLM test cases are ideas, not approved verification procedures. JSON schema validation catches malformed output, not incorrect or unsafe tests. Failed provider calls return a clear error and are not saved as successful generations.

## Assignment decisions

See [`APPROACH.md`](APPROACH.md) for the parser edge cases, time/quality choices, matching and staleness rules, and fallback behavior.
