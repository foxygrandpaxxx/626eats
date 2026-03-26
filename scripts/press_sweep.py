#!/usr/bin/env python3
"""
scripts/press_sweep.py
----------------------
Finds press coverage for each restaurant using Claude's built-in web search tool.
Claude searches Eater LA, LA Times, Timeout, The Infatuation, etc., reads the
actual article pages, and extracts structured data — no separate search API needed.

For each restaurant, Claude:
  1. Searches food publications for reviews and features
  2. Reads the full article text (not just snippets)
  3. Extracts: publication, headline, excerpt, dishes mentioned, date, sentiment

Results are written to a "Press" sheet in Google Sheets:
  press_id | rest_id | rest_name | publication | headline | url |
  dish_mentioned | excerpt | date | date_swept | sentiment

Required secrets:
  GOOGLE_SERVICE_ACCOUNT_JSON  – service account JSON
  SPREADSHEET_ID               – Google Sheets spreadsheet ID
  ANTHROPIC_API_KEY            – Claude API key (uses web search + claude-3-5-haiku)
                                 Cost: ~$0.50–$1.00 for all 273 restaurants

Local usage:
  GOOGLE_SERVICE_ACCOUNT_JSON='...' SPREADSHEET_ID=... ANTHROPIC_API_KEY=... python scripts/press_sweep.py

Options:
  --all                  Re-sweep all restaurants (default: only unsearched ones)
  --restaurant NAME      Sweep a single restaurant by name (partial match)

GitHub Actions trigger:
  workflow_dispatch (manual). Re-run quarterly or after adding new restaurants.
"""

import os, json, time, re, sys
import urllib.request
from datetime import datetime

SA_JSON        = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")

DATA_START = 3  # 0-indexed row offset in get_all_values()

TARGET_PUBLICATIONS = [
    "Eater LA (eater.com)",
    "Los Angeles Times (latimes.com)",
    "Timeout Los Angeles (timeout.com)",
    "The Infatuation (theinfatuation.com)",
    "Thrillist (thrillist.com)",
    "LA Weekly (laweekly.com)",
    "Los Angeles Magazine (lamag.com)",
    "Yelp Blog (yelp.com/blog)",
    "Food & Wine (foodandwine.com)",
    "Bon Appétit (bonappetit.com)",
]


