#!/usr/bin/env python3
"""
scripts/diagnose.py
====================
Runs a full diagnostic on your API setup and shows exactly what
Google Places returns for a real SGV restaurant.

Usage:
  python scripts/diagnose.py

ENV VARS:
  GOOGLE_API_KEY
  ANTHROPIC_API_KEY
  GOOGLE_SERVICE_ACCOUNT_JSON
  SPREADSHEET_ID
"""

import os, sys, json, requests

GOOGLE_API_KEY    = os.environ.get("GOOGLE_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SA_JSON           = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID    = os.environ.get("SPREADSHEET_ID", "")

SEP = "=" * 60

# ── Test 1: Env vars ──────────────────────────────────────────────────────────
def check_env():
    print(SEP)
    print("TEST 1 — Environment Variables")
    print(SEP)
    all_ok = True
    for name, val in [
        ("GOOGLE_API_KEY",              GOOGLE_API_KEY),
        ("ANTHROPIC_API_KEY",           ANTHROPIC_API_KEY),
        ("GOOGLE_SERVICE_ACCOUNT_JSON", SA_JSON),
        ("SPREADSHEET_ID",              SPREADSHEET_ID),
    ]:
        if val:
            preview = val[:12] + "..." if len(val) > 12 else val
            print(f"  OK  {name} = {preview}")
        else:
            print(f"  MISSING  {name}")
            all_ok = False
    return all_ok

# ── Test 2: Google Places Nearby Search ──────────────────────────────────────
def check_nearby():
    print()
    print(SEP)
    print("TEST 2 — Google Places Nearby Search")
    print("  (Alhambra, searching for 'Chinese restaurant')")
    print(SEP)
    if not GOOGLE_API_KEY:
        print("  SKIPPED — no API key")
        return None

    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
            params={
                "location": "34.0953,-118.1270",
                "radius":   1000,
                "keyword":  "Chinese restaurant",
                "type":     "restaurant",
                "key":      GOOGLE_API_KEY,
            },
            timeout=15,
        )
        data = r.json()
        status = data.get("status")
        print(f"  Status:        {status}")

        if status == "OK":
            results = data.get("results", [])
            print(f"  Results found: {len(results)}")
            if results:
                first = results[0]
                pid = first.get("place_id", "")
                print(f"  First result:  {first.get('name')} (place_id: {pid[:20]}...)")
                return pid
        elif status == "REQUEST_DENIED":
            msg = data.get("error_message", "")
            print(f"  ERROR: {msg}")
            print()
            print("  LIKELY CAUSE: Places API not enabled on this key.")
            print("  FIX: console.cloud.google.com → APIs & Services → Library")
            print("       → Enable 'Places API'")
        elif status == "INVALID_REQUEST":
            print(f"  ERROR: Invalid request — {data.get('error_message','')}")
        else:
            print(f"  ERROR: {data}")
        return None
    except Exception as e:
        print(f"  EXCEPTION: {e}")
        return None

