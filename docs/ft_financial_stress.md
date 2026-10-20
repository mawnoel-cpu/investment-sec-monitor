# FT financial stress extension (FIN-1)

This is an evidence overlay invoked by `ft_sector_rotation.py`, not a new
collector, daily decision engine, schedule or trading service. Game end remains
27 November 2026. All thresholds are provisional; predictive effectiveness is
**UNVALIDATED**. Tests establish calculation and constraint behavior only.

## Ownership inventory

| Existing component | Preserved operation | Narrow addition |
| --- | --- | --- |
| `ft_macro_coordinator.py`, Macro Feed, Signals/Regime | Macro collection and interpretation | Read verified existing statuses; no new requests or macro calculations |
| `ft_sec_pipeline.py`, SEC Feed | Filing discovery, accession deduplication | Finance-review cue; read live holdings through TOTAL rather than obsolete row 19 |
| FT Research Intake, Evidence/Sources | Original sources, quality, provenance and calibrated formulas | Typed issuer facts in spare Evidence U:Z; refresh on new information or expiry |
| `ft_price_writer.py`, DAILY CLOSES, PRICE GATES | Verified sessions, prices and price ownership | Shared dynamic TOTAL boundary for holdings |
| `ft_sector_rotation.py`, RULE-015/020, candidate/bench ranks | Independent immediate warnings and qualification | Invoke reusable financial calculation; append causes and constrained options to existing proposals; actual holdings override stale candidate-held flags |
| Decision Cards, TRADE ANALYSIS, TRADE DESK | Existing evidence/decision tables and two-column display | One calculation block in unused Cards A40:AF60; labelled suffix in analysis F; existing proposal Q flows into Desk |
| `ft_pipeline_health.py`, BACKEND HEALTH, health task | One persisted monitor, existing read-only alert task | Verify financial calculation receipt, identities, arithmetic and readback |
| `ft_rotation_replay.py`, Decision History, Signal Autopsies, weekly Lessons | Frozen history, historical testing, weekly outcomes | Captured-as-of financing replay and 5/10-session diagnostics; no retrospective invented observations |

## Input contract: EvidenceTable U:Z

Do not replace A:T. Preserve F/I/K/L formulas, Quality/Extraction dropdowns,
native table bounds and footer. Enrich an existing original row when possible;
append distinct original disclosures inside the table. Financial context can
remain Quality=Context and direction zero, so this separate model never
inflates the calibrated evidence score.

| Column | Meaning |
| --- | --- |
| U | Canonical fact key, e.g. `FIN:TICKER:2026-06-30:fundamentals` |
| V | JSON object with typed values, never invented zeros |
| W | Actual verified-at numeric Toronto Sheets timestamp |
| X | Expiry numeric timestamp, normally next scheduled earnings or justified observation limit |
| Y | Industry, units, accounting/period basis and limitations |
| Z | Material counterevidence |

Required JSON: `ticker`, `kind` (`fundamentals` or `credit`), `profile`,
`unit: USD_millions`, `period_end` ISO date. Profiles: hyperscaler, neocloud,
software, refiner, upstream, miner, pharma, defence. Publication C, capture P,
verified W and expiry X must be ordered, not future; E must be an HTTPS source;
J must be Valid/Context; Q Full/Excerpt/Data. Missing, expired, future and
conflicting observations are excluded with a visible gap. Identical issuer,
kind and period facts are counted once, independent of publisher count. Newer
periodic SEC discoveries invalidate older fundamentals; amendments require
review. Same-period filings already available when the facts were verified do
not repeatedly force a refresh.

Fundamentals use `period_months` (3/6/9/12), `ocf`, `capex`, `capex_basis`;
optional `previous` must carry identical duration and basis. Record reported
FCF separately with its definition: do not force it to equal OCF less a
differently defined capital measure. Record debt, cash, gross/net interest,
issuance, maturity schedule, lease/contract commitments, guidance and separate
one-off acquisition/IPR&D outlays when verified. Unknown fields are null or
omitted, never zero. Coupons are issue terms, not current bond yields or CDS.

Credit scoring accepts verified event flags (`missed_payment`,
`covenant_breach`, `going_concern`, `failed_refinancing` plus `event_verified`),
or explicit complete `all_material_12m_obligations` scope with verified
`available_liquidity` and `obligations_12m`. Cash divided by current debt alone
does not qualify. Verified CDS/bond quotes need independent credit confirmation;
hedging-only/thin-market quotes cannot establish distress. CDS keys:
`cds_5y_bps`, `change_bps`, `quote_quality: verified`,
`independent_credit_confirmation`, `thin_market`, `hedging_only`. Bonds use
`bond_id`, `bond_spread_bps`, `bond_change_bps` and the same quality gates.
Accessible CDS sources were checked once without a reliable current free feed.
Do not repeatedly attempt restricted services or purchase access.

## Calculation and decisions

