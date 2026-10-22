from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .db import SessionLocal, get_version, store_version
from .parser import parse_pdf
from .services import compare_versions


def main():
    parser = argparse.ArgumentParser(description="Ingest CT-200 manuals")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("pdf")
    ingest.add_argument("--version", required=True)
    sub.add_parser("demo")
    args = parser.parse_args()
    if args.command == "ingest":
        path = Path(args.pdf)
        parsed = parse_pdf(path, args.version)
        store_version(args.version, path.name, parsed)
        print(json.dumps({"version_id": args.version, "page_count": parsed["page_count"], "source_sha256": parsed["source_sha256"], "warnings": parsed["warnings"]}, indent=2))
    elif args.command == "demo":
        with SessionLocal() as session:
            if not get_version(session, "v1") or not get_version(session, "v2"):
                raise SystemExit("Ingest v1 and v2 first (see README)")
            print(json.dumps(compare_versions(session, "v1", "v2"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
