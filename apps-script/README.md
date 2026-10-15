# FT Game Intelligence Apps Script

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


## Metadata queue — installed; manual scan verified; time-based trigger present

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

The module now stores formula-like email subjects as literal text and logs collection/status results for verification. All seven offline tests pass. The first manual run stopped at Google's one-time authorization prompt and was cancelled before collection. **Live collection is not verified and the 30-minute trigger is not installed.** Complete Google authorization, run and read back the queue, repeat for duplicate protection, and only then install its trigger. The existing Gmail/SEC writer shims remain disabled as before; no trades or Evidence changes were made by this integration.

### Live verification — 15 October 2026

- User-run collection at 06:27 Toronto created 41 pending queue rows, independently read back from the workbook.
- The next user-run collection started at 06:31:10 Toronto and completed in 38.012 seconds. The supplied Apps Script execution record shows `COMPLETE DISCOVERY`, `queued: 51`, `examined: 76`, `duplicates: 0`, and `lastSearchedBeforeUtc: 2026-10-15T10:27:29.000Z`.
- Independent workbook readback after that execution confirms 92 unique message IDs, zero duplicate IDs and all 92 statuses `PENDING`. These are discovery metadata, not verified investment research.
- Initial scan and resumable writes are verified. Duplicate protection also passed the offline replay test; a full live replay of a completed sweep has not yet been observed.
- At 06:50 Toronto, the user's Apps Script **Triggers** screenshot shows one owner-visible trigger: handler `ftGmailQueueCollect`, deployment `Head`, event `Time-based`. `Last run` and error rate are still dashes, so a scheduled execution has not yet been observed. Do not add a duplicate trigger.
- The installed module's `ftGmailQueueInstallTrigger` creates a 30-minute schedule. The trigger-list screenshot confirms its handler and event type but does not show the configured interval. Browser controls remain unavailable, so the assistant has not independently opened the trigger settings.
