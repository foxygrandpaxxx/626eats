#!/usr/bin/env python3
"""
scripts/enrich_dishes.py
=========================
Pulls real dish data from Google Places reviews + editorial summary,
translates non-English text, then uses Claude AI to extract dish names,
categorize them, and summarize reviewer opinions.

Results are written directly to the Restaurants tab in 9 new columns:
  AP — dish1_name       AQ — dish1_category    AR — dish1_summary
  AS — dish2_name       AT — dish2_category    AU — dish2_summary
  AV — dish3_name       AW — dish3_category    AX — dish3_summary

WHAT CLAUDE DOES (extraction only, no hallucination):
  - Reads the actual review text (translated if needed)
  - Identifies dish names explicitly mentioned
  - Assigns each to a format category for filtering
  - Writes a 1-2 sentence summary using the reviewers' own language

USAGE:
  # Fill restaurants with empty dish columns (safe default)
  python scripts/enrich_dishes.py

  # Preview without writing anything
  python scripts/enrich_dishes.py --dry-run

  # Overwrite ALL existing dish columns with fresh data
  python scripts/enrich_dishes.py --overwrite

  # Test on first 10 restaurants
  python scripts/enrich_dishes.py --limit 10 --dry-run

  # Specific cities only
  python scripts/enrich_dishes.py --filter-city "Alhambra,San Gabriel"

ENV VARS REQUIRED:
  GOOGLE_API_KEY                Google Maps Platform key
  ANTHROPIC_API_KEY             Anthropic API key
  GOOGLE_SERVICE_ACCOUNT_JSON   Service account JSON
  SPREADSHEET_ID                Google Sheet ID
"""

import os, sys, re, json, time, argparse, requests
from datetime import date

# ── Config ───────────────────────────────────────────────────────────────────
GOOGLE_API_KEY    = os.environ.get("GOOGLE_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SA_JSON           = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID    = os.environ.get("SPREADSHEET_ID", "")

# ── Sheet column indices ──────────────────────────────────────────────────────
# Restaurants tab (0-based for reading, 1-based col letters for writing)
COL_ID        = 0   # A
COL_NAME      = 1   # B
COL_STATUS    = 3   # D
COL_CITY      = 5   # F
COL_REGION    = 9   # J
COL_SUBREGION = 10  # K
COL_PLACE_ID  = 33  # AH

# New dish columns (1-based, AP=42 onwards)
# dish1: AP(42), AQ(43), AR(44)
# dish2: AS(45), AT(46), AU(47)
# dish3: AV(48), AW(49), AX(50)
DISH_COLS = [
    ("AP", "AQ", "AR"),  # dish 1: name, category, summary
    ("AS", "AT", "AU"),  # dish 2
    ("AV", "AW", "AX"),  # dish 3
]
DISH_RANGE_START = "AP"  # First new column
DISH_RANGE_END   = "AX"  # Last new column

# ── Valid format categories (must match app's FORMATS array) ─────────────────
VALID_FORMATS = [
    "Noodles",
    "Dumplings",
    "Dim Sum Plates",
    "Roasts & BBQ",
    "Rice Dishes",
    "Soups",
    "Meat Dishes",
    "Small Plates & Cold",
    "Hot Pot",
    "Pastry & Bread",
    "Other",
]

# ── Google Places API ─────────────────────────────────────────────────────────

def fetch_google_text(place_id):
    """
    Fetch review text and editorial summary from Google Places.
    Uses the New Places API which returns originalText (any language)
    alongside auto-translated English text.
    Falls back to legacy API if needed.
    Returns dict: {editorial, reviews: [{text, original_lang}]}
    """
    if not place_id:
        return {"editorial": "", "reviews": []}

    # Try New Places API first (better language handling)
    try:
        r = requests.get(
            f"https://places.googleapis.com/v1/places/{place_id}",
            headers={
                "X-Goog-Api-Key":    GOOGLE_API_KEY,
                "X-Goog-FieldMask":  "reviews,editorialSummary,displayName",
                "Accept-Language":   "en",
            },
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            editorial = (data.get("editorialSummary", {})
                             .get("text", ""))

            reviews = []
            for rev in data.get("reviews", []):
                # Prefer translated English text
                eng = (rev.get("text", {}).get("text", "") or
                       rev.get("originalText", {}).get("text", ""))
                lang = rev.get("originalText", {}).get("languageCode", "en")
                if eng and len(eng.strip()) > 15:
                    reviews.append({
                        "text": eng.strip(),
                        "lang": lang,
                    })
            return {"editorial": editorial, "reviews": reviews}
    except Exception:
        pass

    # Fallback: legacy Places Details API
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={
                "place_id": place_id,
                "fields":   "reviews,editorial_summary",
                "key":      GOOGLE_API_KEY,
            },
            timeout=15,
        )
        r.raise_for_status()
        result = r.json().get("result", {})

        editorial = ""
        summary = result.get("editorial_summary", {})
        if isinstance(summary, dict):
            editorial = summary.get("overview", "")

        reviews = []
        for rev in result.get("reviews", []):
            text = (rev.get("text") or "").strip()
            if text and len(text) > 15:
                # Detect if it's likely Chinese (>30% CJK chars)
                cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
                lang = "zh" if cjk / max(len(text), 1) > 0.3 else "en"
                reviews.append({"text": text, "lang": lang})

        return {"editorial": editorial, "reviews": reviews}
    except Exception as e:
        return {"editorial": "", "reviews": [], "error": str(e)}


# ── Claude AI ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a food critic assistant specializing in San Gabriel Valley "
    "Chinese restaurants. Your only job is to analyze real customer review "
    "text and extract specific dish information. "
    "You extract only what is explicitly mentioned — never infer or hallucinate."
)

