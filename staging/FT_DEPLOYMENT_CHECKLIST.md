# FT Game remaining fixes — prepared, not deployed

Prepared 1 Oct 2026. Nothing in this branch changes the live workbook or default-branch schedules.

## Ownership model

- **FT Research Intake**: sole routine owner of Evidence/checkpoint updates.
- **Apps Script Gmail importer**: retain only as an explicit manual/backfill path; align to `Unverified`, registered source aliases and shared dedupe rules before use.
- **Apps Script quantitative collectors**: FRED/EIA/CFTC write raw observations + `FTMacroRunLog` only. No Evidence/scoring/portfolio writes.
- **GitHub SEC collector**: sole SEC writer. Do not enable the Apps Script SEC writer.
- **Macro snapshot/relay**: one coordinated writer only; never let two jobs clear/rewrite the same snapshot concurrently.

## Prepared scheduling

1. Apps Script quantitative collectors (when the replacement bundle is deployed):
   - FRED daily at **07:00 America/Toronto**.
   - EIA daily at **08:00 America/Toronto**.
   - CFTC Friday at **17:00 America/Toronto**.
   - `ftqdInstallTriggers()` removes duplicate SEC/signal-refresh handlers and recreates only these three collectors.
2. GitHub macro coordinator:
   - daily at **09:15 Toronto**, after the FRED/EIA collectors and before the 15:15 brief;
   - Friday at **17:20 Toronto** for post-CFTC refresh;
   - concurrency group prevents overlapping macro writers;
   - all source calls stop after **27 Nov 2026**.
3. SEC schedule remains separate. Verify one scheduled run before retiring any pilot SEC path.

The proposed GitHub schedule is stored at `staging/macro_pipeline.prepared.yml`; it is intentionally outside `.github/workflows` and cannot run.

## Logging contract

Every recurring job should leave an end-to-end record that can be read back from the sheet:

- status is exactly **Complete**, **Partial** or **Failed**;
- run timestamp;
- source/job name;
- items/series checked;
- rows written or revised;
- failure count/details without credentials;
- freshness/age when a last-good source was retained.

A scheduler/task timestamp alone is not evidence that a workbook write succeeded.

## Macro failure behaviour

`ft_macro_coordinator.py` is prepared to:

- refresh FRED, EIA and CFTC independently;
- retain the last-good rows for any source that returns no usable refresh;
- mark the overall run `Partial` when some sources refresh and others fail;
- mark it `Failed` when no source refreshes;
- record preserved sources, source age and failure details;
- perform one bounded snapshot rewrite after collection, instead of clearing failed-source data first.

## Apps Script files to deploy later

In the existing bound project, **replace** the actual `Quant_Data_Pipeline.gs` with the prepared `FT_Quant_Data_Pipeline.gs`; do not append a second bundle. Reuse existing `FRED_API_KEY` and `EIA_API_KEY` Script Properties without exposing them. Verify the Sheets REST scope/table access first.

The `ft-investment-script` staging branch also contains `PREPARED_Intake_Coordination.js`, which supplies:

- Evidence dedupe against Evidence ID, Gmail message ID, duplicate key and URL;
- preservation of exclusion markers whose Gmail ID exists only in Evidence column A;
- publisher alias normalization;
- a durable `Automation Log` with verified readback;
- a logged manual Gmail-backfill wrapper so routine Evidence ownership remains with FT Research Intake.

## Deployment order — later, not now

1. Inspect live Apps Script files and installed triggers.
2. Replace the quantitative bundle; do not append it.
3. Run FRED, EIA and CFTC manually once each and read back `FTMacroRunLog`.
4. Confirm `Complete/Partial/Failed` behaviour and no Evidence/portfolio writes.
5. Run `ftqdInstallTriggers()` and audit all project triggers, including RSS.
6. Merge/integrate the intake coordination helpers into `Code.js`; confirm the direct importer has no recurring trigger.
7. Promote the prepared GitHub macro workflow schedule and coordinator.
8. Observe one scheduled morning macro run and one scheduled SEC run end-to-end.
9. Only after successful readback, retire redundant pilot writers.

## Explicitly not done

- No live Apps Script push/deployment.
- No trigger creation/deletion.
- No default-branch workflow schedule change.
- No workbook edits.
- No email changes.
- No portfolio/trade changes.
