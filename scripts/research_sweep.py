#!/usr/bin/env python3
"""
scripts/research_sweep.py
==========================
Full SGV restaurant research pipeline.

Phase 1 — Google Places discovery
  Searches all 16 SGV cities × 6 search terms.
  Fetches full details: hours, phone, address, 4 photos.
  Extracts dishes from Google review snippets + editorial summary.

Phase 2 — Yelp enrichment (Playwright headless Chrome)
  Finds each restaurant on Yelp.
  Extracts: rating, review count, price, photos, dish mentions.
  Runs at 10–16s per request with full anti-detection.
  Falls back gracefully on CAPTCHA — marks for retry.
  Yelp dishes MERGE with (not replace) Google dishes.

Phase 3 — Write to Google Sheet
  Appends new restaurants only (skips duplicates by Place ID or name).
  Top 3 dishes written to columns AA–AC.
  Yelp rating + highlights written to Notes.

INSTALL:
  pip install requests gspread google-auth playwright beautifulsoup4
  playwright install chromium

USAGE:
  python scripts/research_sweep.py                    # Full sweep
  python scripts/research_sweep.py --no-yelp          # Google only (faster, no Playwright)
  python scripts/research_sweep.py --no-sheet         # Preview without writing
  python scripts/research_sweep.py --test             # Test API connections
  python scripts/research_sweep.py --cities "Alhambra,San Gabriel"

ENV VARS:
  GOOGLE_API_KEY                Required
  GOOGLE_SERVICE_ACCOUNT_JSON   Required (for Sheet write)
  SPREADSHEET_ID                Required (for Sheet write)
"""

import os, sys, re, json, time, random, asyncio, argparse, requests
from datetime import date
from collections import Counter

# ── Config ─────────────────────────────────────────────────────────────────────
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
SA_JSON        = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

YELP_DELAY_MIN   = 10   # seconds between Yelp requests (longer = safer)
YELP_DELAY_MAX   = 16
MAX_REVIEW_TEXT  = 40   # review snippets to scan per restaurant

# ── SGV Cities ─────────────────────────────────────────────────────────────────
SGV_CITIES = [
    {"city": "Alhambra",         "lat": 34.0953, "lng": -118.1270, "radius": 3000},
    {"city": "Monterey Park",    "lat": 34.0625, "lng": -118.1228, "radius": 3000},
    {"city": "San Gabriel",      "lat": 34.0961, "lng": -118.1058, "radius": 3000},
    {"city": "Rosemead",         "lat": 34.0803, "lng": -118.0830, "radius": 3000},
    {"city": "Arcadia",          "lat": 34.1397, "lng": -118.0353, "radius": 3500},
    {"city": "Temple City",      "lat": 34.1065, "lng": -118.0578, "radius": 2500},
    {"city": "Rowland Heights",  "lat": 33.9764, "lng": -117.9036, "radius": 3000},
    {"city": "Diamond Bar",      "lat": 34.0289, "lng": -117.8103, "radius": 3500},
    {"city": "Walnut",           "lat": 34.0220, "lng": -117.8658, "radius": 2500},
    {"city": "El Monte",         "lat": 34.0686, "lng": -118.0276, "radius": 3500},
    {"city": "West Covina",      "lat": 34.0686, "lng": -117.9390, "radius": 3500},
    {"city": "Hacienda Heights", "lat": 33.9933, "lng": -117.9728, "radius": 3000},
    {"city": "Industry",         "lat": 34.0153, "lng": -117.9623, "radius": 2000},
    {"city": "La Puente",        "lat": 34.0328, "lng": -117.9492, "radius": 2500},
    {"city": "South El Monte",   "lat": 34.0525, "lng": -118.0462, "radius": 2000},
    {"city": "Pasadena",         "lat": 34.1478, "lng": -118.1445, "radius": 3000},
]

SEARCH_TERMS = [
    "Chinese restaurant",
    "dim sum",
    "Sichuan restaurant",
    "Taiwanese restaurant",
    "Chinese noodles",
    "hot pot restaurant",
]

# ── Chinese detection ───────────────────────────────────────────────────────────
CHINESE_KW = {
    "dim sum","cantonese","sichuan","szechuan","shanghainese","taiwanese",
    "peking","beijing","hunan","chinese","mandarin","hong kong","dumplings",
    "noodle","hot pot","hotpot","char siu","chiu chow","teochew","uyghur",
    "xinjiang","shaanxi","yunnan","fujianese","fujian","hakka","seafood restaurant",
    "roast duck","bbq pork",
}
NON_CHINESE = {
    "japanese","korean","thai","vietnamese","indian","mexican","pizza","burger",
    "sushi","ramen","pho","italian","french","sandwich","wings","turkish",
    "persian","mediterranean","greek","american grill",
}

def is_chinese(name, types):
    text = (" " + name + " " + " ".join(types) + " ").lower()
    if any(k in text for k in NON_CHINESE):
        return False
    if any(k in text for k in CHINESE_KW):
        return True
    for ch in name:
        if "\u4e00" <= ch <= "\u9fff":
            return True
    return False

# ── Auto-classify region ────────────────────────────────────────────────────────
REGION_HINTS = [
    ("dim sum",       "Cantonese",        "Dim Sum & Yum Cha"),
    ("yum cha",       "Cantonese",        "Dim Sum & Yum Cha"),
    ("seafood",       "Cantonese",        "Cantonese Seafood Banquet"),
    ("roast duck",    "Cantonese",        "Cantonese Roast & BBQ"),
    ("char siu",      "Cantonese",        "Cantonese Roast & BBQ"),
    ("cantonese",     "Cantonese",        "Classic Cantonese"),
    ("chiu chow",     "Teochew",          "Teochew Noodles"),
    ("teochew",       "Teochew",          "Teochew Noodles"),
    ("chaozhou",      "Teochew",          "Teochew Noodles"),
    ("hong kong",     "Hong Kong",        "Cha Chaan Teng"),
    ("cha chaan",     "Hong Kong",        "Cha Chaan Teng"),
    ("sichuan",       "Sichuan",          "Chengdu Classic"),
    ("szechuan",      "Sichuan",          "Chengdu Classic"),
    ("chengdu",       "Sichuan",          "Chengdu Classic"),
    ("chongqing",     "Sichuan",          "Chongqing Style"),
    ("hot pot",       "Sichuan",          "Sichuan Hot Pot"),
    ("hotpot",        "Sichuan",          "Sichuan Hot Pot"),
    ("hunan",         "Hunan",            "Classic Hunan"),
    ("shanghai",      "Shanghainese",     "Shanghai Classic"),
    ("shanghainese",  "Shanghainese",     "Shanghai Classic"),
    ("xiao long bao", "Shanghainese",     "Shanghai Classic"),
    ("taiwanese",     "Taiwanese",        "Classic Taiwanese"),
    ("beef noodle",   "Taiwanese",        "Classic Taiwanese"),
    ("peking",        "Northern Chinese", "Beijing & Imperial Court"),
    ("beijing",       "Northern Chinese", "Beijing & Imperial Court"),
    ("xi'an",         "Northwestern",     "Shaanxi / Xi'an"),
    ("xian",          "Northwestern",     "Shaanxi / Xi'an"),
    ("shaanxi",       "Northwestern",     "Shaanxi / Xi'an"),
    ("uyghur",        "Northwestern",     "Xinjiang / Uyghur (Halal)"),
    ("xinjiang",      "Northwestern",     "Xinjiang / Uyghur (Halal)"),
    ("halal",         "Northwestern",     "Xinjiang / Uyghur (Halal)"),
    ("yunnan",        "Southwestern",     "Yunnan"),
    ("guizhou",       "Southwestern",     "Guizhou"),
    ("hakka",         "Fujianese / Min",  "Hakka"),
    ("fujian",        "Fujianese / Min",  "Fuzhou (Northern Min)"),
    ("hokkien",       "Fujianese / Min",  "Hokkien / Southern Min"),
    ("hainan",        "Modern & Fusion",  "Hainan / Southeast Asian-Chinese"),
    ("dumpling",      "Northern Chinese", "Northeastern / Dongbei"),
    ("dongbei",       "Northern Chinese", "Northeastern / Dongbei"),
    ("noodle",        "Northern Chinese", "Northeastern / Dongbei"),
]
PRICE_MAP = {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}

