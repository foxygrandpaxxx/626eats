#!/usr/bin/env python3
"""
scripts/press_sweep.py
----------------------
Searches for press coverage (Eater LA, LA Times, Timeout, The Infatuation,
Thrillist, LA Weekly, LA Magazine, etc.) for each restaurant in the spreadsheet.
Uses Serper API for web search + Claude for confirmation and excerpt extraction.

For each restaurant, runs 2 search queries:
  1. "[name]" SGV site:eater.com OR site:latimes.com OR ...
  2. "[name]" "San Gabriel" food review

Claude confirms whether each result is actually about this restaurant,
then extracts: publication, headline, excerpt, dish_mentioned, date.

Results are written to a "Press" sheet in Google Sheets:
  rest_id | rest_name | publication | headline | url | dish_mentioned | excerpt | date | date_swept

Required secrets:
  GOOGLE_SERVICE_ACCOUNT_JSON  – service account JSON
  SPREADSHEET_ID               – Google Sheets spreadsheet ID
  SERPER_API_KEY               – Serper API key (serper.dev — 2,500 free queries on trial)
  ANTHROPIC_API_KEY            – Claude API key for confirmation + extraction

Local usage:
  GOOGLE_SERVICE_ACCOUNT_JSON='...' SPREADSHEET_ID=... SERPER_API_KEY=... ANTHROPIC_API_KEY=... python scripts/press_sweep.py

Options:
  --all         Re-sweep all restaurants (default: only unsearched ones)
  --restaurant  NAME  Sweep a single restaurant by name (partial match)

GitHub Actions trigger:
  workflow_dispatch (manual) or after research_sweep adds new restaurants.
"""

import os, json, time, re, sys
import urllib.request
import urllib.parse
from datetime import datetime

