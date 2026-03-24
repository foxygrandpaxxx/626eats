#!/usr/bin/env python3
"""
scripts/research_sweep.py
==========================
Full SGV restaurant research pipeline:

  Phase 1 - Google Places API
    Searches 16 SGV cities with 6 search terms.
    Fetches full details: hours, phone, photos (4 per restaurant).

  Phase 2 - Yelp Enrichment (Playwright headless browser)
    Automatically finds each restaurant on Yelp.
    Extracts: rating, review count, price, photos, dish mentions
    from review text, and review highlight snippets.
    Uses ~8s delays and browser resets to avoid bot detection.

  Phase 3 - Write to Google Sheet
    Appends new restaurants only (deduplicates by Place ID and name).
    Flags unclassified restaurants for manual review.

INSTALL:
  pip install requests gspread google-auth playwright beautifulsoup4
  playwright install chromium

USAGE:
  python scripts/research_sweep.py
  python scripts/research_sweep.py --test
  python scripts/research_sweep.py --cities "Alhambra,San Gabriel"
  python scripts/research_sweep.py --no-yelp
  python scripts/research_sweep.py --no-sheet

ENV VARS (required):
  GOOGLE_API_KEY
  GOOGLE_SERVICE_ACCOUNT_JSON
  SPREADSHEET_ID
"""

import os, sys, re, json, time, random, asyncio, argparse, requests
from datetime import date
from collections import Counter

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
SA_JSON        = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

YELP_DELAY_MIN      = 6
YELP_DELAY_MAX      = 11
MAX_REVIEW_SNIPPETS = 30

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

CHINESE_KW = {
    "dim sum", "cantonese", "sichuan", "szechuan", "shanghainese", "taiwanese",
    "peking", "beijing", "hunan", "chinese", "mandarin", "hong kong",
    "dumplings", "noodle", "hot pot", "hotpot", "char siu", "chiu chow",
    "teochew", "uyghur", "xinjiang", "shaanxi", "yunnan", "fujianese",
    "fujian", "hakka", "roast duck", "bbq pork",
}
NON_CHINESE = {
    "japanese", "korean", "thai", "vietnamese", "indian", "mexican", "pizza",
    "burger", "sushi", "ramen", "pho", "italian", "french", "sandwich",
    "wings", "turkish", "persian", "mediterranean", "greek", "american grill",
}

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
    ("taiwanese",     "Taiwanese",        "Classic Taiwanese"),
    ("beef noodle",   "Taiwanese",        "Classic Taiwanese"),
    ("peking",        "Northern Chinese", "Beijing & Imperial Court"),
    ("beijing",       "Northern Chinese", "Beijing & Imperial Court"),
    ("dumpling",      "Northern Chinese", "Northeastern / Dongbei"),
    ("dongbei",       "Northern Chinese", "Northeastern / Dongbei"),
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
    ("hainan",        "Modern & Fusion",  "Hainan / SE Asian-Chinese"),
    ("noodle",        "Northern Chinese", "Northeastern / Dongbei"),
]

PRICE_MAP = {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}

DISH_VOCABULARY = [
    "har gow", "siu mai", "char siu bao", "cheung fun", "lo bak go",
    "turnip cake", "egg tart", "dan tat", "congee", "wonton",
    "roast duck", "roast pork", "char siu", "peking duck",
    "dan dan noodles", "mapo tofu", "kung pao chicken", "twice cooked pork",
    "water boiled fish", "toothpick lamb", "mala", "hot pot", "hotpot",
    "dry pot", "liangfen", "cold noodles",
    "xiao long bao", "soup dumplings", "xlb", "sheng jian bao",
    "pan fried bun", "scallion oil noodles", "lion head meatballs",
    "beef noodle soup", "lu rou fan", "braised pork rice",
    "three cup chicken", "oyster vermicelli", "scallion pancake",
    "beef roll", "guotie", "potsticker", "jianbing",
    "biang biang", "hand pulled noodles", "lamb noodle",
    "big plate chicken", "laghman", "cumin lamb", "lamb kebab",
    "rou jia mo", "yangrou paomo",
    "bullfrog", "smoked pork", "fish head",
    "mala broth", "wagyu beef", "beef tripe", "fish balls",
    "lobster", "crab", "shrimp", "fried rice", "chow mein",
    "stir fry", "bbq pork", "spare ribs", "dumplings", "noodles",
    "porridge", "milk tea", "egg waffle", "boba", "sticky rice",
]