# ── Test 3: Google Places Details ────────────────────────────────────────────
def check_details(place_id):
    print()
    print(SEP)
    print("TEST 3 — Google Places Details")
    print(f"  place_id: {place_id[:30]}...")
    print(SEP)

    # Test A: Basic fields (should always work if API is enabled)
    print("\n  Part A — Basic fields (name, address, rating):")
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={
                "place_id": place_id,
                "fields":   "name,formatted_address,rating,user_ratings_total",
                "key":      GOOGLE_API_KEY,
            },
            timeout=15,
        )
        data = r.json()
        status = data.get("status")
        result = data.get("result", {})
        print(f"    Status:  {status}")
        if status == "OK":
            print(f"    Name:    {result.get('name')}")
            print(f"    Address: {result.get('formatted_address')}")
            print(f"    Rating:  {result.get('rating')} ({result.get('user_ratings_total')} reviews)")
        else:
            print(f"    ERROR: {data.get('error_message', data)}")
    except Exception as e:
        print(f"    EXCEPTION: {e}")

    # Test B: Reviews field (costs more — requires billing)
    print("\n  Part B — Reviews field:")
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={
                "place_id": place_id,
                "fields":   "reviews",
                "key":      GOOGLE_API_KEY,
            },
            timeout=15,
        )
        data = r.json()
        status = data.get("status")
        result = data.get("result", {})
        print(f"    Status:       {status}")

        if status == "OK":
            reviews = result.get("reviews", [])
            print(f"    Reviews returned: {len(reviews)}")
            if reviews:
                for i, rev in enumerate(reviews[:3], 1):
                    text = rev.get("text", "").strip()
                    lang = rev.get("language", "?")
                    print(f"    Review {i} [{lang}]: {text[:120]}{'...' if len(text)>120 else ''}")
            else:
                print("    WARNING: reviews field returned but empty")
                print("    This means Google has no indexed reviews for this place,")
                print("    or reviews are all in a language Google won't return.")
        elif status == "REQUEST_DENIED":
            msg = data.get("error_message", "")
            print(f"    ERROR: {msg}")
            print()
            print("    LIKELY CAUSE: Billing not enabled or Places Details API")
            print("    not activated for this key.")
            print("    FIX: console.cloud.google.com → Billing → ensure billing enabled")
            print("         Then: APIs → Places API → must be ENABLED")
        else:
            print(f"    ERROR: {data}")
    except Exception as e:
        print(f"    EXCEPTION: {e}")

    # Test C: Editorial summary
    print("\n  Part C — Editorial summary:")
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={
                "place_id": place_id,
                "fields":   "editorial_summary",
                "key":      GOOGLE_API_KEY,
            },
            timeout=15,
        )
        data = r.json()
        status = data.get("status")
        result = data.get("result", {})
        print(f"    Status: {status}")
        if status == "OK":
            summary = result.get("editorial_summary", {})
            if summary:
                print(f"    Summary: {summary.get('overview', '(empty)')}")
            else:
                print("    No editorial_summary returned (normal — only ~30% of places have one)")
    except Exception as e:
        print(f"    EXCEPTION: {e}")

    # Test D: New Places API (v1)
    print("\n  Part D — New Places API (v1, better language support):")
    try:
        r = requests.get(
            f"https://places.googleapis.com/v1/places/{place_id}",
            headers={
                "X-Goog-Api-Key":   GOOGLE_API_KEY,
                "X-Goog-FieldMask": "reviews,editorialSummary,displayName",
                "Accept-Language":  "en",
            },
            timeout=15,
        )
        print(f"    HTTP status:  {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            reviews = data.get("reviews", [])
            print(f"    Reviews:      {len(reviews)}")
            summary = data.get("editorialSummary", {})
            print(f"    Editorial:    {summary.get('text', '(none)')}")
            if reviews:
                rev = reviews[0]
                text = rev.get("text", {}).get("text", "")
                orig = rev.get("originalText", {}).get("text", "")
                lang = rev.get("originalText", {}).get("languageCode", "?")
                print(f"    First review [{lang}]: {text[:120]}{'...' if len(text)>120 else ''}")
                if lang != "en" and orig:
                    print(f"    Original:     {orig[:80]}...")
        elif r.status_code == 403:
            print("    ERROR 403: New Places API not enabled or key restricted")
            print("    FIX: APIs & Services → Enable 'Places API (New)'")
            try:
                err = r.json()
                print(f"    Detail: {err.get('error', {}).get('message', '')}")
            except Exception:
                pass
        else:
            print(f"    ERROR: {r.text[:200]}")
    except Exception as e:
        print(f"    EXCEPTION: {e}")

# ── Test 4: Test with a well-known restaurant ─────────────────────────────────
def check_known_restaurant():
    print()
    print(SEP)
    print("TEST 4 — Known Restaurant Test (Chengdu Taste, Alhambra)")
    print("  place_id: ChIJaYS_w5vXwoARopWVJyXxpFE")
    print(SEP)

    CHENGDU_TASTE = "ChIJaYS_w5vXwoARopWVJyXxpFE"

    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={
                "place_id": CHENGDU_TASTE,
                "fields":   "name,reviews,editorial_summary",
                "key":      GOOGLE_API_KEY,
            },
            timeout=15,
        )
        data = r.json()
        status = data.get("status")
        result = data.get("result", {})
        print(f"  Status:   {status}")

        if status == "OK":
            print(f"  Name:     {result.get('name')}")
            reviews = result.get("reviews", [])
            print(f"  Reviews:  {len(reviews)} returned")
            summary = result.get("editorial_summary", {})
            print(f"  Editorial: {summary.get('overview', '(none)')}")

            if reviews:
                print("\n  Review texts:")
                for i, rev in enumerate(reviews, 1):
                    text = rev.get("text", "").strip()
                    lang = rev.get("language", "?")
                    rating = rev.get("rating", "?")
                    print(f"    [{i}] {lang} ★{rating}: {text[:200]}{'...' if len(text)>200 else ''}")
            else:
                print()
                print("  No reviews returned for Chengdu Taste.")
                print("  This is the most likely cause of your dish extraction failing.")
                print()
                print("  POSSIBLE REASONS:")
                print("  1. The 'reviews' field requires billing to be enabled")
                print("     on your Google Cloud project.")
                print("  2. Your API key doesn't have Places Details (Advanced)")
                print("     enabled. Check the APIs & Services console.")
                print("  3. Key restrictions are blocking the request.")
                print("     Check: APIs & Services → Credentials → your key → restrictions")
    except Exception as e:
        print(f"  EXCEPTION: {e}")

