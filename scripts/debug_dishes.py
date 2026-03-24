#!/usr/bin/env python3
"""Quick check of what export_json.py actually puts in restaurants.json"""
import os, sys, json
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

def sg(row, idx):
    return row[idx].strip() if idx < len(row) else ""

DATA_START = 3

print("Checking first 5 restaurants with AP data:\n")
found = 0
for row in rows[DATA_START:]:
    name     = sg(row, 1)
    aa       = sg(row, 26)   # AA old dish1
    ap_name  = sg(row, 41)   # AP new dish1 name
    ap_cat   = sg(row, 42)   # AQ new dish1 category
    ap_summ  = sg(row, 43)   # AR new dish1 summary
    as_name  = sg(row, 44)   # AS new dish2 name

    if not ap_name:
        continue

    found += 1
    print(f"Restaurant: {name}")
    print(f"  AA (old): '{aa}'")
    print(f"  AP name:  '{ap_name}'")
    print(f"  AQ cat:   '{ap_cat}'")
    print(f"  AR summ:  '{ap_summ[:80]}...' " if len(ap_summ)>80 else f"  AR summ:  '{ap_summ}'")
    print(f"  AS name:  '{as_name}'")
    print()

    if found >= 5:
        break

print(f"Total rows with AP data: {sum(1 for r in rows[DATA_START:] if len(r)>41 and r[41].strip())}")

# Now simulate what export_json.py does
print("\nSimulating export_json.py output for first restaurant with AP data:")
for row in rows[DATA_START:]:
    ap_name = sg(row, 41)
    if not ap_name:
        continue
    # This is what export_json.py builds
    review_dishes = []
    for slot in [(41,42,43), (44,45,46), (47,48,49)]:
        n = sg(row, slot[0])
        c = sg(row, slot[1])
        s = sg(row, slot[2])
        if n:
            review_dishes.append({"name": n, "category": c, "summary": s})

    print(f"reviewDishes array: {json.dumps(review_dishes, indent=2)}")
    break
