#!/usr/bin/env python3
"""
scripts/enrich_dish_descriptions.py
-------------------------------------
Reads the embedded DISHES array from index.html, uses Claude to generate:
  - description: brief (15-25 word) encyclopedic description of what the dish is
  - regionTags: list of Chinese cuisine regions the dish originates from

Writes the updated DISHES array back to index.html (same mechanism as fix_dishes_embed.py).

Dish description style:
  "Silky tofu in a fiery sauce of chili bean paste, Sichuan peppercorn, and ground pork."
  "Steamed soup dumplings with a juicy pork filling — tilt before biting to catch the broth."

Region tags examples:
  Mapo tofu       → ["Sichuan"]
  Xiaolongbao     → ["Shanghainese"]
  Roast duck      → ["Cantonese"]
  Dan dan noodles → ["Sichuan"]
  Fried rice      → ["Cantonese", "Multiple"]

Required:
  ANTHROPIC_API_KEY – Claude API key

Local usage:
  ANTHROPIC_API_KEY='...' python scripts/enrich_dish_descriptions.py

Options:
  --dry-run   Print changes without writing to index.html
  --missing   Only enrich dishes that don't have a description yet (default)
  --all       Re-generate descriptions for all dishes
"""

import os, json, re, sys, time
import urllib.request

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
INDEX_PATH = os.path.join(os.path.dirname(__file__), "..", "index.html")

BATCH_SIZE = 30  # dishes per Claude API call


def js_to_json(js_str):
    """Convert JavaScript object literal notation to valid JSON.

    Handles:
      - Unquoted object keys:  {id:'D001'} → {"id":"D001"}
      - Single-quoted strings: 'Ying\'s'   → "Ying's"
    After the first enrichment run the array is written back as proper JSON,
    so subsequent runs will hit the fast path (json.loads succeeds directly).
    """
    # Step 1: quote unquoted object keys  (word chars immediately before a colon)
    result = re.sub(r'([{,])\s*(\w+)\s*:', r'\1"\2":', js_str)

    # Step 2: replace single-quoted strings with double-quoted strings
    def replace_sq(m):
        content = m.group(1)
        # protect escaped single-quotes, then escape any bare double-quotes,
        # then restore the single-quotes as plain apostrophes
        content = content.replace("\\'", "\x00SQ\x00")
        content = content.replace('"',   '\\"')
        content = content.replace("\x00SQ\x00", "'")
        return '"' + content + '"'

    result = re.sub(r"'((?:[^'\\]|\\.)*)'", replace_sq, result)

    # Step 3: strip trailing commas before ] or }  (invalid in JSON, fine in JS)
    result = re.sub(r',\s*([}\]])', r'\1', result)

    return result


def extract_dishes_array(html):
    """Extract the DISHES = [...] array from index.html."""
    match = re.search(r'var DISHES\s*=\s*(\[.*?\]);', html, re.DOTALL)
    if not match:
        raise ValueError("Could not find 'var DISHES = [...]' in index.html")
    js_str = match.group(1)
    # Try strict JSON first (works after first enrichment run writes JSON back)
    try:
        dishes = json.loads(js_str)
    except json.JSONDecodeError:
        dishes = json.loads(js_to_json(js_str))
    return dishes, match.start(1), match.end(1)


def claude_enrich_batch(dishes_batch):
    """
    Send a batch of dishes to Claude for description + regionTags generation.
    Returns a dict: {dish_name -> {description, regionTags}}
    """
    if not ANTHROPIC_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set")

    dishes_list = "\n".join(
        f"- {d['name']} (category: {d.get('fmt', d.get('icon',''))})"
        for d in dishes_batch
    )

    prompt = f"""You are a Chinese food expert writing for a restaurant discovery app aimed at food lovers who want to learn about regional Chinese cuisine.

For each dish below, provide:
1. A brief description (15-25 words) that explains what the dish IS — ingredients, cooking technique, flavor profile, texture. Write it for someone who has never encountered the dish. Be appetizing and specific. Use present tense.
2. regionTags: a list of Chinese cuisine regions the dish originates from. Use these exact region names only: Cantonese, Sichuan, Shanghainese, Hunan, Beijing/Northern, Taiwanese, Hakka, Fujian/Hokkien, Yunnan, Xinjiang, Shaanxi/Xi'an, Chaozhou, Dongbei, Multiple. Use "Multiple" if the dish is ubiquitous across many regions (e.g. fried rice). Use an empty list [] only if truly unknown.

Dishes to describe:
{dishes_list}

Return a JSON object where keys are exact dish names and values have "description" and "regionTags":
{{
  "Mapo Tofu": {{
    "description": "Silky tofu in a fiery sauce of chili bean paste, Sichuan peppercorn, and ground pork.",
    "regionTags": ["Sichuan"]
  }},
  "Xiaolongbao": {{
    "description": "Steamed soup dumplings with a juicy pork filling — tilt before biting to catch the broth.",
    "regionTags": ["Shanghainese"]
  }}
}}

Return only the JSON object, no other text."""

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4096,
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
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())

    text = data["content"][0]["text"].strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in Claude response: {text[:200]}")
    return json.loads(match.group())


def run(dry_run=False, enrich_all=False):
    print(f"Reading {INDEX_PATH}...")
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    dishes, start, end = extract_dishes_array(html)
    print(f"Found {len(dishes)} dishes in DISHES array.")

    if enrich_all:
        to_enrich = dishes
    else:
        # Only enrich dishes missing description
        to_enrich = [d for d in dishes if not d.get("description")]
        print(f"{len(to_enrich)} dishes missing descriptions.")

    if not to_enrich:
        print("All dishes already have descriptions. Use --all to re-generate.")
        return

    # Process in batches
    enriched = {}
    total = len(to_enrich)
    for i in range(0, total, BATCH_SIZE):
        batch = to_enrich[i:i+BATCH_SIZE]
        print(f"Processing batch {i//BATCH_SIZE + 1}/{(total-1)//BATCH_SIZE + 1} ({len(batch)} dishes)...")
        try:
            result = claude_enrich_batch(batch)
            enriched.update(result)
            print(f"  Got {len(result)} descriptions.")
        except Exception as e:
            print(f"  Error: {e}")
        time.sleep(1)

    # Apply enrichments to dishes array
    updated = 0
    for d in dishes:
        name = d.get("name", "")
        if name in enriched:
            info = enriched[name]
            if "description" in info:
                d["description"] = info["description"]
            if "regionTags" in info:
                d["regionTags"] = info["regionTags"]
            updated += 1

    print(f"Updated {updated} dishes with descriptions + region tags.")

    if dry_run:
        print("Dry run — not writing to index.html.")
        print("Sample:")
        for d in dishes[:3]:
            print(f"  {d['name']}: {d.get('description','(no description)')}")
            print(f"    regionTags: {d.get('regionTags',[])}")
        return

    # Serialize and write back to index.html
    dishes_json = json.dumps(dishes, ensure_ascii=False, separators=(',', ':'))
    # Compact arrays on same line for readability
    new_html = html[:start] + dishes_json + html[end:]

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"Written back to {INDEX_PATH}")


if __name__ == "__main__":
    dry_run    = "--dry-run" in sys.argv
    enrich_all = "--all" in sys.argv
    run(dry_run=dry_run, enrich_all=enrich_all)
