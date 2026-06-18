#!/usr/bin/env python
"""Import-only smoke test for the deterministic extraction engines (chunk 0.5).

Confirms Docling, Camelot, and pdfplumber import in the active venv and prints each
importable version. Side-effect-free: no PDF conversion, no model-weight download
(those are deferred to chunk 1.1's integration test).

Exit 0 if all three import; exit 1 with a clear message naming the failed module.
"""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version

# (display name, distribution name for importlib.metadata, import statement)
_ENGINES = [
    ("docling", "docling", "from docling.document_converter import DocumentConverter"),
    ("camelot", "camelot-py", "import camelot"),
    ("pdfplumber", "pdfplumber", "import pdfplumber"),
]


def _version(dist: str) -> str:
    try:
        return version(dist)
    except PackageNotFoundError:
        return "unknown"


def main() -> int:
    failed: list[str] = []
    for name, dist, stmt in _ENGINES:
        try:
            exec(stmt)  # noqa: S102 - fixed literal import strings, not user input
        except Exception as exc:  # noqa: BLE001 - report any import failure clearly
            print(f"FAIL  {name}: import failed ({exc})")
            failed.append(name)
        else:
            print(f"ok    {name} {_version(dist)}")

    if failed:
        print(f"\nsmoke FAILED: could not import {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\nok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