def auto_classify(name, types=None):
    text = (" " + name + " " + " ".join(types or []) + " ").lower()
    for kw, region, sub in REGION_HINTS:
        if kw in text:
            return region, sub
    return "NEEDS CLASSIFICATION", ""

def normalize(name):
    n = re.sub(r"[^\w\s]", "", name.lower())
    n = re.sub(r"\s+", " ", n).strip()
    for s in ["restaurant","kitchen","cafe","house","garden",
              "bistro","seafood","noodle","dumplings","bbq","grill"]:
        n = n.replace(s, "").strip()
    return n

def safe_get(row, idx, default=""):
    """Safely get a cell value from a Sheet row list."""
    try:
        return row[idx].strip() if idx < len(row) else default
    except Exception:
        return default

# ── Dish vocabulary for review text mining ──────────────────────────────────────
DISH_VOCAB = {
    # Cantonese / Dim Sum
    "har gow","siu mai","char siu bao","cheung fun","lo bak go","turnip cake",
    "egg tart","dan tat","congee","wonton","zhaliang","roast duck","roast pork",
    "char siu","peking duck","pork ribs","spare ribs","chicken feet","tripe",
    "egg white","mango pudding","sesame ball","century egg",
    # Sichuan
    "dan dan noodles","dan dan","mapo tofu","kung pao chicken","twice cooked pork",
    "water boiled fish","boiled fish","toothpick lamb","mala","dry pot","liangfen",
    "cold noodles","chongqing noodles","green pepper","fish in chili oil",
    # Shanghai / Dumplings
    "xiao long bao","soup dumplings","xlb","sheng jian bao","pan fried bun",
    "scallion oil noodles","lion head meatball","dongpo pork","stir fried rice cake",
    "nian gao","rice cake",
    # Taiwanese
    "beef noodle soup","beef noodle","lu rou fan","braised pork rice",
    "three cup chicken","san bei ji","oyster vermicelli","scallion pancake",
    "pork chop rice","stinky tofu","pineapple cake",
    # Northern Chinese
    "beef roll","guotie","potsticker","hand pulled noodles","biang biang",
    "lamb noodle","jianbing","pancake",
    # Uyghur / Northwestern
    "big plate chicken","da pan ji","laghman","cumin lamb","lamb kebab",
    "rou jia mo","yangrou paomo","naan","pulled noodle",
    # Hunan
    "bullfrog","smoked pork","fish head","pickled pepper","red braised pork",
    # Hot pot
    "mala broth","wagyu beef","beef tripe","fish balls","hot pot",
    # Cantonese seafood
    "lobster","crab","geoduck","abalone","pea shoots","pea sprouts","clam",
    # Uyghur
    "lamb chop","skewer","kebab",
    # General popular
    "fried rice","chow mein","lo mein","wonton soup","shrimp","dumplings",
    "noodles","boba","milk tea","egg waffle","fantuan","sticky rice",
    "soy milk","hainan chicken","chicken rice","soup","porridge",
}

def extract_dishes(text):
    """Find dish mentions in text, return top 5 most-mentioned."""
    text_lower = text.lower()
    counts = Counter()
    for dish in DISH_VOCAB:
        if dish in text_lower:
            counts[dish] += text_lower.count(dish)
    return [d.title() for d, _ in counts.most_common(5)]

def merge_dishes(primary, secondary):
    """
    Merge two dish lists. Primary (Google) dishes come first.
    Secondary (Yelp) adds any new dishes not already in primary.
    Returns top 5 unique dishes.
    """
    seen = set()
    merged = []
    for d in primary + secondary:
        key = d.lower().strip()
        if key and key not in seen:
            seen.add(key)
            merged.append(d)
        if len(merged) >= 5:
            break
    return merged


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — GOOGLE PLACES (Discovery + Dish extraction)
# ══════════════════════════════════════════════════════════════════════════════

def g_nearby(lat, lng, radius, keyword, token=None):
    params = {
        "location": str(lat) + "," + str(lng),
        "radius": radius,
        "keyword": keyword,
        "type": "restaurant",
        "key": GOOGLE_API_KEY,
    }
    if token:
        params = {"pagetoken": token, "key": GOOGLE_API_KEY}
    r = requests.get(
        "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
        params=params, timeout=15
    )
    r.raise_for_status()
    return r.json()

def g_details(place_id):
    """
    Fetch full place details including reviews and editorial summary.
    reviews gives up to 5 snippets from real customers mentioning food.
    editorial_summary is Google's own short description — often mentions dishes.
    """
    r = requests.get(
        "https://maps.googleapis.com/maps/api/place/details/json",
        params={
            "place_id": place_id,
            "fields": (
                "place_id,name,formatted_address,geometry,"
                "formatted_phone_number,website,opening_hours,"
                "price_level,rating,user_ratings_total,"
                "photos,business_status,"
                "reviews,"           # ← up to 5 review snippets
                "editorial_summary"  # ← Google's own dish-mentioning blurb
            ),
            "key": GOOGLE_API_KEY,
        },
        timeout=15
    )
    r.raise_for_status()
    return r.json().get("result", {})

