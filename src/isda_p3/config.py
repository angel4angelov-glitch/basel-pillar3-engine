"""Central paths, constants, and logging setup for isda_p3.

Stdlib-only (pathlib, logging, decimal). No I/O at import time except the cheap
project-root detection. Ported in spirit from boe-rag's ``config.py``.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _detect_root() -> Path:
    """Repo root = nearest ancestor of this file containing ``pyproject.toml``."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("Could not locate project root (no pyproject.toml above config.py).")


class Paths:
    """Central directory/file resolver, all rooted at the repo root.

    Attributes are plain ``Path`` objects (pure joins — no filesystem access at
    import). Use :meth:`ensure` to create the writable data dirs on demand.
    """

    ROOT = _detect_root()

    CONFIG = ROOT / "config"
    TEMPLATES = CONFIG / "templates"

    DATA = ROOT / "data"
    RAW = DATA / "raw"
    REGISTRY = DATA / "registry"
    MANIFEST = REGISTRY / "manifest.csv"
    EXTRACTED = DATA / "extracted"
    DATASET_DIR = DATA / "dataset"
    DATASET = DATASET_DIR / "values.parquet"
    REVIEW_QUEUE = DATA / "review_queue"

    GOLDEN = DATA / "golden"
    GOLDEN_PDFS = GOLDEN / "pdfs"
    GOLDEN_EXPECTED = GOLDEN / "expected"

    # Dirs the pipeline writes to. The golden/config dirs are checked in and
    # never created here.
    _WRITABLE = ("RAW", "REGISTRY", "EXTRACTED", "DATASET_DIR", "REVIEW_QUEUE")

    @classmethod
    def ensure(cls) -> None:
        """Create the writable data dirs (idempotent). Never touches golden/config."""
        for attr in cls._WRITABLE:
            getattr(cls, attr).mkdir(parents=True, exist_ok=True)


# --- LLM mapping (box 4, bounded) ------------------------------------------------
MAP_MODEL_SIMPLE = "claude-haiku-4-5"  # default mapper for unmatched row labels
MAP_MODEL_COMPLEX = "claude-sonnet-4-6"  # escalation for ambiguous classifications
MAP_MAX_TOKENS = 1024  # mapping output is a tiny structured object
PROMPT_VERSION = "v1"  # bump when the mapping prompt changes (audit field)

# --- HTTP fetch (boxes 1-2) ------------------------------------------------------
USER_AGENT = "isda-p3/0.1 (Basel Pillar 3 disclosure benchmarking; research/non-commercial)"
FETCH_TIMEOUT_S = 30  # per-request timeout, seconds
FETCH_RATE_LIMIT_S = 2.0  # min delay between requests to one host, seconds
FETCH_RETRIES = 1  # retries after the first attempt

# --- Reconciliation routing ------------------------------------------------------
CONFIDENCE_AUTO_ACCEPT = Decimal("0.95")  # >= this and no hard FAIL -> AUTO_PASSED

_LOG_HANDLER_FLAG = "_isda_p3_handler"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the ``isda_p3`` package logger once (idempotent)."""
    logger = logging.getLogger("isda_p3")
    logger.setLevel(level)
    logger.propagate = False  # we own the handler; avoid double-logging via root
    if any(getattr(h, _LOG_HANDLER_FLAG, False) for h in logger.handlers):
        return
    handler = logging.StreamHandler()
    setattr(handler, _LOG_HANDLER_FLAG, True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
