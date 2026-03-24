#!/usr/bin/env python3
"""
scripts/refresh_photos.py
--------------------------
Reads the Restaurants sheet from Google Sheets, finds rows where
photo URLs are older than MAX_AGE_DAYS, fetches fresh URLs via the
Google Places API using the stored Place ID, and writes them back.

Runs automatically via GitHub Actions every Sunday.
Can also be run locally:
    GOOGLE_API_KEY=... GOOGLE_SERVICE_ACCOUNT_JSON='...' SPREADSHEET_ID=... python scripts/refresh_photos.py
"""

import os, json, time, requests
from datetime import datetime, timedelta, date

# ── Config from environment ───────────────────────────────────────────────────
GOOGLE_API_KEY  = os.environ.get("GOOGLE_API_KEY", "")
SA_JSON         = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID  = os.environ.get("SPREADSHEET_ID", "")
MAX_AGE_DAYS    = int(os.environ.get("MAX_AGE_DAYS", "90"))
DRY_RUN         = os.environ.get("DRY_RUN", "false").lower() == "true"

# Column indices (1-based, matching the spreadsheet)
COL_ID          = 1   # A
COL_NAME        = 2   # B
COL_STATUS      = 4   # D
COL_PHOTO_EXT   = 30  # AD
COL_PHOTO_FOOD1 = 31  # AE
COL_PHOTO_FOOD2 = 32  # AF
COL_PHOTO_INT   = 33  # AG
COL_PLACE_ID    = 34  # AH
COL_DATE_VERIFIED = 40  # AN

PHOTO_COLS = [COL_PHOTO_EXT, COL_PHOTO_FOOD1, COL_PHOTO_FOOD2, COL_PHOTO_INT]
PHOTO_LABELS = ["exterior", "food_1", "food_2", "interior"]

def get_sheets_client():
    """Authenticate with Google Sheets API via service account."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        raise ImportError("Run: pip install gspread google-auth")

    if not SA_JSON:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON env var not set")

    creds_dict = json.loads(SA_JSON)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client

def fetch_place_photos(place_id, max_photos=4):
    """Fetch photo references for a Place ID and return signed photo URLs."""
    if not place_id or not GOOGLE_API_KEY:
        return []

    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "photos",
        "key": GOOGLE_API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        photos = data.get("result", {}).get("photos", [])
        urls = []
        for photo in photos[:max_photos]:
            ref = photo.get("photo_reference")
            if ref:
                photo_url = (
                    f"https://maps.googleapis.com/maps/api/place/photo"
                    f"?maxwidth=1200&photo_reference={ref}&key={GOOGLE_API_KEY}"
                )
                urls.append(photo_url)
        return urls
    except Exception as e:
        print(f"    Places API error for {place_id}: {e}")
        return []

def is_stale(date_str, max_age_days):
    """Check if a date string is older than max_age_days."""
    if not date_str:
        return True  # No date = assume stale
    try:
        verified = datetime.strptime(str(date_str).strip(), "%Y-%m-%d")
        return datetime.today() - verified > timedelta(days=max_age_days)
    except ValueError:
        return True  # Unparseable = stale

def run_refresh():
    print(f"Photo Refresh — max_age={MAX_AGE_DAYS}d, dry_run={DRY_RUN}")
    print(f"Spreadsheet: {SPREADSHEET_ID}")

    if not SPREADSHEET_ID:
        print("ERROR: SPREADSHEET_ID not set")
        return

    # Connect to Google Sheets
    print("Connecting to Google Sheets...")
    client = get_sheets_client()
    sheet = client.open_by_key(SPREADSHEET_ID)
    ws = sheet.worksheet("Restaurants")

    all_rows = ws.get_all_values()
    header_row = 2  # 0-indexed row 2 = spreadsheet row 3 (headers)
    data_start = 3  # 0-indexed row 3 = spreadsheet row 4 (first data)

    today_str = date.today().isoformat()
    refreshed = 0
    skipped_fresh = 0
    skipped_no_id = 0
    errors = 0

    # Collect batch updates (more efficient than cell-by-cell)
    updates = []

    for row_idx in range(data_start, len(all_rows)):
        row = all_rows[row_idx]

        # Pad row if short
        while len(row) < max(PHOTO_COLS + [COL_PLACE_ID, COL_DATE_VERIFIED]):
            row.append("")

        rest_id   = row[COL_ID - 1]
        name      = row[COL_NAME - 1]
        status    = row[COL_STATUS - 1]
        place_id  = row[COL_PLACE_ID - 1].strip()
        date_ver  = row[COL_DATE_VERIFIED - 1].strip()

        if not rest_id:
            continue  # Empty row

        if status == "CLOSED":
            continue  # Skip permanently closed

        if not place_id:
            skipped_no_id += 1
            continue  # No Place ID — can't refresh

        if not is_stale(date_ver, MAX_AGE_DAYS):
            skipped_fresh += 1
            continue  # Still fresh

        print(f"  Refreshing: {name} ({place_id})...")

        if DRY_RUN:
            print(f"    [DRY RUN] Would refresh {name}")
            refreshed += 1
            continue

        photo_urls = fetch_place_photos(place_id, max_photos=4)

        if not photo_urls:
            print(f"    No photos found for {name}")
            errors += 1
            time.sleep(0.1)
            continue

        # Prepare updates for each photo column
        spreadsheet_row = row_idx + 1  # Convert to 1-based

        for col_offset, (col, label) in enumerate(zip(PHOTO_COLS, PHOTO_LABELS)):
            url = photo_urls[col_offset] if col_offset < len(photo_urls) else ""
            cell_ref = f"{chr(64 + col)}{spreadsheet_row}"  # e.g. "AD5"
            updates.append({
                "range": cell_ref,
                "values": [[url]],
            })

        # Update date verified
        date_col_ref = f"{chr(64 + COL_DATE_VERIFIED)}{spreadsheet_row}"
        updates.append({"range": date_col_ref, "values": [[today_str]]})

        refreshed += 1

        # Batch write every 50 rows to avoid API rate limits
        if len(updates) >= 200:
            ws.batch_update(updates)
            updates = []
            print(f"    Written batch of updates...")
            time.sleep(1)

        time.sleep(0.08)  # ~12 requests/second, well within quota

    # Write remaining updates
    if updates and not DRY_RUN:
        ws.batch_update(updates)

    print(f"\nDone:")
    print(f"  Refreshed:       {refreshed}")
    print(f"  Skipped (fresh): {skipped_fresh}")
    print(f"  Skipped (no ID): {skipped_no_id}")
    print(f"  Errors:          {errors}")

    # Write a summary to a log sheet (creates it if missing)
    try:
        try:
            log_ws = sheet.worksheet("Refresh Log")
        except Exception:
            log_ws = sheet.add_worksheet("Refresh Log", rows=500, cols=8)
            log_ws.append_row(["Date", "Refreshed", "Skipped Fresh",
                                "Skipped No ID", "Errors", "Max Age Days", "Dry Run"])
        log_ws.append_row([
            today_str, refreshed, skipped_fresh,
            skipped_no_id, errors, MAX_AGE_DAYS, str(DRY_RUN)
        ])
    except Exception as e:
        print(f"  (Log write failed: {e})")

if __name__ == "__main__":
    run_refresh()
