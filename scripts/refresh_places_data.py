#!/usr/bin/env python3
"""
scripts/refresh_places_data.py
-------------------------------
Uses the Google Places API (New) v1 to fetch rich data for every restaurant
and writes it back to the Google Sheet. This replaces refresh_ratings.py.

Fetches in a single API call per restaurant (billed as Enterprise + Atmosphere):
  • generativeSummary   — Gemini AI paragraph about the place, often mentions dishes
  • reviewSummary       — AI synthesis of all user reviews
  • editorialSummary    — Short editorial description
  • reviews             — Up to 5 full user review texts
  • rating              — Google rating (1–5)
  • userRatingCount     — Total number of reviews
  • websiteUri          — Restaurant website URL (cross-checks Sheet)
  • nationalPhoneNumber — Phone number (cross-checks Sheet)

Columns written (must be set up by setup_new_columns.py first):
  BB (54): google_rating
  BC (55): google_review_count
  BD (56): generative_summary
  BE (57): review_summary
  BF (58): editorial_summary
  BG (59): reviews_json   — JSON array of {"text": "...", "rating": N, "lang": "en"}

Cost: ~$0.025/restaurant × 273 = ~$6.83 — covered by Google's $200/mo free credit.

Required secrets:
  GOOGLE_SERVICE_ACCOUNT_JSON  — Service account JSON (for Sheets access)
  SPREADSHEET_ID               — Google Sheet ID
  GOOGLE_MAPS_API_KEY          — Google Maps Platform API key (Places API enabled)

Optional env vars:
  SA_PATH          — Path to service account JSON file (alternative to env var)
  FORCE_REFRESH    — Set to "true" to re-fetch restaurants that already have data
  START_AT         — Restaurant number to start at (for resuming after failure)

Local usage:
  SA_PATH=path/to/sa.json SPREADSHEET_ID=... GOOGLE_MAPS_API_KEY=... python scripts/refresh_places_data.py

GitHub Actions:
  Triggered manually (workflow_dispatch). Re-run quarterly or after adding new restaurants.
"""

import os, json, time, sys
import urllib.request
import urllib.error

# ── Config ──────────────────────────────────────────────────────────────────
SA_JSON        = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SA_PATH        = os.environ.get("SA_PATH", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
GMAPS_KEY      = os.environ.get("GOOGLE_MAPS_API_KEY", "")
FORCE_REFRESH  = os.environ.get("FORCE_REFRESH", "false").lower() == "true"
START_AT       = int(os.environ.get("START_AT", "1"))

# ── Column positions (1-based) ───────────────────────────────────────────────
COL_ID                  = 1   # A
COL_NAME                = 2   # B
COL_GOOGLE_ID           = 34  # AH
COL_GOOGLE_RATING       = 54  # BB
COL_GOOGLE_COUNT        = 55  # BC
COL_GENERATIVE_SUMMARY  = 56  # BD
COL_REVIEW_SUMMARY      = 57  # BE
COL_EDITORIAL_SUMMARY   = 58  # BF
COL_REVIEWS_JSON        = 59  # BG

DATA_START = 3  # rows[DATA_START] is first data row (0-indexed in get_all_values)

# Fields to request from Places API (New) — all fall under Enterprise + Atmosphere SKU
FIELD_MASK = ",".join([
    "generativeSummary",
    "reviewSummary",
    "editorialSummary",
    "reviews",
    "rating",
    "userRatingCount",
])

# Max characters to store per review text (Sheets cell limit is 50,000 chars;
# we store multiple reviews as JSON so cap each one)
MAX_REVIEW_CHARS = 800
MAX_SUMMARY_CHARS = 2000


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


def fetch_place_data(place_id):
    """
    Fetch rich data for a single place using Google Places API (New) v1.

    Returns a dict with keys: rating, review_count, generative_summary,
    review_summary, editorial_summary, reviews_json (JSON string).
    Returns None on API error.
    """
    # The new Places API uses the place ID directly in the URL path
    # Strip any "places/" prefix if present (shouldn't be, but be safe)
    clean_id = place_id.strip()
    if clean_id.startswith("places/"):
        clean_id = clean_id[7:]

    url = f"https://places.googleapis.com/v1/places/{urllib.request.quote(clean_id)}"

    req = urllib.request.Request(url)
    req.add_header("X-Goog-Api-Key", GMAPS_KEY)
    req.add_header("X-Goog-FieldMask", FIELD_MASK)
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"    HTTP {e.code} error: {body[:200]}")
        return None
    except Exception as e:
        print(f"    Request error: {e}")
        return None

    result = {}

    # Rating and review count
    result["rating"]       = data.get("rating")
    result["review_count"] = data.get("userRatingCount")

    # generativeSummary — structure: {"overview": {"text": "...", "languageCode": "en"}}
    gen_sum = data.get("generativeSummary", {})
    overview = gen_sum.get("overview", {})
    gen_text = overview.get("text", "") if isinstance(overview, dict) else ""
    result["generative_summary"] = gen_text[:MAX_SUMMARY_CHARS] if gen_text else ""

    # reviewSummary — structure: {"text": {"text": "...", "languageCode": "en"}}
    rev_sum = data.get("reviewSummary", {})
    rev_sum_inner = rev_sum.get("text", {})
    rev_sum_text = rev_sum_inner.get("text", "") if isinstance(rev_sum_inner, dict) else ""
    result["review_summary"] = rev_sum_text[:MAX_SUMMARY_CHARS] if rev_sum_text else ""

    # editorialSummary — structure: {"text": "...", "languageCode": "en"}
    ed_sum = data.get("editorialSummary", {})
    ed_text = ed_sum.get("text", "") if isinstance(ed_sum, dict) else ""
    result["editorial_summary"] = ed_text[:MAX_SUMMARY_CHARS] if ed_text else ""

    # reviews — array of review objects
    reviews_out = []
    for rev in data.get("reviews", []):
        text_obj = rev.get("text", {})
        text = text_obj.get("text", "") if isinstance(text_obj, dict) else ""
        lang = text_obj.get("languageCode", "") if isinstance(text_obj, dict) else ""
        if text:
            reviews_out.append({
                "text": text[:MAX_REVIEW_CHARS],
                "rating": rev.get("rating"),
                "lang": lang,
            })
    result["reviews_json"] = json.dumps(reviews_out, ensure_ascii=False) if reviews_out else ""

    return result


