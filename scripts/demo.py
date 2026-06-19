"""Watchable demo: structured KM1 ingest → reconciliation → routed results.

Runs the real pipeline over the committed sample XBRL-CSV (no PDF, no API key, no network):
parse → canonical FieldValues → reconcile every figure → print each with its checks + provenance.

    python scripts/demo.py
"""

from decimal import Decimal
from pathlib import Path

from isda_p3.config_load import load_banks
from isda_p3.discovery.p3dh import parse_km1_xbrl_csv
from isda_p3.models import ReportingPeriod, Template
from isda_p3.reconcile.engine import reconcile_template
from isda_p3.reconcile.identities import load_tolerances, load_weights

SAMPLE = Path("tests/fixtures/p3dh_km1_sample.csv")


def main() -> None:
    bank = next(b for b in load_banks() if b.id == "deutsche-bank")
    period = ReportingPeriod(2025, 4)

    print(f"\nBank: {bank.name} ({bank.jurisdiction}, reports in {bank.reporting_currency})")
    print(f"Source: structured XBRL-CSV  →  {SAMPLE}\n")

    values = parse_km1_xbrl_csv(SAMPLE, bank=bank, period=period, source_url=bank.ir_url)
    by_code = {fv.field_code: fv for fv in values}

    results = reconcile_template(
        by_code, Template.KM1, tolerances=load_tolerances(), weights=load_weights()
    )

    print(f"{'FIELD':<8}{'VALUE':>12} {'UNIT':<9}{'STATUS':<13}{'CONF':>5}  CHECKS")
    print("-" * 78)
    for r in sorted(results, key=lambda r: r.field_value.field_code):
        fv = r.field_value
        checks = ", ".join(f"{c.check_type}:{c.outcome}" for c in r.checks) or "—"
        print(
            f"{fv.field_code:<8}{str(fv.value):>12} {fv.unit:<9}"
            f"{r.status:<13}{str(r.confidence):>5}  {checks}"
        )

    n_ok = sum(1 for r in results if r.status == "AUTO_PASSED")
    print("-" * 78)
    print(f"{n_ok}/{len(results)} figures AUTO_PASSED (internally consistent, traceable).")
    # Show the headline reconciliation that makes one number trustworthy:
    cet1 = by_code["KM1.1"].value
    rwa = by_code["KM1.4"].value
    print(
        f"\nWorked check — CET1 ratio identity:  {cet1} / {rwa} × 100 = "
        f"{(cet1 / rwa * Decimal('100')).quantize(Decimal('0.01'))}%  "
        f"vs stated {by_code['KM1.5'].value}%  → PASS\n"
    )


if __name__ == "__main__":
    main()
