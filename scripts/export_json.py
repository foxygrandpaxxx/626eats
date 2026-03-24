#!/usr/bin/env python3
"""
scripts/export_json.py
-----------------------
Reads the Restaurants, Dishes, and Restaurant_Dishes sheets from
Google Sheets and exports a clean restaurants.json file to data/.

This runs automatically after every photo refresh and research sweep.
Can also be triggered manually from the GitHub Actions tab.

Local usage:
    GOOGLE_SERVICE_ACCOUNT_JSON='...' SPREADSHEET_ID=... python scripts/export_json.py
"""

import os, json
from datetime import datetime
from pathlib import Path

SA_JSON       = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
OUTPUT_PATH   = Path("data/restaurants.json")

# Column map for Restaurants sheet (1-based)
REST_COLS = {
    "id":            1,   # A
    "name":          2,   # B
    "name_cn":       3,   # C
    "status":        4,   # D
    "address":       5,   # E
    "city":          6,   # F
    "zip":           7,   # G
    "lat":           8,   # H
    "lng":           9,   # I
    "region":        10,  # J
    "subregion":     11,  # K
    "province":      12,  # L
    "sec_regions":   13,  # M
    "category":      14,  # N
    "price":         15,  # O
    "phone":         16,  # P
    "website":       17,  # Q
    "halal":         18,  # R
    "michelin":      19,  # S
    "hours_mon":     20,  # T
    "hours_tue":     21,  # U
    "hours_wed":     22,  # V
    "hours_thu":     23,  # W
    "hours_fri":     24,  # X
    "hours_sat":     25,  # Y
    "hours_sun":     26,  # Z
    "dish1":         27,  # AA
    "dish2":         28,  # AB
    "dish3":         29,  # AC
    "photo_ext":     30,  # AD
    "photo_food1":   31,  # AE
    "photo_food2":   32,  # AF
    "photo_int":     33,  # AG
    "google_id":     34,  # AH
    "yelp_id":       35,  # AI
    "dianping_id":   36,  # AJ
    "notes":         37,  # AK
    "sources":       38,  # AL
    "date_added":    39,  # AM
    "date_verified": 40,  # AN
    "added_by":      41,  # AO
}

DISH_COLS = {
    "id":       1,
    "name":     2,
    "chinese":  3,
    "format":   4,
    "region":   5,
    "origin":   6,
    "flavors":  7,
    "desc":     8,
    "best_in":  9,
    "best_src": 10,
    "photo":    11,
    "photo2":   12,
    "notes":    13,
}

LINK_COLS = {
    "link_id":    1,
    "rest_id":    2,
    "rest_name":  3,
    "dish_id":    4,
    "dish_name":  5,
    "is_best":    6,
    "score":      7,
    "sources":    8,
    "photo":      9,
    "note":       10,
}

def get_cell(row, col_1based, default=""):
    """Safely get a cell value from a row (1-based col index)."""
    idx = col_1based - 1
    if idx < len(row):
        return row[idx]
    return default

def parse_bool(val):
    if isinstance(val, bool): return val
    return str(val).upper() in ("TRUE", "YES", "1")

def parse_float(val):
    try: return float(val)
    except: return None

def parse_list(val, sep=","):
    if not val: return []
    return [s.strip() for s in str(val).split(sep) if s.strip()]

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
    return gspread.authorize(creds)

