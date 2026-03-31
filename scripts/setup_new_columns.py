#!/usr/bin/env python3
"""
scripts/setup_new_columns.py
----------------------------
One-time setup: expands the Restaurants sheet and adds column headers
for the new data pipeline columns.

New columns added (1-based column numbers):
  56 BD  generative_summary  — Gemini AI overview from Places API (New)
  57 BE  review_summary      — AI synthesis of user reviews from Places API (New)
  58 BF  editorial_summary   — Short editorial description from Places API
  59 BG  reviews_json        — JSON array of up to 5 Google review texts
  60 BH  top_dishes_json     — JSON array of extracted dish objects (name/chinese/pinyin/etc.)
  61 BI  cuisine_primary     — Primary cuisine taxonomy ID (e.g. "sichuan")
  62 BJ  cuisine_secondary   — Secondary cuisine taxonomy ID or empty

Run once locally before running the data pipeline:
  python scripts/setup_new_columns.py

Credentials supplied via env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON  — JSON string of service account
  SPREADSHEET_ID               — Google Sheet ID

Or pass a path and ID directly:
  SA_PATH=... SPREADSHEET_ID=... python scripts/setup_new_columns.py
"""

import os, json, sys

SA_JSON        = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SA_PATH        = os.environ.get("SA_PATH", "")  # path to SA JSON file (alternative)
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

# Columns to add: (1-based column number, header string)
NEW_COLUMNS = [
    (56, "generative_summary"),
    (57, "review_summary"),
    (58, "editorial_summary"),
    (59, "reviews_json"),
    (60, "top_dishes_json"),
    (61, "cuisine_primary"),
    (62, "cuisine_secondary"),
]

HEADER_ROW = 3   # gspread 1-based row where column names live
TARGET_COLS = 70  # expand sheet to this many columns (with buffer)


def col_letter(n):
    """Convert 1-based column number to spreadsheet letter(s). e.g. 56 → 'BD'"""
    result = ''
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def get_sheets_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        raise ImportError("Run: pip install gspread google-auth")

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    if SA_PATH and os.path.exists(SA_PATH):
        creds = Credentials.from_service_account_file(SA_PATH, scopes=scopes)
    elif SA_JSON:
        creds = Credentials.from_service_account_info(json.loads(SA_JSON), scopes=scopes)
    else:
        raise ValueError("Set GOOGLE_SERVICE_ACCOUNT_JSON or SA_PATH")

    return gspread.Client(auth=creds)


def run():
    if not SPREADSHEET_ID:
        raise ValueError("SPREADSHEET_ID not set")

    print(f"Connecting to Google Sheets ({SPREADSHEET_ID})...")
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet("Restaurants")

    current_cols = ws.col_count
    print(f"Current sheet size: {current_cols} columns")

    # Step 1: Expand columns if needed
    if current_cols < TARGET_COLS:
        print(f"Expanding sheet to {TARGET_COLS} columns...")
        body = {
            "requests": [{
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": ws.id,
                        "gridProperties": {"columnCount": TARGET_COLS}
                    },
                    "fields": "gridProperties.columnCount"
                }
            }]
        }
        spreadsheet.batch_update(body)
        print(f"  Expanded to {TARGET_COLS} columns ✓")
    else:
        print(f"  Sheet already has {current_cols} columns, no expansion needed.")

    # Step 2: Check what's already in the header row for these columns
    print(f"\nChecking existing headers in row {HEADER_ROW}...")
    to_write = []
    for col_num, header in NEW_COLUMNS:
        current = ws.cell(HEADER_ROW, col_num).value
        col_ref = col_letter(col_num)
        if current == header:
            print(f"  {col_ref}{HEADER_ROW} ({col_num}): '{header}' already set ✓")
        elif current:
            print(f"  {col_ref}{HEADER_ROW} ({col_num}): currently '{current}' → will overwrite with '{header}'")
            to_write.append((col_num, header))
        else:
            print(f"  {col_ref}{HEADER_ROW} ({col_num}): empty → will write '{header}'")
            to_write.append((col_num, header))

    if not to_write:
        print("\nAll headers already in place. Nothing to do.")
        return

    # Step 3: Write missing headers
    print(f"\nWriting {len(to_write)} header(s)...")
    for col_num, header in to_write:
        ws.update_cell(HEADER_ROW, col_num, header)
        print(f"  Wrote '{header}' → {col_letter(col_num)}{HEADER_ROW}")

    # Step 4: Verify
    print("\nVerifying...")
    all_ok = True
    for col_num, header in NEW_COLUMNS:
        actual = ws.cell(HEADER_ROW, col_num).value
        ok = actual == header
        status = "✓" if ok else "✗ MISMATCH"
        print(f"  {col_letter(col_num)}{HEADER_ROW}: expected '{header}', got '{actual}' {status}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\n✓ All columns set up correctly.")
        print("\nNew columns summary:")
        for col_num, header in NEW_COLUMNS:
            print(f"  {col_letter(col_num)} ({col_num:2d}): {header}")
    else:
        print("\n✗ Some columns did not write correctly. Check errors above.")
        sys.exit(1)


if __name__ == "__main__":
    run()
