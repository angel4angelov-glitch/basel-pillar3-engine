# Basel Pillar 3 Disclosure Framework — Taxonomy Briefing

Research date: 2026-06-17. All claims citation-backed; uncertainties flagged at §8.

## 1. Template taxonomy (codes, titles, families)

Authoritative complete enumeration = **BCBS d604 Annex 2, Table 4** (Dec 2025 consultation
"Machine-readable Pillar 3 disclosure"). **82 templates total: 15 qualitative-only, 67 quantitative/mixed.**
Grouped by DIS chapter:

- **DIS20 — Overview / key metrics / RWA:** KM1 (key metrics, group), KM2 (TLAC), OVA (qual), **OV1 (overview of RWA)**. (d604 proposes MD1 metadata, MD2 references — machine-readable only.)
- **DIS21 — Modelled vs standardised RWA:** **CMS1** (RWA at risk level), **CMS2** (credit risk by asset class). ← output-floor comparison.
- **DIS25 — Composition of capital & TLAC:** CCA, CC1 (composition of regulatory capital), CC2 (reconciliation to balance sheet), TLAC1/2/3.
- **DIS26:** CDC (capital distribution constraints).
- **DIS30 — Links to financial statements:** LIA, LI1, LI2, PV1 (prudent valuation).
- **DIS31:** ENC (asset encumbrance). **DIS35 — Remuneration:** REMA, REM1/2/3.
- **DIS40 — Credit risk (CR\*):** CRA/CRB/CRB-A/CRC/CRD/CRE (qual), CR1 (credit quality of assets), CR2 (changes in defaulted stock), CR3 (CRM overview), CR4 (SA exposure & CRM), CR5 (SA by asset class & RW), **CR6 (IRB by portfolio & PD range)**, CR7 (IRB credit-deriv effect), CR8 (RWA flow IRB), CR9 (IRB PD backtesting), CR10 (slotting).
- **DIS42 — Counterparty credit risk (CCR\*):** CCRA (qual), CCR1 (exposures by approach), CCR3 (SA by portfolio & RW), CCR4 (IRB by portfolio & PD), CCR5 (collateral), CCR6 (credit derivatives), CCR7 (RWA flow IMM), CCR8 (exposures to CCPs). **No CCR2** (CVA moved to DIS51).
- **DIS43 — Securitisation:** SECA, SEC1/2/3/4. **DIS45 — Sovereign:** SOV1/2/3.
- **DIS50 — Market risk (MR\*):** MRA/MRB (qual), MR1 (SA), MR2 (IMA), MR3 (simplified SA).
- **DIS51 — CVA risk:** CVAA/CVAB (qual), CVA1 (reduced BA-CVA), CVA2 (full BA-CVA), CVA3 (SA-CVA), CVA4 (RWA flow).
- **DIS55 — Cryptoasset:** CAEA, CAE1/2/3. **DIS60 — Operational risk:** ORA, OR1 (losses), OR2 (business indicator), OR3 (min capital).
- **DIS70 — IRRBB:** IRRBBA, IRRBB1. **DIS75 — Macroprudential:** GSIB1, CCyB1.
- **DIS80 — Leverage ratio (LR\*):** LR1 (summary comparison), LR2 (common disclosure).
- **DIS85 — Liquidity:** LIQA, LIQ1 (LCR), LIQ2 (NSFR).

### KM1 — exact rows (EU/UK, from PRA UKB KM1 instructions; mirrors EBA/CRR3)
Own funds: `1 CET1` (+`1a` fully-loaded ECL), `2 Tier 1` (+`2a`), `3 Total capital` (+`3a`).
RWA: `4 Total RWEA`, `4a Total RWEA (pre-floor)`.
Ratios: `5 CET1 %` (+`5a`, `5b pre-floor`), `6 Tier 1 %` (+`6a`,`6b`), `7 Total capital %` (+`7a`,`7b`).
SREP (UK): `UK 7a–7d` additional CET1/AT1/T2/total SREP requirements.
Buffers: `8 conservation`, `9 countercyclical` (+`UK 9a`), `10 G-SII` (+`UK 10a O-SII`), `11 combined buffer`, `UK 11a OCR %`, `12 CET1 available after SREP %`.
Leverage: `13 total exposure measure`, `14 leverage ratio %` (+`14a–14e`).
Liquidity: `15 total HQLA`, `UK 16a/16b cash out/inflows`, `16 net cash outflows`, `17 LCR %`, `18 ASF`, `19 RSF`, NSFR ratio.
→ `4a/5b/6b/7b "pre-floor"` rows = CRR3 output-floor additions, high benchmarking value.

