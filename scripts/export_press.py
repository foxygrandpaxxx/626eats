#!/usr/bin/env python3
"""
scripts/export_press.py
-----------------------
Reads the "Press" sheet from Google Sheets and exports data/press.json.
This file is fetched by the app alongside restaurants.json to display
press coverage badges and "In the Press" sections.

Output format:
{
  "version": "...",
  "byRestaurant": {
    "R0001": [
      {
        "publication": "Eater LA",
        "headline": "...",
        "url": "...",
        "excerpt": "...",
        "dishMentioned": "...",
        "date": "2024-03-15"
      }
    ]
  }
}

Local usage:
    GOOGLE_SERVICE_ACCOUNT_JSON='...' SPREADSHEET_ID=... python scripts/export_press.py
"""

import os, json
from datetime import datetime
from pathlib import Path

SA_JSON        = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
OUTPUT_PATH    = Path("data/press.json")

# Press sheet columns (1-based)
PRESS_COLS = {
    "press_id":      1,
    "rest_id":       2,
    "rest_name":     3,
    "publication":   4,
    "headline":      5,
    "url":           6,
    "dish_mentioned":7,
    "excerpt":       8,
    "date":          9,
    "date_swept":    10,
    "verified":      11,
}


def get_sheets_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        raise ImportError("Run: pip install gspread google-auth")
    if not SA_JSON:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    creds_dict = json.loads(SA_JSON)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.Client(auth=creds)


def get_cell(row, col_1based, default=""):
    idx = col_1based - 1
    return row[idx] if idx < len(row) else default


def export():
    print(f"Exporting press data from Google Sheets: {SPREADSHEET_ID}")

    client = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    try:
        press_ws = spreadsheet.worksheet("Press")
    except Exception:
        print("No 'Press' sheet found. Run press_sweep.py first.")
        # Write empty output
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump({"version": datetime.utcnow().isoformat()+"Z", "byRestaurant": {}}, f)
        return

    rows = press_ws.get_all_values()
    by_restaurant = {}  # rest_id -> [press entries]

    for row in rows[1:]:  # skip header
        press_id = get_cell(row, PRESS_COLS["press_id"])
        if not press_id:
            continue

        verified = get_cell(row, PRESS_COLS["verified"]).upper()
        if verified not in ("TRUE", "1", "YES"):
            continue  # skip unverified entries

        rest_id = get_cell(row, PRESS_COLS["rest_id"])
        if not rest_id:
            continue

        entry = {
            "publication":  get_cell(row, PRESS_COLS["publication"]),
            "headline":     get_cell(row, PRESS_COLS["headline"]),
            "url":          get_cell(row, PRESS_COLS["url"]),
            "excerpt":      get_cell(row, PRESS_COLS["excerpt"]),
            "dishMentioned": get_cell(row, PRESS_COLS["dish_mentioned"]) or None,
            "date":         get_cell(row, PRESS_COLS["date"]) or None,
        }

        if not entry["url"]:
            continue

        if rest_id not in by_restaurant:
            by_restaurant[rest_id] = []
        by_restaurant[rest_id].append(entry)

    # Sort each restaurant's press by date (newest first)
    for rid in by_restaurant:
        by_restaurant[rid].sort(key=lambda x: x.get("date") or "", reverse=True)

    output = {
        "version":      datetime.utcnow().isoformat() + "Z",
        "exportedAt":   datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "totalEntries": sum(len(v) for v in by_restaurant.values()),
        "byRestaurant": by_restaurant,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total = output["totalEntries"]
    print(f"Exported {total} press entries for {len(by_restaurant)} restaurants → {OUTPUT_PATH}")


if __name__ == "__main__":
    export()
