#!/usr/bin/env python3
"""
scripts/reclassify_restaurants.py
-----------------------------------
Re-classifies every restaurant in the Sheet into the new 20-region taxonomy
using Claude Sonnet. Sonnet is used (not Haiku) because cuisine classification
requires nuanced judgment — distinguishing Teochew from Cantonese, or knowing
that Lanzhou beef noodle shops are Northwest not Sichuan.

Uses as evidence (in order of reliability):
  1. top_dishes_json (BH) — extracted dish list with categories
  2. generative_summary (BD) — Gemini AI overview
  3. review_summary (BE) — AI synthesis of reviews
  4. editorial_summary (BF) — short description
  5. Existing region/category fields — as a starting hint (not definitive)

Writes results to:
  BI (61): cuisine_primary    — taxonomy region ID (e.g. "sichuan")
  BJ (62): cuisine_secondary  — optional second cuisine ID, or empty

Skips restaurants already classified unless FORCE_RECLASSIFY=true.

Required secrets:
  ANTHROPIC_API_KEY            — Claude API key
  GOOGLE_SERVICE_ACCOUNT_JSON  — Service account JSON
  SPREADSHEET_ID               — Google Sheet ID

Optional:
  SA_PATH           — Path to service account JSON file
  FORCE_RECLASSIFY  — Set "true" to re-classify all restaurants
  BATCH_SIZE        — Restaurants per Sonnet call (default: 10)

Cost: ~$0.50 total using Claude Sonnet with batching.
"""

import os, json, re, time, sys
import urllib.request
import urllib.error

# ── Config ───────────────────────────────────────────────────────────────────
SA_JSON           = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SA_PATH           = os.environ.get("SA_PATH", "")
SPREADSHEET_ID    = os.environ.get("SPREADSHEET_ID", "")
ANTHROPIC_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
FORCE_RECLASSIFY  = os.environ.get("FORCE_RECLASSIFY", "false").lower() == "true"
BATCH_SIZE        = int(os.environ.get("BATCH_SIZE", "10"))

# Claude Sonnet for classification quality
CLAUDE_MODEL = "claude-sonnet-4-5-20251001"

# ── Column positions (1-based) ────────────────────────────────────────────────
COL_ID                 = 1   # A
COL_NAME               = 2   # B
COL_NAME_CN            = 3   # C
COL_REGION             = 10  # J  (existing region field — hint only)
COL_CATEGORY           = 14  # N  (existing category field — hint only)
COL_GENERATIVE_SUMMARY = 56  # BD
COL_REVIEW_SUMMARY     = 57  # BE
COL_EDITORIAL_SUMMARY  = 58  # BF
COL_TOP_DISHES_JSON    = 60  # BH
COL_CUISINE_PRIMARY    = 61  # BI
COL_CUISINE_SECONDARY  = 62  # BJ

DATA_START = 3

# Load taxonomy once at module level
_TAXONOMY_PATH = os.path.join(os.path.dirname(__file__), "taxonomy.json")
with open(_TAXONOMY_PATH, "r", encoding="utf-8") as _f:
    _TAXONOMY = json.load(_f)

# Valid taxonomy IDs for validation
VALID_IDS = {r["id"] for r in _TAXONOMY["regions"]}

# Build compact taxonomy reference for the prompt (just id, name, chinese, key dishes)
TAXONOMY_PROMPT_BLOCK = "VALID CUISINE IDs (use EXACTLY these strings):\n"
for r in _TAXONOMY["regions"]:
    TAXONOMY_PROMPT_BLOCK += (
        f'  "{r["id"]}" — {r["name"]} ({r["chinese"]}): '
        f'{", ".join(r["keyDishes"][:4])}\n'
    )


def col_letter(n):
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


def format_dishes_for_prompt(dishes_json_str):
    """Format top_dishes_json into a readable dish list for the prompt."""
    if not dishes_json_str:
        return ""
    try:
        dishes = json.loads(dishes_json_str)
        if not dishes:
            return ""
        lines = []
        for d in dishes[:12]:  # limit to 12 dishes in prompt
            sig = "★" if d.get("isSignature") else " "
            lines.append(f"  {sig} {d.get('name', '')} ({d.get('chinese', '')}) [{d.get('category', '')}]")
        return "Top dishes:\n" + "\n".join(lines)
    except (json.JSONDecodeError, TypeError):
        return ""


