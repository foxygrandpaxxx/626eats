#!/usr/bin/env python3
"""
scripts/dish_harvest.py
------------------------
For every restaurant in the Sheet, extracts a structured list of its top dishes
using Claude Haiku and the rich data fetched by refresh_places_data.py.

For each dish extracts:
  name         — English name
  chinese      — Simplified Chinese characters (e.g. 麻婆豆腐)
  pinyin       — Pinyin with tone marks (e.g. má pó dòu fu)
  isSignature  — true if specifically praised as must-order or highlighted
  category     — One of: Noodles | Dumplings | Dim Sum | Rice Dishes | Hot Pot |
                  BBQ & Roasted | Seafood | Soup | Cold Dishes | Dessert |
                  Bread & Pastry | Tofu & Vegetables | Meat & Poultry | Drink | Other
  description  — Optional 10-20 word description if evident from source text

Special rules:
  - Hot pot restaurants: extract SOUP BASE STYLES only (not ingredient lists)
  - Non-dishes (ingredients, cooking styles, vague names) are excluded
  - Dishes are de-duplicated (same dish with slightly different English = one entry)
  - Maximum 15 dishes per restaurant

Writes results to:
  BH (60): top_dishes_json — JSON array of dish objects

Also back-fills:
  AA (27): dish1 — top dish name (for backwards compatibility with existing export)
  AB (28): dish2 — second dish name
  AC (29): dish3 — third dish name

Data sources (in priority order):
  1. generative_summary (BD) — Gemini AI overview, often names popular dishes
  2. review_summary (BE)     — AI synthesis of reviews
  3. editorial_summary (BF)  — Short description
  4. reviews_json (BG)       — Up to 5 full user reviews

Skips restaurants that already have top_dishes_json with 5+ dishes,
unless FORCE_HARVEST=true.

Required secrets:
  ANTHROPIC_API_KEY            — Claude API key
  GOOGLE_SERVICE_ACCOUNT_JSON  — Service account JSON (for Sheets access)
  SPREADSHEET_ID               — Google Sheet ID

Optional:
  SA_PATH          — Path to service account JSON file
  FORCE_HARVEST    — Set "true" to re-process all restaurants
  BATCH_SIZE       — Restaurants per Claude call (default: 15)

Cost: ~$0.10 total using Claude Haiku with batching.
"""

import os, json, re, time, sys
import urllib.request
import urllib.error

# ── Config ───────────────────────────────────────────────────────────────────
SA_JSON         = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SA_PATH         = os.environ.get("SA_PATH", "")
SPREADSHEET_ID  = os.environ.get("SPREADSHEET_ID", "")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
FORCE_HARVEST   = os.environ.get("FORCE_HARVEST", "false").lower() == "true"
BATCH_SIZE      = int(os.environ.get("BATCH_SIZE", "15"))

# Claude model — use Haiku for cost efficiency (dish extraction is straightforward)
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# ── Column positions (1-based) ────────────────────────────────────────────────
COL_ID                 = 1   # A
COL_NAME               = 2   # B
COL_DISH1              = 27  # AA  (backwards compat)
COL_DISH2              = 28  # AB
COL_DISH3              = 29  # AC
COL_GENERATIVE_SUMMARY = 56  # BD
COL_REVIEW_SUMMARY     = 57  # BE
COL_EDITORIAL_SUMMARY  = 58  # BF
COL_REVIEWS_JSON       = 59  # BG
COL_TOP_DISHES_JSON    = 60  # BH

DATA_START = 3

VALID_CATEGORIES = {
    "Noodles", "Dumplings", "Dim Sum", "Rice Dishes", "Hot Pot",
    "BBQ & Roasted", "Seafood", "Soup", "Cold Dishes", "Dessert",
    "Bread & Pastry", "Tofu & Vegetables", "Meat & Poultry", "Drink", "Other"
}


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


