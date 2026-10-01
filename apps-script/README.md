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