def call_claude(prompt, max_tokens=1200):
    """Make a single Claude API call. Returns response text or None."""
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":    "claude-sonnet-4-20250514",
                "max_tokens": max_tokens,
                "system":   SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        return None

def translate_reviews(reviews, restaurant_name):
    """
    Translate any non-English reviews to English using Claude.
    Returns list of translated text strings.
    """
    non_english = [r for r in reviews if r.get("lang", "en") != "en"]
    if not non_english:
        return [r["text"] for r in reviews]

    # Batch all non-English reviews into one Claude call
    to_translate = "\n\n".join(
        f"Review {i+1} ({r['lang']}):\n{r['text']}"
        for i, r in enumerate(non_english)
    )

    prompt = (
        f"Translate these customer reviews of '{restaurant_name}' to English. "
        f"Preserve specific dish names, flavors, and reviewer opinions exactly. "
        f"Return ONLY the translated reviews numbered the same way, "
        f"with no other commentary:\n\n{to_translate}"
    )

    result = call_claude(prompt, max_tokens=800)
    if not result:
        # If translation fails, use original text anyway
        return [r["text"] for r in reviews]

    # Merge: use translated versions for non-English, originals for English
    translated = {}
    lines = result.split("\n")
    current_num = None
    current_text = []
    for line in lines:
        m = re.match(r"^Review\s+(\d+)", line, re.I)
        if m:
            if current_num is not None and current_text:
                translated[current_num] = " ".join(current_text).strip()
            current_num = int(m.group(1))
            current_text = [re.sub(r"^Review\s+\d+.*?:\s*", "", line, flags=re.I).strip()]
        elif current_num is not None:
            current_text.append(line.strip())
    if current_num is not None and current_text:
        translated[current_num] = " ".join(current_text).strip()

    # Build final list: translated non-English + original English
    ne_idx = 0
    all_texts = []
    for r in reviews:
        if r.get("lang", "en") != "en":
            ne_idx += 1
            all_texts.append(translated.get(ne_idx, r["text"]))
        else:
            all_texts.append(r["text"])
    return all_texts

def extract_dishes_claude(name, region, editorial, review_texts):
    """
    Send real review text to Claude and get back structured dish data.
    Only extracts dishes explicitly named in the text.
    Returns list of up to 3 dish dicts, or [] if nothing found.
    """
    all_text = ""
    if editorial:
        all_text += f"EDITORIAL SUMMARY:\n{editorial}\n\n"
    if review_texts:
        all_text += "CUSTOMER REVIEWS:\n"
        for i, text in enumerate(review_texts, 1):
            all_text += f"Review {i}: {text}\n"

    if len(all_text.strip()) < 40:
        return []

    valid_formats_str = ", ".join(f'"{f}"' for f in VALID_FORMATS)

    prompt = f"""Restaurant: {name}
Cuisine style: {region}

REVIEW TEXT TO ANALYZE:
{all_text}
---
Extract ONLY dishes that are explicitly named in the review text above.

For each dish (maximum 3), provide:
- name: The dish name as mentioned (standardize capitalization)
- category: MUST be exactly one of: {valid_formats_str}
- is_best: true only if multiple reviewers specifically highlight it or call it a must-order
- summary: 1-2 sentences of what reviewers specifically say about THIS dish at THIS restaurant. Use their language and specific details. Never be generic.

RULES:
- Only include dishes explicitly named — no inference
- If reviews are vague ("the food was good", "everything was delicious") return []
- Maximum 3 dishes, ranked by how prominently reviewers mention them
- summary must be specific to reviewer comments, not a generic description

Respond with ONLY a JSON array:
[
  {{
    "name": "Toothpick Lamb",
    "category": "Meat Dishes",
    "is_best": true,
    "summary": "Reviewers consistently call this the must-order, praising the crispy cumin-coated lamb skewers that arrive sizzling."
  }}
]

If no specific dishes are named in the text, respond with exactly: []"""

    raw = call_claude(prompt, max_tokens=1000)
    if not raw:
        return []

    # Strip markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw).strip()

    try:
        dishes = json.loads(raw)
        if not isinstance(dishes, list):
            return []
        valid = []
        for d in dishes:
            if not isinstance(d, dict):
                continue
            dish_name = str(d.get("name", "")).strip()
            category  = str(d.get("category", "Other")).strip()
            is_best   = bool(d.get("is_best", False))
            summary   = str(d.get("summary", "")).strip()
            if not dish_name:
                continue
            if category not in VALID_FORMATS:
                category = "Other"
            valid.append({
                "name":     dish_name,
                "category": category,
                "is_best":  is_best,
                "summary":  summary,
            })
        return valid[:3]
    except json.JSONDecodeError:
        return []