def claude_classify_batch(batch):
    """
    Classify a batch of restaurants using Claude Sonnet.

    batch: list of {"id", "name", "name_cn", "hint_region", "hint_category",
                    "gen_summary", "rev_summary", "ed_summary", "dishes_text"}
    Returns: dict of {restaurant_id: {"primary": id, "secondary": id|None, "reasoning": str}}
    """
    if not ANTHROPIC_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set")

    restaurants_block = ""
    for item in batch:
        restaurants_block += f"\n---\nID: {item['id']}\n"
        restaurants_block += f"Name: {item['name']}"
        if item.get("name_cn"):
            restaurants_block += f" / {item['name_cn']}"
        restaurants_block += "\n"
        if item.get("hint_region"):
            restaurants_block += f"Current region tag (hint only): {item['hint_region']}\n"
        if item.get("gen_summary"):
            restaurants_block += f"Google AI Overview: {item['gen_summary'][:600]}\n"
        if item.get("rev_summary"):
            restaurants_block += f"Review Summary: {item['rev_summary'][:400]}\n"
        if item.get("ed_summary"):
            restaurants_block += f"Description: {item['ed_summary'][:300]}\n"
        if item.get("dishes_text"):
            restaurants_block += f"{item['dishes_text']}\n"

    prompt = f"""You are an expert in Chinese regional cuisines. Classify each restaurant into the correct cuisine category from the taxonomy below.

{TAXONOMY_PROMPT_BLOCK}

CLASSIFICATION RULES:
1. Assign cuisine_primary to the SINGLE best-matching cuisine ID
2. Assign cuisine_secondary ONLY if the restaurant genuinely spans two distinct regional styles at roughly equal prominence (rare — most restaurants focus on one cuisine). Return null otherwise.
3. Use the dish list as the STRONGEST signal — what a restaurant actually serves reveals its cuisine better than its name
4. The Chinese restaurant name often contains strong clues — a name with "川" suggests Sichuan, "粤" suggests Cantonese, "台" suggests Taiwanese, etc.
5. "dim_sum" applies when the restaurant's primary format is yum cha / dim sum service, even if the underlying food is Cantonese
6. "hot_pot" applies when hot pot is the primary format
7. "cantonese_bbq" applies when roasted meats (char siu, roast duck) is the primary product
8. For Lanzhou beef noodle shops → use "shaanxi_lanzhou"
9. For snail noodle (luosifen) shops → use "guizhou_guangxi"
10. When genuinely uncertain, prefer the more specific category over "modern_fusion"

RESTAURANTS TO CLASSIFY:
{restaurants_block}

Respond with ONLY a valid JSON object (no markdown, no other text):
{{
  "results": [
    {{
      "id": "restaurant_id",
      "cuisine_primary": "valid_taxonomy_id",
      "cuisine_secondary": null,
      "reasoning": "1-2 sentences explaining the classification"
    }}
  ]
}}"""

    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 4000,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Claude API error {e.code}: {body[:300]}")

    text = data["content"][0]["text"].strip()
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        raise ValueError(f"No JSON found in Claude response: {text[:300]}")

    parsed = json.loads(json_match.group())
    results = {}
    for item in parsed.get("results", []):
        rid = str(item.get("id", "")).strip()
        primary   = str(item.get("cuisine_primary", "")).strip()
        secondary = item.get("cuisine_secondary")
        if secondary:
            secondary = str(secondary).strip()
        reasoning = str(item.get("reasoning", "")).strip()

        # Validate — reject unknown taxonomy IDs
        if primary not in VALID_IDS:
            print(f"  ⚠ Unknown cuisine_primary '{primary}' for {rid} — defaulting to modern_fusion")
            primary = "modern_fusion"
        if secondary and secondary not in VALID_IDS:
            print(f"  ⚠ Unknown cuisine_secondary '{secondary}' for {rid} — clearing")
            secondary = None
        # Don't let secondary == primary
        if secondary == primary:
            secondary = None

        if rid:
            results[rid] = {
                "primary":   primary,
                "secondary": secondary or "",
                "reasoning": reasoning,
            }

    return results


