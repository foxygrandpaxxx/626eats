#!/usr/bin/env python3
"""
scripts/fix_dishes_embed.py
============================
Directly embeds dishes_array.js into index.html.
More robust than update_app_dishes.py — handles all edge cases.

Run from your repo root:
  python scripts/fix_dishes_embed.py
"""
import re, sys, os

# ── Read dishes_array.js ──────────────────────────────────────────────────────
js_path = os.path.join("scripts", "dishes_array.js")
if not os.path.exists(js_path):
    sys.exit(f"ERROR: {js_path} not found. Run rebuild_taxonomy.py first.")

with open(js_path, encoding="utf-8") as f:
    js_content = f.read()

dish_count = js_content.count("{id:")
print(f"dishes_array.js: {dish_count} dishes, {len(js_content):,} chars")

if dish_count == 0:
    sys.exit("ERROR: dishes_array.js has 0 dishes. Run rebuild_taxonomy.py first.")

# Extract just the var DISHES = [...]; block
m = re.search(r"(var DISHES = \[[\s\S]*?\];)", js_content)
if not m:
    sys.exit("ERROR: Cannot find DISHES array in dishes_array.js")

new_dishes = m.group(1)
print(f"DISHES array: {len(new_dishes):,} chars")

# Extract categories from the array
cats = sorted(set(re.findall(r"fmt:'([^']+)'", new_dishes)))
print(f"Categories: {len(cats)}")

# ── Read index.html ───────────────────────────────────────────────────────────
html_path = "index.html"
if not os.path.exists(html_path):
    sys.exit("ERROR: index.html not found. Run from repo root.")

with open(html_path, encoding="utf-8") as f:
    src = f.read()

print(f"\nindex.html: {len(src):,} chars")

# ── Step 1: Replace DISHES array ─────────────────────────────────────────────
# Use a precise replacement — find var DISHES = [...]; with any content
old_match = re.search(r"var DISHES = \[[\s\S]*?\];", src)
if old_match:
    old_text = old_match.group(0)
    old_dish_count = old_text.count("{id:")
    print(f"\nFound existing DISHES array ({old_dish_count} dishes, {len(old_text):,} chars)")
    src = src[:old_match.start()] + new_dishes + src[old_match.end():]
    print(f"Replaced with new array ({dish_count} dishes)")
else:
    # Fallback: find the empty placeholder and replace it
    if "var DISHES = [];" in src:
        src = src.replace("var DISHES = [];", new_dishes, 1)
        print(f"\nReplaced empty DISHES placeholder with {dish_count} dishes")
    else:
        sys.exit("ERROR: Cannot find DISHES array in index.html to replace")

# ── Step 2: Replace FORMATS array ────────────────────────────────────────────
new_formats = (
    "var FORMATS = [\n  'All',\n" +
    ",\n".join(f"  '{c}'" for c in cats) +
    "\n];"
)

old_fmt = re.search(r"var FORMATS = \[[\s\S]*?\];", src)
if old_fmt:
    src = src[:old_fmt.start()] + new_formats + src[old_fmt.end():]
    print(f"Updated FORMATS array ({len(cats)} categories)")
else:
    print("WARNING: FORMATS array not found — filter chips won't update")

# ── Step 3: Verify DISHES is now populated ────────────────────────────────────
verify = re.search(r"var DISHES = \[[\s\S]*?\];", src)
if verify:
    count = verify.group(0).count("{id:")
    print(f"\nVerification: DISHES array now has {count} entries")
    if count == 0:
        print("ERROR: Still empty after replacement!")
        sys.exit(1)
else:
    print("ERROR: DISHES array not found after replacement!")
    sys.exit(1)

# ── Write ─────────────────────────────────────────────────────────────────────
with open(html_path, "w", encoding="utf-8") as f:
    f.write(src)

print(f"\nSaved {html_path} ({len(src):,} chars)")
print("\nDone! Now run:")
print("  git add index.html")
print("  git pull origin main")
print('  git commit -m "Embed real dish taxonomy into app"')
print("  git push origin main")
