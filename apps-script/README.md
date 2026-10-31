# FT Game Intelligence Apps Script

## Queue sunset — direct-Gmail cutover pending (31 October 2026)

**Current operational decision: do not deploy the newer screening copy of `FT_Gmail_Queue.gs`.** Gmail research discovery, message-body reading, issuer verification, and Evidence persistence are now owned by the existing scheduled **FT Research Intake** task using its connected Gmail account, independently of Apps Script. The existing **FT Daily Trade Decision** task independently checks urgent Gmail messages after the last finalized intake receipt. Both task instructions were amended on 31 October; the source file in this repository is retained for history and rollback, not as a requested deployment.

**Temporary status:** one spreadsheet-bound time-driven `ftGmailQueueCollect` trigger, every 30 minutes, was owner-verified running and continues for now. Its queue is metadata-only; the resulting `FT GMAIL QUEUE` historical PENDING counts are not proof of unscreened priority research and must not gate a daily trade decision. Do not add another trigger or change `Code.gs`, `SecCollector.gs`, `onOpen` or historical evidence.

**Safe retirement gate (do not backdate):** On an actual weekday (first check Monday 2 Nov), both of the following must be independently read back from the existing FT workbook: (1) a genuinely scheduled FT Research Intake receipt with a successful direct connected Gmail search, dated cutoff and >=48h overlap, relevant body reads/dedup/primary-source checks and durable Evidence/FTIntakeRunLog writes; (2) that day's genuinely scheduled Daily Trade Decision Reviews M late_news search/readback and complete 5-stage O:X checkpoints with numeric N handoff by 14:45 Toronto. A manual chat test or offline Node test does **not** satisfy the scheduled gate. The existing read-only **FT Pipeline Health Alert** checks both and should notify the owner once when safe.

**Owner action after BOTH gates PASS:** FT Game Intelligence → Extensions → Apps Script → Triggers → on the single `ftGmailQueueCollect` time-based trigger choose three-dot menu → **Delete trigger**. Keep script files and `onOpen` intact for rollback. There is no authorized connected Apps Script trigger-administration action in the current ChatGPT toolset; an update to repository files does not change the bound deployment. Once disabled, recheck the next scheduled research and decision receipts to confirm no dependency on queue progress. No new collector, task or workbook should be created.


This directory is reserved for the container-bound Google Apps Script project attached to the **FT Game Intelligence** spreadsheet.

## Project

- Script ID: `162pFkFVQ_b7GFXORmzIY2Y7Zd9ZNOgUPAxyGVfem63fqP-sdRSTGh5fX`
- Spreadsheet: `FT Game Intelligence`

## First sync from Google Apps Script

1. Enable the Apps Script API at https://script.google.com/home/usersettings.
2. Install clasp locally: `npm install -g @google/clasp`.
3. Run `clasp login` and authorize the Google account that owns the spreadsheet/script.
4. In this directory, copy `.clasp.example.json` to `.clasp.json`.
5. Run `clasp pull`.
6. Review the downloaded files, especially `appsscript.json`, before committing them to GitHub.

`.clasp.json` and OAuth credential files are intentionally git-ignored. The Apps Script source files and `appsscript.json` manifest can be committed normally.


## Metadata queue — installed; scheduled collection verified

The module `FT_Gmail_Queue.gs` is installed as a separate file in the spreadsheet-bound project. Its original files were backed up privately and verified unchanged. This directory is not a complete mirror of that project: do **not** replace it or run `clasp push` blindly.

It searches Gmail by fixed time windows, in pages of 25 threads (at most 75 per run), keeps a resumable offset in Script Properties, and deduplicates by Gmail message ID. It creates a separate `FT GMAIL QUEUE` tab holding message metadata only. Subjects and sender domains provide tentative P1 (held or queued), P2 (focus candidates), or P3 (other investing) priority. This is *not* original-source research or a stock signal. It does not change Gmail messages, Evidence, Portfolio, Trade Log, source weights or TRADE DESK.