def run():
    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set.")
        sys.exit(1)
    if not SPREADSHEET_ID:
        print("ERROR: SPREADSHEET_ID not set.")
        sys.exit(1)

    print("Connecting to Google Sheets...")
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet("Restaurants")
    rows = ws.get_all_values()
    print(f"  Loaded {len(rows)} rows.")

    to_process = []
    skipped = 0

    for i, row in enumerate(rows[DATA_START:], start=DATA_START + 1):
        spreadsheet_row = i + 1

        def get_cell(col):
            idx = col - 1
            return row[idx].strip() if idx < len(row) else ''

        rest_id = get_cell(COL_ID)
        name    = get_cell(COL_NAME)

        if not rest_id:
            continue

        # Skip if already classified (unless FORCE_RECLASSIFY)
        if not FORCE_RECLASSIFY:
            existing = get_cell(COL_CUISINE_PRIMARY)
            if existing and existing in VALID_IDS:
                skipped += 1
                continue

        to_process.append({
            "id":           rest_id,
            "name":         name,
            "name_cn":      get_cell(COL_NAME_CN),
            "hint_region":  get_cell(COL_REGION),
            "hint_category":get_cell(COL_CATEGORY),
            "gen_summary":  get_cell(COL_GENERATIVE_SUMMARY),
            "rev_summary":  get_cell(COL_REVIEW_SUMMARY),
            "ed_summary":   get_cell(COL_EDITORIAL_SUMMARY),
            "dishes_text":  format_dishes_for_prompt(get_cell(COL_TOP_DISHES_JSON)),
            "spreadsheet_row": spreadsheet_row,
        })

    print(f"  {len(to_process)} restaurants to classify, {skipped} skipped.")

    if not to_process:
        print("Nothing to do. Use FORCE_RECLASSIFY=true to re-classify all.")
        return

    updates   = []
    ok_count  = 0
    err_count = 0
    total_batches = (len(to_process) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(0, len(to_process), BATCH_SIZE):
        batch = to_process[batch_idx:batch_idx + BATCH_SIZE]
        batch_num = batch_idx // BATCH_SIZE + 1
        print(f"\nBatch {batch_num}/{total_batches} ({len(batch)} restaurants)...")

        try:
            classifications = claude_classify_batch(batch)
        except Exception as e:
            print(f"  ✗ Claude API error: {e}")
            err_count += len(batch)
            time.sleep(5)
            continue

        for item in batch:
            rid  = item["id"]
            srow = item["spreadsheet_row"]
            name = item["name"]
            cls  = classifications.get(rid)

            if not cls:
                print(f"  ✗ {name}: no classification returned")
                err_count += 1
                continue

            updates.append((srow, COL_CUISINE_PRIMARY,   cls["primary"]))
            updates.append((srow, COL_CUISINE_SECONDARY, cls["secondary"]))

            sec_str = f" + {cls['secondary']}" if cls["secondary"] else ""
            print(f"  ✓ {name}: {cls['primary']}{sec_str}  ({cls['reasoning'][:80]}...)")
            ok_count += 1

        if updates:
            _flush_updates(ws, updates)
            updates = []

        time.sleep(2.0)  # Sonnet is slower, give it breathing room

    print(f"\n{'─'*50}")
    print(f"Done.  Classified: {ok_count}  Errors: {err_count}  Skipped: {skipped}")

    # Print a summary breakdown
    if ok_count > 0:
        print("\nNote: Run export_json.py to push updated classifications to the live app.")


def _flush_updates(ws, updates):
    if not updates:
        return
    batch = [
        {"range": f"'{ws.title}'!{col_letter(col)}{row}", "values": [[val]]}
        for row, col, val in updates
    ]
    print(f"  → Writing {len(updates)} cells...", end=" ", flush=True)
    for chunk_start in range(0, len(batch), 100):
        ws.batch_update(batch[chunk_start:chunk_start + 100])
        time.sleep(0.3)
    print("done.")


if __name__ == "__main__":
    run()
