"""Tests for isda_p3.config — Paths, constants, setup_logging (chunk 0.2)."""

import logging
from decimal import Decimal
from pathlib import Path

from isda_p3 import config
from isda_p3.config import (
    CONFIDENCE_AUTO_ACCEPT,
    MAP_MAX_TOKENS,
    MAP_MODEL_COMPLEX,
    MAP_MODEL_SIMPLE,
    PROMPT_VERSION,
    Paths,
    setup_logging,
)

# Every public Paths attribute that must resolve to a Path under ROOT.
_PATH_ATTRS = [
    "ROOT",
    "CONFIG",
    "TEMPLATES",
    "DATA",
    "RAW",
    "REGISTRY",
    "MANIFEST",
    "EXTRACTED",
    "DATASET_DIR",
    "DATASET",
    "REVIEW_QUEUE",
    "GOLDEN",
    "GOLDEN_PDFS",
    "GOLDEN_EXPECTED",
]


def test_root_contains_pyproject():
    assert (Paths.ROOT / "pyproject.toml").is_file()


def test_dataset_path_composition():
    assert Paths.DATASET == Paths.ROOT / "data" / "dataset" / "values.parquet"
    assert Paths.MANIFEST == Paths.ROOT / "data" / "registry" / "manifest.csv"


def test_all_paths_are_paths_under_root():
    for attr in _PATH_ATTRS:
        value = getattr(Paths, attr)
        assert isinstance(value, Path), f"{attr} is not a Path"
        assert value == Paths.ROOT or Paths.ROOT in value.parents, f"{attr} not under ROOT"


def test_ensure_creates_writable_dirs_and_is_idempotent(tmp_path, monkeypatch):
    writable = {
        "RAW": tmp_path / "data" / "raw",
        "REGISTRY": tmp_path / "data" / "registry",
        "EXTRACTED": tmp_path / "data" / "extracted",
        "DATASET_DIR": tmp_path / "data" / "dataset",
        "REVIEW_QUEUE": tmp_path / "data" / "review_queue",
    }
    protected = {
        "CONFIG": tmp_path / "config",
        "GOLDEN": tmp_path / "data" / "golden",
        "GOLDEN_PDFS": tmp_path / "data" / "golden" / "pdfs",
        "GOLDEN_EXPECTED": tmp_path / "data" / "golden" / "expected",
    }
    for attr, p in {**writable, **protected}.items():
        monkeypatch.setattr(Paths, attr, p)

    Paths.ensure()
    Paths.ensure()  # idempotent: second call must not raise

    for attr, p in writable.items():
        assert p.is_dir(), f"ensure() did not create {attr}"
    for attr, p in protected.items():
        assert not p.exists(), f"ensure() must not create {attr}"


def test_setup_logging_idempotent_single_handler():
    logger = logging.getLogger("isda_p3")
    logger.handlers.clear()

    setup_logging()
    setup_logging()

    assert len(logger.handlers) == 1
    assert logger.level == logging.INFO


def test_setup_logging_respects_level():
    logger = logging.getLogger("isda_p3")
    logger.handlers.clear()

    setup_logging(level=logging.DEBUG)

    assert logger.level == logging.DEBUG


def test_confidence_auto_accept_is_decimal():
    assert isinstance(CONFIDENCE_AUTO_ACCEPT, Decimal)
    assert CONFIDENCE_AUTO_ACCEPT == Decimal("0.95")


def test_model_constants_are_nonempty_strings():
    for const in (MAP_MODEL_SIMPLE, MAP_MODEL_COMPLEX, PROMPT_VERSION):
        assert isinstance(const, str) and const
    assert isinstance(MAP_MAX_TOKENS, int) and MAP_MAX_TOKENS > 0


def test_fetch_constants_present():
    assert isinstance(config.USER_AGENT, str) and config.USER_AGENT
    assert config.FETCH_TIMEOUT_S == 30
    assert config.FETCH_RATE_LIMIT_S == 2.0
    assert config.FETCH_RETRIES == 1
