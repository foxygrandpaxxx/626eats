#!/usr/bin/env python3
"""
scripts/test_enrich.py
=======================
Tests dish extraction on the first 5 restaurants in your Sheet
that have Google Place IDs. Shows exactly what review text Google
returns and what Claude extracts from it.

Run this before the full enrich_dishes.py to confirm it will work.

Usage:
  python scripts/test_enrich.py
"""

import os, sys, json, time, re, requests

GOOGLE_API_KEY    = os.environ.get("GOOGLE_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SA_JSON           = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID    = os.environ.get("SPREADSHEET_ID", "")

VALID_FORMATS = [
    "Noodles","Dumplings","Dim Sum Plates","Roasts & BBQ","Rice Dishes",
    "Soups","Meat Dishes","Small Plates & Cold","Hot Pot","Pastry & Bread","Other",
]

SEP = "-" * 60

def fetch_reviews(place_id):
    r = requests.get(
        "https://maps.googleapis.com/maps/api/place/details/json",
        params={
            "place_id": place_id,
            "fields":   "name,reviews,editorial_summary",
            "key":      GOOGLE_API_KEY,
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("result", {})

def call_claude(prompt):
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "system":     "You extract dish data from real restaurant reviews only. Never infer.",
            "messages":   [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()

def get_sheet_rows():
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        json.loads(SA_JSON),
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"],
    )
    gc   = gspread.authorize(creds)
    ws   = gc.open_by_key(SPREADSHEET_ID).worksheet("Restaurants")
    return ws.get_all_values()

print("626 Eats — Dish Enrichment Test (first 5 restaurants with Place IDs)")
print()

# Load Sheet
print("Loading Sheet...")
rows = get_sheet_rows()
DATA_START = 3

# Grab first 5 with a Place ID
candidates = []
for row in rows[DATA_START:]:
    if len(row) < 34: continue
    name     = row[1].strip()
    city     = row[5].strip()
    region   = row[9].strip()
    sub      = row[10].strip()
    place_id = row[33].strip()
    if name and place_id:
        candidates.append({
            "name": name, "city": city,
            "region": (region + " / " + sub) if sub else region,
            "place_id": place_id,
        })
    if len(candidates) >= 5:
        break

print(f"Testing {len(candidates)} restaurants\n")

for i, r in enumerate(candidates):
    print(SEP)
    print(f"[{i+1}/5] {r['name']} ({r['city']}) — {r['region']}")
    print(SEP)

    # Step 1: Fetch Google data
    print("  Fetching Google reviews...", end=" ", flush=True)
    try:
        d = fetch_reviews(r["place_id"])
        time.sleep(0.1)
    except Exception as e:
        print(f"FAILED: {e}")
        continue

    editorial = d.get("editorial_summary", {}).get("overview", "")
    reviews   = [rev.get("text","").strip()
                 for rev in d.get("reviews", [])
                 if rev.get("text","").strip()]

    print(f"got {len(reviews)} reviews, editorial={'yes' if editorial else 'no'}")

    # Show raw text
    if editorial:
        print(f"\n  Editorial: {editorial}")
    if reviews:
        print(f"\n  Review snippets:")
        for j, text in enumerate(reviews, 1):
            lang_cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
            lang = "zh" if lang_cjk / max(len(text),1) > 0.3 else "en"
            preview = text[:180] + ("..." if len(text) > 180 else "")
            print(f"    [{j}][{lang}] {preview}")
    else:
        print("  No review text returned.")

    if not editorial and not reviews:
        print("  → SKIP: nothing to extract from")
        continue

    # Step 2: Claude extraction
    all_text = ""
    if editorial:
        all_text += f"EDITORIAL:\n{editorial}\n\n"
    if reviews:
        all_text += "REVIEWS:\n"
        for j, text in enumerate(reviews, 1):
            all_text += f"Review {j}: {text}\n"

    valid_fmts = ", ".join(f'"{f}"' for f in VALID_FORMATS)
    prompt = f"""Restaurant: {r['name']}
Cuisine: {r['region']}

REVIEW TEXT:
{all_text}
---
Extract ONLY dishes explicitly named in this text. Max 3.
For each dish:
- name: dish name as mentioned
- category: exactly one of {valid_fmts}
- is_best: true if highlighted by multiple reviewers
- summary: 1-2 sentences of what reviewers say about it specifically

If no dishes are explicitly named, return [].
Respond with ONLY a JSON array."""

    print(f"\n  Sending to Claude...", end=" ", flush=True)
    try:
        raw = call_claude(prompt)
        time.sleep(0.5)
    except Exception as e:
        print(f"FAILED: {e}")
        continue

    # Parse response
    raw_clean = re.sub(r"^```(?:json)?\s*", "", raw)
    raw_clean = re.sub(r"\s*```$", "", raw_clean).strip()

    try:
        dishes = json.loads(raw_clean)
        if not dishes:
            print("returned []  (no dishes named in reviews)")
        else:
            print(f"found {len(dishes)} dish(es)")
            for d in dishes:
                best = " 🏆" if d.get("is_best") else ""
                print(f"\n    ✓ {d['name']}{best}  [{d.get('category','?')}]")
                summary = d.get("summary","")
                if summary:
                    print(f'      "{summary}"')
    except json.JSONDecodeError:
        print(f"PARSE ERROR — Claude returned:\n{raw[:300]}")

    print()

print(SEP)
print("Test complete.")
print()
print("If dishes were found above, the full run will work.")
print("Run: python scripts/enrich_dishes.py --dry-run")
