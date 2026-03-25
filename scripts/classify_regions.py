#!/usr/bin/env python3
"""
scripts/classify_regions.py
============================
Uses the Claude API to automatically classify restaurant regions
for any row in your Google Sheet where column J = "NEEDS CLASSIFICATION".

For each unclassified restaurant it sends:
  - Restaurant name (English + Chinese if available)
  - City
  - Yelp-detected dishes (columns AA-AC)
  - Existing notes
  ...to Claude, which returns region + subregion + confidence.

Low-confidence results are written with the best guess AND the entire row
is highlighted yellow in the Sheet so you can spot-check them easily.

INSTALL:
  pip install anthropic gspread google-auth

USAGE:
  # Classify all unclassified rows
  python scripts/classify_regions.py

  # Preview what it would classify without writing to Sheet
  python scripts/classify_regions.py --dry-run

  # Re-classify everything, even already-classified rows
  python scripts/classify_regions.py --reclassify

  # Process only first N rows (for testing)
  python scripts/classify_regions.py --limit 10

ENV VARS:
  ANTHROPIC_API_KEY               Required
  GOOGLE_SERVICE_ACCOUNT_JSON     Required
  SPREADSHEET_ID                  Required
"""

import os, sys, json, time, argparse, re
import anthropic
import gspread
from google.oauth2.service_account import Credentials

# ── Config ─────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
SA_JSON            = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID     = os.environ.get("SPREADSHEET_ID", "")

# Delay between Claude API calls (be gentle, avoid rate limits)
API_DELAY = 0.5  # seconds

# Column indices (0-based) in the Sheet
COL_ID         = 0   # A
COL_NAME_EN    = 1   # B
COL_NAME_ZH    = 2   # C
COL_STATUS     = 3   # D
COL_CITY       = 5   # F
COL_REGION     = 9   # J
COL_SUBREGION  = 10  # K
COL_PROVINCE   = 11  # L
COL_DISH1      = 26  # AA
COL_DISH2      = 27  # AB
COL_DISH3      = 28  # AC
COL_NOTES      = 36  # AK
# Enriched dish columns (from Google reviews + Claude)
COL_DISH1_NAME = 41  # AP
COL_DISH2_NAME = 44  # AS
COL_DISH3_NAME = 47  # AV
# Confidence is stored inline in the AI notes field: [AI: ... confidence:high]

# ── The 12 valid regions and their subregions ──────────────────────────────────
TAXONOMY = {
    "Cantonese": [
        "Classic Cantonese",
        "Cantonese Seafood Banquet",
        "Cantonese Roast & BBQ",
        "Dim Sum & Yum Cha",
        "Congee & Noodle Shop",
    ],
    "Teochew": [
        "Teochew Noodles",
        "Teochew Seafood",
        "Teochew Hot Pot",
    ],
    "Hong Kong": [
        "Cha Chaan Teng",
        "Hong Kong Cafe",
        "Hong Kong Seafood",
        "Milk Tea & Toast",
    ],
    "Fujianese / Min": [
        "Fuzhou (Northern Min)",
        "Hokkien / Southern Min",
        "Hakka",
    ],
    "Shanghainese": [
        "Shanghai Classic",
        "Soup Dumplings (XLB)",
        "Shanghainese Noodles",
    ],
    "Sichuan": [
        "Chengdu Classic",
        "Chongqing Style",
        "Sichuan Hot Pot",
        "Sichuan Cold Dishes",
    ],
    "Hunan": [
        "Classic Hunan",
        "Hunan Farmhouse",
    ],
    "Northern Chinese": [
        "Beijing & Imperial Court",
        "Northeastern / Dongbei",
        "Hand-Pulled Noodles",
        "Dumplings & Pancakes",
    ],
    "Northwestern": [
        "Shaanxi / Xi'an",
        "Xinjiang / Uyghur (Halal)",
        "Gansu / Lanzhou Beef Noodle",
    ],
    "Taiwanese": [
        "Classic Taiwanese",
        "Taiwanese Beef Noodle",
        "Taiwanese Night Market",
        "Bubble Tea & Dessert",
    ],
    "Southwestern": [
        "Yunnan",
        "Guizhou",
        "Hainan / Southeast Asian-Chinese",
    ],
    "Modern & Fusion": [
        "Contemporary Chinese",
        "Chinese-American",
        "Pan-Asian",
        "Chinese Dessert Shop",
        "Bakery & Pastry",
    ],
}

TAXONOMY_TEXT = "\n".join(
    f"  {region}:\n" + "\n".join(f"    - {s}" for s in subs)
    for region, subs in TAXONOMY.items()
)

