#!/usr/bin/env python3
"""
scripts/expand_sheet.py
========================
Adds 9 new columns to the Restaurants tab (AP through AX)
with headers for the dish enrichment data, then re-runs
the failed batch_update from enrich_dishes.py.

Run this ONCE to fix the column limit error, then
re-run enrich_dishes.py normally.

Usage:
  python scripts/expand_sheet.py
"""

import os, sys, json, time, requests
import gspread
from google.oauth2.service_account import Credentials

GOOGLE_API_KEY    = os.environ.get("GOOGLE_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SA_JSON           = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID    = os.environ.get("SPREADSHEET_ID", "")

def get_sheet():
    creds = Credentials.from_service_account_info(
        json.loads(SA_JSON),
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc   = gspread.authorize(creds)
    book = gc.open_by_key(SPREADSHEET_ID)
    return book, book.worksheet("Restaurants")

def col_letter(n):
    """Convert 1-based column number to letter(s). 41=AO, 42=AP, etc."""
    result = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result

print("626 Eats — Expand Restaurants Sheet")
print()

if not SA_JSON or not SPREADSHEET_ID:
    sys.exit("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON and SPREADSHEET_ID required")

print("Connecting to Sheet...")
book, ws = get_sheet()

# Check current column count
all_vals   = ws.get_all_values()
max_cols   = max(len(row) for row in all_vals) if all_vals else 0
sheet_meta = ws.spreadsheet.fetch_sheet_metadata()

# Find this worksheet in the metadata
ws_id    = ws.id
grid_props = None
for s in sheet_meta["sheets"]:
    if s["properties"]["sheetId"] == ws_id:
        grid_props = s["properties"]["gridProperties"]
        break

current_cols = grid_props["columnCount"] if grid_props else max_cols
current_rows = grid_props["rowCount"]    if grid_props else len(all_vals)

print(f"  Current grid:    {current_rows} rows × {current_cols} columns")
print(f"  Data columns:    {max_cols}")
print(f"  Columns needed:  50 (AP=42 through AX=50)")
print()

# Step 1: Expand columns if needed
if current_cols < 50:
    cols_to_add = 50 - current_cols
    print(f"Adding {cols_to_add} columns to reach column 50 (AX)...")
    book.batch_update({
        "requests": [{
            "appendDimension": {
                "sheetId":   ws_id,
                "dimension": "COLUMNS",
                "length":    cols_to_add,
            }
        }]
    })
    time.sleep(1)
    print(f"  Done — sheet now has {current_cols + cols_to_add} columns")
else:
    print(f"  Sheet already has {current_cols} columns — no expansion needed")

# Step 2: Write headers to the new columns
# Row 1 = main header, Row 2 = sub-header, Row 3 = column letter reference
# We'll write to row 1 (the first header row at index 0)
print()
print("Writing column headers...")

new_headers = [
    "Dish 1 Name",    "Dish 1 Category",    "Dish 1 Review Summary",
    "Dish 2 Name",    "Dish 2 Category",    "Dish 2 Review Summary",
    "Dish 3 Name",    "Dish 3 Category",    "Dish 3 Review Summary",
]

# Write to row 1, columns AP(42)–AX(50)
ws.update(
    "AP1:AX1",
    [new_headers],
    value_input_option="USER_ENTERED",
)
time.sleep(0.5)

# Format the header cells to match the rest of the header row
# (bold, background color matching existing headers)
ws.format("AP1:AX1", {
    "textFormat":        {"bold": True},
    "backgroundColor":   {"red": 0.27, "green": 0.51, "blue": 0.71},
    "horizontalAlignment": "CENTER",
})
time.sleep(0.5)

print(f"  Headers written to AP1:AX1")
print()

# Step 3: Verify
all_vals_new = ws.get_all_values()
new_max = max(len(row) for row in all_vals_new) if all_vals_new else 0
print(f"  Verification: Sheet now has data in {new_max} columns")
if new_max >= 42:
    print("  Column AP and beyond are accessible")

print()
print("=" * 60)
print("Sheet expanded successfully!")
print()
print("Now re-run the dish enrichment:")
print("  python scripts\\enrich_dishes.py --overwrite")
print("=" * 60)