# ── Google Sheets ─────────────────────────────────────────────────────────────

def get_sheet():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("Run: pip install gspread google-auth")
    creds = Credentials.from_service_account_info(
        json.loads(SA_JSON),
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID).worksheet("Restaurants")

def safe_get(row, idx):
    try:
        return row[idx].strip() if idx < len(row) else ""
    except Exception:
        return ""

def has_dish_data(row):
    """Check if any of the new dish columns (AP-AX) already have data."""
    # AP = index 41 (0-based)
    return any(safe_get(row, 41 + i) for i in range(9))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Enrich restaurant dish columns from real Google reviews"
    )
    parser.add_argument("--overwrite",   action="store_true",
                        help="Overwrite existing dish data")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Preview without writing")
    parser.add_argument("--limit",       type=int, default=0,
                        help="Process only first N restaurants")
    parser.add_argument("--filter-city", default="",
                        help='Only process these cities e.g. "Alhambra,San Gabriel"')
    args = parser.parse_args()

    # Validate env vars
    missing = []
    if not GOOGLE_API_KEY:    missing.append("GOOGLE_API_KEY")
    if not ANTHROPIC_API_KEY: missing.append("ANTHROPIC_API_KEY")
    if not SA_JSON:           missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not SPREADSHEET_ID:    missing.append("SPREADSHEET_ID")
    if missing:
        sys.exit("ERROR: Missing env vars:\n  " + "\n  ".join(missing))

    city_filter = {c.strip() for c in args.filter_city.split(",")} \
                  if args.filter_city else set()

    print("=" * 60)
    print("626 Eats — Dish Enrichment from Google Reviews")
    print("Source:  Google Places reviews + editorial summary")
    print("Trans:   Non-English reviews translated via Claude")
    print("Extract: Claude identifies dishes, categories, summaries")
    print("Output:  Columns AP–AX in Restaurants tab")
    print()
    print("Mode:    " + ("OVERWRITE all" if args.overwrite
                          else "Fill empty dish columns only"))
    if args.dry_run:  print("DRY RUN: No changes written")
    if city_filter:   print("Cities:  " + ", ".join(sorted(city_filter)))
    if args.limit:    print("Limit:   " + str(args.limit))
    print("=" * 60)

    print("\nConnecting to Google Sheet...")
    ws   = get_sheet()
    rows = ws.get_all_values()
    DATA_START = 3

    # ── Build list to process ─────────────────────────────────────────────────
    to_process = []
    for i, row in enumerate(rows[DATA_START:], start=DATA_START):
        rest_id  = safe_get(row, COL_ID)
        name     = safe_get(row, COL_NAME)
        status   = safe_get(row, COL_STATUS)
        city     = safe_get(row, COL_CITY)
        region   = safe_get(row, COL_REGION)
        sub      = safe_get(row, COL_SUBREGION)
        place_id = safe_get(row, COL_PLACE_ID)

        if not rest_id or not name:
            continue
        if status.upper() == "CLOSED":
            continue
        if not place_id:
            continue
        if city_filter and city not in city_filter:
            continue
        if has_dish_data(row) and not args.overwrite:
            continue

        to_process.append({
            "sheet_row": i,
            "rest_id":   rest_id,
            "name":      name,
            "city":      city,
            "region":    (region + " / " + sub) if sub else region,
            "place_id":  place_id,
        })

        if args.limit and len(to_process) >= args.limit:
            break

    total = len(to_process)
    if total == 0:
        msg = ("No restaurants with Google Place IDs found."
               if args.overwrite
               else "All restaurants already have dish data. "
                    "Use --overwrite to refresh.")
        print("\n" + msg)
        return

    # Estimate costs
    g_cost  = total * 0.017   # Places Details per restaurant
    ai_cost = total * 0.005   # ~1500 tokens/restaurant (fetch + translate + extract)
    print(f"\nRestaurants to process: {total}")
    print(f"Estimated API cost:     ~${g_cost + ai_cost:.2f}")
    print(f"  Google Places:        ~${g_cost:.2f}")
    print(f"  Claude (AI):          ~${ai_cost:.2f}")
    print()

    # ── Process each restaurant ───────────────────────────────────────────────
    batch_updates = []   # Collect all Sheet updates for batch write
    stats = {"dishes": 0, "no_reviews": 0, "no_dishes": 0, "translated": 0}

    for i, r in enumerate(to_process):
        print(f"  [{i+1}/{total}] {r['name']} ({r['city']})", end=" ", flush=True)

        # Step 1: Fetch Google review text
        data      = fetch_google_text(r["place_id"])
        editorial = data.get("editorial", "")
        reviews   = data.get("reviews", [])
        time.sleep(0.08)

        if not editorial and not reviews:
            print("→ no review text")
            stats["no_reviews"] += 1
            continue

        # Step 2: Translate non-English reviews
        non_en = [rv for rv in reviews if rv.get("lang", "en") != "en"]
        if non_en:
            print(f"→ translating {len(non_en)} non-English reviews", end=" ", flush=True)
            stats["translated"] += len(non_en)
            review_texts = translate_reviews(reviews, r["name"])
            time.sleep(0.4)
        else:
            review_texts = [rv["text"] for rv in reviews]

        # Step 3: Claude extracts dishes from the real text
        dishes = extract_dishes_claude(
            r["name"], r["region"], editorial, review_texts
        )
        time.sleep(0.4)

        if not dishes:
            print("→ no dishes named in reviews")
            stats["no_dishes"] += 1
            continue

        print("→ " + ", ".join(d["name"] for d in dishes))
        stats["dishes"] += 1

        # Step 4: Build the 9-cell update (AP:AX) for this row
        # Pad to 3 dishes with empty strings
        cell_values = []
        for slot in range(3):
            if slot < len(dishes):
                d = dishes[slot]
                cell_values += [d["name"], d["category"], d["summary"]]
            else:
                cell_values += ["", "", ""]

        sheet_row = r["sheet_row"] + 1  # 1-based
        batch_updates.append({
            "range":  f"AP{sheet_row}:AX{sheet_row}",
            "values": [cell_values],
        })

    # ── Show preview or write ─────────────────────────────────────────────────
    print(f"\n  Got dish data:    {stats['dishes']}/{total}")
    print(f"  No review text:   {stats['no_reviews']}/{total}")
    print(f"  Reviews, no dish: {stats['no_dishes']}/{total}")
    print(f"  Reviews translated: {stats['translated']} snippets")

    if args.dry_run:
        print(f"\nDRY RUN — {len(batch_updates)} rows would be updated.\n")
        for upd in batch_updates[:10]:
            vals = upd["values"][0]
            rng  = upd["range"]
            print(f"  {rng}:")
            for slot in range(3):
                name = vals[slot * 3]
                cat  = vals[slot * 3 + 1]
                summ = vals[slot * 3 + 2]
                if name:
                    best_marker = ""
                    print(f"    Dish {slot+1}: {name} [{cat}]")
                    if summ:
                        print(f"             \"{summ[:80]}{'...' if len(summ)>80 else ''}\"")
        if len(batch_updates) > 10:
            print(f"  ... and {len(batch_updates) - 10} more")
        print(f"\nRun without --dry-run to write these changes.")
        return

    if not batch_updates:
        print("\nNothing to write.")
        return

    print(f"\nWriting {len(batch_updates)} rows to Sheet...")
    BATCH = 200
    for start in range(0, len(batch_updates), BATCH):
        chunk = batch_updates[start:start + BATCH]
        ws.batch_update(chunk)
        print(f"  Wrote batch {start // BATCH + 1} ({len(chunk)} rows)")
        time.sleep(0.8)

    print("\n" + "=" * 60)
    print("Done!")
    print(f"  {stats['dishes']} restaurants got dish data")
    print(f"  {stats['no_reviews'] + stats['no_dishes']} had no usable data")
    print()
    print("Next steps:")
    print("  1. python scripts/export_json.py")
    print("  2. git add data/restaurants.json && git commit && git push")
    print("=" * 60)


if __name__ == "__main__":
    main()