def g_photo_url(ref, width=1200):
    return (
        "https://maps.googleapis.com/maps/api/place/photo"
        "?maxwidth=" + str(width) +
        "&photo_reference=" + ref +
        "&key=" + GOOGLE_API_KEY
    )

def extract_dishes_from_google(details):
    """
    Extract dish mentions from Google Places data:
    1. editorial_summary — Google's own description
    2. reviews — up to 5 customer review snippets
    Returns top 5 dishes ranked by mention frequency.
    """
    texts = []

    # Editorial summary (very high signal — Google explicitly describes the restaurant)
    summary = details.get("editorial_summary", {})
    if summary:
        overview = summary.get("overview", "")
        if overview:
            texts.append(overview)
            texts.append(overview)  # Weight it double — highest quality signal

    # Customer reviews (each is a short snippet Google shows in search results)
    for review in details.get("reviews", []):
        text = review.get("text", "").strip()
        if text:
            texts.append(text)

    if not texts:
        return []

    combined = " ".join(texts)
    return extract_dishes(combined)

def collect_google(cities):
    """Phase 1a: Discover all Chinese restaurants via nearby search."""
    seen, results = set(), []
    for ci in cities:
        print("  [" + ci["city"] + "]", end=" ", flush=True)
        city_count = 0
        for term in SEARCH_TERMS:
            token, page = None, 0
            while True:
                try:
                    data = g_nearby(ci["lat"], ci["lng"], ci["radius"], term, token)
                except Exception as e:
                    print("\n    Error (" + term + "): " + str(e))
                    break
                for p in data.get("results", []):
                    pid = p.get("place_id")
                    if not pid or pid in seen:
                        continue
                    if p.get("business_status") in (
                            "PERMANENTLY_CLOSED", "CLOSED_TEMPORARILY"):
                        continue
                    name  = p.get("name", "")
                    types = p.get("types", [])
                    if not is_chinese(name, types):
                        continue
                    seen.add(pid)
                    city_count += 1
                    results.append({
                        "google_place_id": pid,
                        "name":  name,
                        "city":  ci["city"],
                        "lat":   p.get("geometry", {}).get("location", {}).get("lat"),
                        "lng":   p.get("geometry", {}).get("location", {}).get("lng"),
                        "price_level": p.get("price_level"),
                        "status": "OPEN",
                    })
                token = data.get("next_page_token")
                page += 1
                if not token or page >= 3:
                    break
                time.sleep(2)
        print(str(city_count) + " found")
    return results

def enrich_google_details(restaurants):
    """
    Phase 1b: Fetch full details for each restaurant.
    This is where we get hours, phone, photos, reviews, and editorial summary.
    Dishes are extracted from review text and editorial summary here.
    """
    enriched = []
    total = len(restaurants)
    dish_found = 0

    for i, r in enumerate(restaurants):
        pid = r["google_place_id"]
        if i % 20 == 0:
            print("    Details " + str(i+1) + "/" + str(total) +
                  " (dishes found: " + str(dish_found) + ")...")
        try:
            d = g_details(pid)

            # Hours
            texts = d.get("opening_hours", {}).get("weekday_text", [])
            days  = ["monday","tuesday","wednesday","thursday",
                     "friday","saturday","sunday"]
            hours = {}
            for day in days:
                m = [t for t in texts if t.lower().startswith(day)]
                hours[day[:3]] = m[0].split(": ", 1)[1] if m else None

            # Photos
            refs = [p["photo_reference"]
                    for p in d.get("photos", [])[:4]
                    if p.get("photo_reference")]
            urls = [g_photo_url(ref) for ref in refs]

            # ── Dish extraction from Google data ─────────────────────────
            google_dishes = extract_dishes_from_google(d)
            if google_dishes:
                dish_found += 1

            r.update({
                "address":        d.get("formatted_address", r.get("city", "")),
                "phone":          d.get("formatted_phone_number", ""),
                "website":        d.get("website", ""),
                "price_level":    d.get("price_level", r.get("price_level")),
                "google_rating":  d.get("rating"),
                "review_count":   d.get("user_ratings_total"),
                "hours":          hours,
                "photo_exterior": urls[0] if len(urls) > 0 else "",
                "photo_food1":    urls[1] if len(urls) > 1 else "",
                "photo_food2":    urls[2] if len(urls) > 2 else "",
                "photo_interior": urls[3] if len(urls) > 3 else "",
                "google_dishes":  google_dishes,   # ← NEW: dishes from Google
            })
        except Exception as e:
            print("    Warn (" + r["name"] + "): " + str(e))
            r.setdefault("hours", {})
            r.setdefault("google_dishes", [])
            for k in ["photo_exterior","photo_food1","photo_food2","photo_interior"]:
                r.setdefault(k, "")
        enriched.append(r)
        time.sleep(0.08)  # ~12 req/sec, well within quota

    print("  Google dish extraction: " + str(dish_found) + "/" + str(total) +
          " restaurants got dishes from Google")
    return enriched


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — YELP ENRICHMENT (Playwright, dishes merge with Google data)
# ══════════════════════════════════════════════════════════════════════════════

# User agents to rotate — makes each session look like a different browser
USER_AGENTS = [
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
]

VIEWPORTS = [
    {"width": 1280, "height": 900},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
]