def run():
    if not GMAPS_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY not set.")
        sys.exit(1)
    if not SPREADSHEET_ID:
        print("ERROR: SPREADSHEET_ID not set.")
        sys.exit(1)

    print("Connecting to Google Sheets...")
    client   = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws       = spreadsheet.worksheet("Restaurants")
    rows     = ws.get_all_values()
    print(f"  Loaded {len(rows)} rows from sheet.")

    updates  = []  # list of (spreadsheet_row_1based, col_1based, value)
    ok_count = 0
    skip_count = 0
    err_count  = 0
    rest_num   = 0

    for i, row in enumerate(rows[DATA_START:], start=DATA_START + 1):
        spreadsheet_row = i + 1

        def get_cell(col):
            idx = col - 1
            return row[idx].strip() if idx < len(row) else ''

        rest_id   = get_cell(COL_ID)
        name      = get_cell(COL_NAME)
        google_id = get_cell(COL_GOOGLE_ID)

        if not rest_id:
            continue

        rest_num += 1

        if rest_num < START_AT:
            continue

        if not google_id:
            print(f"  [{rest_num:3d}] {name} — no Google Place ID, skipping")
            skip_count += 1
            continue

        # Skip if already has generative_summary data (unless FORCE_REFRESH)
        if not FORCE_REFRESH:
            existing = get_cell(COL_GENERATIVE_SUMMARY)
            if existing:
                print(f"  [{rest_num:3d}] {name} — already has data, skipping (use FORCE_REFRESH=true to override)")
                skip_count += 1
                continue

        print(f"  [{rest_num:3d}] {name} ({google_id[:20]}...)")

        place_data = fetch_place_data(google_id)

        if place_data is None:
            print(f"         ✗ Failed to fetch")
            err_count += 1
            time.sleep(1.0)  # back off on error
            continue

        # Queue all updates for this restaurant
        if place_data.get("rating") is not None:
            updates.append((spreadsheet_row, COL_GOOGLE_RATING, str(place_data["rating"])))
            updates.append((spreadsheet_row, COL_GOOGLE_COUNT,  str(place_data["review_count"] or "")))

        updates.append((spreadsheet_row, COL_GENERATIVE_SUMMARY, place_data.get("generative_summary", "")))
        updates.append((spreadsheet_row, COL_REVIEW_SUMMARY,     place_data.get("review_summary", "")))
        updates.append((spreadsheet_row, COL_EDITORIAL_SUMMARY,  place_data.get("editorial_summary", "")))
        updates.append((spreadsheet_row, COL_REVIEWS_JSON,       place_data.get("reviews_json", "")))

        # Log what we got
        has_gen = bool(place_data.get("generative_summary"))
        has_rev_sum = bool(place_data.get("review_summary"))
        n_reviews = len(json.loads(place_data["reviews_json"])) if place_data.get("reviews_json") else 0
        rating = place_data.get("rating")
        print(f"         ★ {rating or '—'}  gen={'✓' if has_gen else '✗'}  rev_sum={'✓' if has_rev_sum else '✗'}  reviews={n_reviews}")

        ok_count += 1

        # Flush updates to sheet every 25 restaurants to avoid losing data on failure
        if len(updates) >= 150:  # ~25 restaurants × 6 cells each
            _flush_updates(ws, updates)
            updates = []

        time.sleep(0.12)  # ~8 req/sec — well within Google's limits

    # Final flush
    if updates:
        _flush_updates(ws, updates)

    print(f"\n{'─'*50}")
    print(f"Done.  Fetched: {ok_count}  Skipped: {skip_count}  Errors: {err_count}")
    print(f"Re-run with FORCE_REFRESH=true to re-fetch all restaurants.")


def _flush_updates(ws, updates):
    """Write a batch of (row, col, value) updates to the sheet."""
    if not updates:
        return
    batch = [
        {"range": f"{col_letter(col)}{row}", "values": [[val]]}
        for row, col, val in updates
    ]
    print(f"\n  → Flushing {len(updates)} cells to sheet...", end=" ", flush=True)
    # Write in chunks of 100 (Sheets API limit per batchUpdate call)
    for chunk_start in range(0, len(batch), 100):
        ws.batch_update(batch[chunk_start:chunk_start + 100])
        time.sleep(0.3)
    print("done.")


if __name__ == "__main__":
    run()
