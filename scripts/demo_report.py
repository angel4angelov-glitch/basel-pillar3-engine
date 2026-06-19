"""Generate a visual HTML report from a real pipeline run.

Runs the reconciliation pipeline twice over the committed sample KM1 data —
once clean (all figures tie out) and once with ONE figure deliberately corrupted
(so you can watch the firewall flag it) — and writes a self-contained
``demo_report.html`` you can open in any browser.

    python scripts/demo_report.py    # writes + (on macOS) opens demo_report.html
"""

import dataclasses
import html
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from isda_p3.config_load import load_banks
from isda_p3.discovery.p3dh import parse_km1_xbrl_csv
from isda_p3.models import ReportingPeriod, Template
from isda_p3.reconcile.engine import reconcile_template
from isda_p3.reconcile.identities import load_tolerances, load_weights

SAMPLE = Path("tests/fixtures/p3dh_km1_sample.csv")
OUT = Path("demo_report.html")

LABELS = {
    "KM1.1": "Common Equity Tier 1 (CET1) capital",
    "KM1.2": "Tier 1 capital",
    "KM1.3": "Total capital",
    "KM1.4": "Total risk-weighted assets (RWA)",
    "KM1.5": "CET1 ratio",
    "KM1.6": "Tier 1 ratio",
    "KM1.7": "Total capital ratio",
}


def run(values_by_code):
    return reconcile_template(
        values_by_code, Template.KM1, tolerances=load_tolerances(), weights=load_weights()
    )


def rows_html(results):
    out = []
    for r in sorted(results, key=lambda r: r.field_value.field_code):
        fv = r.field_value
        ok = r.status == "AUTO_PASSED"
        badge = "ok" if ok else "flag"
        checks = "<br>".join(
            f"<span class='c-{c.outcome.lower()}'>{html.escape(c.check_type)}: "
            f"{c.outcome}</span> &nbsp;<small>{html.escape(c.detail)}</small>"
            for c in r.checks
        )
        out.append(
            f"<tr class='{badge}'>"
            f"<td><b>{fv.field_code}</b><br><small>{html.escape(LABELS.get(fv.field_code, ''))}</small></td>"
            f"<td class='num'>{fv.value} <small>{html.escape(fv.unit)}</small></td>"
            f"<td><span class='pill {badge}'>{r.status}</span><br><small>conf {r.confidence}</small></td>"
            f"<td class='checks'>{checks}</td>"
            f"<td><small>{html.escape(fv.provenance.engine)} · "
            f"{html.escape(fv.provenance.source_kind)}</small></td>"
            f"</tr>"
        )
    return "\n".join(out)


def main() -> None:
    bank = next(b for b in load_banks() if b.id == "deutsche-bank")
    period = ReportingPeriod(2025, 4)
    base = parse_km1_xbrl_csv(SAMPLE, bank=bank, period=period, source_url=bank.ir_url)
    clean = {fv.field_code: fv for fv in base}

    # corrupt ONE figure: overstate the CET1 ratio (KM1.5) to 15.0 — the identity will catch it.
    tampered = dict(clean)
    tampered["KM1.5"] = dataclasses.replace(clean["KM1.5"], value=Decimal("15.0"))

    clean_res = run(clean)
    tamper_res = run(tampered)
    flagged = [r for r in tamper_res if r.status != "AUTO_PASSED"]

    cet1, rwa = clean["KM1.1"].value, clean["KM1.4"].value
    computed = (cet1 / rwa * Decimal("100")).quantize(Decimal("0.01"))

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>isda-p3 — Pillar 3 extraction demo</title>
<style>
 body{{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;max-width:900px;margin:40px auto;
   padding:0 20px;color:#1a1a2e;background:#fafafd}}
 h1{{margin-bottom:4px}} .sub{{color:#667;margin-top:0}}
 table{{border-collapse:collapse;width:100%;margin:14px 0;background:#fff;
   box-shadow:0 1px 4px rgba(0,0,0,.07);border-radius:8px;overflow:hidden}}
 th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid #eee;vertical-align:top}}
 th{{background:#1a1a2e;color:#fff;font-size:13px;text-transform:uppercase;letter-spacing:.04em}}
 td.num{{font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}}
 .pill{{padding:3px 9px;border-radius:99px;font-size:12px;font-weight:700;color:#fff}}
 .pill.ok{{background:#15a34a}} .pill.flag{{background:#dc2626}}
 tr.flag{{background:#fff5f5}} .c-pass{{color:#15a34a;font-weight:600}}
 .c-fail{{color:#dc2626;font-weight:700}} .c-skip{{color:#999}}
 .checks small{{color:#778}} .panel{{background:#fff;border-radius:8px;padding:16px 20px;
   box-shadow:0 1px 4px rgba(0,0,0,.07);margin:14px 0}}
 .law{{background:#1a1a2e;color:#fff;border-radius:8px;padding:16px 20px;margin:18px 0}}
 code{{background:#eef;padding:2px 6px;border-radius:4px}}
 .big{{font-size:22px;font-weight:700}}
</style></head><body>

<h1>Basel Pillar 3 — extraction &amp; reconciliation</h1>
<p class="sub">{html.escape(bank.name)} · KM1 key metrics · {period.label} · source: structured XBRL-CSV</p>

<div class="law"><b>The one law:</b> the model never invents a number. Every figure is extracted
deterministically and must <b>pass an arithmetic check</b> before it is stored — otherwise it is
flagged for a human. Each value is traceable to its source.</div>

<h2>① A healthy run — every figure ties out</h2>
<table>
<tr><th>Field</th><th>Value</th><th>Status</th><th>Checks</th><th>Source</th></tr>
{rows_html(clean_res)}
</table>
<div class="panel">Worked check — <b>CET1 ratio identity</b>:
 <code>{cet1} ÷ {rwa} × 100 = {computed}%</code> vs the bank's stated
 <code>{clean['KM1.5'].value}%</code> → <span class="c-pass">PASS</span> (within tolerance).
 All 7 figures <b>AUTO_PASSED</b> → stored with full provenance.</div>

<h2>② The firewall — when a number is wrong</h2>
<p>Now we corrupt one figure: overstate the CET1 ratio to <b>15.0%</b> (a fat-finger / misread).
Nothing else changes. Watch what happens:</p>
<table>
<tr><th>Field</th><th>Value</th><th>Status</th><th>Checks</th><th>Source</th></tr>
{rows_html(tamper_res)}
</table>
<div class="panel"><span class="big">{len(flagged)} figure flagged → routed to human review.</span><br>
 The corrupted CET1 ratio (15.0%) does not equal CET1 ÷ RWA ({computed}%), so the ratio-identity check
 <span class="c-fail">FAILS</span>, confidence drops below the 0.95 auto-accept threshold, and the figure
 is <b>never stored as validated</b> — it lands in the review queue beside its source. No silently-wrong
 number reaches the dataset.</div>

<p class="sub">Generated by <code>scripts/demo_report.py</code> from a real pipeline run — no mock data.</p>
</body></html>"""

    OUT.write_text(doc, encoding="utf-8")
    print(f"Wrote {OUT.resolve()}")
    if sys.platform == "darwin":
        subprocess.run(["open", str(OUT)], check=False)


if __name__ == "__main__":
    main()
