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


## Chunk 4: staged metadata queue — NOT DEPLOYED

The separate module `FT_Gmail_Queue.gs` now lives in this repository, but the current spreadsheet-bound Apps Script source has not been pulled into GitHub. Do **not** replace the existing project or run `clasp push` blindly. The module is opt-in and does not run until added to the bound project.

It searches Gmail by fixed time windows, in pages of 25 threads (at most 75 per run), keeps a resumable offset in Script Properties, and deduplicates by Gmail message ID. It creates a separate `FT GMAIL QUEUE` tab holding message metadata only. Subjects and sender domains provide tentative P1 (held or queued), P2 (focus candidates), or P3 (other investing) priority. This is *not* original-source research or a stock signal. It does not change Gmail messages, Evidence, Portfolio, Trade Log, source weights or TRADE DESK.

Offline tests: `node --test tests/test_ft_gmail_queue.cjs`, also run by GitHub's **Validate FT Gmail Queue Offline** workflow.

### Activate safely

1. Open the workbook's **Extensions → Apps Script** to review the existing bound source and installed triggers.
2. Back up the source using `clasp pull` with the existing project configuration, or copy the files from the Apps Script editor.
3. Check for name collisions, then add `FT_Gmail_Queue.gs` as one separate file without overwriting any existing code. Review Gmail and spreadsheet access during authorization.
4. Run `ftGmailQueueCollect` manually, confirm the queue is metadata-only, and repeat to check duplicate protection. `ftGmailQueueStatus` reports pending counts and cursor progress.
5. Only after review, manually run `ftGmailQueueInstallTrigger` for a 30-minute schedule. `ftGmailQueueRemoveTrigger` removes it.

Until activation and readback testing, the existing scheduled **FT Research Intake** remains responsible for Gmail discovery. After activation, integrate its P1/P2 discovery with the queue while retaining broader searches as a catch-all. The queue does not mark items screened or verified on its own. Keep sensitive authorization files outside GitHub.