def is_chinese(name, types):
    text = " " + name.lower() + " " + " ".join(types).lower() + " "
    if any(k in text for k in NON_CHINESE):
        return False
    if any(k in text for k in CHINESE_KW):
        return True
    return any("\u4e00" <= ch <= "\u9fff" for ch in name)


def auto_classify(name, types=None):
    text = " " + name.lower() + " " + " ".join(types or []).lower() + " "
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


def extract_dishes_from_text(text):
    text_lower = text.lower()
    counts = Counter()
    for dish in DISH_VOCABULARY:
        if dish in text_lower:
            counts[dish] += text_lower.count(dish)
    return [d.title() for d, _ in counts.most_common(5)]


# ── Google Places ────────────────────────────────────────────────────────

def g_nearby(lat, lng, radius, keyword, token=None):
    if token:
        params = {"pagetoken": token, "key": GOOGLE_API_KEY}
    else:
        params = {
            "location": str(lat) + "," + str(lng),
            "radius": radius,
            "keyword": keyword,
            "type": "restaurant",
            "key": GOOGLE_API_KEY,
        }
    r = requests.get(
        "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
        params=params, timeout=15
    )
    r.raise_for_status()
    return r.json()


def g_details(place_id):
    r = requests.get(
        "https://maps.googleapis.com/maps/api/place/details/json",
        params={
            "place_id": place_id,
            "fields": "place_id,name,formatted_address,geometry,"
                      "formatted_phone_number,website,opening_hours,"
                      "price_level,rating,user_ratings_total,photos,business_status",
            "key": GOOGLE_API_KEY,
        },
        timeout=15
    )
    r.raise_for_status()
    return r.json().get("result", {})


def g_photo_url(ref, w=1200):
    return ("https://maps.googleapis.com/maps/api/place/photo"
            "?maxwidth=" + str(w) +
            "&photo_reference=" + ref +
            "&key=" + GOOGLE_API_KEY)


def collect_google(cities):
    seen, results = set(), []
    for ci in cities:
        print("  [" + ci["city"] + "] searching...")
        for term in SEARCH_TERMS:
            token, page = None, 0
            while True:
                try:
                    data = g_nearby(ci["lat"], ci["lng"], ci["radius"], term, token)
                except Exception as e:
                    print("    error (" + term + "): " + str(e))
                    break
                for p in data.get("results", []):
                    pid = p.get("place_id")
                    if not pid or pid in seen:
                        continue
                    if p.get("business_status") in ("PERMANENTLY_CLOSED","CLOSED_TEMPORARILY"):
                        continue
                    name  = p.get("name", "")
                    types = p.get("types", [])
                    if not is_chinese(name, types):
                        continue
                    seen.add(pid)
                    loc = (p.get("geometry") or {}).get("location", {})
                    results.append({
                        "google_place_id": pid,
                        "name":  name,
                        "city":  ci["city"],
                        "lat":   loc.get("lat"),
                        "lng":   loc.get("lng"),
                        "price_level": p.get("price_level"),
                        "status": "OPEN",
                    })
                token = data.get("next_page_token")
                page += 1
                if not token or page >= 3:
                    break
                time.sleep(2)
    return results