Offline tests: `node --test tests/test_ft_gmail_queue.cjs`, also run by GitHub's **Validate FT Gmail Queue Offline** workflow.

### Activate safely

1. Open the workbook's **Extensions → Apps Script** to review the existing bound source and installed triggers.
2. Back up the source using `clasp pull` with the existing project configuration, or copy the files from the Apps Script editor.
3. Check for name collisions, then add `FT_Gmail_Queue.gs` as one separate file without overwriting any existing code. Review Gmail and spreadsheet access during authorization.
4. Run `ftGmailQueueCollect` manually, confirm the queue is metadata-only, and repeat to check duplicate protection. `ftGmailQueueStatus` reports pending counts and cursor progress.
5. Only after review, manually run `ftGmailQueueInstallTrigger` for a 30-minute schedule. `ftGmailQueueRemoveTrigger` removes it.

Until activation and readback testing, the existing scheduled **FT Research Intake** remains responsible for Gmail discovery. After activation, integrate its P1/P2 discovery with the queue while retaining broader searches as a catch-all. The queue does not mark items screened or verified on its own. Keep sensitive authorization files outside GitHub.

### Live integration checkpoint — 14 October 2026

The bound project was opened through Extensions → Apps Script. Its original `Code.gs`, `SecCollector.gs`, `appsscript.json`, script properties and zero owner-visible triggers were backed up privately. The three original source/manifest files were read back after reload and matched the backup exactly. `FT_Gmail_Queue.gs` was added separately and also matched the tested source after reload.

The module now stores formula-like email subjects as literal text and logs collection/status results for verification. All seven offline tests pass. The first manual run stopped at Google's one-time authorization prompt and was cancelled before collection. At that checkpoint, live collection was not verified and the 30-minute trigger was not installed. The manual and scheduled verification below supersedes that checkpoint. The existing Gmail/SEC writer shims remain disabled as before; no trades or Evidence changes were made by this integration.

### Live verification — 15 October 2026

- User-run collection at 06:27 Toronto created 41 pending queue rows, independently read back from the workbook.
- The next user-run collection started at 06:31:10 Toronto and completed in 38.012 seconds. The supplied Apps Script execution record shows `COMPLETE DISCOVERY`, `queued: 51`, `examined: 76`, `duplicates: 0`, and `lastSearchedBeforeUtc: 2026-10-15T10:27:29.000Z`.
- Independent workbook readback after that execution confirms 92 unique message IDs, zero duplicate IDs and all 92 statuses `PENDING`. These are discovery metadata, not verified investment research.
- Initial scan and resumable writes are verified. Duplicate protection passed the offline replay test; the scheduled run below also demonstrates live duplicate skips without duplicate rows.
- At 06:50 Toronto, the user's Apps Script **Triggers** screenshot showed one owner-visible trigger: handler `ftGmailQueueCollect`, deployment `Head`, event `Time-based`. It had no recorded scheduled run at that point. Do not add a duplicate trigger.
- After Google sign-in was restored, direct inspection of the bound project's trigger settings confirmed exactly one `ftGmailQueueCollect` trigger: `Head`, `Time-driven`, `Minutes timer`, **Every 30 minutes**. The settings were closed without changes.

### First automatic run verified — 15 October 2026

- Apps Script execution history shows `ftGmailQueueCollect`, type `Time-driven`, status **Completed**, duration **7.37 seconds**, starting at approximately **07:13:39 America/Toronto**. The browser displayed 04:13:39; the run's UTC window and workbook timestamps correspond to 07:13 Toronto.
- The execution log reports `PARTIAL DISCOVERY`, `queued: 2`, `examined: 81`, `duplicates: 35`, `nextOffset: 75`, and `lastSearchedBeforeUtc: 2026-10-15T11:13:40.000Z`.
- Independent workbook readback confirms **94 unique message IDs**, **zero duplicate IDs**, and all 94 statuses `PENDING`. Two rows were added after the manual scan, with the latest write at `2026-10-15T11:13:42.664Z`.
- `PARTIAL DISCOVERY` here is the normal per-run page limit: the successful run saved its cursor and the next scheduled run can resume at offset 75. It is not evidence that the entire new search window has finished or that any queued research has been verified.
- The original private backup passes archive integrity checks. The tested queue source still matches the repository blob exactly. No original project source, manifest, unrelated trigger, Gmail message, Evidence row, portfolio order or trade was changed during this verification.
- Queue installation, manual collection, the 30-minute trigger and a successful automatic workbook write are now verified.