### OV1 — exact columns & rows (PRA UK OV1 instructions)
Columns: **a** = RWEA current, **b** = RWEA prior (T-1), **c** = total own funds requirement (min capital).
Rows: `1 Credit risk (excl CCR)` → `2 SA`,`3 F-IRB`,`4 slotting`,`UK 4a equities`,`5 A-IRB`; `6 CCR` → `7 SA`,`8 IMM`,`UK 8a CCP`,`UK 8b CVA`,`9 other`; `15 settlement`; `16 securitisation` → `17 SEC-IRBA`,`18 SEC-ERBA`,`19 SEC-SA`,`UK 19a 1250%`; `20 market risk` → `21 SA`,`22 IMA`,`UK 22a large exposures`; `23 operational` → `UK 23a–c BIA/SA/AMA`; `24 amounts below deduction threshold (memo)`.

## 2. Fixed vs flexible — the leverage point
BIS FSI: *fixed format* = all rows/cols/fields predetermined; *flexible* = bank discretion.
Rule (d604): banks may delete irrelevant rows or add sub-rows, **must not renumber prescribed rows** → row number = stable concept across banks. Fixed/flexible flag per template in **d604 Annex 2 Table 4**.
**Fixed (target these):** KM1, KM2, OV1, CMS1/2, CC1, TLAC1-3, CDC, PV1, ENC, CR1-8, CCR1/3/4/7/8, SEC3/4, SOV1-3, MR1/2/3, CVA1-4, OR1-3, IRRBB1, LR1/2, LIQ1/2.
**Flexible (less comparable):** CC2, LI1/2, REM1-3, CR9/10, CCR5/6, SEC1/2, CAE1, GSIB1, CCyB1; all `*A`/`*B` qualitative.

## 3. Governing standards
- **BCBS:** Basel Framework DIS chapters (in force) https://www.bis.org/basel_framework/standard/DIS.htm ; origin d400/d432/d455; **d604** machine-readable consultation (Dec 2025, comments by 5 Mar 2026) https://www.bis.org/bcbs/publ/d604.pdf
- **EU:** Commission Implementing Reg **(EU) 2021/637** (ITS on disclosures) https://eur-lex.europa.eu/eli/reg_impl/2021/637 ; CRR3 successor **(EU) 2024/3172** (draft EBA/ITS/2024/05). Underlying: CRR 575/2013 Part Eight, amended by CRR3 2024/1623; frequency Arts 433/433a/b/c.
- **UK:** PRA Rulebook Disclosure (CRR) Part https://www.prarulebook.co.uk/pra-rules/disclosure-crr ; PS22/21; Basel 3.1 via CP16/22.
- **US:** no single ITS — 12 CFR Part 217 Subpart D + FFIEC 101 report.

## 4. Cadence
Basel: prescribed frequencies are minima. Quarterly = KM1/KM2/OV1/CMS1/CR8/CCR7/CVA4/MR2/LR1/LR2/LIQ1; semi-annual = most credit/CCR/market/sec detail + CC1/LIQ2/ENC/SOV; annual = capital narrative, links-to-financials, REM, OR1-3, IRRBB, GSIB/CCyB.
**EU/UK size-dependent (operative rule):** Art 433a large = quarterly KM1 + subset; 433b SNCI = annual most, KM1 semi-annual; 433c other = annual, KM1 semi-annual. G-SIB adds TLAC + GSIB1.

