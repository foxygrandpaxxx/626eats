#!/usr/bin/env python3
"""Check index.html for JS errors around the DISHES array."""
import re, sys

with open("index.html", encoding="utf-8") as f:
    src = f.read()

print(f"File size: {len(src):,} chars, {src.count(chr(10))} lines")

# Find DISHES array
m = re.search(r"var DISHES = \[", src)
if not m:
    print("ERROR: var DISHES not found in index.html")
    sys.exit(1)

start = m.start()
# Find the matching closing ];
depth = 0
pos = start + len("var DISHES = ")
for i, c in enumerate(src[pos:], pos):
    if c == '[': depth += 1
    elif c == ']':
        depth -= 1
        if depth == 0:
            end = i + 1
            break

array_src = src[start:end+1]
print(f"DISHES array: chars {start}–{end}, length {end-start:,}")
print(f"Dish entries: {array_src.count('{id:')}")

# Check for common JS issues
issues = []

# Unescaped single quotes inside single-quoted strings
# Look for patterns like: name:'O'Brien' (unescaped apostrophe)
bad_quotes = re.findall(r"name:'[^']*'[^']*'", array_src)
if bad_quotes:
    issues.append(f"Unescaped quotes in names: {bad_quotes[:3]}")

# Check for emoji that might cause issues (usually fine but check)
# Check overall syntax by looking for obvious breaks
if "undefined" in array_src:
    issues.append("'undefined' found in array")

# Check FORMATS array
fm = re.search(r"var FORMATS = \[.*?\];", src, re.DOTALL)
if fm:
    print(f"\nFORMATS array found: {fm.group()[:200]}")
else:
    print("\nWARNING: FORMATS array not found")

# Check for syntax around the array end
print(f"\nChars after DISHES array (first 200):")
print(repr(src[end+1:end+201]))

if issues:
    print(f"\nISSUES FOUND:")
    for i in issues:
        print(f"  {i}")
else:
    print("\nNo obvious issues found in DISHES array")

# Check file can be parsed as HTML with JS extracted
script_start = src.index('<script>')
script_end   = src.rindex('</script>')
js = src[script_start+8:script_end]
print(f"\nJS section: {len(js):,} chars")

# Look for unclosed brackets/parens in DISHES area
bracket_depth = 0
for c in array_src:
    if c == '[': bracket_depth += 1
    elif c == ']': bracket_depth -= 1
print(f"Bracket balance in DISHES: {bracket_depth} (should be 0)")