def export():
    print(f"Exporting from Google Sheets: {SPREADSHEET_ID}")

    client = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    # ── Read Dishes ────────────────────────────────────────────────────────────
    print("Reading Dishes sheet...")
    dish_ws = spreadsheet.worksheet("Dishes")
    dish_rows = dish_ws.get_all_values()
    DATA_START = 3  # 0-indexed; spreadsheet row 4

    dishes = []
    dish_map = {}  # id -> dish dict

    for row in dish_rows[DATA_START:]:
        did = get_cell(row, DISH_COLS["id"])
        if not did:
            continue
        d = {
            "id":      did,
            "name":    get_cell(row, DISH_COLS["name"]),
            "chinese": get_cell(row, DISH_COLS["chinese"]),
            "format":  get_cell(row, DISH_COLS["format"]),
            "region":  get_cell(row, DISH_COLS["region"]),
            "origin":  get_cell(row, DISH_COLS["origin"]),
            "flavors": parse_list(get_cell(row, DISH_COLS["flavors"]), "|"),
            "desc":    get_cell(row, DISH_COLS["desc"]),
            "bestIn":  get_cell(row, DISH_COLS["best_in"]),
            "bestSrc": get_cell(row, DISH_COLS["best_src"]),
            "photoUrl": get_cell(row, DISH_COLS["photo"]),
            "photoAlt": get_cell(row, DISH_COLS["photo2"]),
        }
        dishes.append(d)
        dish_map[did] = d

    print(f"  Loaded {len(dishes)} dishes")

    # ── Read Restaurant_Dishes links ───────────────────────────────────────────
    print("Reading Restaurant_Dishes sheet...")
    link_ws = spreadsheet.worksheet("Restaurant_Dishes")
    link_rows = link_ws.get_all_values()

    links_by_rest = {}  # rest_id -> [link, ...]
    for row in link_rows[DATA_START:]:
        lid = get_cell(row, LINK_COLS["link_id"])
        if not lid:
            continue
        rid = get_cell(row, LINK_COLS["rest_id"])
        if rid not in links_by_rest:
            links_by_rest[rid] = []
        links_by_rest[rid].append({
            "dishId":   get_cell(row, LINK_COLS["dish_id"]),
            "name":     get_cell(row, LINK_COLS["dish_name"]),
            "isBest":   parse_bool(get_cell(row, LINK_COLS["is_best"])),
            "score":    parse_float(get_cell(row, LINK_COLS["score"])),
            "sources":  parse_list(get_cell(row, LINK_COLS["sources"])),
            "photoUrl": get_cell(row, LINK_COLS["photo"]),
            "note":     get_cell(row, LINK_COLS["note"]),
        })

    print(f"  Loaded {sum(len(v) for v in links_by_rest.values())} dish links")

    # ── Read Restaurants ───────────────────────────────────────────────────────
    print("Reading Restaurants sheet...")
    rest_ws = spreadsheet.worksheet("Restaurants")
    rest_rows = rest_ws.get_all_values()

    restaurants = []
    skipped = []

    for row in rest_rows[DATA_START:]:
        rid = get_cell(row, REST_COLS["id"])
        if not rid:
            continue

        status = get_cell(row, REST_COLS["status"]).upper()
        if status in ("CLOSED", "PERMANENTLY CLOSED"):
            skipped.append(get_cell(row, REST_COLS["name"]))
            continue

        # Build dish arrays from links (fall back to manual dish columns)
        rest_links = links_by_rest.get(rid, [])
        if rest_links:
            all_dishes  = [lk["name"] for lk in rest_links if lk["name"]]
            best_dishes = [lk["name"] for lk in rest_links if lk["isBest"] and lk["name"]]
        else:
            # Fall back to the three manual dish columns
            all_dishes = [d for d in [
                get_cell(row, REST_COLS["dish1"]),
                get_cell(row, REST_COLS["dish2"]),
                get_cell(row, REST_COLS["dish3"]),
            ] if d]
            best_dishes = all_dishes[:1]

        # Secondary regions
        sec = parse_list(get_cell(row, REST_COLS["sec_regions"]))

        # Michelin value: TRUE / FALSE / BIB GOURMAND
        mich_raw = str(get_cell(row, REST_COLS["michelin"])).upper()
        michelin = mich_raw in ("TRUE", "BIB GOURMAND")
        michelin_label = mich_raw if mich_raw not in ("FALSE", "") else None

        lat = parse_float(get_cell(row, REST_COLS["lat"]))
        lng = parse_float(get_cell(row, REST_COLS["lng"]))

        restaurant = {
            "id":     rid,
            "name":   get_cell(row, REST_COLS["name"]),
            "nameChinese": get_cell(row, REST_COLS["name_cn"]) or None,
            "status": status or "OPEN",
            "address": get_cell(row, REST_COLS["address"]),
            "city":   get_cell(row, REST_COLS["city"]),
            "zip":    get_cell(row, REST_COLS["zip"]) or None,
            "lat":    lat,
            "lng":    lng,
            "region":    get_cell(row, REST_COLS["region"]),
            "subregion": get_cell(row, REST_COLS["subregion"]),
            "province":  get_cell(row, REST_COLS["province"]),
            "secondaryRegions": sec,
            "category":  get_cell(row, REST_COLS["category"]),
            "price":     get_cell(row, REST_COLS["price"]),
            "phone":     get_cell(row, REST_COLS["phone"]) or None,
            "website":   get_cell(row, REST_COLS["website"]) or None,
            "halal":     parse_bool(get_cell(row, REST_COLS["halal"])),
            "michelin":  michelin,
            "michelinLabel": michelin_label,
            "hours": {
                "mon": get_cell(row, REST_COLS["hours_mon"]) or None,
                "tue": get_cell(row, REST_COLS["hours_tue"]) or None,
                "wed": get_cell(row, REST_COLS["hours_wed"]) or None,
                "thu": get_cell(row, REST_COLS["hours_thu"]) or None,
                "fri": get_cell(row, REST_COLS["hours_fri"]) or None,
                "sat": get_cell(row, REST_COLS["hours_sat"]) or None,
                "sun": get_cell(row, REST_COLS["hours_sun"]) or None,
            },
            "dishes":      all_dishes,
            "bestDishes":  best_dishes,
            "dishDetails": rest_links,
            "photos": {
                "exterior": get_cell(row, REST_COLS["photo_ext"]) or None,
                "food1":    get_cell(row, REST_COLS["photo_food1"]) or None,
                "food2":    get_cell(row, REST_COLS["photo_food2"]) or None,
                "interior": get_cell(row, REST_COLS["photo_int"]) or None,
            },
            "googlePlaceId": get_cell(row, REST_COLS["google_id"]) or None,
            "yelpId":        get_cell(row, REST_COLS["yelp_id"]) or None,
            "dianpingId":    get_cell(row, REST_COLS["dianping_id"]) or None,
            "notes":   get_cell(row, REST_COLS["notes"]) or None,
            "sources": parse_list(get_cell(row, REST_COLS["sources"])),
            "dateAdded":    get_cell(row, REST_COLS["date_added"]) or None,
            "dateVerified": get_cell(row, REST_COLS["date_verified"]) or None,
        }
        restaurants.append(restaurant)

    print(f"  Loaded {len(restaurants)} open restaurants ({len(skipped)} closed skipped)")

    # ── Write output ───────────────────────────────────────────────────────────
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "version":         datetime.utcnow().isoformat() + "Z",
        "exportedAt":      datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "restaurantCount": len(restaurants),
        "dishCount":       len(dishes),
        "restaurants":     restaurants,
        "dishes":          dishes,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    size_kb = OUTPUT_PATH.stat().st_size // 1024
    print(f"\nExported to {OUTPUT_PATH} ({size_kb}KB)")
    print(f"  {len(restaurants)} restaurants | {len(dishes)} dishes")
    if skipped:
        print(f"  Skipped (closed): {', '.join(skipped[:5])}{'...' if len(skipped)>5 else ''}")

if __name__ == "__main__":
    export()
