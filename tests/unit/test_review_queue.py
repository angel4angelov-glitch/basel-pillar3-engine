"""Tests for isda_p3.review.queue (chunk 2.4) — low-confidence triage queue.

The human-review boundary (TOOLING.md box 6): a FLAGGED figure must be fully
adjudicable WITHOUT reopening the PDF — value beside its verbatim source cell, both
engine values when they disagree, and the checks that fired. The audit invariants
(CLAUDE.md §A): Decimals serialise as STRINGS and read back as exact ``Decimal``
(never float), and ``raw_text`` surfaces the verbatim cell (``"13.6¹"``), not just
the parsed number. AUTO_PASSED rows never enter the queue.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from isda_p3.config import Paths
from isda_p3.models import (
    BBox,
    Bank,
    CheckOutcome,
    CheckResult,
    CheckType,
    EclBasis,
    Engine,
    FieldValue,
    FloorBasis,
    Jurisdiction,
    MappingDecision,
    MappingMethod,
    Provenance,
    ReconciliationResult,
    ReportingPeriod,
    SourceKind,
    Template,
    Unit,
    ValidationStatus,
)
from isda_p3.review.queue import (
    ReviewItem,
    enqueue_flagged,
    load_queue,
    render_item,
    resolve,
)

# --- builders ------------------------------------------------------------------

_BANK = Bank(
    id="barclays",
    name="Barclays",
    jurisdiction=Jurisdiction.UK,
    ir_url="https://home.barclays",
    p3dh_lei=None,
    number_locale="en_GB",
    reporting_currency="GBP",
)
_MAP = MappingDecision(
    method=MappingMethod.RULE,
    model=None,
    prompt_sha=None,
    prompt_version=None,
    matched_alias="cet1 ratio",
    confidence=Decimal("1"),
)
_BBOX = BBox(page=4, x0=10.0, y0=20.0, x1=30.0, y1=40.0)
_RUN_ID = "run-2025q4-001"


def _check(
    check_type: CheckType,
    outcome: CheckOutcome,
    detail: str,
    *,
    field_codes: tuple[str, ...] = ("KM1.5",),
) -> CheckResult:
    return CheckResult(
        check_type=check_type,
        outcome=outcome,
        field_codes=field_codes,
        expected=None,
        actual=None,
        tolerance=None,
        detail=detail,
    )


def _result(
    field_code: str,
    value: str,
    *,
    status: ValidationStatus,
    engine_values: dict[Engine, Decimal] | None = None,
    checks: tuple[CheckResult, ...] = (),
    raw_text: str | None = None,
    confidence: str = "0.50",
    unit: Unit = Unit.PERCENT,
    bbox: BBox | None = _BBOX,
) -> ReconciliationResult:
    v = Decimal(value)
    prov = Provenance(
        bank_id="barclays",
        period=ReportingPeriod(2025, 4),
        source_url="https://home.barclays/p3.pdf",
        source_kind=SourceKind.PDF,
        engine=Engine.DOCLING,
        bbox=bbox,
    )
    fv = FieldValue(
        template=Template.KM1,
        field_code=field_code,
        value=v,
        unit=unit,
        ecl_basis=EclBasis.NA,
        floor_basis=FloorBasis.FINAL,
        provenance=prov,
        mapping=_MAP,
        raw_text=value if raw_text is None else raw_text,
        engine_values=engine_values if engine_values is not None else {Engine.DOCLING: v},
    )
    return ReconciliationResult(
        field_value=fv,
        checks=checks,
        confidence=Decimal(confidence),
        validation_basis=(),
        status=status,
    )


@pytest.fixture
def tmp_queue(tmp_path, monkeypatch):
    """Point the review queue at an isolated tmp dir (no real ``data/`` writes)."""
    qdir = tmp_path / "review_queue"
    monkeypatch.setattr(Paths, "REVIEW_QUEUE", qdir)
    monkeypatch.setattr(
        Paths, "ensure", classmethod(lambda cls: qdir.mkdir(parents=True, exist_ok=True))
    )
    return qdir


# --- enqueue writes only flagged -----------------------------------------------


def test_enqueue_writes_only_flagged(tmp_queue):
    flagged = _result("KM1.5", "13.6", status=ValidationStatus.FLAGGED)
    passed = _result("KM1.4", "368000", status=ValidationStatus.AUTO_PASSED, unit=Unit.GBP_M)

    written = enqueue_flagged([flagged, passed], bank=_BANK, run_id=_RUN_ID)
    assert written == 1

    queue = load_queue()
    assert len(queue) == 1
    assert queue[0].field_code == "KM1.5"
    assert queue[0].status is ValidationStatus.FLAGGED


def test_enqueue_no_flagged_is_noop(tmp_queue):
    passed = _result("KM1.4", "368000", status=ValidationStatus.AUTO_PASSED, unit=Unit.GBP_M)
    assert enqueue_flagged([passed], bank=_BANK, run_id=_RUN_ID) == 0
    assert load_queue() == []


# --- round-trip: exact Decimal, no float ---------------------------------------


def test_round_trip_decimals_are_exact_not_float(tmp_queue):
    engines = {Engine.DOCLING: Decimal("13.6"), Engine.CAMELOT: Decimal("13.8")}
    res = _result(
        "KM1.5", "13.6", status=ValidationStatus.FLAGGED, engine_values=engines, confidence="0.50"
    )
    enqueue_flagged([res], bank=_BANK, run_id=_RUN_ID)

    item = load_queue()[0]
    assert isinstance(item.value, Decimal)
    assert isinstance(item.confidence, Decimal)
    assert item.value == Decimal("13.6")
    assert item.confidence == Decimal("0.50")
    assert not isinstance(item.value, float)
    for v in item.engine_values.values():
        assert isinstance(v, Decimal)
    assert item.engine_values[Engine.DOCLING] == Decimal("13.6")
    assert item.engine_values[Engine.CAMELOT] == Decimal("13.8")


def test_decimals_are_serialised_as_strings_on_disk(tmp_queue):
    res = _result("KM1.5", "13.6", status=ValidationStatus.FLAGGED, confidence="0.50")
    enqueue_flagged([res], bank=_BANK, run_id=_RUN_ID)

    raw = json.loads((tmp_queue / f"{_RUN_ID}.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert raw["value"] == "13.6"  # string, never a JSON float
    assert isinstance(raw["value"], str)
    assert isinstance(raw["confidence"], str)
    assert all(isinstance(v, str) for v in raw["engine_values"].values())


def test_hand_edited_float_value_is_rejected_not_coerced(tmp_queue):
    # A human edits the jsonl and writes a bare number instead of a string. Decimal(13.6)
    # would silently corrupt to 13.5999...; the loader must FAIL LOUD (CLAUDE.md §A).
    res = _result("KM1.5", "13.6", status=ValidationStatus.FLAGGED)
    enqueue_flagged([res], bank=_BANK, run_id=_RUN_ID)
    path = tmp_queue / f"{_RUN_ID}.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    record["value"] = 13.6  # bare JSON float, not a string
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="value"):
        load_queue()


# --- render: value beside source cell, both engines ----------------------------


def test_render_shows_both_engines_and_raw_cell(tmp_queue):
    engines = {Engine.DOCLING: Decimal("13.6"), Engine.CAMELOT: Decimal("13.8")}
    checks = (
        _check(CheckType.TWO_ENGINE, CheckOutcome.FAIL, "DOCLING 13.6 vs CAMELOT 13.8"),
        _check(CheckType.RATIO_IDENTITY, CheckOutcome.PASS, ""),
    )
    res = _result(
        "KM1.5",
        "13.6",
        status=ValidationStatus.FLAGGED,
        engine_values=engines,
        checks=checks,
        raw_text="13.6¹",
    )
    enqueue_flagged([res], bank=_BANK, run_id=_RUN_ID)
    rendered = render_item(load_queue()[0])

    assert "DOCLING=13.6" in rendered
    assert "CAMELOT=13.8" in rendered  # BOTH engine values shown
    assert "disagreement" in rendered  # the engines differ
    assert "TWO_ENGINE FAIL" in rendered
    assert "RATIO_IDENTITY PASS" in rendered
    assert "page 4" in rendered or "p4" in rendered
    assert "0.50" in rendered  # confidence


def test_render_surfaces_verbatim_raw_text_not_just_parsed(tmp_queue):
    # A fused footnote superscript: the human must see "13.6¹", not just 13.6.
    res = _result("KM1.5", "13.6", status=ValidationStatus.FLAGGED, raw_text="13.6¹")
    enqueue_flagged([res], bank=_BANK, run_id=_RUN_ID)
    item = load_queue()[0]

    assert item.raw_text == "13.6¹"  # verbatim survives the round-trip
    assert "13.6¹" in render_item(item)  # and is surfaced to the reviewer


# --- resolve: confirm / correct / errors ---------------------------------------


def test_resolve_confirm_sets_human_confirmed(tmp_queue):
    res = _result("KM1.5", "13.6", status=ValidationStatus.FLAGGED)
    enqueue_flagged([res], bank=_BANK, run_id=_RUN_ID)

    updated = resolve(_RUN_ID, "KM1.5", action="confirm", note="looks right")
    assert updated.status is ValidationStatus.HUMAN_CONFIRMED
    assert updated.note == "looks right"
    assert updated.value == Decimal("13.6")  # value unchanged on confirm

    reloaded = load_queue()[0]
    assert reloaded.status is ValidationStatus.HUMAN_CONFIRMED  # persisted to disk


def test_resolve_correct_updates_value_and_status(tmp_queue):
    res = _result("KM1.5", "13.6", status=ValidationStatus.FLAGGED)
    enqueue_flagged([res], bank=_BANK, run_id=_RUN_ID)

    updated = resolve(
        _RUN_ID, "KM1.5", action="correct", corrected_value=Decimal("13.8"), note="OCR fix"
    )
    assert updated.status is ValidationStatus.HUMAN_CORRECTED
    assert updated.value == Decimal("13.8")

    reloaded = load_queue()[0]
    assert reloaded.status is ValidationStatus.HUMAN_CORRECTED
    assert reloaded.value == Decimal("13.8")  # corrected value persisted exactly


def test_resolve_correct_without_value_raises(tmp_queue):
    res = _result("KM1.5", "13.6", status=ValidationStatus.FLAGGED)
    enqueue_flagged([res], bank=_BANK, run_id=_RUN_ID)
    with pytest.raises(ValueError, match="corrected_value"):
        resolve(_RUN_ID, "KM1.5", action="correct")


def test_resolve_unknown_field_raises(tmp_queue):
    res = _result("KM1.5", "13.6", status=ValidationStatus.FLAGGED)
    enqueue_flagged([res], bank=_BANK, run_id=_RUN_ID)
    with pytest.raises(KeyError, match="KM1.7"):
        resolve(_RUN_ID, "KM1.7", action="confirm")


def test_resolve_unknown_run_raises(tmp_queue):
    with pytest.raises(FileNotFoundError, match="nope"):
        resolve("nope", "KM1.5", action="confirm")


def test_resolve_leaves_other_items_untouched(tmp_queue):
    a = _result("KM1.5", "13.6", status=ValidationStatus.FLAGGED)
    b = _result("KM1.6", "17.5", status=ValidationStatus.FLAGGED)
    enqueue_flagged([a, b], bank=_BANK, run_id=_RUN_ID)

    resolve(_RUN_ID, "KM1.5", action="confirm")
    by = {it.field_code: it for it in load_queue()}
    assert by["KM1.5"].status is ValidationStatus.HUMAN_CONFIRMED
    assert by["KM1.6"].status is ValidationStatus.FLAGGED  # untouched


# --- ReviewItem is frozen ------------------------------------------------------


def test_review_item_is_frozen(tmp_queue):
    res = _result("KM1.5", "13.6", status=ValidationStatus.FLAGGED)
    enqueue_flagged([res], bank=_BANK, run_id=_RUN_ID)
    item = load_queue()[0]
    with pytest.raises(Exception):
        item.value = Decimal("99")  # type: ignore[misc]
    assert isinstance(item, ReviewItem)
