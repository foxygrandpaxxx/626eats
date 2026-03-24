#!/usr/bin/env python3
"""
scripts/sync_dishes.py
=======================
Copies dish names from the new AP/AS/AV columns back to the
old AA/AB/AC columns so export_json.py picks them up correctly.

Run this once after enrich_dishes.py has written to AP-AX.

Usage:
  python scripts/sync_dishes.py
  python scripts/sync_dishes.py --dry-run
"""

import os, sys, json, time, argparse
import gspread
from google.oauth2.service_account import Credentials

SA_JSON        = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

def get_sheet():
    creds = Credentials.from_service_account_info(
        json.loads(SA_JSON),
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"],
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID).worksheet("Restaurants")

def sg(row, idx):
    return row[idx].strip() if idx < len(row) else ""

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

print("626 Eats — Sync dish names from AP-AX → AA-AC")
print("Dry run:", args.dry_run)
print()

ws   = get_sheet()
rows = ws.get_all_values()
DATA_START = 3

updates  = []
skipped  = 0
synced   = 0
no_data  = 0

for i, row in enumerate(rows[DATA_START:], start=DATA_START):
    name = sg(row, 1)
    if not name:
        continue

    # New columns (0-based): AP=41, AS=44, AV=47
    dish1_new = sg(row, 41)  # AP — new dish1 name
    dish2_new = sg(row, 44)  # AS — new dish2 name
    dish3_new = sg(row, 47)  # AV — new dish3 name

    # Old columns (0-based): AA=26, AB=27, AC=28
    dish1_old = sg(row, 26)
    dish2_old = sg(row, 27)
    dish3_old = sg(row, 28)

    if not dish1_new:
        no_data += 1
        continue

    # Skip if old already matches new (already synced)
    if dish1_old == dish1_new and dish2_old == dish2_new and dish3_old == dish3_new:
        skipped += 1
        continue

    sheet_row = i + 1  # 1-based
    updates.append({
        "range":  f"AA{sheet_row}:AC{sheet_row}",
        "values": [[dish1_new, dish2_new, dish3_new]],
    })
    synced += 1

    if args.dry_run and synced <= 10:
        old = " | ".join(filter(None, [dish1_old, dish2_old, dish3_old])) or "(empty)"
        new = " | ".join(filter(None, [dish1_new, dish2_new, dish3_new]))
        print(f"  {name[:35]}")
        print(f"    Old AA-AC: {old}")
        print(f"    New AA-AC: {new}")

print(f"\n  Rows to sync: {synced}")
print(f"  Already synced: {skipped}")
print(f"  No new data: {no_data}")

if args.dry_run:
    if synced > 10:
        print(f"  ... and {synced - 10} more")
    print(f"\nRun without --dry-run to write {synced} rows.")
    sys.exit(0)

if not updates:
    print("Nothing to sync.")
    sys.exit(0)

print(f"\nWriting {len(updates)} rows...")
BATCH = 200
for start in range(0, len(updates), BATCH):
    chunk = updates[start:start + BATCH]
    ws.batch_update(chunk)
    print(f"  Wrote batch {start // BATCH + 1} ({len(chunk)} rows)")
    time.sleep(0.8)

print(f"\nDone — {synced} rows synced from AP-AX to AA-AC")
print()
print("Now run:")
print("  python scripts\\export_json.py")
print("  git add data\\restaurants.json")
print("  git pull origin main")
print("  git commit -m \"Add dish data\"")
print("  git push origin main")
