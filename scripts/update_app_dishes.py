#!/usr/bin/env python3
"""
scripts/update_app_dishes.py
=============================
Reads the generated dishes_array.js and embeds it into index.html,
replacing the old hardcoded DISHES array and updating the By Dish
browse mode to use the real data from your Sheet.

Also updates the filter row to show all real categories instead of
the old hardcoded FORMATS list.

Usage:
  python scripts/update_app_dishes.py
"""

import os, sys, re

def main():
    # Read dishes_array.js
    js_path = "scripts/dishes_array.js"
    if not os.path.exists(js_path):
        sys.exit(f"ERROR: {js_path} not found. Run rebuild_taxonomy.py first.")

    with open(js_path, encoding="utf-8") as f:
        dishes_js = f.read()

    # Extract just the var DISHES = [...]; part
    m = re.search(r"(var DISHES = \[.*?\];)", dishes_js, re.DOTALL)
    if not m:
        sys.exit("ERROR: Could not find DISHES array in dishes_array.js")
    new_dishes_array = m.group(1)

    # Count dishes and categories
    dish_count = dishes_js.count("{id:")
    print(f"Loading {dish_count} dishes from dishes_array.js")

    # Extract all unique categories from the array
    cats = sorted(set(re.findall(r"fmt:'([^']+)'", dishes_js)))
    print(f"Categories found: {len(cats)}")
    for c in cats:
        print(f"  {c}")

    # Read index.html
    html_path = "index.html"
    if not os.path.exists(html_path):
        sys.exit("ERROR: index.html not found. Run from your repo root.")

    with open(html_path, encoding="utf-8") as f:
        src = f.read()

    print(f"\nProcessing index.html ({len(src)} chars)...")

    # ── Replace old DISHES array ──────────────────────────────────────────────
    # Find var DISHES = [...]; in the script
    old_dishes_match = re.search(
        r"var DISHES = \[.*?\];",
        src, re.DOTALL
    )
    if old_dishes_match:
        src = src[:old_dishes_match.start()] + new_dishes_array + src[old_dishes_match.end():]
        print("  Replaced DISHES array")
    else:
        # Insert after var RESTS declaration
        src = src.replace(
            "var RESTS = [];\nvar DISHES = [];",
            "var RESTS = [];\n" + new_dishes_array
        )
        if new_dishes_array in src:
            print("  Inserted new DISHES array")
        else:
            print("  WARNING: Could not find where to insert DISHES array")

    # ── Replace hardcoded FORMATS list with real categories ───────────────────
    # The old FORMATS array drives the filter chips in By Dish mode
    old_formats = re.search(
        r"var FORMATS = \[.*?\];",
        src, re.DOTALL
    )
    new_formats = "var FORMATS = [\n  'All',\n" + \
                  ",\n".join(f"  '{c}'" for c in cats) + "\n];"

    if old_formats:
        src = src[:old_formats.start()] + new_formats + src[old_formats.end():]
        print("  Updated FORMATS array with real categories")
    else:
        print("  WARNING: Could not find FORMATS array to update")

    # ── Fix renderDishMode to use new dish structure ──────────────────────────
    # Old code uses d.fmt, d.icon, d.name, d.zh, d.region, d.bestIn, d.rests
    # New dishes have: id, name, fmt, icon, count, desc, rests
    # Update makeDishCard to handle new structure

    old_card = """function makeDishCard(d){
  var cnt = d.rests ? d.rests.length : 0;
  var card = document.createElement('div');
  card.className = 'dish-card anim';
  var ico = document.createElement('div'); ico.className='dish-card-icon'; ico.textContent=d.icon;
  var body = document.createElement('div'); body.className='dish-card-body';
  var nm = document.createElement('div'); nm.className='dish-card-name'; nm.textContent=d.name;
  var meta = document.createElement('div'); meta.className='dish-card-meta'; meta.textContent=d.zh+' · '+d.region;
  body.appendChild(nm); body.appendChild(meta);
  if(d.bestIn){
    var best = document.createElement('div'); best.className='dish-best';
    best.innerHTML = '🏆 Best: '+d.bestIn;
    body.appendChild(best);
  }
  var cntEl = document.createElement('div'); cntEl.className='dish-card-count';
  cntEl.textContent = cnt>0 ? cnt+' spot'+(cnt!==1?'s':'') : '';
  card.appendChild(ico); card.appendChild(body); card.appendChild(cntEl);
  card.onclick = function(){ openDishDetail(d); };
  return card;
}"""

    new_card = """function makeDishCard(d){
  var cnt = d.count || (d.rests ? d.rests.length : 0);
  var card = document.createElement('div');
  card.className = 'dish-card anim';
  var ico = document.createElement('div'); ico.className='dish-card-icon'; ico.textContent=d.icon||'🍽';
  var body = document.createElement('div'); body.className='dish-card-body';
  var nm = document.createElement('div'); nm.className='dish-card-name'; nm.textContent=d.name;
  var meta = document.createElement('div'); meta.className='dish-card-meta';
  meta.textContent = d.fmt || d.region || '';
  body.appendChild(nm); body.appendChild(meta);
  // desc not stored in DISHES array (kept lean for performance)
  var cntEl = document.createElement('div'); cntEl.className='dish-card-count';
  cntEl.textContent = cnt>0 ? cnt+' spot'+(cnt!==1?'s':'') : '';
  card.appendChild(ico); card.appendChild(body); card.appendChild(cntEl);
  card.onclick = function(){ openDishDetail(d); };
  return card;
}"""

    if old_card in src:
        src = src.replace(old_card, new_card)
        print("  Updated makeDishCard for new dish structure")
    else:
        print("  NOTE: makeDishCard not found in old form — may already be updated")

    # ── Fix openDishDetail to handle new dish structure ───────────────────────
    # New dishes have .desc instead of .zh/.origin, .rests is array of names
    old_dish_detail = """  document.getElementById('dish-icon').textContent = d.icon;
  document.getElementById('dish-name-h').textContent = d.name;
  document.getElementById('dish-origin').textContent = d.zh+' · '+d.origin;"""

    new_dish_detail = """  document.getElementById('dish-icon').textContent = d.icon||'🍽';
  document.getElementById('dish-name-h').textContent = d.name;
  document.getElementById('dish-origin').textContent = d.fmt||d.region||'';"""

    if old_dish_detail in src:
        src = src.replace(old_dish_detail, new_dish_detail)
        print("  Updated dish detail header")

    # Fix dish detail description + flavors section
    old_dish_desc = """      document.getElementById('dish-desc').textContent = d.desc;

      var ft = document.getElementById('dish-flavors');
      ft.innerHTML = '';
      d.flavors.forEach(function(f){
        var t = document.createElement('span'); t.className='flavor-tag'; t.textContent=f; ft.appendChild(t);
      });"""

    new_dish_desc = """      document.getElementById('dish-desc').textContent = d.desc || '(No description available)';

      var ft = document.getElementById('dish-flavors');
      ft.innerHTML = '';
      var tags = d.flavors || d.tags || [];
      tags.forEach(function(f){
        var t = document.createElement('span'); t.className='flavor-tag'; t.textContent=f; ft.appendChild(t);
      });"""

    if old_dish_desc in src:
        src = src.replace(old_dish_desc, new_dish_desc)
        print("  Updated dish description section")

    # Fix dish detail restaurants list — rests is now array of strings not objects
    old_dish_rests = """      d.rests.forEach(function(entry, i){
      var r = RstMap[entry.id];
      if(!r) return;"""

    new_dish_rests = """      // rests is array of restaurant names (strings) or {id,..} objects
      var restItems = (d.rests||[]).map(function(entry){
        if(typeof entry === 'string'){
          // Find by name
          return RESTS.find(function(r){ return r.name===entry; }) || null;
        }
        return RstMap[entry.id] || null;
      }).filter(Boolean);

      restItems.forEach(function(r, i){"""

    if old_dish_rests in src:
        src = src.replace(old_dish_rests, new_dish_rests)
        print("  Updated dish restaurants list for new rests format")

    # Write updated file
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(src)

    print(f"\nSaved {html_path} ({len(src)} chars)")
    print("\n" + "=" * 60)
    print("Done! The app now uses real dish data.")
    print(f"  {dish_count} dishes across {len(cats)} categories")
    print("\nNext:")
    print("  git add index.html")
    print("  git pull origin main")
    print('  git commit -m "Replace hardcoded dishes with real data"')
    print("  git push origin main")
    print("=" * 60)

if __name__ == "__main__":
    main()