def get_sheets_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        raise ImportError("Run: pip install gspread google-auth")
    if not SA_JSON:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    creds = Credentials.from_service_account_info(
        json.loads(SA_JSON),
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return gspread.Client(auth=creds)


def search_press_with_claude(rest_name, city, existing_urls):
    """
    Ask Claude to use its web search tool to find press coverage for a restaurant.
    Claude reads the actual articles and extracts structured data directly.

    Returns list of dicts: {publication, headline, url, excerpt, dish_mentioned, date, sentiment}
    """
    if not ANTHROPIC_KEY:
        return []

    publications_str = "\n".join(f"  - {p}" for p in TARGET_PUBLICATIONS)

    prompt = f"""I need to find press coverage for a restaurant called "{rest_name}" located in {city}, California (San Gabriel Valley area).

Please search these food publications for articles, reviews, or features about this specific restaurant:
{publications_str}

Search tips:
- Try: "{rest_name}" site:eater.com
- Try: "{rest_name}" SGV restaurant review
- Try: "{rest_name}" San Gabriel Valley food
- Also try variations if the restaurant name has Chinese characters

For each article you find that is genuinely about "{rest_name}" (not a different restaurant):
1. Read the article content
2. Extract a compelling 2-3 sentence excerpt that captures what the writer loves about the restaurant
3. Note any specific dishes mentioned by name
4. Determine the publication and date

Skip: Yelp listings, Google Maps pages, delivery app pages, articles that only briefly mention the restaurant in a list.

Already-found URLs to skip (don't re-report these):
{chr(10).join(existing_urls) if existing_urls else "(none yet)"}

Return a JSON array with this structure:
[
  {{
    "publication": "Eater LA",
    "headline": "The Best Dan Dan Noodles in LA Are in the SGV",
    "url": "https://...",
    "excerpt": "2-3 sentences from the article about this restaurant specifically.",
    "dish_mentioned": "dan dan noodles",
    "date": "2024-03-15",
    "sentiment": "rave"
  }}
]

sentiment options: "rave" (enthusiastic praise), "positive" (favorable), "mixed", "neutral" (informational only)

If you find no legitimate press coverage, return an empty array: []

Return only the JSON array, no other text."""

    payload = json.dumps({
        "model": "claude-haiku-4-5",
        "max_tokens": 2048,
        "tools": [
            {"type": "web_search_20250305", "name": "web_search"}
        ],
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "web-search-2025-03-05",
            "content-type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())

        # Find the final text response (after any tool use)
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text = block["text"].strip()

        if not text:
            return []

        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            results = json.loads(match.group())
            # Filter out already-seen URLs
            return [r for r in results if r.get("url") not in existing_urls]

    except Exception as e:
        print(f"    Claude error: {e}")

    return []


def get_or_create_press_sheet(spreadsheet):
    """Get or create the Press worksheet with headers."""
    try:
        return spreadsheet.worksheet("Press")
    except Exception:
        ws = spreadsheet.add_worksheet(title="Press", rows=2000, cols=20)
        ws.append_row([
            "press_id", "rest_id", "rest_name", "publication", "headline",
            "url", "dish_mentioned", "excerpt", "date", "date_swept", "sentiment",
        ])
        return ws


def load_existing_press(press_ws):
    """Return set of already-recorded URLs and max press_id counter."""
    rows = press_ws.get_all_values()
    existing_urls = set()
    max_id = 0
    for row in rows[1:]:  # skip header
        if len(row) >= 6 and row[5]:
            existing_urls.add(row[5].strip())
        if row and row[0] and row[0].startswith("P"):
            try:
                max_id = max(max_id, int(row[0][1:]))
            except ValueError:
                pass
    return existing_urls, max_id


def run_sweep(filter_name=None, sweep_all=False):
    if not ANTHROPIC_KEY:
        print("ANTHROPIC_API_KEY not set.")
        return

    print("Connecting to Google Sheets...")
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    rest_ws = spreadsheet.worksheet("Restaurants")
    press_ws = get_or_create_press_sheet(spreadsheet)

    existing_urls, press_id_counter = load_existing_press(press_ws)
    rows = rest_ws.get_all_values()

    today = datetime.utcnow().strftime("%Y-%m-%d")
    new_rows = []
    swept = 0
    found = 0

    for i, row in enumerate(rows[DATA_START:], start=1):
        def get_cell(col):
            idx = col - 1
            return row[idx] if idx < len(row) else ''

        rest_id = get_cell(1)
        name    = get_cell(2)
        city    = get_cell(6)

        if not rest_id or not name:
            continue
        if filter_name and filter_name.lower() not in name.lower():
            continue

        print(f"[{i}] {name} ({city or 'SGV'})")
        swept += 1

        # Pass a limited set of existing URLs to avoid re-reporting
        results = search_press_with_claude(name, city or "San Gabriel Valley", existing_urls)

        if results:
            for r in results:
                url = r.get("url", "")
                if not url or url in existing_urls:
                    continue
                press_id_counter += 1
                new_rows.append([
                    f"P{press_id_counter:04d}",
                    rest_id,
                    name,
                    r.get("publication", ""),
                    r.get("headline", ""),
                    url,
                    r.get("dish_mentioned", ""),
                    r.get("excerpt", ""),
                    r.get("date", ""),
                    today,
                    r.get("sentiment", ""),
                ])
                existing_urls.add(url)
                sentiment_label = r.get("sentiment", "")
                print(f"  ✓ [{sentiment_label}] {r.get('publication','')}: {r.get('headline','')[:65]}")
                found += 1
        else:
            print("  No press coverage found.")

        # Batch-write every 20 restaurants to avoid losing data if interrupted
        if len(new_rows) >= 20:
            press_ws.append_rows(new_rows)
            print(f"  (Saved {len(new_rows)} entries to sheet)")
            new_rows = []

        time.sleep(1.5)  # Respect rate limits

    if new_rows:
        press_ws.append_rows(new_rows)

    print(f"\nDone. Swept {swept} restaurants, found {found} new press entries.")


if __name__ == "__main__":
    filter_name = None
    sweep_all = "--all" in sys.argv
    args = sys.argv[1:]
    for j, arg in enumerate(args):
        if arg == "--restaurant" and j + 1 < len(args):
            filter_name = args[j + 1]
    run_sweep(filter_name=filter_name, sweep_all=sweep_all)
