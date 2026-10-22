# Assignment approach and decision log

## Input and inspection

The supplied update file supersedes the original Markdown wording: the source is the PDF. The bundle contains `ct200_manual.pdf` (6 pages) and `ct200_manual_v2.pdf` (7 pages). Both expose extractable text in Drive's PDF viewer; this implementation independently checks each page's text layer and only invokes OCR on an empty page.

Notable irregularities observed:

- Heading `2.1.1.1 Battery Life Under Typical Use` skips the expected `2.1.1` level.
- Page 3 presents `3.4 Auto Shutoff` before `3.3 Result Display and Classification`. Preserve original reading order; do not sort by number.
- The blood-pressure classification list is numbered like sections but is body content, not section hierarchy.
- Specifications and error-code tables can be emitted as flattened text by PDF layout engines. Attempt table detection and preserve text fallback with page provenance.
- v2 adds a page and changes values/wording. A filename or page-count change must not be mistaken for a single changed section.
- High-impact observed v1→v2 edits include battery cycles 300→250 and low-battery threshold 15%→10% in 2.1.1.1, cuff inflation increments 40→30 mmHg in 3.2, an added E6 error code reflected by 4.2/4.3, and new section 5.3 Data Export. These values should be checked against the rendered PDF before anyone uses a generated test idea.

## Extraction and tree

Pypdf extracts positioned text fragments and font metadata. Fragments are grouped by baseline and sorted into reading order. Numbered section headings are recognized using section-number syntax plus layout/font cues; the list-number false-positive is tested. A map of existing numeric prefixes determines parentage. When a number jumps levels, parent to the nearest existing prefix and retain the full number and reported level. Paragraphs/lists/tables are content children of the active heading. All nodes retain page provenance and a canonical content hash.

When a page contains no text layer, attempt OCRmyPDF (which uses Tesseract/Ghostscript) if available. If OCR is unavailable or errors, preserve a warning and an empty-page record; do not invent text or mark extraction complete without warning. Borderless tables are reconstructed only when explicit column headers are found; output includes raw rows and an alignment warning. Images are recorded as references, not clinically interpreted. pypdf is not itself OCR software and PDF text extraction cannot infer semantic structure; see the [official extraction notes](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).

## Version matching and traceability

The primary matching key is the explicit section number. Unnumbered title/heading nodes use normalized heading path and parent path; the fallback is heuristic and returned as such. For a logical match, compare the canonical subtree hash. A changed hash marks any prior generation tied to that node and version stale. Parent sections also report a subtree change when descendants change. If a node is absent or the match is ambiguous, report `unknown` rather than claiming current. Selection records pin the exact version, node keys, and hashes used to create the prompt. Retrieval compares those pins to the requested/current version and exposes changed/missing sources.

This approach favors an explainable, auditable diff for this short sample over semantic embeddings. Semantic matching could improve renamed sections but adds false-match risk; it is not required to demonstrate stale traceability.

## LLM output and failure policy

The prompt requests 3–5 test-case ideas as JSON. Pydantic validates IDs, source references, rationale, steps, and expected result. Invalid output gets one repair attempt when using a live provider; if still invalid, return an error and store no successful generation. A network/provider failure is surfaced, not converted to made-up output. If no API key is configured, the clearly labelled offline demo generator supports local end-to-end testing. Duplicate requests are allowed as separate generation records so retries remain auditable; no hidden deduplication.

Generated test ideas are not approved tests, clinical advice, or a substitute for engineering review. The stored output records the selection ID, exact source version/hash, model/provider, and prompt hash.

## Time / quality tradeoffs

Priority was given to source provenance, stable section keys, explicit hashes, v1/v2 diffing, stale retrieval, and runnable tests. No frontend, authentication, auto-regeneration, or generalized document platform was added because the brief excludes or deprioritizes them. The parser is deliberately conservative: ambiguous content remains text with warnings instead of being silently reclassified.

## Limitations

OCR, table reconstruction, semantic heading recognition, and path matching need human review. A number-looking list item can still confuse a heuristic in a document with unusual typography. Hashes are sensitive to extraction whitespace; normalization reduces harmless differences but can’t prove semantic equivalence. This prototype has not been validated for regulated use.
