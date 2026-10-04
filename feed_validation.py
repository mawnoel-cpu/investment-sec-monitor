"""Read-only destination validation shared by the active feed writers."""


def validate_destination(book, ws, specs):
    """Check exact headers and bounded native tables before any sheet mutation.

    specs maps table names to (header A1 range, headers, start column).
    Row ends may grow/shrink; the header row and column bounds may not move.
    """
    for name, (header_range, headers, _) in specs.items():
        if ws.get(header_range) != [headers]:
            raise ValueError(f"{name}: destination headers mismatch at {header_range}")
    meta = book.fetch_sheet_metadata(params={
        "fields": "sheets(properties(sheetId,title),tables(tableId,name,range))"
    })
    sheets = [s for s in meta.get("sheets", []) if s["properties"]["sheetId"] == ws.id]
    if len(sheets) != 1:
        raise ValueError("Destination sheet metadata missing or ambiguous")
    tables = {}
    for table in sheets[0].get("tables", []):
        name = table["name"]
        if name in tables:
            raise ValueError(f"Duplicate native table: {name}")
        tables[name] = table
    for name, (_, headers, column) in specs.items():
        table = tables.get(name)
        if table is None or not table.get("tableId"):
            raise ValueError(f"Missing native table: {name}")
        region = table.get("range", {})
        if (region.get("sheetId") != ws.id
                or region.get("startRowIndex", 0) != 6
                or region.get("startColumnIndex", 0) != column
                or region.get("endColumnIndex") != column + len(headers)
                or type(region.get("endRowIndex")) is not int
                or not 7 <= region["endRowIndex"] <= ws.row_count):
            raise ValueError(f"{name}: unexpected native-table bounds")
    return tables