Weights are fixed: financing 35%, sustainability 30%, existing price/rotation
25%, existing macro 10%. Each known severity contributes severity times weight;
unknown contributions stay blank. **No redistribution; no total unless all four
categories are supported.** Missing CDS can coexist with a complete category
only when a qualifying labelled alternative supports it. Confidence is LOW /
incomplete unless all categories are present (then MODERATE, never a claim of
statistical validation). CDS status remains explicit in either case.

Sustainability measures issuer cash funding, not whether investment is good or
bad: cash capex/OCF <=80% starts at 20, >80% at 45, >100% at 60, >125% or
nonpositive OCF at 70. A comparable >15pp increase with shrinking cash surplus
raises severity to at least 55. Positive cash surplus caps it at 55 absent a
verified cash-guidance cut. Guidance cuts raise it to 60 or 80 at >=20%.
Refiners, upstream, miners and defence require at least six months to reduce
seasonal distortion. Pharma acquisition/IPR&D cash exceeding PPE surplus
requires a separate funding review (at least 45), not an automatic distress
trade. Noncash leases and differing FCF definitions remain separate.

Financing uses the strongest qualifying independent observation, never the
sum of correlated reports. Verified adverse credit events score 95. Complete
liquidity/obligation coverage <0.5x/1x/2x scores 90/70/40; >=2x scores 15.
Confirmed CDS >=500bp/250bp or >=100bp widening scores 85/65; >=25bp widening
40; otherwise 20. Confirmed bond spread >=500bp scores 85, >=100bp widening 65,
otherwise 40. These are review heuristics, not default probabilities.

Price severity references existing verified proposal outputs and existing
threshold decisions. Macro references current Signals statuses for HY, IG,
real yields and NFCI; maximum severity (Green15/Amber45/Red80) avoids summing
correlated macro warnings. It never recollects these series. All raw inputs,
contributions, coverage, two leading causes, contradictions, changes and JSON
are exposed in Decision Cards A40:AF60.

Interpretation: 0–39 HOLD-supportive; 40–59 WATCH; 60–79 evaluate 2–5pp trim;
80–100 evaluate larger trim/exit. **A score alone never trades.** Existing price
loss controls remain independent. New financing action needs a verified credit
event or deteriorating cash/guidance plus adverse existing price evidence.
The existing candidate valuation, catalyst, eligibility and no-chase checks
remain mandatory. New pair recommendations name exact sell/buy pp and require
the same execution venue/session. Pending orders block new finance pairings.
Cash <=25%, each name <=25%, >=5 names and existing minimum-size rules are
checked. A cut without room or a qualifying simultaneous purchase is BLOCKED;
retain that portion and say NO TRADE. No code submits an order or writes actual
holdings, queued allocations or Trade Log.

## Persistence, failure and rollback

The existing run writes and reads back Cards, proposal Q and its labelled
`FINANCIAL STRESS [...]` suffix in TRADE ANALYSIS F. Human prose and target
columns remain intact. PRICE MONITOR U210:V215 records VERIFIED only after
readback. Any addon exception retains verified base price proposals and records
FAILED; the existing health writer exposes it. Missing issuer data produces a
valid incomplete calculation, not a failed price engine.

Daily Decision freezes the exact assessment JSON in each new Decision History
S after its existing VERIFIED PRIOR CLOSE marker (`FIN_JSON=` suffix). Freeze
first financial warnings in existing Signal Autopsies; never overwrite prior
first capture. Weekly Lessons owns later outcome evaluation. None of the four
existing task schedules or GitHub cron expressions change.

Rollback: revert the implementation commit normally, preserving subsequent
commits and all Evidence/history/allocations. Retain evidence for audit; mark
the optional calculation unavailable if it is disabled by rollback. Do not
delete records or disable working collectors/schedules. Main before this change:
`49682a58e94cccab13bdcde266e2d8fc81ba78d0`.

## Validation limits

The 20-Oct read-only replay reused 6,836 verified archive rows, 80 prior held
Decision History snapshots and unchanged thresholds over 30-Sep–19-Oct. It
found **zero contemporaneously captured typed issuer-financing observations**;
today's extraction cannot be backdated. Existing price replay first identifies
SOXX versus XLV on 8-Oct close, for a 9-Oct review / earliest 12-Oct execution.
Its five-close relative diagnostic was +13.99pp for XLV versus SOXX; ten closes
were immature at the endpoint. This is neither a qualified stock replacement
nor an FT return. There is no credible estimate of financing's added predictive
value, financial false alarms or profitability yet. Missing historical
replacement qualification remains unknown. Synthetic tests cover HOLD, cash
TRIM, paired ROTATE, cash blocks, pending orders, missing/stale/conflicting
inputs, deduplication, hedging distortion, arithmetic and no-lookahead.

Run `python tests/run_offline.py`. Replay uses existing
`ft_rotation_replay.py --archive ... --financial-evidence ... --decision-history
... --start ... --end ... --output ...`; private workbook snapshots must not be
committed to this repository. Prospective 5/10-session evidence is required
before any predictive claim or threshold revision.