async def new_browser_context(pw):
    """Create a new browser + context with randomised fingerprint."""
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-extensions",
        ]
    )
    ua = random.choice(USER_AGENTS)
    vp = random.choice(VIEWPORTS)
    context = await browser.new_context(
        viewport=vp,
        user_agent=ua,
        locale="en-US",
        timezone_id="America/Los_Angeles",
        java_script_enabled=True,
    )
    # Mask all common automation signals
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
        window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}};
        Object.defineProperty(navigator, 'permissions', {
            get: () => ({query: () => Promise.resolve({state: 'granted'})})
        });
    """)
    return browser, context

async def yelp_find_business(page, name, city):
    """
    Search Yelp for a restaurant. Returns (url, biz_id) or (None, None).
    Detects CAPTCHA and returns None immediately rather than hanging.
    """
    search_url = (
        "https://www.yelp.com/search"
        "?find_desc=" + requests.utils.quote(name) +
        "&find_loc=" + requests.utils.quote(city + " CA")
    )
    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        # Human-like pause before reading the page
        await page.wait_for_timeout(random.randint(1500, 2800))

        content = await page.content()

        # Detect CAPTCHA / bot challenge
        captcha_signals = [
            "captcha", "unusual traffic", "are you a robot",
            "access to this page has been", "please verify"
        ]
        if any(s in content.lower() for s in captcha_signals):
            return None, None  # Caller marks as captcha

        # Find first /biz/ link that looks like a business page
        links = await page.query_selector_all('a[href*="/biz/"]')
        for link in links:
            href = await link.get_attribute("href")
            if not href or "/biz/" not in href:
                continue
            biz_part = href.split("/biz/")[1].split("?")[0].strip("/")
            if not biz_part or "/" in biz_part:
                continue  # Skip categories/collections
            full_url = "https://www.yelp.com/biz/" + biz_part
            return full_url, biz_part

        return None, None

    except Exception as e:
        return None, None

async def yelp_scrape_business(page, url):
    """
    Scrape a Yelp business page.
    Returns dict with rating, review_count, price, photos, dishes, highlights.
    Returns None on CAPTCHA.
    """
    from bs4 import BeautifulSoup

    result = {
        "yelp_rating":       None,
        "yelp_review_count": None,
        "yelp_price":        None,
        "yelp_photos":       [],
        "yelp_dishes":       [],
        "yelp_highlights":   [],
    }

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(random.randint(2500, 4000))

        html = await page.content()

        # Check for CAPTCHA on business page too
        if any(s in html.lower() for s in ["captcha","unusual traffic","are you a robot"]):
            return None  # Signal CAPTCHA to caller

        soup = BeautifulSoup(html, "html.parser")

        # Rating
        for el in soup.find_all(attrs={"aria-label": True}):
            m = re.search(r"([\d.]+) star rating", el.get("aria-label", ""))
            if m:
                result["yelp_rating"] = float(m.group(1))
                break

        # Review count
        for el in soup.find_all(string=re.compile(r"\d[\d,]*\s+reviews?", re.I)):
            m = re.search(r"([\d,]+)\s+review", str(el), re.I)
            if m:
                result["yelp_review_count"] = int(m.group(1).replace(",", ""))
                break

        # Price
        for el in soup.find_all(string=re.compile(r"^\$+$")):
            result["yelp_price"] = el.strip()
            break

        # Photos
        photos = []
        for img in soup.find_all("img", src=re.compile(r"yelpcdn\.com")):
            src = img.get("src", "")
            src = re.sub(r"/(ms|ss|ls|s|m)\.", "/o.", src)
            if src and src not in photos and "avatar" not in src:
                photos.append(src)
            if len(photos) >= 4:
                break
        result["yelp_photos"] = photos

        # Review text for dish extraction
        chunks = []
        for el in soup.find_all("p", {"lang": "en"}):
            chunks.append(el.get_text(" ", strip=True))
        for el in soup.find_all("span", class_=re.compile(r"raw__", re.I)):
            t = el.get_text(" ", strip=True)
            if len(t) > 40:
                chunks.append(t)
        result["yelp_dishes"] = extract_dishes(" ".join(chunks[:MAX_REVIEW_TEXT]))

        # Review highlights (first sentence of top reviews)
        highlights = []
        for p in soup.find_all("p", {"lang": "en"})[:5]:
            text = p.get_text(" ", strip=True)
            if len(text) < 30:
                continue
            sent = re.split(r"(?<=[.!?])\s", text)[0].strip()
            if 25 < len(sent) < 180:
                highlights.append(sent)
        result["yelp_highlights"] = highlights[:3]

    except Exception as e:
        pass  # Return partial result, not None (None = CAPTCHA)

    return result

async def enrich_with_yelp(restaurants):
    """
    Phase 2: Yelp enrichment with anti-detection and graceful CAPTCHA handling.
    Yelp dishes MERGE with Google dishes (Google takes priority).
    Tracks CAPTCHA hits and reports them.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("\nWARNING: playwright not installed — skipping Yelp enrichment")
        print("  Fix: pip install playwright && playwright install chromium\n")
        return restaurants

    total      = len(restaurants)
    found      = 0
    not_found  = 0
    captcha    = 0
    dish_added = 0

    est_min = total * (YELP_DELAY_MIN + YELP_DELAY_MAX) // 2 // 60
    print("\nPhase 2 — Yelp enrichment")
    print("  Restaurants: " + str(total))
    print("  Delay: " + str(YELP_DELAY_MIN) + "-" + str(YELP_DELAY_MAX) + "s per request")
    print("  Estimated: ~" + str(est_min) + " minutes")
    print("  Note: CAPTCHA hits are skipped automatically\n")

    enriched = []

    async with async_playwright() as pw:
        browser, context = await new_browser_context(pw)
        page = await context.new_page()

        # Warm up — visit Yelp homepage before searching
        try:
            await page.goto("https://www.yelp.com",
                            wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(random.randint(2000, 3500))
        except Exception:
            pass

        for i, r in enumerate(restaurants):
            name = r.get("name", "")
            city = r.get("city", "")
            google_dishes = r.get("google_dishes", [])

            print("  [" + str(i+1) + "/" + str(total) + "] " +
                  name + " (" + city + ")", end=" ... ", flush=True)

            # Step 1: Find on Yelp
            url, biz_id = await yelp_find_business(page, name, city)

            if url is None and biz_id is None:
                # Could be CAPTCHA or just not found — check page content
                try:
                    content = await page.content()
                    hit_captcha = any(s in content.lower() for s in
                                     ["captcha","unusual traffic","are you a robot"])
                except Exception:
                    hit_captcha = False

                if hit_captcha:
                    print("CAPTCHA — resetting browser")
                    captcha += 1
                    # Reset browser completely on CAPTCHA
                    try:
                        await browser.close()
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(8, 15))
                    browser, context = await new_browser_context(pw)
                    page = await context.new_page()
                    # Re-warm after reset
                    try:
                        await page.goto("https://www.yelp.com",
                                        wait_until="domcontentloaded", timeout=15000)
                        await page.wait_for_timeout(random.randint(3000, 5000))
                    except Exception:
                        pass
                else:
                    print("not found on Yelp")
                    not_found += 1

                r["yelp_id"] = ""
                r["yelp_dishes"] = []
                # Keep Google dishes as the dish source
                r["final_dishes"] = google_dishes[:3]
                enriched.append(r)
                await page.wait_for_timeout(random.randint(2000, 3500))
                continue

            r["yelp_id"] = biz_id or ""

            # Step 2: Scrape business page
            await page.wait_for_timeout(random.randint(1000, 2000))
            yd = await yelp_scrape_business(page, url)

            if yd is None:
                # CAPTCHA on business page
                print("CAPTCHA on biz page — resetting")
                captcha += 1
                try:
                    await browser.close()
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(8, 15))
                browser, context = await new_browser_context(pw)
                page = await context.new_page()
                try:
                    await page.goto("https://www.yelp.com",
                                    wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(random.randint(3000, 5000))
                except Exception:
                    pass
                r["final_dishes"] = google_dishes[:3]
                enriched.append(r)
                continue

            # Merge: Google dishes first, Yelp adds anything new
            yelp_dishes = yd.get("yelp_dishes", [])
            final_dishes = merge_dishes(google_dishes, yelp_dishes)
            if len(final_dishes) > len(google_dishes):
                dish_added += 1

            r.update({
                "yelp_rating":       yd["yelp_rating"],
                "yelp_review_count": yd["yelp_review_count"],
                "yelp_price":        yd["yelp_price"],
                "yelp_dishes":       yelp_dishes,
                "yelp_highlights":   yd["yelp_highlights"],
                "final_dishes":      final_dishes,
            })

            # Fill photo gaps: use Yelp photos where Google has none
            yelp_ph = yd.get("yelp_photos", [])
            if not r.get("photo_food1")    and len(yelp_ph) > 0:
                r["photo_food1"]    = yelp_ph[0]
            if not r.get("photo_food2")    and len(yelp_ph) > 1:
                r["photo_food2"]    = yelp_ph[1]
            if not r.get("photo_exterior") and len(yelp_ph) > 2:
                r["photo_exterior"] = yelp_ph[2]
            if not r.get("photo_interior") and len(yelp_ph) > 3:
                r["photo_interior"] = yelp_ph[3]

            # Yelp price as fallback
            if not r.get("price_level") and yd.get("yelp_price"):
                r["yelp_price_str"] = yd["yelp_price"]

            found += 1

            dishes_preview = ", ".join(final_dishes[:3]) or "none"
            rating_str = str(yd["yelp_rating"]) if yd["yelp_rating"] else "n/a"
            print("★" + rating_str + " | dishes: " + dishes_preview)

            enriched.append(r)

            # Pacing — longer delays = fewer CAPTCHAs
            delay = random.uniform(YELP_DELAY_MIN, YELP_DELAY_MAX)
            await page.wait_for_timeout(int(delay * 1000))

            # Reset browser every 40 restaurants to avoid fingerprint buildup
            if (i + 1) % 40 == 0 and i < total - 1:
                print("  Rotating browser session...")
                try:
                    await browser.close()
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(3, 6))
                browser, context = await new_browser_context(pw)
                page = await context.new_page()
                try:
                    await page.goto("https://www.yelp.com",
                                    wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(random.randint(2000, 4000))
                except Exception:
                    pass

        try:
            await browser.close()
        except Exception:
            pass

    print("\n  Yelp enrichment complete:")
    print("    Found on Yelp:   " + str(found) + "/" + str(total))
    print("    Not found:       " + str(not_found))
    print("    CAPTCHA hits:    " + str(captcha))
    print("    Yelp added new dishes to: " + str(dish_added) + " restaurants")
    return enriched


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — WRITE TO GOOGLE SHEET
# ══════════════════════════════════════════════════════════════════════════════

def sheets_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("Run: pip install gspread google-auth")
    creds = Credentials.from_service_account_info(
        json.loads(SA_JSON),
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
    )
    return gspread.Client(auth=creds)

def write_to_sheet(restaurants):
    client = sheets_client()
    ws     = client.open_by_key(SPREADSHEET_ID).worksheet("Restaurants")

    existing      = ws.get_all_values()
    DATA_START    = 3
    existing_pids  = set()
    existing_names = set()
    for row in existing[DATA_START:]:
        if len(row) >= 34 and row[33].strip():
            existing_pids.add(row[33].strip())
        if len(row) >= 2 and row[1].strip():
            existing_names.add(normalize(row[1].strip()))

    today   = date.today().isoformat()
    to_add  = []
    skipped = 0

    for r in restaurants:
        pid = r.get("google_place_id", "")
        if pid and pid in existing_pids:
            skipped += 1; continue
        if normalize(r.get("name", "")) in existing_names:
            skipped += 1; continue

        region, sub = auto_classify(r.get("name", ""))
        price = (r.get("yelp_price_str") or r.get("yelp_price")
                 or PRICE_MAP.get(r.get("price_level"), "$$"))
        hours = r.get("hours", {})

        addr   = r.get("address", "")
        street = addr.split(",")[0].strip() if "," in addr else addr
        zip_m  = re.search(r"\b(9\d{4})\b", addr)
        zip_cd = zip_m.group(1) if zip_m else ""

        # Use final_dishes (Google + Yelp merged) for columns AA-AC
        dishes = r.get("final_dishes") or r.get("google_dishes") or r.get("yelp_dishes") or []
        dish1 = dishes[0] if len(dishes) > 0 else ""
        dish2 = dishes[1] if len(dishes) > 1 else ""
        dish3 = dishes[2] if len(dishes) > 2 else ""

        # Build notes from Yelp rating + highlights
        parts = []
        if r.get("yelp_rating"):
            cnt = r.get("yelp_review_count", "")
            parts.append("Yelp {:.1f}".format(r["yelp_rating"]) +
                         (" (" + str(cnt) + " reviews)" if cnt else ""))
        highlights = r.get("yelp_highlights", [])
        if highlights:
            parts.append(highlights[0])
        notes = " — ".join(parts)

        sources = "Google Maps"
        if r.get("yelp_id"):
            sources += ",Yelp"

        row = [
            "R" + str(5000 + len(to_add)).zfill(4),  # A  ID
            r.get("name", ""),              # B
            "",                             # C  Chinese name
            "OPEN",                         # D
            street,                         # E
            r.get("city", ""),              # F
            zip_cd,                         # G
            r.get("lat") or "",             # H
            r.get("lng") or "",             # I
            region,                         # J
            sub,                            # K
            "",                             # L  Province
            "",                             # M  Secondary regions
            "",                             # N  Category
            price,                          # O
            r.get("phone", ""),             # P
            r.get("website", ""),           # Q
            "FALSE",                        # R  Halal
            "FALSE",                        # S  Michelin
            hours.get("mon", "") or "",     # T
            hours.get("tue", "") or "",     # U
            hours.get("wed", "") or "",     # V
            hours.get("thu", "") or "",     # W
            hours.get("fri", "") or "",     # X
            hours.get("sat", "") or "",     # Y
            hours.get("sun", "") or "",     # Z
            dish1,                          # AA
            dish2,                          # AB
            dish3,                          # AC
            r.get("photo_exterior", ""),    # AD
            r.get("photo_food1", ""),       # AE
            r.get("photo_food2", ""),       # AF
            r.get("photo_interior", ""),    # AG
            pid,                            # AH  Google Place ID
            r.get("yelp_id", ""),           # AI  Yelp ID
            "",                             # AJ  Dianping
            notes,                          # AK
            sources,                        # AL
            today,                          # AM  Date Added
            today,                          # AN  Date Verified
            "Research Script",              # AO
        ]
        to_add.append(row)

    if to_add:
        BATCH = 100
        for start in range(0, len(to_add), BATCH):
            chunk = to_add[start:start + BATCH]
            ws.append_rows(chunk, value_input_option="USER_ENTERED")
            print("    Wrote rows " + str(start+1) + "–" + str(start+len(chunk)))
            time.sleep(1)

    print("\n  Added:   " + str(len(to_add)) + " new restaurants")
    print("  Skipped: " + str(skipped) + " (already in sheet)")
    return len(to_add)


# ══════════════════════════════════════════════════════════════════════════════
# TEST + MAIN
# ══════════════════════════════════════════════════════════════════════════════

def test_google():
    print("Google Places API...", end=" ")
    if not GOOGLE_API_KEY:
        print("ERROR: GOOGLE_API_KEY not set"); return False
    try:
        # Test nearby search
        data = g_nearby(34.0831, -118.1286, 300, "restaurant")
        n = len(data.get("results", []))
        # Test details with reviews field on a known SGV place
        detail = g_details("ChIJaYS_w5vXwoARopWVJyXxpFE")  # Chengdu Taste
        has_reviews = bool(detail.get("reviews"))
        has_summary = bool(detail.get("editorial_summary"))
        print("OK (" + str(n) + " nearby results, " +
              "reviews=" + str(has_reviews) + ", " +
              "editorial_summary=" + str(has_summary) + ")")
        return True
    except Exception as e:
        print("FAILED: " + str(e)); return False

async def test_yelp():
    print("Playwright + Yelp...", end=" ")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: playwright not installed")
        print("  Fix: pip install playwright && playwright install chromium")
        return False
    try:
        async with async_playwright() as pw:
            browser, context = await new_browser_context(pw)
            page = await context.new_page()
            await page.goto(
                "https://www.yelp.com/biz/chengdu-taste-alhambra-2",
                wait_until="domcontentloaded", timeout=20000
            )
            content = await page.content()
            await browser.close()
        if any(s in content.lower() for s in
               ["captcha","unusual traffic","are you a robot"]):
            print("CAPTCHA on test — Yelp is blocking. Use --no-yelp for now.")
            return False
        if "chengdu" in content.lower():
            print("OK (Yelp page loaded, no CAPTCHA)")
            return True
        print("WARN (loaded but unexpected content)")
        return True
    except Exception as e:
        print("FAILED: " + str(e)); return False

# ══════════════════════════════════════════════════════════════════════════════
# ENRICH MODE — Update dish columns on existing Sheet rows
# ══════════════════════════════════════════════════════════════════════════════

# Sheet column indices (0-based)
COL_ID       = 0   # A
COL_NAME     = 1   # B
COL_STATUS   = 3   # D
COL_CITY     = 5   # F
COL_DISH1    = 26  # AA
COL_DISH2    = 27  # AB
COL_DISH3    = 28  # AC
COL_PLACE_ID = 33  # AH
COL_YELP_ID  = 34  # AI

async def enrich_existing_dishes(overwrite=False, run_yelp=False,
                                  dry_run=False, limit=0, city_filter=None):
    """
    Reads all existing restaurants from the Sheet and enriches their
    dish columns (AA-AC) using Google reviews + optional Yelp scraping.

    overwrite   — if True, overwrites existing dish data
                  if False, only fills rows where AA-AC are all empty
    run_yelp    — if True, also run Yelp scraping after Google
    dry_run     — preview changes without writing anything
    limit       — max restaurants to process (0 = all)
    city_filter — set of city names to restrict to, or None for all
    """
    print("=" * 60)
    print("626 Eats — Dish Enrichment Mode")
    print("Mode:    " + ("OVERWRITE all dish data" if overwrite
                          else "Fill empty dish columns only"))
    print("Yelp:    " + ("enabled" if run_yelp else "disabled"))
    print("Dry run: " + str(dry_run))
    if city_filter:
        print("Cities:  " + ", ".join(sorted(city_filter)))
    if limit:
        print("Limit:   " + str(limit) + " restaurants")
    print("=" * 60)

    print("\nConnecting to Google Sheet...")
    client = sheets_client()
    ws     = client.open_by_key(SPREADSHEET_ID).worksheet("Restaurants")
    rows   = ws.get_all_values()
    DATA_START = 3

    # ── Build list of rows to process ────────────────────────────────────────
    to_process = []
    for i, row in enumerate(rows[DATA_START:], start=DATA_START):
        rest_id  = safe_get(row, COL_ID)
        name     = safe_get(row, COL_NAME)
        status   = safe_get(row, COL_STATUS)
        city     = safe_get(row, COL_CITY)
        place_id = safe_get(row, COL_PLACE_ID)
        dish1    = safe_get(row, COL_DISH1)
        dish2    = safe_get(row, COL_DISH2)
        dish3    = safe_get(row, COL_DISH3)
        yelp_id  = safe_get(row, COL_YELP_ID)

        if not rest_id or not name:
            continue
        if status.upper() == "CLOSED":
            continue
        if city_filter and city not in city_filter:
            continue
        if not place_id:
            continue  # Can't enrich without a Google Place ID

        has_dishes = bool(dish1 or dish2 or dish3)
        if has_dishes and not overwrite:
            continue  # Skip rows that already have dishes

        to_process.append({
            "sheet_row": i,
            "rest_id":   rest_id,
            "name":      name,
            "city":      city,
            "place_id":  place_id,
            "yelp_id":   yelp_id,
            "current":   [dish1, dish2, dish3],
            "has_dishes": has_dishes,
        })

        if limit and len(to_process) >= limit:
            break

    total = len(to_process)
    if total == 0:
        if overwrite:
            print("\nNo restaurants with Google Place IDs found.")
        else:
            print("\nAll restaurants already have dish data.")
            print("Use --enrich --overwrite to refresh them.")
        return

    already = sum(1 for r in to_process if r["has_dishes"])
    print("\nRestaurants to process: " + str(total))
    if overwrite and already:
        print("  " + str(already) + " already have dishes (will overwrite)")
    print("  Google API cost: ~$" + "{:.2f}".format(total * 0.017))

    # ── Phase 1: Google dish extraction ──────────────────────────────────────
    print("\nExtracting dishes from Google reviews + editorial summaries...")
    google_results = {}
    g_found = 0

    for i, r in enumerate(to_process):
        if i % 25 == 0 and i > 0:
            print("  " + str(i) + "/" + str(total) +
                  " done (" + str(g_found) + " got dishes)")
        try:
            d = g_details(r["place_id"])
            dishes = extract_dishes_from_google(d)
        except Exception as e:
            print("  Error (" + r["name"] + "): " + str(e))
            dishes = []
        google_results[r["place_id"]] = dishes
        if dishes:
            g_found += 1
        time.sleep(0.08)

    print("  Google done: " + str(g_found) + "/" + str(total) +
          " restaurants got dish data")

    # ── Phase 2: Yelp enrichment (optional) ──────────────────────────────────
    yelp_results = {}
    yelp_ids     = {}

    if run_yelp:
        try:
            from playwright.async_api import async_playwright
            playwright_ok = True
        except ImportError:
            print("\nWARNING: playwright not installed — skipping Yelp")
            print("  Fix: pip install playwright && playwright install chromium\n")
            playwright_ok = False

        if playwright_ok:
            y_found = 0
            y_captcha = 0
            est = total * (YELP_DELAY_MIN + YELP_DELAY_MAX) // 2 // 60
            direct = sum(1 for r in to_process if r["yelp_id"])
            print("\nYelp enrichment (~" + str(est) + " min)")
            print("  " + str(direct) + " have existing Yelp IDs (direct page load)")
            print("  " + str(total - direct) + " need search\n")

            async with async_playwright() as pw:
                browser, context = await new_browser_context(pw)
                page = await context.new_page()
                try:
                    await page.goto("https://www.yelp.com",
                                    wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(random.randint(2000, 3500))
                except Exception:
                    pass

                for i, r in enumerate(to_process):
                    name    = r["name"]
                    city    = r["city"]
                    pid     = r["place_id"]
                    yelp_id = r["yelp_id"]

                    print("  [" + str(i+1) + "/" + str(total) + "] " +
                          name, end=" ... ", flush=True)

                    # Go direct to biz page if we already have the Yelp ID
                    captcha_hit = False
                    new_yelp_id = yelp_id
                    y_dishes    = []

                    if yelp_id:
                        biz_url = "https://www.yelp.com/biz/" + yelp_id
                        try:
                            await page.goto(biz_url,
                                            wait_until="domcontentloaded",
                                            timeout=25000)
                            await page.wait_for_timeout(
                                random.randint(2000, 3500))
                            html = await page.content()
                            if any(s in html.lower() for s in [
                                    "captcha","unusual traffic","are you a robot"]):
                                captcha_hit = True
                            else:
                                from bs4 import BeautifulSoup
                                soup = BeautifulSoup(html, "html.parser")
                                chunks = [el.get_text(" ", strip=True)
                                          for el in soup.find_all("p", {"lang":"en"})]
                                y_dishes = extract_dishes(
                                    " ".join(chunks[:MAX_REVIEW_TEXT]))
                        except Exception:
                            pass
                    else:
                        url, new_yelp_id = await yelp_find_business(
                            page, name, city)
                        if url is None:
                            try:
                                html = await page.content()
                                captcha_hit = any(s in html.lower() for s in [
                                    "captcha","unusual traffic","are you a robot"])
                            except Exception:
                                captcha_hit = False
                        elif url:
                            yd = await yelp_scrape_business(page, url)
                            if yd is None:
                                captcha_hit = True
                            else:
                                y_dishes = yd.get("yelp_dishes", [])

                    if captcha_hit:
                        print("CAPTCHA — resetting browser")
                        y_captcha += 1
                        try:
                            await browser.close()
                        except Exception:
                            pass
                        await asyncio.sleep(random.uniform(8, 15))
                        browser, context = await new_browser_context(pw)
                        page = await context.new_page()
                        try:
                            await page.goto("https://www.yelp.com",
                                            wait_until="domcontentloaded",
                                            timeout=15000)
                            await page.wait_for_timeout(
                                random.randint(3000, 5000))
                        except Exception:
                            pass
                    else:
                        if y_dishes:
                            print(", ".join(y_dishes[:3]))
                            y_found += 1
                        else:
                            print("no dishes")

                    yelp_results[pid] = y_dishes
                    yelp_ids[pid]     = new_yelp_id

                    delay = random.uniform(YELP_DELAY_MIN, YELP_DELAY_MAX)
                    await page.wait_for_timeout(int(delay * 1000))

                    if (i + 1) % 40 == 0 and i < total - 1:
                        print("  Rotating browser...")
                        try:
                            await browser.close()
                        except Exception:
                            pass
                        await asyncio.sleep(random.uniform(4, 8))
                        browser, context = await new_browser_context(pw)
                        page = await context.new_page()
                        try:
                            await page.goto("https://www.yelp.com",
                                            wait_until="domcontentloaded",
                                            timeout=15000)
                            await page.wait_for_timeout(
                                random.randint(2000, 4000))
                        except Exception:
                            pass

                try:
                    await browser.close()
                except Exception:
                    pass

            print("\n  Yelp complete: " + str(y_found) + " got dishes, " +
                  str(y_captcha) + " CAPTCHA hits")

    # ── Write to Sheet ────────────────────────────────────────────────────────
    print("\n" + ("-" * 60))
    if dry_run:
        print("DRY RUN — showing what would be written:\n")

    updated       = 0
    no_data       = 0
    unchanged     = 0
    batch_updates = []

    for r in to_process:
        pid      = r["place_id"]
        g_dishes = google_results.get(pid, [])
        y_dishes = yelp_results.get(pid, [])
        final    = merge_dishes(g_dishes, y_dishes)[:3]

        if not final:
            no_data += 1
            continue

        dish1 = final[0] if len(final) > 0 else ""
        dish2 = final[1] if len(final) > 1 else ""
        dish3 = final[2] if len(final) > 2 else ""

        current  = r["current"]
        new_vals = [dish1, dish2, dish3]
        if current == new_vals:
            unchanged += 1
            continue

        src_tag = ("G+Y" if (g_dishes and y_dishes) else
                   "G"   if g_dishes else "Y")
        old_str = " | ".join(filter(None, current)) or "(empty)"
        new_str = " | ".join(filter(None, new_vals))

        print("  [" + src_tag + "] " + r["name"] + " (" + r["city"] + ")")
        if overwrite and any(current):
            print("    Was: " + old_str)
        print("    Now: " + new_str)

        if not dry_run:
            sheet_row = r["sheet_row"] + 1  # 1-based
            batch_updates.append({
                "range":  "AA" + str(sheet_row) + ":AC" + str(sheet_row),
                "values": [[dish1, dish2, dish3]],
            })
            # Save new Yelp ID if we discovered one
            nyi = yelp_ids.get(pid)
            if run_yelp and nyi and not r["yelp_id"]:
                batch_updates.append({
                    "range":  "AI" + str(sheet_row),
                    "values": [[nyi]],
                })

        updated += 1

    # Batch write all changes
    if not dry_run and batch_updates:
        BATCH = 200
        for start in range(0, len(batch_updates), BATCH):
            ws.batch_update(batch_updates[start:start + BATCH])
            time.sleep(0.5)

    print("\n" + ("=" * 60))
    print("Enrich complete" + (" (DRY RUN)" if dry_run else "") + "!")
    print("  Updated:   " + str(updated))
    print("  No data:   " + str(no_data) +
          "  ← no dish mentions found in Google reviews")
    print("  Unchanged: " + str(unchanged))

    if no_data:
        print("\nTip: " + str(no_data) + " restaurants had no dish mentions.")
        print("  Add dishes manually for these, or try --enrich --yelp")
        print("  for Yelp review data.")
    if dry_run:
        print("\nRun without --dry-run to write changes to your Sheet.")


async def main():
    parser = argparse.ArgumentParser(
        description="626 Eats Research Sweep — Google + Yelp"
    )
    # ── Sweep modes ────────────────────────────────────────────────────────
    parser.add_argument("--test",      action="store_true",
                        help="Test API connections and exit")
    parser.add_argument("--no-yelp",   action="store_true",
                        help="Skip Yelp enrichment (faster, Google dishes only)")
    parser.add_argument("--no-sheet",  action="store_true",
                        help="Preview only, no Sheet write")
    parser.add_argument("--cities",    default="all",
                        help='Cities to sweep e.g. "Alhambra,San Gabriel" or "all"')
    # ── Enrich mode (update existing rows) ────────────────────────────────
    parser.add_argument("--enrich",    action="store_true",
                        help="Enrich dish columns on existing Sheet rows (no new discovery)")
    parser.add_argument("--overwrite", action="store_true",
                        help="With --enrich: overwrite dish data even if already filled")
    parser.add_argument("--yelp",      action="store_true",
                        help="With --enrich: also run Yelp after Google")
    parser.add_argument("--dry-run",   action="store_true",
                        help="With --enrich: preview changes without writing")
    parser.add_argument("--limit",     type=int, default=0,
                        help="With --enrich: only process first N restaurants")
    parser.add_argument("--filter-city", default="",
                        help='With --enrich: only process these cities e.g. "Alhambra"')
    args = parser.parse_args()

    if args.test:
        print("Testing connections...\n")
        g_ok = test_google()
        y_ok = await test_yelp()
        print("\n" + ("All systems go." if (g_ok and y_ok) else
              "Issues found — see above. Use --no-yelp if Yelp is blocking."))
        sys.exit(0 if g_ok else 1)

    if not GOOGLE_API_KEY:
        sys.exit("ERROR: Set GOOGLE_API_KEY\n  $env:GOOGLE_API_KEY = 'AIzaSy...'")

    # ── ENRICH MODE: update dish columns on existing rows ────────────────────
    if args.enrich:
        if not SPREADSHEET_ID or not SA_JSON:
            sys.exit("ERROR: SPREADSHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON "
                     "are required for --enrich")
        city_filter = None
        if args.filter_city:
            city_filter = {c.strip() for c in args.filter_city.split(",")}
        await enrich_existing_dishes(
            overwrite=args.overwrite,
            run_yelp=args.yelp,
            dry_run=args.dry_run,
            limit=args.limit,
            city_filter=city_filter,
        )
        return  # Done — don't run the sweep

    # ── SWEEP MODE: discover new restaurants ─────────────────────────────────
    if args.cities.lower() == "all":
        cities = SGV_CITIES
    else:
        wanted = {c.strip() for c in args.cities.split(",")}
        cities = [c for c in SGV_CITIES if c["city"] in wanted]
        if not cities:
            sys.exit("No cities matched. Available: " +
                     ", ".join(c["city"] for c in SGV_CITIES))

    yelp_on = not args.no_yelp
    print("=" * 60)
    print("626 Eats Research Sweep")
    print("Cities:  " + str(len(cities)))
    print("Google:  enabled (discovery + dish extraction from reviews)")
    print("Yelp:    " + ("enabled (merges with Google dishes)" if yelp_on
                          else "disabled (--no-yelp)"))
    print("=" * 60)

    # Phase 1a: Discover
    print("\nPhase 1a — Google Places discovery...")
    raw = collect_google(cities)
    print("\n  Found " + str(len(raw)) + " unique Chinese restaurants")

    if not raw:
        print("No results. Run --test to check your API key.")
        return

    # Phase 1b: Enrich with details + extract dishes from Google reviews
    print("\nPhase 1b — Fetching details + extracting dishes from Google reviews...")
    restaurants = enrich_google_details(raw)

    # Phase 2: Yelp enrichment (merge dishes, fill photo gaps)
    if yelp_on:
        restaurants = await enrich_with_yelp(restaurants)
    else:
        print("\nPhase 2 — Yelp skipped (--no-yelp)")
        # Set final_dishes = google_dishes for consistency
        for r in restaurants:
            r["final_dishes"] = r.get("google_dishes", [])[:3]

    # Phase 3: Write
    print("\nPhase 3 — Writing to Google Sheet...")
    if args.no_sheet:
        print("  (--no-sheet: preview only)\n")
        total_with_dishes = sum(1 for r in restaurants if r.get("final_dishes"))
        print("  " + str(total_with_dishes) + "/" + str(len(restaurants)) +
              " restaurants got dishes")
        print()
        print("  " + "Name".ljust(35) + "City".ljust(18) + "Dishes")
        print("  " + "-" * 80)
        for r in restaurants[:20]:
            dishes = ", ".join(r.get("final_dishes", [])[:2]) or "—"
            print("  " + r["name"][:34].ljust(35) +
                  r["city"][:17].ljust(18) + dishes)
        if len(restaurants) > 20:
            print("  ... and " + str(len(restaurants)-20) + " more")
        return

    if not SPREADSHEET_ID or not SA_JSON:
        print("  NOTE: SPREADSHEET_ID or SA_JSON not set. Set env vars to write.")
        total_with_dishes = sum(1 for r in restaurants if r.get("final_dishes"))
        print("  " + str(total_with_dishes) + "/" + str(len(restaurants)) +
              " restaurants would get dishes")
        return

    added = write_to_sheet(restaurants)

    # Count dishes
    total_with_dishes = sum(1 for r in restaurants if r.get("final_dishes"))

    print("\n" + "=" * 60)
    print("Sweep complete!")
    print("  " + str(added) + " new restaurants added to your Sheet")
    print("  " + str(total_with_dishes) + "/" + str(len(restaurants)) +
          " restaurants got dish data")
    print("\nNext steps:")
    print("  1. Filter col J = 'NEEDS CLASSIFICATION' and assign regions")
    print("  2. Review cols AA-AC (dishes) — edit any wrong ones")
    print("  3. Run classify_regions.py for AI classification")
    print("  4. Run export_json.py then git push")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
