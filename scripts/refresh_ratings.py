#!/usr/bin/env python3
"""
scripts/refresh_ratings.py
--------------------------
Fetches Google Places ratings + review counts for all restaurants
and writes them back to the Google Sheet.

Adds/updates two columns (add headers manually to the sheet first):
  Column BB (54): google_rating       – e.g. 4.3
  Column BC (55): google_review_count – e.g. 847

Required secrets:
  GOOGLE_SERVICE_ACCOUNT_JSON  – service account JSON (for Sheets access)
  SPREADSHEET_ID               – Google Sheets spreadsheet ID
  GOOGLE_MAPS_API_KEY          – Places API key
                                 Cost: $17/1000 requests, but Google gives $200/mo free credit
                                 → effectively free for 273 restaurants (~$4.64/run)

Local usage:
  GOOGLE_SERVICE_ACCOUNT_JSON='...' SPREADSHEET_ID=... GOOGLE_MAPS_API_KEY=... python scripts/refresh_ratings.py

GitHub Actions:
  Triggered manually (workflow_dispatch). Re-run quarterly to keep ratings fresh.
"""

import os, json, time
import urllib.request
import urllib.parse

SA_JSON        = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
GMAPS_KEY      = os.environ.get("GOOGLE_MAPS_API_KEY", "")

# Column positions in Restaurants sheet (1-based) — existing columns
COL_GOOGLE_ID = 34  # AH
COL_NAME      = 2   # B
COL_ID        = 1   # A

# New rating columns — add these headers to your sheet before running
COL_GOOGLE_RATING  = int(os.environ.get("COL_GOOGLE_RATING",  "54"))  # BB
COL_GOOGLE_COUNT   = int(os.environ.get("COL_GOOGLE_COUNT",   "55"))  # BC

DATA_START = 3  # 0-indexed row in get_all_values() output (row 4 in spreadsheet)


def col_letter(n):
    """Convert 1-based column number to spreadsheet letter(s). e.g. 30 -> 'AD'"""
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
    if not SA_JSON:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    creds_dict = json.loads(SA_JSON)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.Client(auth=creds)


def fetch_google_rating(place_id):
    """Fetch rating and review count from Google Places Details API."""
    if not GMAPS_KEY or not place_id:
        return None, None
    url = (
        "https://maps.googleapis.com/maps/api/place/details/json"
        "?fields=rating%2Cuser_ratings_total"
        f"&place_id={urllib.parse.quote(place_id)}"
        f"&key={GMAPS_KEY}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        if data.get("status") == "OK":
            result = data.get("result", {})
            return result.get("rating"), result.get("user_ratings_total")
    except Exception as e:
        print(f"    Google Places error for {place_id}: {e}")
    return None, None


def run_refresh():
    if not GMAPS_KEY:
        print("GOOGLE_MAPS_API_KEY not set.")
        return

    print("Connecting to Google Sheets...")
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet("Restaurants")
    rows = ws.get_all_values()

    updates = []  # list of (row_num_1based, col_1based, value)
    google_ok = 0

    for i, row in enumerate(rows[DATA_START:], start=DATA_START + 1):
        # 1-based spreadsheet row
        spreadsheet_row = i + 1

        def get_cell(col):
            idx = col - 1
            return row[idx] if idx < len(row) else ''

        rest_id   = get_cell(COL_ID)
        name      = get_cell(COL_NAME)
        google_id = get_cell(COL_GOOGLE_ID)

        if not rest_id:
            continue

        if not google_id:
            continue

        print(f"  [{i - DATA_START + 1}] {name}")

        g_rating, g_count = fetch_google_rating(google_id)
        if g_rating is not None:
            updates.append((spreadsheet_row, COL_GOOGLE_RATING, str(g_rating)))
            updates.append((spreadsheet_row, COL_GOOGLE_COUNT, str(g_count or '')))
            print(f"      Google: {g_rating} ({g_count} reviews)")
            google_ok += 1
        time.sleep(0.05)  # be polite to the API

    # Batch write all updates
    if updates:
        print(f"\nWriting {len(updates)} cells to sheet...")
        batch = [{"range": f"{col_letter(col)}{row}", "values": [[val]]}
                 for row, col, val in updates]
        # Write in chunks of 100 to avoid request size limits
        for chunk_start in range(0, len(batch), 100):
            ws.batch_update(batch[chunk_start:chunk_start+100])
            time.sleep(0.5)

    print(f"\nDone. Google: {google_ok} updated")


if __name__ == "__main__":
    run_refresh()
