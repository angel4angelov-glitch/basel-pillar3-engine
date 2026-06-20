#!/usr/bin/env python
"""Pin + fetch the Barclays PLC Q1 2026 Pillar 3 PDF (chunk 5.1, box 2 ingest).

The PDF itself is NOT committed (copyright + bloat — data/raw/ is gitignored); this
script + the pinned (url, sha256) ARE, so anyone can reproduce data/raw/ from the
public source. Idempotent: if a local copy already hashes to the pin it re-registers
without re-downloading; a hash MISMATCH fails loud (the upstream file drifted — never
silently extract a different document, CLAUDE.md §A.2/§A.4).

    python scripts/fetch_barclays_pillar3.py

If the network is blocked, fetch the URL manually into data/raw/ under FRIENDLY_NAME
and re-run — the script will verify the SHA and register it.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import date

from isda_p3.config import FETCH_TIMEOUT_S, Paths, USER_AGENT
from isda_p3.ingest.registry import register_file
from isda_p3.models import ReportingPeriod, SourceKind, Template

# --- the pin (the whole point of this file) --------------------------------------
PINNED_URL = (
    "https://home.barclays/content/dam/home-barclays/documents/investor-relations/"
    "ResultAnnouncements/Q12026Results/Q126-BPLC-Pillar-3.pdf"
)
EXPECTED_SHA256 = "897e31da5cfc9ef27accf6f5c23ae826374e13db632400cdfb4dca4b7803a685"
RETRIEVED = "2026-06-20"

BANK_ID = "barclays"
PERIOD = ReportingPeriod(2026, 1)  # as at 31 March 2026
FRIENDLY_NAME = "Q126-BPLC-Pillar-3.pdf"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def main() -> int:
    Paths.ensure()
    dest = Paths.RAW / FRIENDLY_NAME

    if dest.exists() and _sha256(dest.read_bytes()) == EXPECTED_SHA256:
        print(f"ok    cached {dest} (sha matches pin)")
    else:
        print(f"fetch {PINNED_URL}")
        try:
            import requests

            resp = requests.get(
                PINNED_URL, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT_S
            )
            resp.raise_for_status()
            content = resp.content
        except Exception as exc:  # noqa: BLE001 — any fetch failure must be actionable
            print(
                f"\nDOWNLOAD FAILED ({exc}).\n"
                f"Fetch it manually and re-run:\n"
                f"  curl -L -o {dest} '{PINNED_URL}'\n",
                file=sys.stderr,
            )
            return 1

        got = _sha256(content)
        if got != EXPECTED_SHA256:
            print(
                f"\nSHA-256 MISMATCH — refusing to use a drifted document.\n"
                f"  expected {EXPECTED_SHA256}\n  got      {got}\n"
                f"The upstream PDF changed; re-pin EXPECTED_SHA256 only after a human "
                f"re-verifies the golden against the new file.",
                file=sys.stderr,
            )
            return 1
        dest.write_bytes(content)
        print(f"ok    wrote {dest} ({len(content)} bytes, sha matches pin)")

    # Box-2 registry: immutable content-addressed copy + manifest ledger row.
    row = register_file(
        dest,
        bank_id=BANK_ID,
        period=PERIOD,
        template=Template.KM1,
        url=PINNED_URL,
        source_kind=SourceKind.PDF,
        fetched_at=date.fromisoformat(RETRIEVED).isoformat(),
    )
    print(f"ok    registry: {row.status}  sha={row.sha256[:12]}…  -> {row.local_path}")
    print(f"\nURL pinned : {PINNED_URL}")
    print(f"SHA-256    : {EXPECTED_SHA256}")
    print(f"Retrieved  : {RETRIEVED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