# ── Claude API call ────────────────────────────────────────────────────────────

def classify_restaurant(client, name_en, name_zh, city, dishes, notes):
    """
    Send restaurant info to Claude and get back region + subregion + confidence.
    Returns (region, subregion, confidence, reasoning) tuple.

    Improvements over v1:
    - Chinese name is emphasized as the primary signal (e.g. 成都小吃 → Sichuan is unambiguous)
    - All enriched dish names are included (not just manual columns)
    - No keyword fallback — always uses Claude for accuracy
    """
    dish_str = ", ".join(filter(None, dishes)) or "unknown"

    prompt = f"""You are an expert on Chinese regional cuisine, specifically the San Gabriel Valley restaurant scene in Los Angeles.

Classify this restaurant into the correct region and subregion from the taxonomy below.

RESTAURANT:
  English name: {name_en}
  Chinese name: {name_zh or "(not available)"}
  City: {city}, CA
  Dishes from reviews: {dish_str}
  Notes: {notes or "none"}

IMPORTANT: The Chinese name is often the single most reliable classification signal.
Examples: 成都 (Chengdu) → Sichuan, 潮州/汕头 (Chaoshan) → Teochew, 上海 (Shanghai) → Shanghainese,
新疆/维吾尔 → Northwestern, 港式 (Hong Kong style) → Hong Kong, 台灣/台式 → Taiwanese,
兰州拉面 → Northwestern (Lanzhou noodle), 北京烤鸭 → Northern Chinese, 云南 → Southwestern

VALID TAXONOMY (you must choose from these exactly):
{TAXONOMY_TEXT}

Respond with JSON only, no other text:
{{
  "region": "exact region name from taxonomy",
  "subregion": "exact subregion name from taxonomy",
  "confidence": "high" | "medium" | "low",
  "reasoning": "one sentence explaining your classification, citing the strongest evidence"
}}

Rules:
- region and subregion must be copied exactly from the taxonomy above
- "high": Chinese name, specific regional dishes, or description make it unambiguous
- "medium": good indicators but could be another region
- "low": generic name, no specific regional signals — flag for manual review
- Only default to Cantonese if truly no other signals exist (do not over-apply this default)"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    # Strip any accidental markdown fences
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(raw)
        region    = data.get("region", "Cantonese")
        subregion = data.get("subregion", "Classic Cantonese")
        confidence = data.get("confidence", "low")
        reasoning  = data.get("reasoning", "")

        # Validate against taxonomy
        if region not in TAXONOMY:
            region    = "Cantonese"
            subregion = "Classic Cantonese"
            confidence = "low"
            reasoning  = f"Invalid region returned, defaulted. Original: {raw[:100]}"
        elif subregion not in TAXONOMY.get(region, []):
            # Region is valid but subregion isn't — use first subregion for that region
            subregion  = TAXONOMY[region][0]
            confidence = "low"
            reasoning  = reasoning + " (subregion corrected)"

        return region, subregion, confidence, reasoning

    except json.JSONDecodeError:
        return "Cantonese", "Classic Cantonese", "low", f"Parse error: {raw[:100]}"


# ── Google Sheets ──────────────────────────────────────────────────────────────

def get_sheet():
    creds = Credentials.from_service_account_info(
        json.loads(SA_JSON),
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
    )
    gc = gspread.Client(auth=creds)
    return gc.open_by_key(SPREADSHEET_ID).worksheet("Restaurants")

def safe_get(row, idx, default=""):
    try:
        return row[idx].strip() if idx < len(row) else default
    except Exception:
        return default


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI region classifier for 626 Eats")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Preview classifications without writing to Sheet")
    parser.add_argument("--reclassify",    action="store_true",
                        help="Re-classify ALL rows (even already-classified)")
    parser.add_argument("--reclass-low",   action="store_true",
                        help="Re-classify rows with low confidence (have '[AI:' and 'confidence:low')")
    parser.add_argument("--reclass-medium", action="store_true",
                        help="Re-classify rows with low or medium confidence")
    parser.add_argument("--limit",         type=int, default=0,
                        help="Only process first N rows (for testing)")
    args = parser.parse_args()

    # Validate env vars
    missing = []
    if not ANTHROPIC_API_KEY:  missing.append("ANTHROPIC_API_KEY")
    if not SA_JSON:            missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not SPREADSHEET_ID:     missing.append("SPREADSHEET_ID")
    if missing:
        sys.exit(f"ERROR: Missing env vars: {', '.join(missing)}")

    print("Connecting to Google Sheet...")
    ws   = get_sheet()
    rows = ws.get_all_values()

    DATA_START = 3  # Row 4 in Sheet (0-indexed row 3) is first data row

    # Find rows to classify
    to_classify = []
    for i, row in enumerate(rows[DATA_START:], start=DATA_START):
        status  = safe_get(row, COL_STATUS)
        region  = safe_get(row, COL_REGION)
        name_en = safe_get(row, COL_NAME_EN)
        notes   = safe_get(row, COL_NOTES)

        if not name_en or status.upper() in ("CLOSED", "PERMANENTLY CLOSED"):
            continue

        needs = region == "NEEDS CLASSIFICATION"
        is_low    = "[AI:" in notes and "confidence:low"    in notes.lower()
        is_medium = "[AI:" in notes and "confidence:medium" in notes.lower()

        if (needs
                or args.reclassify
                or (args.reclass_low    and (is_low or needs))
                or (args.reclass_medium and (is_low or is_medium or needs))):
            to_classify.append((i, row))
        if args.limit and len(to_classify) >= args.limit:
            break

    total = len(to_classify)
    if total == 0:
        print("No rows need classification. Use --reclassify to re-run all.")
        return

    print(f"Found {total} rows to classify")
    if args.dry_run:
        print("DRY RUN — no changes will be written\n")

    # Initialize Claude client
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    results = {
        "high":   0,
        "medium": 0,
        "low":    0,
        "errors": 0,
    }

    for idx, (sheet_row_idx, row) in enumerate(to_classify):
        name_en  = safe_get(row, COL_NAME_EN)
        name_zh  = safe_get(row, COL_NAME_ZH)
        city     = safe_get(row, COL_CITY)
        # Use both manual dish columns AND enriched review dishes (deduped)
        dishes_raw = [
            safe_get(row, COL_DISH1), safe_get(row, COL_DISH2), safe_get(row, COL_DISH3),
            safe_get(row, COL_DISH1_NAME), safe_get(row, COL_DISH2_NAME), safe_get(row, COL_DISH3_NAME),
        ]
        seen_d = set()
        dishes = []
        for d in dishes_raw:
            if d and d not in seen_d:
                seen_d.add(d); dishes.append(d)
        notes    = safe_get(row, COL_NOTES)

        print(f"  [{idx+1}/{total}] {name_en} ({city})", end=" ... ", flush=True)

        try:
            region, subregion, confidence, reasoning = classify_restaurant(
                client, name_en, name_zh, city, dishes, notes
            )
        except Exception as e:
            print(f"ERROR: {e}")
            results["errors"] += 1
            continue

        results[confidence] = results.get(confidence, 0) + 1
        conf_tag = {"high": "✓", "medium": "~", "low": "?"}[confidence]
        print(f"{conf_tag} {region} / {subregion}")

        if not args.dry_run:
            # Build updated notes with AI reasoning
            new_notes = notes
            reasoning_note = f"[AI: {reasoning} confidence:{confidence}]"
            if "[AI:" in new_notes:
                new_notes = re.sub(r"\[AI:.*?\]", reasoning_note, new_notes)
            else:
                new_notes = (new_notes + " " + reasoning_note).strip()

            # Write region, subregion, notes back to Sheet
            # gspread uses 1-based row/col
            sheet_row_1based = sheet_row_idx + 1
            ws.update_cell(sheet_row_1based, COL_REGION    + 1, region)
            ws.update_cell(sheet_row_1based, COL_SUBREGION + 1, subregion)
            ws.update_cell(sheet_row_1based, COL_NOTES     + 1, new_notes)

            # Highlight low-confidence rows yellow
            if confidence == "low":
                # A1 notation for the full row (columns A through AO = 1 to 41)
                row_range = f"A{sheet_row_1based}:AO{sheet_row_1based}"
                ws.format(row_range, {
                    "backgroundColor": {
                        "red":   1.0,
                        "green": 0.95,
                        "blue":  0.4,
                    }
                })

            # Throttle to avoid Sheets API rate limits
            time.sleep(0.3)

        time.sleep(API_DELAY)

    # Summary
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Classification complete!")
    print(f"  High confidence:   {results['high']}")
    print(f"  Medium confidence: {results['medium']}")
    print(f"  Low confidence:    {results['low']}  ← review these")
    print(f"  Errors:            {results['errors']}")

    if not args.dry_run and results["low"] > 0:
        low_count = results["low"]
        print(f"\nTip: {low_count} rows are highlighted yellow in your Sheet.")
        print(f"  Review those and correct the region/subregion if needed.")


if __name__ == "__main__":
    main()