def enrich_google_details(restaurants):
    enriched = []
    total = len(restaurants)
    for i, r in enumerate(restaurants):
        if i % 25 == 0:
            print("    details " + str(i+1) + "/" + str(total) + "...")
        try:
            d = g_details(r["google_place_id"])
            texts = (d.get("opening_hours") or {}).get("weekday_text", [])
            hours = {}
            for day in ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]:
                m = [t for t in texts if t.lower().startswith(day)]
                hours[day[:3]] = m[0].split(": ",1)[1] if m else None
            refs = [p["photo_reference"] for p in d.get("photos",[])[:4]
                    if p.get("photo_reference")]
            urls = [g_photo_url(ref) for ref in refs]
            r.update({
                "address":       d.get("formatted_address", r.get("city","")),
                "phone":         d.get("formatted_phone_number",""),
                "website":       d.get("website",""),
                "price_level":   d.get("price_level", r.get("price_level")),
                "hours":         hours,
                "photo_exterior": urls[0] if len(urls)>0 else "",
                "photo_food1":    urls[1] if len(urls)>1 else "",
                "photo_food2":    urls[2] if len(urls)>2 else "",
                "photo_interior": urls[3] if len(urls)>3 else "",
                "google_rating":  d.get("rating"),
                "review_count":   d.get("user_ratings_total"),
            })
        except Exception as e:
            print("    warn (" + r["name"] + "): " + str(e))
            r.setdefault("hours", {})
            for k in ["photo_exterior","photo_food1","photo_food2","photo_interior"]:
                r.setdefault(k, "")
        enriched.append(r)
        time.sleep(0.06)
    return enriched


# ── Yelp Playwright ──────────────────────────────────────────────────────

async def yelp_find_business(page, name, city):
    url = ("https://www.yelp.com/search"
           "?find_desc=" + requests.utils.quote(name) +
           "&find_loc=" + requests.utils.quote(city + " CA"))
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(random.randint(1500, 2500))
        content = await page.content()
        if "captcha" in content.lower() or "robot" in content.lower():
            print("    [CAPTCHA] " + name)
            return None, None
        links = await page.query_selector_all('a[href*="/biz/"]')
        for link in links:
            href = await link.get_attribute("href")
            if not href or "/biz/" not in href:
                continue
            biz = href.split("/biz/")[1].split("?")[0].strip("/")
            if biz and "/" not in biz:
                if href.startswith("/"):
                    href = "https://www.yelp.com" + href
                return href, biz
        return None, None
    except Exception as e:
        print("    [search error] " + name + ": " + type(e).__name__)
        return None, None


async def yelp_scrape_details(page, url):
    from bs4 import BeautifulSoup
    result = {
        "yelp_rating": None, "yelp_review_count": None,
        "yelp_price": None, "yelp_photos": [],
        "yelp_dishes": [], "yelp_highlights": [],
    }
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(random.randint(2000, 3500))
        soup = BeautifulSoup(await page.content(), "html.parser")

        # Rating
        el = soup.find(attrs={"aria-label": re.compile(r"[\d.]+ star rating")})
        if el:
            m = re.search(r"([\d.]+) star", el.get("aria-label",""))
            if m:
                result["yelp_rating"] = float(m.group(1))

        # Review count
        rv = soup.find(string=re.compile(r"\d+\s+reviews?", re.I))
        if rv:
            m = re.search(r"(\d[\d,]*)\s+review", str(rv), re.I)
            if m:
                result["yelp_review_count"] = int(m.group(1).replace(",",""))

        # Price
        spans = soup.find_all("span", string=re.compile(r"^\$+$"))
        if spans:
            result["yelp_price"] = spans[0].get_text(strip=True)

        # Photos (upgrade thumbnails to full-size)
        photos = []
        for img in soup.find_all("img", src=re.compile(r"yelpcdn\.com")):
            src = img.get("src","")
            src = re.sub(r"/(ms|ss|ls)\.", "/o.", src)
            if src not in photos and "user_photos" not in src:
                photos.append(src)
            if len(photos) >= 4:
                break
        result["yelp_photos"] = photos

        # Dish extraction from review text
        texts = []
        for p in soup.find_all("p", {"lang": "en"}):
            texts.append(p.get_text(" ", strip=True))
        for el in soup.find_all(class_=re.compile(r"review|comment", re.I)):
            t = el.get_text(" ", strip=True)
            if len(t) > 30:
                texts.append(t)
        result["yelp_dishes"] = extract_dishes_from_text(" ".join(texts[:MAX_REVIEW_SNIPPETS]))

        # Review highlights
        highlights = []
        for el in soup.find_all(["blockquote","q"]):
            t = el.get_text(" ", strip=True)
            if 20 < len(t) < 200:
                highlights.append(t)
        for p in soup.find_all("p", {"lang":"en"})[:3]:
            t = p.get_text(" ", strip=True)
            s = re.split(r"[.!?]", t)
            if s and len(s[0]) > 20:
                highlights.append(s[0].strip() + ".")
        result["yelp_highlights"] = highlights[:3]

    except Exception as e:
        print("    [scrape error] " + url + ": " + str(e))
    return result