# ── Test 5: Anthropic API ─────────────────────────────────────────────────────
def check_anthropic():
    print()
    print(SEP)
    print("TEST 5 — Anthropic (Claude) API")
    print(SEP)
    if not ANTHROPIC_API_KEY:
        print("  SKIPPED — ANTHROPIC_API_KEY not set")
        return

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-sonnet-4-20250514",
                "max_tokens": 30,
                "messages":   [{"role": "user", "content": "Say 'API OK' only."}],
            },
            timeout=15,
        )
        if r.status_code == 200:
            text = r.json()["content"][0]["text"]
            print(f"  OK — Claude responded: {text.strip()}")
        elif r.status_code == 401:
            print("  ERROR 401: Invalid API key")
            print("  FIX: Check ANTHROPIC_API_KEY — get a new one at console.anthropic.com")
        elif r.status_code == 429:
            print("  ERROR 429: Rate limit hit — key is valid but you're over quota")
        else:
            print(f"  ERROR {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  EXCEPTION: {e}")

# ── Test 6: Google Sheet access ───────────────────────────────────────────────
def check_sheet():
    print()
    print(SEP)
    print("TEST 6 — Google Sheet Access")
    print(SEP)
    if not SA_JSON or not SPREADSHEET_ID:
        print("  SKIPPED — SA_JSON or SPREADSHEET_ID not set")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("  SKIPPED — run: pip install gspread google-auth")
        return

    try:
        creds = Credentials.from_service_account_info(
            json.loads(SA_JSON),
            scopes=[
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive",
            ],
        )
        gc   = gspread.authorize(creds)
        book = gc.open_by_key(SPREADSHEET_ID)
        ws   = book.worksheet("Restaurants")
        rows = ws.get_all_values()
        data_rows = len(rows) - 3  # minus headers
        print(f"  OK — Sheet accessible")
        print(f"  Spreadsheet: {book.title}")
        print(f"  Restaurants: {data_rows} data rows")

        # Check a sample row for Place ID
        place_ids = 0
        for row in rows[3:]:
            if len(row) >= 34 and row[33].strip():
                place_ids += 1
        print(f"  Rows with Google Place ID: {place_ids}/{data_rows}")

        # Check if new dish columns exist yet
        has_ap = False
        for row in rows[3:6]:
            if len(row) >= 42:
                has_ap = True
                break
        print(f"  New dish columns (AP-AX): {'present' if has_ap else 'not yet written'}")

    except json.JSONDecodeError:
        print("  ERROR: GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON")
        print("  Make sure you copied the entire file contents including the { }")
    except Exception as e:
        print(f"  ERROR: {e}")

# ── Summary ───────────────────────────────────────────────────────────────────
def print_summary():
    print()
    print(SEP)
    print("SUMMARY & NEXT STEPS")
    print(SEP)
    print("""
If TEST 3 Part B shows no reviews for Chengdu Taste:
  → This is the root cause. Fix one of:
  
  A) Enable billing on your Google Cloud project:
     console.cloud.google.com → Billing → Link a billing account
     (You get $200/month free — won't cost anything at our scale)
     
  B) Enable Places API (Advanced) on your key:
     APIs & Services → Library → search "Places API" → Enable
     Make sure it's the full Places API, not just the basic one

If TEST 3 Part D (New Places API) shows 403:
  → Enable "Places API (New)" in your API library
  → OR remove HTTP referrer restrictions from your key temporarily to test

If TEST 2 shows REQUEST_DENIED:
  → Places API itself is not enabled — fix this first

If TEST 5 fails:
  → Anthropic key issue — get a fresh one at console.anthropic.com
""")

# ── Run all tests ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("626 Eats — API Diagnostic")
    print()

    env_ok   = check_env()
    place_id = None

    if GOOGLE_API_KEY:
        place_id = check_nearby()
        if place_id:
            check_details(place_id)
        check_known_restaurant()

    check_anthropic()
    check_sheet()
    print_summary()