## Seeking Alpha headline/preview evidence bridge — 22 October 2026

The pre-existing `ftGmailQueueCollect` trigger still runs every 30 minutes.
The current GitHub version of `FT_Gmail_Queue.gs` additionally calls
`ftGmailQueueScreen_` after discovery. This is an **extension of the same
queue**, not a second research collector or additional scheduled trigger.
It reads up to eight queued Seeking Alpha messages per scheduled run,
extracts newsletter-visible teasers, and writes one idempotent entry per Gmail
message into the existing `Evidence` tab. No email is modified. It does not
touch Portfolio, Trade Log, execution or trade targets.

The bridge uses the original `FT GMAIL QUEUE!A:J` metadata/status schema
and the existing `Evidence!A:T` research schema. It checks previously
ingested Gmail IDs in Evidence!O to prevent duplicates. Every preview is
recorded with `J=Unverified`, `G=0` (no scored direction), source-specific
publisher attribution, `Q=Excerpt` or `Headline only`, a factual access
limitation and a follow-up issuer/price-check question. Existing `K` and
`L` scoring formulas consequently evaluate to **zero**. No premium article
is represented as read; previews cannot independently justify a trade.

The screening step reads the complete email **digest**, not the original
linked/premium article. A mixed-topic newsletter can include useful company
teasers even if its subject sounds unrelated. Explicit onboarding/marketing
emails are excluded. Screening respects the game end date and retains
`PENDING` when Evidence readback fails. A screening error is visible
separately and does not undo the successful discovery receipt.

**Live deployment is not automatic.** This repository contains a source
copy of one Apps Script module, not an authenticated `clasp` deployment to
the spreadsheet-bound script. An owner/editor must:

1. Open FT Game Intelligence > Extensions > Apps Script, back up its current
   `FT_Gmail_Queue.gs`, and compare it with the
   [GitHub source](https://github.com/mawnoel-cpu/investment-sec-monitor/blob/main/apps-script/FT_Gmail_Queue.gs).
   Replace **only** the separate `FT_Gmail_Queue.gs` module, not `Code.gs`,
   `SecCollector.gs`, other manifest entries, or script properties.
2. Save, run `ftGmailQueueScreen` once with existing Gmail/Sheets
   authorizations, and inspect its log. The 22 October manual backfill was
   entered directly into Evidence!308:319 with exact Gmail IDs; the same
   emails are now SCREENED. Therefore this first run can legitimately save
   zero new records. It must not duplicate those rows.
3. Confirm that exactly one existing `ftGmailQueueCollect` 30-minute trigger
   still exists; **do not** run `ftGmailQueueInstallTrigger` a second time.
   Observe a subsequent scheduled collector execution and check that
   a new Seeking Alpha digest goes from PENDING to SCREENED, with an Evidence
   Gmail ID, `J=Unverified`, and calculated `K=L=0`.
4. Check normal daily FT research review independently; a saved zero-weight
   lead is an early warning, **not** proof the original claim has been
   verified, nor a ready-to-trade recommendation.

The manual backfill saved 12 Seeking Alpha leads to
`Evidence!A308:T319` and read back `K=L=0`; the separate manual
`TRADE DESK!B13` note records the unresolved live deployment.
Offline test validation: `node --test tests/test_ft_gmail_queue.cjs`.