SA_JSON        = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SERPER_KEY     = os.environ.get("SERPER_API_KEY", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")

DATA_START = 3  # 0-indexed row offset in get_all_values()

# Publications to search (ordered by prestige)
TARGET_SITES = [
    "eater.com",
    "latimes.com",
    "timeout.com",
    "theinfatuation.com",
    "thrillist.com",
    "laweekly.com",
    "lamag.com",
    "sfgate.com",
    "yelp.com/blog",
]

SITE_DISPLAY = {
    "eater.com":        "Eater LA",
    "latimes.com":      "LA Times",
    "timeout.com":      "Timeout LA",
    "theinfatuation.com": "The Infatuation",
    "thrillist.com":    "Thrillist",
    "laweekly.com":     "LA Weekly",
    "lamag.com":        "Los Angeles Magazine",
    "sfgate.com":       "SFGate",
    "yelp.com/blog":    "Yelp Blog",
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
    if not SA_JSON:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    creds_dict = json.loads(SA_JSON)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.Client(auth=creds)


def serper_search(query, num=5):
    """Run a Google search via Serper API. Returns list of result dicts."""
    if not SERPER_KEY:
        return []
    payload = json.dumps({"q": query, "num": num, "gl": "us", "hl": "en"}).encode()
    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=payload,
        headers={
            "X-API-KEY": SERPER_KEY,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read())
        return data.get("organic", [])
    except Exception as e:
        print(f"    Serper error: {e}")
        return []


def fetch_article_text(url, max_chars=3000):
    """Attempt to fetch and extract plain text from an article URL."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; 626EatsBot/1.0)",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # Strip tags and collapse whitespace
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except Exception:
        return ""


def claude_confirm_and_extract(rest_name, city, results):
    """
    Use Claude to:
    1. Filter results that are actually about this restaurant
    2. Extract: publication, headline, excerpt (1-2 sentences), dish_mentioned, date

    Returns list of dicts: {publication, headline, url, excerpt, dish_mentioned, date}
    """
    if not ANTHROPIC_KEY or not results:
        return []

    results_text = ""
    for i, r in enumerate(results):
        results_text += f"\n[{i+1}] Title: {r.get('title','')}\n"
        results_text += f"    URL: {r.get('link','')}\n"
        results_text += f"    Snippet: {r.get('snippet','')}\n"
        if r.get('article_text'):
            results_text += f"    Article excerpt: {r['article_text'][:500]}\n"

    prompt = f"""I'm checking if these search results are about a specific restaurant: "{rest_name}" in {city}, California (San Gabriel Valley area).

Search results:
{results_text}

For each result, determine:
1. Is this actually about "{rest_name}" in the SGV? (not a different restaurant with a similar name)
2. If yes: what publication is it from, what is the headline, what's a 1-2 sentence excerpt that mentions the restaurant specifically, is any specific dish mentioned, and what is the publication date?

Return a JSON array. Only include results that are confirmed to be about this restaurant. Skip results that are unclear, about the wrong restaurant, or just a Yelp listing.

Format:
[
  {{
    "confirmed": true,
    "publication": "Eater LA",
    "headline": "...",
    "url": "...",
    "excerpt": "One to two sentences about this restaurant from the article.",
    "dish_mentioned": "dish name or empty string",
    "date": "YYYY-MM-DD or empty string"
  }}
]

Return only the JSON array, no other text."""

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

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
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        text = data["content"][0]["text"].strip()
        # Extract JSON from response
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            confirmed = json.loads(match.group())
            return [c for c in confirmed if c.get("confirmed")]
    except Exception as e:
        print(f"    Claude error: {e}")
    return []


def get_or_create_press_sheet(spreadsheet):
    """Get or create the Press worksheet."""
    try:
        return spreadsheet.worksheet("Press")
    except Exception:
        ws = spreadsheet.add_worksheet(title="Press", rows=1000, cols=20)
        ws.append_row([
            "press_id", "rest_id", "rest_name", "publication", "headline",
            "url", "dish_mentioned", "excerpt", "date", "date_swept", "verified"
        ])
        return ws


def load_existing_press(press_ws):
    """Load existing press entries keyed by URL to avoid duplicates."""
    rows = press_ws.get_all_values()
    existing_urls = set()
    for row in rows[1:]:  # skip header
        if len(row) >= 6 and row[5]:
            existing_urls.add(row[5].strip())
    return existing_urls


def run_sweep(filter_name=None, sweep_all=False):
    if not SERPER_KEY:
        print("SERPER_API_KEY not set. Get a free key at https://serper.dev")
        return
    if not ANTHROPIC_KEY:
        print("ANTHROPIC_API_KEY not set.")
        return

    print("Connecting to Google Sheets...")
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    rest_ws = spreadsheet.worksheet("Restaurants")
    press_ws = get_or_create_press_sheet(spreadsheet)

    existing_urls = load_existing_press(press_ws)
    rows = rest_ws.get_all_values()

    today = datetime.utcnow().strftime("%Y-%m-%d")
    new_rows = []
    press_id_counter = press_ws.row_count  # rough counter for new IDs

    sites_query = " OR ".join(f"site:{s}" for s in TARGET_SITES)

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

        print(f"[{i}] {name} ({city})")

        # Search queries
        q1 = f'"{name}" SGV {sites_query}'
        q2 = f'"{name}" "San Gabriel" food review'

        results = []
        for q in [q1, q2]:
            hits = serper_search(q, num=5)
            for h in hits:
                url = h.get("link", "")
                if url not in existing_urls:
                    # Try to fetch article text for better context (skip paywalled sites)
                    if not any(s in url for s in ["latimes.com", "timeout.com"]):
                        h["article_text"] = fetch_article_text(url)
                        time.sleep(0.3)
                    results.append(h)
            time.sleep(0.4)

        if not results:
            print("  No results.")
            continue

        # Deduplicate by URL
        seen = set()
        unique = []
        for r in results:
            if r.get("link") not in seen:
                seen.add(r.get("link"))
                unique.append(r)

        confirmed = claude_confirm_and_extract(name, city, unique)
        if confirmed:
            for c in confirmed:
                url = c.get("url", "")
                if url in existing_urls:
                    continue
                press_id_counter += 1
                new_rows.append([
                    f"P{press_id_counter:04d}",
                    rest_id,
                    name,
                    c.get("publication", ""),
                    c.get("headline", ""),
                    url,
                    c.get("dish_mentioned", ""),
                    c.get("excerpt", ""),
                    c.get("date", ""),
                    today,
                    "TRUE",
                ])
                existing_urls.add(url)
                print(f"  ✓ {c.get('publication')}: {c.get('headline','')[:60]}")
        else:
            print("  No confirmed press coverage found.")

        time.sleep(0.5)

    if new_rows:
        print(f"\nAdding {len(new_rows)} new press entries to sheet...")
        press_ws.append_rows(new_rows)
    else:
        print("\nNo new press entries found.")

    print("Done.")


if __name__ == "__main__":
    filter_name = None
    sweep_all = "--all" in sys.argv
    for arg in sys.argv[1:]:
        if arg == "--restaurant" and sys.argv.index(arg) + 1 < len(sys.argv):
            filter_name = sys.argv[sys.argv.index(arg) + 1]
    run_sweep(filter_name=filter_name, sweep_all=sweep_all)