## 5. Jurisdictional divergence
- **EU:** most structured. Fixed ITS templates + **EBA Pillar 3 Data Hub (P3DH) from Jan 2026** = centralised machine-readable **XBRL-CSV**, publicly accessible. Step-change.
- **UK:** same templates (UK-prefixed), PDF on bank/PRA channels, no central hub.
- **US:** different architecture — 12 CFR 217 Subpart D tables + **FFIEC 101** (Schedules A–S, machine-readable; embeds Basel III capital template). Bank Pillar 3 = standalone PDF. Basel III endgame proposal (Cat I–IV ≥$100bn) in flux as of 2026.
- **Switzerland (FINMA Circ 2016/1):** Basel templates, central collection, PDF docs.
- **Japan (FSA):** Basel structure via FSA notices, PDF. (Lowest-confidence jurisdiction.)
Machine-readability: **EU = XBRL via P3DH; US = FFIEC 101 structured; UK/CH/JP = PDF-only.**

## 6. Machine-readable taxonomy (exists today)
1. **EBA DPM + XBRL taxonomy** — EU machine-readable dictionary (supervisory + Pillar 3). https://www.eba.europa.eu/risk-and-data-analysis/reporting-frameworks/dpm-data-dictionary
2. **EBA P3DH** — operational Jan 2026, CRR3 Art 434, XBRL-CSV, public via European Data Access Portal. https://www.eba.europa.eu/risk-and-data-analysis/pillar-3-data-hub
3. **Proposed BCBS global DSD (d604)** — unique field ID per cell, e.g. `KM1.Capital.AvailableCapital.Current.CET1.T`; formats JSON Schema / SDMX-CSV / XBRL-JSON / XBRL-CSV; REST API. National carve-out until 1 Jan 2029. → build canonical schema mapped to this for forward-compat.

## 7. Highest-value benchmarking fields → template/row
- Total RWA → OV1 (sum), KM1 row 4. Pre-floor RWA → KM1 4a; floor comparison CMS1/CMS2.
- RWA by risk type → OV1 rows 1/6/15/16/20/23/UK8b (col a); min capital col c.
- SA vs model splits → OV1: credit 2/3/5 (CR4/CR6), CCR 7/8 (CCR1/3/4), market 21/22 (MR1/MR2).
- CVA → OV1 UK8b; CVA1-4. Op risk → OR1/2/3. Capital amounts/ratios → KM1 1-7 (CC1 detail).
- Output floor → KM1 pre-floor rows + CMS1/2. Pillar 2/buffers → KM1 UK7a-d, 8-12.
- Leverage → KM1 13/14, LR1/2. LCR → KM1 15-17, LIQ1. NSFR → KM1 18/19, LIQ2.
- Modelled-vs-std divergence → CMS1/2. IRB params/RWA density → CR6. G-SIB → GSIB1, KM2/TLAC1.
**Sweet spot: KM1 + OV1 + CMS1/2 + CR6 + CVA/OR/MR detail.**

## 8. Flagged uncertainties (do NOT treat as settled)
1. OV1 exact CRR3 post-output-floor row labels — confirm vs 2024/3172 ITS (single-source PRA PDF, "superseded" banner).
2. Per-template frequency split within each EU size tier — read off 2024/3172 instructions.
3. US disclosure-table structure under Basel III endgame — genuinely unsettled as of 2026.
4. Japan FSA template structure — only LCR notice corroborated; rest inferred.
5. d604 is a *consultation* (Dec 2025), not final — DSD/API/field-IDs may change.

### Primary sources
d604 https://www.bis.org/bcbs/publ/d604.pdf · FSI summary https://www.bis.org/fsi/fsisummaries/pillar3_framework.pdf · DIS chapters https://www.bis.org/basel_framework/standard/DIS.htm · ITS 2021/637 https://eur-lex.europa.eu/eli/reg_impl/2021/637 · P3DH https://www.eba.europa.eu/risk-and-data-analysis/pillar-3-data-hub · EBA DPM https://www.eba.europa.eu/risk-and-data-analysis/reporting-frameworks/dpm-data-dictionary · PRA Disclosure https://www.prarulebook.co.uk/pra-rules/disclosure-crr · FFIEC 101 https://www.ffiec.gov/sites/default/files/data/reporting-forms/hv-101/FFIEC101_201609_i.pdf