def build_restaurant_context(name, gen_summary, rev_summary, ed_summary, reviews_json_str):
    """
    Build a concise text block for one restaurant to send to Claude.
    Combines all available data sources, prioritizing the most informative.
    """
    parts = []
    if gen_summary:
        parts.append(f"[Google AI Overview] {gen_summary}")
    if rev_summary:
        parts.append(f"[Review Summary] {rev_summary}")
    if ed_summary:
        parts.append(f"[Description] {ed_summary}")

    if reviews_json_str:
        try:
            reviews = json.loads(reviews_json_str)
            for j, r in enumerate(reviews[:5], 1):
                text = r.get("text", "").strip()
                if text:
                    parts.append(f"[Review {j}] {text[:400]}")
        except (json.JSONDecodeError, TypeError):
            pass

    if not parts:
        return None  # No data available

    return "\n".join(parts)


def claude_extract_dishes(batch):
    """
    Send a batch of restaurants to Claude Haiku for dish extraction.

    batch: list of {"id": ..., "name": ..., "context": ...}
    Returns: dict of {restaurant_id: [dish_obj, ...]}
    """
    if not ANTHROPIC_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set")

    # Build the prompt
    restaurants_block = ""
    for item in batch:
        restaurants_block += f"\n---\nRESTAURANT ID: {item['id']}\nNAME: {item['name']}\n{item['context']}\n"

    prompt = f"""You are a Chinese culinary expert. Extract the top dishes from each restaurant's text below.

CRITICAL RULES:
1. Only include REAL ORDERABLE DISHES — items a diner orders and receives as food
2. DO NOT include: raw ingredients, condiments, vague phrases like "house special" or "seasonal items", or cooking methods
3. HOT POT restaurants: extract ONLY the soup base STYLES (e.g. "Sichuan Mala Broth", "Mushroom Broth", "Clear Bone Broth") — NOT individual add-in ingredients like "beef slices" or "tofu"
4. Chinese characters must be Simplified Chinese (简体字)
5. Pinyin must include tone marks (e.g. má pó dòu fu, not ma po dou fu)
6. isSignature = true ONLY when a dish is specifically praised, called "must-order", "famous for", "popular", or highlighted by name
7. Maximum 15 dishes per restaurant. Minimum 3 if any dish information is available.
8. If two dish names refer to the same dish (e.g. "Mapo Tofu" and "Ma Po Tofu"), output only one entry
9. category MUST be exactly one of: Noodles | Dumplings | Dim Sum | Rice Dishes | Hot Pot | BBQ & Roasted | Seafood | Soup | Cold Dishes | Dessert | Bread & Pastry | Tofu & Vegetables | Meat & Poultry | Drink | Other

RESTAURANTS:
{restaurants_block}

Respond with ONLY a valid JSON object in this exact format (no other text, no markdown):
{{
  "results": [
    {{
      "id": "restaurant_id_here",
      "dishes": [
        {{
          "name": "Mapo Tofu",
          "chinese": "麻婆豆腐",
          "pinyin": "má pó dòu fu",
          "isSignature": true,
          "category": "Tofu & Vegetables"
        }},
        {{
          "name": "Dan Dan Noodles",
          "chinese": "担担面",
          "pinyin": "dàn dàn miàn",
          "isSignature": false,
          "category": "Noodles"
        }}
      ]
    }}
  ]
}}"""

    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 6000,
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
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Claude API error {e.code}: {body[:300]}")

    text = data["content"][0]["text"].strip()

    # Extract JSON from response (handle any accidental markdown wrapping)
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        raise ValueError(f"No JSON found in Claude response: {text[:300]}")

    parsed = json.loads(json_match.group())
    results = {}
    for item in parsed.get("results", []):
        rid = str(item.get("id", "")).strip()
        dishes = item.get("dishes", [])
        # Validate and sanitize each dish
        clean_dishes = []
        for d in dishes:
            name = str(d.get("name", "")).strip()
            if not name:
                continue
            cat = d.get("category", "Other")
            if cat not in VALID_CATEGORIES:
                cat = "Other"
            clean_dishes.append({
                "name": name,
                "chinese": str(d.get("chinese", "")).strip(),
                "pinyin": str(d.get("pinyin", "")).strip(),
                "isSignature": bool(d.get("isSignature", False)),
                "category": cat,
            })
        if rid:
            results[rid] = clean_dishes

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

    # Collect restaurants to process
    to_process = []  # list of {"id", "name", "context", "spreadsheet_row"}
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

        # Skip if already has enough dishes (unless FORCE_HARVEST)
        if not FORCE_HARVEST:
            existing_json = get_cell(COL_TOP_DISHES_JSON)
            if existing_json:
                try:
                    existing_dishes = json.loads(existing_json)
                    if len(existing_dishes) >= 5:
                        skipped += 1
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass  # malformed JSON — re-process

        context = build_restaurant_context(
            name,
            get_cell(COL_GENERATIVE_SUMMARY),
            get_cell(COL_REVIEW_SUMMARY),
            get_cell(COL_EDITORIAL_SUMMARY),
            get_cell(COL_REVIEWS_JSON),
        )

        if not context:
            print(f"  Skipping {name} — no source data (run refresh_places_data.py first)")
            skipped += 1
            continue

        to_process.append({
            "id": rest_id,
            "name": name,
            "context": context,
            "spreadsheet_row": spreadsheet_row,
        })

    print(f"  {len(to_process)} restaurants to process, {skipped} skipped (already done).")

    if not to_process:
        print("Nothing to do. Use FORCE_HARVEST=true to re-process all.")
        return

    # Process in batches
    updates = []
    ok_count  = 0
    err_count = 0
    total_batches = (len(to_process) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(0, len(to_process), BATCH_SIZE):
        batch = to_process[batch_idx:batch_idx + BATCH_SIZE]
        batch_num = batch_idx // BATCH_SIZE + 1
        print(f"\nBatch {batch_num}/{total_batches} ({len(batch)} restaurants)...")

        try:
            dish_results = claude_extract_dishes(batch)
        except Exception as e:
            print(f"  ✗ Claude API error: {e}")
            err_count += len(batch)
            time.sleep(5)
            continue

        for item in batch:
            rid = item["id"]
            srow = item["spreadsheet_row"]
            name = item["name"]
            dishes = dish_results.get(rid, [])

            if not dishes:
                print(f"  ✗ {name}: no dishes returned")
                err_count += 1
                continue

            dishes_json = json.dumps(dishes, ensure_ascii=False)
            updates.append((srow, COL_TOP_DISHES_JSON, dishes_json))

            # Back-fill dish1/2/3 with top signature dishes first, then any
            sig_dishes = [d["name"] for d in dishes if d.get("isSignature")]
            all_names  = [d["name"] for d in dishes]
            ordered    = sig_dishes + [n for n in all_names if n not in sig_dishes]

            updates.append((srow, COL_DISH1, ordered[0] if len(ordered) > 0 else ""))
            updates.append((srow, COL_DISH2, ordered[1] if len(ordered) > 1 else ""))
            updates.append((srow, COL_DISH3, ordered[2] if len(ordered) > 2 else ""))

            sig_count = sum(1 for d in dishes if d.get("isSignature"))
            print(f"  ✓ {name}: {len(dishes)} dishes ({sig_count} signature)")
            ok_count += 1

        # Flush every batch
        if updates:
            _flush_updates(ws, updates)
            updates = []

        time.sleep(1.5)  # be polite to Claude API between batches

    print(f"\n{'─'*50}")
    print(f"Done.  Processed: {ok_count}  Errors: {err_count}  Skipped: {skipped}")


def _flush_updates(ws, updates):
    if not updates:
        return
    batch = [
        {"range": f"{col_letter(col)}{row}", "values": [[val]]}
        for row, col, val in updates
    ]
    print(f"  → Writing {len(updates)} cells...", end=" ", flush=True)
    for chunk_start in range(0, len(batch), 100):
        ws.batch_update(batch[chunk_start:chunk_start + 100])
        time.sleep(0.3)
    print("done.")


if __name__ == "__main__":
    run()
