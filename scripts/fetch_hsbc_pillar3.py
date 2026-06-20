#!/usr/bin/env python
"""Pin + fetch the HSBC Holdings plc Q1 2026 Pillar 3 PDF (chunk H1, box 2 ingest).

Bank #2, onboarded as CONFIG ONLY (the scalability test: the Nth bank is a config
entry, not a code change — CLAUDE.md §A.5). This script is the one new file per bank:
it mirrors scripts/fetch_barclays_pillar3.py exactly, pinning the public (url, sha256)
so anyone can reproduce data/raw/ from the source. The PDF itself is NOT committed
(copyright + bloat — data/raw/ is gitignored). Idempotent: a local copy that hashes to
the pin re-registers without re-downloading; a hash MISMATCH fails loud (the upstream
file drifted — never silently extract a different document, CLAUDE.md §A.2/§A.4).

    python scripts/fetch_hsbc_pillar3.py

If the network is blocked, fetch the URL manually into data/raw/ under FRIENDLY_NAME
and re-run — the script will verify the SHA and register it.

NOTE (HSBC reports in USD $bn, not $m): the KM1 figures are disclosed in billions and
the capital basis is the UK CRR end-point (leverage) — these surface as accuracy
concerns in H2, not here. This chunk only acquires + pins the source.
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
    "https://www.hsbc.com/-/files/hsbc/investors/hsbc-results/2026/1q/pdfs/"
    "hsbc-holdings-plc/260508-hsbc-holdings-plc-pillar-3-disclosures-at-31-march-2026-english.pdf"
)
EXPECTED_SHA256 = "23e81299767a6d2b88468e47bea2c3e57718fcf173bc8a619b9f73b39feba8a2"
RETRIEVED = "2026-06-20"

BANK_ID = "hsbc"
PERIOD = ReportingPeriod(2026, 1)  # as at 31 March 2026
FRIENDLY_NAME = "260508-hsbc-holdings-plc-pillar-3-disclosures-at-31-march-2026-english.pdf"


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
