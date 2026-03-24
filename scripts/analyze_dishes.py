#!/usr/bin/env python3
"""Reads all dish names from AP column and prints a frequency list."""
import os, json
import gspread
from google.oauth2.service_account import Credentials

SA_JSON        = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

creds = Credentials.from_service_account_info(
    json.loads(SA_JSON),
    scopes=["https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"],
)
gc   = gspread.authorize(creds)
ws   = gc.open_by_key(SPREADSHEET_ID).worksheet("Restaurants")
rows = ws.get_all_values()

from collections import Counter
dishes = Counter()

for row in rows[3:]:
    for idx in [41, 44, 47]:  # AP, AS, AV
        val = row[idx].strip() if idx < len(row) else ""
        if val:
            dishes[val] += 1

print(f"Total unique dish names: {len(dishes)}\n")
print("All dishes (sorted by frequency):\n")
for dish, count in sorted(dishes.items(), key=lambda x: (-x[1], x[0])):
    print(f"  {count:3d}x  {dish}")