async def enrich_with_yelp(restaurants):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("\nWARNING: playwright not installed.")
        print("  pip install playwright && playwright install chromium")
        return restaurants

    total = len(restaurants)
    est = total * (YELP_DELAY_MIN + YELP_DELAY_MAX) // 2 // 60
    print("\nPhase 2 - Yelp enrichment (" + str(total) + " restaurants, ~" + str(est) + " min)\n")

    enriched = []
    found = not_found = 0

    async with async_playwright() as pw:
        def make_browser_context(browser):
            ctx = browser.new_context(
                viewport={"width":1280,"height":900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/Los_Angeles",
            )
            return ctx

        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-blink-features=AutomationControlled","--disable-dev-shm-usage"]
        )
        context = await make_browser_context(browser)
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        page = await context.new_page()

        # Warm up
        try:
            await page.goto("https://www.yelp.com", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(random.randint(1500, 2500))
        except Exception:
            pass

        for i, r in enumerate(restaurants):
            name = r.get("name","")
            city = r.get("city","")
            print("  [" + str(i+1) + "/" + str(total) + "] " + name + " - " + city)

            url, biz_id = await yelp_find_business(page, name, city)

            if not url:
                not_found += 1
                r["yelp_id"] = ""
                enriched.append(r)
                await page.wait_for_timeout(random.randint(2000, 3000))
                continue

            r["yelp_id"] = biz_id or ""
            await page.wait_for_timeout(random.randint(1000, 2000))
            yd = await yelp_scrape_details(page, url)

            r.update({
                "yelp_rating":       yd["yelp_rating"],
                "yelp_review_count": yd["yelp_review_count"],
                "yelp_price":        yd["yelp_price"],
                "yelp_dishes":       yd["yelp_dishes"],
                "yelp_highlights":   yd["yelp_highlights"],
            })

            yp = yd.get("yelp_photos", [])
            if not r.get("photo_food1")    and len(yp)>0: r["photo_food1"]    = yp[0]
            if not r.get("photo_food2")    and len(yp)>1: r["photo_food2"]    = yp[1]
            if not r.get("photo_exterior") and len(yp)>2: r["photo_exterior"] = yp[2]
            if not r.get("price_level") and yd.get("yelp_price"):
                r["yelp_price_str"] = yd["yelp_price"]

            found += 1
            enriched.append(r)

            dishes_str = ", ".join(yd["yelp_dishes"][:3]) or "none"
            delay = random.uniform(YELP_DELAY_MIN, YELP_DELAY_MAX)
            print("    " + str(biz_id) + " | rating=" + str(yd["yelp_rating"]) +
                  " | dishes=" + dishes_str + " | next in " + str(round(delay)) + "s")
            await page.wait_for_timeout(int(delay * 1000))

            # Browser session reset every 50
            if (i+1) % 50 == 0 and i < len(restaurants)-1:
                print("  Resetting browser session...")
                await browser.close()
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox","--disable-blink-features=AutomationControlled"]
                )
                context = await make_browser_context(browser)
                await context.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                )
                page = await context.new_page()
                await page.goto("https://www.yelp.com", wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(3000)

        await browser.close()

    print("\n  Yelp done: " + str(found) + " found, " + str(not_found) + " not found")
    return enriched


# ── Google Sheet write ───────────────────────────────────────────────────

def sheets_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("pip install gspread google-auth")
    creds = Credentials.from_service_account_info(
        json.loads(SA_JSON),
        scopes=["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)


def write_to_sheet(restaurants):
    client = sheets_client()
    ws = client.open_by_key(SPREADSHEET_ID).worksheet("Restaurants")
    existing = ws.get_all_values()

    existing_pids  = set()
    existing_names = set()
    for row in existing[3:]:
        if len(row) >= 34 and row[33].strip():
            existing_pids.add(row[33].strip())
        if len(row) >= 2 and row[1].strip():
            existing_names.add(normalize(row[1].strip()))

    today  = date.today().isoformat()
    to_add = []
    skipped = 0

    for r in restaurants:
        pid = r.get("google_place_id","")
        if (pid and pid in existing_pids) or normalize(r.get("name","")) in existing_names:
            skipped += 1
            continue

        region, sub = auto_classify(r.get("name",""))
        price = r.get("yelp_price_str") or PRICE_MAP.get(r.get("price_level"),"$$")
        hours = r.get("hours") or {}
        addr  = r.get("address","")
        street = addr.split(",")[0].strip() if "," in addr else addr
        zm = re.search(r"\b(9\d{4})\b", addr)
        zip_cd = zm.group(1) if zm else ""

        yd = r.get("yelp_dishes") or []
        hl = r.get("yelp_highlights") or []
        rating_str = ""
        if r.get("yelp_rating"):
            rating_str = "Yelp " + str(r["yelp_rating"])
            if r.get("yelp_review_count"):
                rating_str += " (" + str(r["yelp_review_count"]) + " reviews)"

        notes   = " -- ".join(filter(None, [rating_str, " | ".join(hl[:2])]))
        sources = "Google Maps" + (",Yelp" if r.get("yelp_id") else "")

        to_add.append([
            "R" + str(5000+len(to_add)).zfill(4), # A
            r.get("name",""),                       # B
            "",                                     # C Chinese name
            "OPEN",                                 # D
            street,                                 # E
            r.get("city",""),                       # F
            zip_cd,                                 # G
            r.get("lat") or "",                     # H
            r.get("lng") or "",                     # I
            region,                                 # J
            sub,                                    # K
            "", "",                                 # L M
            "",                                     # N
            price,                                  # O
            r.get("phone",""),                      # P
            r.get("website",""),                    # Q
            "FALSE","FALSE",                        # R S
            hours.get("mon") or "",                 # T
            hours.get("tue") or "",                 # U
            hours.get("wed") or "",                 # V
            hours.get("thu") or "",                 # W
            hours.get("fri") or "",                 # X
            hours.get("sat") or "",                 # Y
            hours.get("sun") or "",                 # Z
            yd[0] if len(yd)>0 else "",             # AA
            yd[1] if len(yd)>1 else "",             # AB
            yd[2] if len(yd)>2 else "",             # AC
            r.get("photo_exterior",""),             # AD
            r.get("photo_food1",""),                # AE
            r.get("photo_food2",""),                # AF
            r.get("photo_interior",""),             # AG
            pid,                                    # AH
            r.get("yelp_id",""),                    # AI
            "",                                     # AJ
            notes,                                  # AK
            sources,                                # AL
            today, today,                           # AM AN
            "Research Script",                      # AO
        ])

    if to_add:
        for start in range(0, len(to_add), 100):
            chunk = to_add[start:start+100]
            ws.append_rows(chunk, value_input_option="USER_ENTERED")
            print("    wrote " + str(start+1) + "-" + str(start+len(chunk)))
            time.sleep(1)

    print("  Added " + str(len(to_add)) + " | skipped " + str(skipped))
    return len(to_add)


# ── Test helpers ─────────────────────────────────────────────────────────

def test_google_api():
    print("Testing Google Places API...")
    if not GOOGLE_API_KEY:
        print("  ERROR: GOOGLE_API_KEY not set")
        return False
    try:
        data = g_nearby(34.0831, -118.1286, 300, "restaurant")
        print("  OK - " + str(len(data.get("results",[]))) + " results")
        return True
    except Exception as e:
        print("  FAILED: " + str(e))
        return False


async def test_playwright_yelp():
    print("Testing Playwright + Yelp...")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  ERROR: playwright not installed")
        return False
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("https://www.yelp.com/biz/chengdu-taste-alhambra-2",
                            wait_until="domcontentloaded", timeout=20000)
            ok = "chengdu" in (await page.content()).lower()
            await browser.close()
            print("  " + ("OK" if ok else "WARN - content unexpected"))
            return True
    except Exception as e:
        print("  FAILED: " + str(e))
        return False


# ── Entry point ──────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="626 Eats Research Sweep")
    parser.add_argument("--test",     action="store_true")
    parser.add_argument("--no-yelp",  action="store_true")
    parser.add_argument("--no-sheet", action="store_true")
    parser.add_argument("--cities",   default="all")
    args = parser.parse_args()

    if args.test:
        g = test_google_api()
        y = await test_playwright_yelp()
        print("\nResult: " + ("All OK" if g and y else "Failures - see above"))
        sys.exit(0 if (g and y) else 1)

    if not GOOGLE_API_KEY:
        sys.exit("Set GOOGLE_API_KEY env var")

    cities = SGV_CITIES
    if args.cities.lower() != "all":
        wanted = {c.strip() for c in args.cities.split(",")}
        cities = [c for c in SGV_CITIES if c["city"] in wanted]
        if not cities:
            sys.exit("No cities matched: " + str([c["city"] for c in SGV_CITIES]))

    print("=" * 60)
    print("626 Eats - Research Sweep")
    print("Cities: " + str(len(cities)) + "  Yelp: " + ("off" if args.no_yelp else "on"))
    print("=" * 60)

    print("\nPhase 1 - Google Places discovery...")
    raw = collect_google(cities)
    print("  " + str(len(raw)) + " unique Chinese restaurants found\n")
    if not raw:
        print("No results. Run --test to verify API key.")
        return

    print("Phase 1b - Fetching details...")
    restaurants = enrich_google_details(raw)
    print("  " + str(len(restaurants)) + " enriched\n")

    if not args.no_yelp:
        restaurants = await enrich_with_yelp(restaurants)
    else:
        print("Phase 2 - Yelp skipped\n")

    print("Phase 3 - Writing to Google Sheet...")
    if args.no_sheet:
        print("  (preview only)\n")
        for r in restaurants[:15]:
            region, _ = auto_classify(r["name"])
            print("  " + r["name"][:35].ljust(36) + r["city"][:16].ljust(17) +
                  region[:25] + "  " + str(r.get("yelp_dishes",[""])[:2]))
        if len(restaurants) > 15:
            print("  ..." + str(len(restaurants)-15) + " more")
        return

    if not SPREADSHEET_ID or not SA_JSON:
        print("  SPREADSHEET_ID/SA_JSON not set - skipping write")
        return

    added = write_to_sheet(restaurants)
    print("\n" + "=" * 60)
    print("Done! " + str(added) + " restaurants added.")
    print("Next: filter col J = NEEDS CLASSIFICATION and assign regions.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
