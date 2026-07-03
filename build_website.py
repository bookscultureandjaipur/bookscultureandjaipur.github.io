"""
Events Website Builder
======================
Reads all available event JSON files and generates a self-contained index.html
website — a public-facing events discovery platform.

Usage:
    python build_website.py
    python build_website.py --out my_website.html --title "Delhi Plays"
"""

import json
import re
import argparse
from datetime import date
from pathlib import Path


# ── JSON-LD helpers ───────────────────────────────────────────────────────────

_MONTHS_MAP = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
               'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}

def _parse_iso_datetime(date_str, time_str=''):
    """'Sat, 4 Jul 2026' + '6:30 PM'  →  '2026-07-04T18:30'"""
    s = re.sub(r'\s*onwards.*', '', date_str, flags=re.I)
    s = re.split(r'\s*[-–]\s*(?=[A-Z])', s)[0].strip()
    m = re.search(r'(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*(\d{4})?', s, re.I)
    if not m:
        return ''
    day   = int(m.group(1))
    month = _MONTHS_MAP[m.group(2).lower()[:3]]
    year  = int(m.group(3)) if m.group(3) else 2026
    time_part = ''
    if time_str:
        tm = re.match(r'(\d{1,2}):(\d{2})\s*(AM|PM)', time_str.strip(), re.I)
        if tm:
            h, mn, mer = int(tm.group(1)), int(tm.group(2)), tm.group(3).upper()
            if mer == 'PM' and h != 12: h += 12
            if mer == 'AM' and h == 12: h = 0
            time_part = f'T{h:02d}:{mn:02d}'
    return f'{year}-{month:02d}-{day:02d}{time_part}'


def _event_end_date(date_str):
    """Return the last date of the event as a date object, or None to never auto-remove."""
    if not date_str:
        return None
    if re.search(r'onwards', date_str, re.I):
        return None
    matches = list(re.finditer(
        r'(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*(\d{4})?',
        date_str, re.I,
    ))
    if not matches:
        return None
    year_m = re.search(r'\b(202\d)\b', date_str)
    year = int(year_m.group(1)) if year_m else 2026
    last = matches[-1]
    day   = int(last.group(1))
    month = _MONTHS_MAP[last.group(2).lower()[:3]]
    ev_year = int(last.group(3)) if last.group(3) else year
    try:
        return date(ev_year, month, day)
    except ValueError:
        return None


def generate_event_jsonld(events, site_url, max_events=40):
    """Return a <script type="application/ld+json"> tag with Event schema for the page."""
    items = []
    for ev in events[:max_events]:
        dt = _parse_iso_datetime(ev.get('date', ''), ev.get('time', ''))
        if not dt:
            continue
        obj = {
            '@type': 'Event',
            'name': ev.get('title', ''),
            'startDate': dt,
            'eventStatus': 'https://schema.org/EventScheduled',
            'eventAttendanceMode': 'https://schema.org/OfflineEventAttendanceMode',
        }
        if ev.get('venue'):
            obj['location'] = {
                '@type': 'Place',
                'name': ev['venue'],
                'address': {
                    '@type': 'PostalAddress',
                    'addressLocality': ev.get('_city') or ev.get('city', ''),
                    'addressCountry': 'IN',
                },
            }
        price_raw = str(ev.get('price', '')).strip()
        link = ev.get('link', '')
        if price_raw and price_raw.lower() != 'free':
            digits = re.sub(r'[^\d]', '', price_raw)
            if digits:
                offer = {'@type': 'Offer', 'price': digits,
                         'priceCurrency': 'INR',
                         'availability': 'https://schema.org/InStock'}
                if link:
                    offer['url'] = link
                obj['offers'] = offer
        elif price_raw.lower() == 'free':
            obj['offers'] = {'@type': 'Offer', 'price': '0',
                             'priceCurrency': 'INR',
                             'availability': 'https://schema.org/InStock'}
        img = ev.get('image', '')
        if img:
            obj['image'] = img if img.startswith('http') else f'{site_url}/{img}'
        if link:
            obj['url'] = link
        items.append(obj)
    if not items:
        return ''
    schema = {'@context': 'https://schema.org', '@graph': items}
    return (f'<script type="application/ld+json">'
            f'{json.dumps(schema, ensure_ascii=False, separators=(",",":"))}'
            f'</script>')

# ── Config ────────────────────────────────────────────────────────────────────

SITE_NAME    = "Books Culture and Jaipur"
SITE_TAGLINE = "Discover plays, music & cultural events near you"
OUTPUT_FILE    = "index.html"
SITE_URL       = "https://bookscultureandjaipur.github.io"
GENRE_PAGE_MIN = 8   # minimum events needed to generate a city+genre page

JSON_SOURCES = [
    {"file": "bms_events.json",           "source": "bms",       "city": "Delhi"},
    {"file": "ig_events.json",            "source": "instagram",  "city": "Delhi"},
    {"file": "bms_mumbai_events.json",    "source": "bms",       "city": "Mumbai"},
    {"file": "ig_mumbai_events.json",     "source": "instagram",  "city": "Mumbai"},
    {"file": "bms_bengaluru_events.json", "source": "bms",       "city": "Bengaluru"},
    {"file": "bms_jaipur_events.json",    "source": "bms",       "city": "Jaipur"},
    {"file": "custom_events.json",        "source": "custom",    "city": ""},
]

CATEGORY_ICONS = {
    "comedy":    "😂", "drama":     "🎭", "musical":   "🎵",
    "sufi":      "🎶", "heritage":  "🏛️", "walk":      "🚶",
    "workshop":  "🎨", "music":     "🎸", "classical": "🎼",
    "dance":     "💃", "art":       "🖼️", "poetry":    "📜",
    "standup":   "🎤", "improv":    "🎪", "folk":      "🪘",
}

# ── Data loading ──────────────────────────────────────────────────────────────

def detect_genres(ev):
    title_text = ev.get("title", "").lower()
    other_text = " ".join([
        ev.get("genre",""), ev.get("genreLine",""),
        ev.get("caption_full","")[:200]
    ]).lower()

    kw_map = {
        "Comedy":    ["comedy","standup","stand-up","improv","laughter","funny"],
        "Drama":     ["drama","play","theatre","theater","natak"],
        "Musical":   ["musical","music show","concert","live music","ghazal","qawwali","sufi"],
        "Heritage":  ["heritage","walk","museum","fort","history","archaeological","monument"],
        "Workshop":  ["workshop","masterclass","training","acting class","craft class"],
        "Poetry":    ["poetry","poem","shayari","open mic","storytelling","kavishala"],
        "Dance":     ["dance","bhangra","kathak","bharatanatyam","nritya"],
        "Art":       ["art","painting","exhibition","gallery","sculpture"],
        "Classical": ["classical","hindustani","carnatic","tabla","sitar","flute","santoor"],
        "Film":      ["film","cinema","screening","movie"],
    }
    # Title matches get priority — they always appear before genre/caption matches
    title_found = []
    other_found = []
    for label, keywords in kw_map.items():
        if any(k in title_text for k in keywords):
            title_found.append(label)
        elif any(k in other_text for k in keywords):
            other_found.append(label)

    combined = title_found + other_found
    return combined[:3] if combined else ["Event"]


def normalize(ev, source, city):
    ev["_source"] = source
    # Custom events carry their own city; fall back to the source-level city
    ev["_city"]   = ev.get("city") or ev.get("_city") or city
    if not ev.get("genres"):
        ev["_genres"] = detect_genres(ev)
    else:
        ev["_genres"] = ev.get("genres", [])
    # Normalize price
    price_raw = str(ev.get("price","")).strip()
    if re.search(r'\bfree\b|open to all|complimentary', price_raw, re.I):
        ev["_price_display"] = "Free"
        ev["_price_num"]     = 0
    elif re.search(r'[\d,]+', price_raw):
        digits = re.sub(r'[^\d]', '', price_raw)
        if digits:
            suffix = '+' if ev.get('_source') == 'bms' else ''
            ev["_price_display"] = f"₹{int(digits):,}{suffix}"
        else:
            ev["_price_display"] = price_raw
        ev["_price_num"] = int(digits) if digits else None
    elif price_raw:
        ev["_price_display"] = price_raw
        ev["_price_num"]     = None
    else:
        ev["_price_display"] = ""
        ev["_price_num"]     = None
    return ev


SOLD_OUT_PATTERN = re.compile(r'sold\s*out|housefull|house\s*full', re.I)

def is_sold_out(ev):
    check = " ".join(str(ev.get(f) or "") for f in ["price", "status", "genre", "title"])
    return ev.get("sold_out") is True or bool(SOLD_OUT_PATTERN.search(check))


def load_all_events():
    base = Path(__file__).parent

    excl_file = base / "excluded_links.json"
    excluded  = set(json.loads(excl_file.read_text(encoding="utf-8"))) if excl_file.exists() else set()
    if excluded:
        print(f"  Exclusion list: {len(excluded)} links\n")

    # Custom events override scraped events with the same link
    custom_fp = base / "custom_events.json"
    custom_links = set()
    if custom_fp.exists():
        for ev in json.loads(custom_fp.read_text(encoding="utf-8")):
            lnk = ev.get("link", "").strip().rstrip("/")
            if lnk:
                custom_links.add(lnk)

    all_events = []
    for src in JSON_SOURCES:
        fp = base / src["file"]
        if not fp.exists():
            print(f"  Skipping (not found): {src['file']}")
            continue
        events = json.loads(fp.read_text(encoding="utf-8"))
        for ev in events:
            normalize(ev, src["source"], src["city"])
        sold_out = [e for e in events if is_sold_out(e)]
        events   = [e for e in events if not is_sold_out(e)]
        events   = [e for e in events if e.get("link", "").strip().rstrip("/") not in {u.rstrip("/") for u in excluded}]
        if src["file"] != "custom_events.json":
            events = [e for e in events if e.get("link", "").strip().rstrip("/") not in custom_links]
        all_events.extend(events)
        skipped = f"  ({len(sold_out)} sold out skipped)" if sold_out else ""
        print(f"  Loaded {len(events):>3} events from {src['file']}{skipped}")
    # Drop events whose last date has already passed
    today = date.today()
    before = len(all_events)
    all_events = [
        e for e in all_events
        if (_event_end_date(e.get('date', '')) or date.max) >= today
    ]
    removed = before - len(all_events)
    if removed:
        print(f"  Removed {removed} past event(s)\n")
    print(f"  Total: {len(all_events)} events\n")
    return all_events


# ── HTML generation ───────────────────────────────────────────────────────────

def city_filename(city):
    return city.lower().replace(" ", "-") + ".html"


def genre_filename(city, genre):
    return f"{city.lower().replace(' ', '-')}-{genre.lower().replace(' ', '-')}.html"


def build_html(events, site_name, tagline, city_filter=None, genre_filter=None, all_cities=None, output_filename=None):
    # City pages and genre pages both filter by city; genre filtering is JS-side via GENRE_PAGE
    if city_filter:
        events = [e for e in events if (e.get("_city") or "").lower() == city_filter.lower()]

    events_json = json.dumps(events, ensure_ascii=False)
    all_genres  = sorted(set(g for e in events for g in e.get("_genres",[])))

    # SEO metadata
    if city_filter and genre_filter:
        page_title    = f"{genre_filter} Events in {city_filter} — {site_name}"
        page_desc     = (f"Discover {genre_filter.lower()} shows, concerts and performances "
                         f"in {city_filter}. Updated weekly from BookMyShow.")
        fname             = output_filename or genre_filename(city_filter, genre_filter)
        canonical_tag_url = f"{SITE_URL}/{fname}"
        canonical_tag     = f'<link rel="canonical" href="{canonical_tag_url}">'
    elif city_filter:
        page_title    = f"Events in {city_filter} | Plays, Music &amp; More — {site_name}"
        page_desc     = (f"Discover upcoming plays, music concerts, comedy shows and cultural events "
                         f"in {city_filter}. Updated weekly from BookMyShow.")
        fname             = output_filename or city_filename(city_filter)
        canonical_tag_url = f"{SITE_URL}/{fname}"
        canonical_tag     = f'<link rel="canonical" href="{canonical_tag_url}">'
    else:
        page_title        = f"{site_name} — {tagline}"
        page_desc         = tagline
        canonical_tag_url = f"{SITE_URL}/"
        canonical_tag     = f'<link rel="canonical" href="{canonical_tag_url}">'

    # JS constants so client-side code knows which city/genre page this is
    city_page_js  = f"const CITY_PAGE  = '{city_filter}';"  if city_filter  else "const CITY_PAGE  = null;"
    genre_page_js = f"const GENRE_PAGE = '{genre_filter}';" if genre_filter else "const GENRE_PAGE = null;"

    # City tabs: <a> navigation links on city pages, JS buttons on index
    all_c = all_cities or sorted(set(e.get("_city","") for e in events if e.get("_city")))
    if city_filter:
        city_tabs = (
            '<a class="city-tab" href="index.html" data-city="all">All Cities</a>'
            + "".join(
                f'<a class="city-tab{" active" if c == city_filter else ""}" '
                f'href="{city_filename(c)}" data-city="{c}">{c}</a>'
                for c in all_c
            )
        )
    else:
        city_tabs = (
            '<a class="city-tab active" href="index.html" data-city="all">All Cities</a>'
            + "".join(
                f'<a class="city-tab" href="{city_filename(c)}" data-city="{c}">{c}</a>'
                for c in all_c
            )
        )

    genre_chips = "".join(
        f'<button class="genre-chip" data-genre="{g}" onclick="toggleGenre(\'{g}\')">'
        f'{CATEGORY_ICONS.get(g.lower(), "✨")} {g}</button>'
        for g in all_genres
    )

    jsonld_tag = generate_event_jsonld(events, SITE_URL)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title}</title>
<meta name="description" content="{page_desc}">
{canonical_tag}
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{page_title}">
<meta property="og:description" content="{page_desc}">
<meta property="og:image" content="{SITE_URL}/images/logo.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="{canonical_tag_url}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{SITE_URL}/images/logo.jpg">
<meta name="google-site-verification" content="eCR9lUR6rnhGIpUEE4KPyNc0ENVJga41rt24Fcc3koc">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-DLJ79K6E6V"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments)}}gtag('js',new Date());gtag('config','G-DLJ79K6E6V');</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;800&display=swap" rel="stylesheet">
<style>
/* ── Reset & Base ── */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --brand:#C97B63;--brand-dark:#A65A44;--brand-light:#F0D5CB;
  --green:#27AE60;--purple:#9B59B6;
  --bg:#FDF6F2;--surface:#fff;--border:#EDD5C8;
  --text:#2C1810;--text2:#8A6255;--radius:12px;
  --shadow:0 2px 12px rgba(44,24,16,.09);
}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
a{{text-decoration:none;color:inherit}}
button{{cursor:pointer;border:none;background:none;font-family:inherit}}

/* ── Header ── */
header{{background:#fff;border-bottom:2px solid var(--brand-light);position:sticky;top:0;z-index:100;box-shadow:0 2px 10px rgba(44,24,16,.07)}}
.header-inner{{max-width:1200px;margin:0 auto;padding:0 20px;height:68px;display:flex;align-items:center;justify-content:space-between;gap:16px}}
.logo{{display:flex;align-items:center;gap:10px;font-family:'Playfair Display',Georgia,serif;font-size:1.15rem;font-weight:700;color:var(--brand);letter-spacing:.01em}}
.logo img{{height:46px;width:46px;object-fit:cover;border-radius:8px;flex-shrink:0}}
.header-search{{flex:1;max-width:420px;position:relative}}
.header-search input{{width:100%;padding:10px 16px 10px 40px;border:1.5px solid var(--border);border-radius:24px;font-size:.9rem;outline:none;transition:border .2s;background:#fff}}
.header-search input:focus{{border-color:var(--brand)}}
.header-search .search-icon{{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--text2);font-size:1rem}}

/* ── Hero ── */
.hero{{background:linear-gradient(135deg,#C97B63 0%,#A65A44 55%,#8B3A26 100%);color:#fff;padding:64px 20px 52px;text-align:center;position:relative;overflow:hidden}}
.hero::before{{content:'';position:absolute;inset:0;background:url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.04'%3E%3Cpath d='M30 0 C20 0 12 8 12 18 L12 22 C12 26 15 28 15 28 L12 28 L12 42 L48 42 L48 28 L45 28 C45 28 48 26 48 22 L48 18 C48 8 40 0 30 0 Z M30 4 C38 4 44 10 44 18 L44 22 C44 25 42 27 42 27 L18 27 C18 27 16 25 16 22 L16 18 C16 10 22 4 30 4 Z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");opacity:.5}}
.hero h1{{font-family:'Playfair Display',Georgia,serif;font-size:clamp(2rem,4.5vw,3.2rem);font-weight:800;margin-bottom:14px;line-height:1.2;position:relative}}
.hero h1 span{{color:#FFE0D4}}
.hero p{{color:rgba(255,255,255,.82);font-size:1.05rem;margin-bottom:32px;position:relative}}
.hero-search{{max-width:560px;margin:0 auto;display:flex;background:#fff;border-radius:32px;overflow:hidden;box-shadow:0 8px 32px rgba(44,24,16,.3);position:relative}}
.hero-search input{{flex:1;padding:16px 20px;border:none;outline:none;font-size:1rem;color:var(--text)}}
.hero-search button{{padding:12px 28px;background:var(--brand-dark);color:#fff;font-weight:700;font-size:.95rem;border-radius:0 32px 32px 0;transition:background .2s}}
.hero-search button:hover{{background:#7A3020}}
.hero-stats{{display:flex;justify-content:center;gap:40px;margin-top:36px;position:relative}}
.hero-stat{{text-align:center}}
.hero-stat .num{{font-family:'Playfair Display',Georgia,serif;font-size:1.9rem;font-weight:800;color:#fff}}
.hero-stat .lbl{{font-size:.78rem;color:rgba(255,255,255,.65);text-transform:uppercase;letter-spacing:.6px;margin-top:2px}}

/* ── Filter Bar ── */
.filter-bar{{background:#fff;border-bottom:1px solid var(--border);position:sticky;top:68px;z-index:90}}
.filter-toggle-row{{display:none}}
.filter-content{{display:block}}
.filter-inner{{max-width:1200px;margin:0 auto;padding:12px 20px;display:flex;flex-direction:column;gap:10px}}
.city-tabs{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.city-tabs span{{font-size:.75rem;font-weight:700;color:var(--text2);margin-right:4px;text-transform:uppercase;letter-spacing:.6px}}
.city-tab{{padding:7px 18px;border-radius:20px;font-size:.85rem;font-weight:600;color:var(--text2);border:1.5px solid var(--border);transition:all .2s}}
.city-tab:hover,.city-tab.active{{background:var(--brand);color:#fff;border-color:var(--brand)}}
.genre-row{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.genre-row span{{font-size:.75rem;font-weight:700;color:var(--text2);margin-right:4px;text-transform:uppercase;letter-spacing:.6px}}
.genre-chip{{padding:5px 14px;border-radius:16px;font-size:.82rem;font-weight:500;color:var(--text2);border:1.5px solid var(--border);transition:all .2s}}
.genre-chip:hover,.genre-chip.active{{background:var(--text);color:#fff;border-color:var(--text)}}
.date-row{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.date-row span{{font-size:.75rem;font-weight:700;color:var(--text2);margin-right:4px;text-transform:uppercase;letter-spacing:.6px}}
.date-pill{{padding:5px 14px;border-radius:16px;font-size:.82rem;font-weight:500;color:var(--text2);border:1.5px solid var(--border);transition:all .2s}}
.date-pill:hover,.date-pill.active{{background:var(--brand);color:#fff;border-color:var(--brand)}}
.custom-date-row{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding-top:4px}}
.custom-date-row label{{font-size:.8rem;color:var(--text2);font-weight:600}}
.custom-date-row input[type="date"]{{padding:6px 11px;border:1.5px solid var(--border);border-radius:16px;font-size:.82rem;outline:none;color:var(--text);background:#fff;transition:border .18s;font-family:inherit}}
.custom-date-row input[type="date"]:focus{{border-color:var(--brand)}}
.date-range-clear{{padding:5px 12px;border-radius:16px;font-size:.78rem;font-weight:600;color:var(--text2);border:1.5px solid var(--border);cursor:pointer;background:#fff;transition:all .2s}}
.date-range-clear:hover{{background:var(--brand-light);color:var(--brand-dark);border-color:var(--brand)}}

/* ── Main Content ── */
.main{{max-width:1200px;margin:0 auto;padding:28px 20px}}
.results-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}}
.results-count{{font-size:.95rem;color:var(--text2);font-weight:500}}
.sort-select{{padding:7px 14px;border:1.5px solid var(--border);border-radius:8px;font-size:.85rem;outline:none;color:var(--text);background:#fff}}

/* ── Event Grid ── */
.event-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:18px}}

/* ── Event Card ── */
.event-card{{background:var(--surface);border-radius:var(--radius);border:1.5px solid var(--border);box-shadow:var(--shadow);overflow:hidden;cursor:pointer;display:flex;flex-direction:column;transition:transform .22s,box-shadow .22s,border-color .22s}}
.event-card:hover{{transform:translateY(-4px);box-shadow:0 14px 36px rgba(44,24,16,.15);border-color:var(--brand-light)}}

/* square image zone */
.card-img-wrap{{position:relative;width:100%;aspect-ratio:1/1;overflow:hidden;background:#2C1810;flex-shrink:0}}
.card-img-bg{{position:absolute;inset:-10px;width:calc(100% + 20px);height:calc(100% + 20px);object-fit:cover;filter:blur(14px) brightness(.55) saturate(1.2);transform:scale(1.05);transition:transform .38s}}
.card-poster{{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;display:block;transition:transform .38s;z-index:1}}
.event-card:hover .card-poster{{transform:scale(1.04)}}
.event-card:hover .card-img-bg{{transform:scale(1.06)}}
.card-poster-placeholder{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:4rem;z-index:1}}
.card-poster-placeholder.bms{{background:linear-gradient(160deg,#3a1008,#C97B6344)}}
.card-poster-placeholder.instagram{{background:linear-gradient(160deg,#2a0818,#9B59B644,#C97B6322)}}
.card-poster-placeholder.custom{{background:linear-gradient(160deg,#0a2a1a,#27AE6044)}}

/* floating badges */
.card-top{{position:absolute;top:9px;left:9px;right:9px;display:flex;justify-content:space-between;align-items:flex-start;gap:6px;z-index:1}}
.badge{{padding:3px 9px;border-radius:10px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.3px}}
.badge-bms{{background:rgba(201,123,99,.92);color:#fff;backdrop-filter:blur(4px)}}
.badge-instagram{{background:rgba(131,58,180,.9);color:#fff;backdrop-filter:blur(4px)}}
.badge-custom{{background:rgba(39,174,96,.9);color:#fff;backdrop-filter:blur(4px)}}
.badge-free{{background:rgba(39,174,96,.92);color:#fff;backdrop-filter:blur(4px)}}
.badge-price{{background:rgba(44,24,16,.72);color:#fff;backdrop-filter:blur(4px)}}
.badge-city{{background:rgba(44,24,16,.5);color:rgba(255,255,255,.9);backdrop-filter:blur(4px)}}

/* hover scrim */
.card-hover-scrim{{position:absolute;inset:0;background:rgba(44,24,16,.45);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;opacity:0;transition:opacity .22s;z-index:2}}
.event-card:hover .card-hover-scrim{{opacity:1}}
.card-hover-scrim a{{padding:10px 22px;border-radius:24px;font-size:.84rem;font-weight:700;color:#fff;text-decoration:none;border:2px solid rgba(255,255,255,.5);backdrop-filter:blur(6px);transition:transform .15s,border-color .15s;white-space:nowrap;width:72%;text-align:center}}
.card-hover-scrim a.primary{{background:rgba(201,123,99,.92)}}
.card-hover-scrim a.whatsapp{{background:rgba(37,211,102,.92)}}
.card-hover-scrim a.instagram-btn{{background:linear-gradient(135deg,rgba(131,58,180,.9),rgba(253,29,29,.9))}}
.card-hover-scrim a.register{{background:rgba(201,123,99,.92)}}
.card-hover-scrim a.email-btn{{background:rgba(26,115,232,.92)}}
.card-hover-scrim a:hover{{transform:scale(1.06);border-color:rgba(255,255,255,.9)}}

/* card body */
.card-body{{padding:12px 14px 14px;display:flex;flex-direction:column;gap:6px}}
.card-source-row{{display:flex;align-items:center;gap:6px}}
.card-city-label{{font-size:.72rem;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.4px}}
.card-title{{font-size:.95rem;font-weight:700;color:var(--text);line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.card-meta{{display:flex;flex-direction:column;gap:3px}}
.meta-row{{font-size:.78rem;color:var(--text2);display:flex;align-items:baseline;gap:5px;line-height:1.4}}
.card-footer{{padding:10px 14px 12px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;margin-top:auto}}
.price-tag{{font-size:.95rem;font-weight:700;color:var(--text)}}
.price-tag.free{{color:var(--green)}}
.genre-tags{{display:flex;gap:4px;flex-wrap:wrap}}
.genre-tag{{background:#FDF0EB;color:var(--brand);padding:2px 8px;border-radius:8px;font-size:.68rem;font-weight:600}}

/* ── Empty State ── */
.empty-state{{text-align:center;padding:80px 20px;color:var(--text2)}}
.empty-state .icon{{font-size:3rem;margin-bottom:16px}}
.empty-state h3{{font-size:1.2rem;margin-bottom:8px;color:var(--text)}}

/* ── Modal ── */
.modal-overlay{{display:none;position:fixed;inset:0;background:rgba(44,24,16,.7);z-index:200;align-items:center;justify-content:center;padding:20px}}
.modal-overlay.open{{display:flex}}
.modal{{background:#fff;border-radius:16px;width:100%;max-width:540px;max-height:92vh;overflow-y:auto;position:relative}}
.modal-poster-wrap{{position:relative;width:100%;height:300px;overflow:hidden;border-radius:16px 16px 0 0;background:#2C1810;flex-shrink:0}}
.modal-poster-wrap img{{width:100%;height:100%;object-fit:cover}}
.modal-poster-gradient{{position:absolute;bottom:0;left:0;right:0;height:120px;background:linear-gradient(to top,rgba(44,24,16,.85),transparent)}}
.modal-poster-title{{position:absolute;bottom:0;left:0;right:0;padding:16px 20px;color:#fff;font-family:'Playfair Display',Georgia,serif;font-size:1.2rem;font-weight:700;line-height:1.3}}
.modal-close{{position:absolute;top:12px;right:12px;z-index:2;width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:rgba(44,24,16,.5);color:#fff;font-size:1.1rem;flex-shrink:0;transition:background .2s;backdrop-filter:blur(4px)}}
.modal-close:hover{{background:rgba(201,123,99,.9)}}
.modal-body{{padding:20px}}
.modal-source-bar{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}}
.detail-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}}
.detail-item{{background:var(--bg);border-radius:10px;padding:12px 14px;border:1px solid var(--border)}}
.detail-label{{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.4px;color:var(--text2);margin-bottom:4px}}
.detail-value{{font-size:.9rem;font-weight:600;color:var(--text)}}
.detail-item.full{{grid-column:1/-1}}
.caption-box{{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:18px}}
.caption-label{{font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.4px;color:var(--text2);margin-bottom:8px}}
.caption-text{{font-size:.87rem;color:var(--text);line-height:1.6;white-space:pre-wrap;max-height:200px;overflow-y:auto}}
.modal-actions{{display:flex;gap:10px;flex-wrap:wrap}}
.action-btn{{flex:1;min-width:140px;padding:13px 20px;border-radius:24px;font-size:.9rem;font-weight:700;text-align:center;transition:all .2s;display:flex;align-items:center;justify-content:center;gap:8px}}
.action-btn.primary{{background:var(--brand);color:#fff}}
.action-btn.primary:hover{{background:var(--brand-dark)}}
.action-btn.whatsapp{{background:#25D366;color:#fff}}
.action-btn.whatsapp:hover{{background:#1da851}}
.action-btn.secondary{{background:var(--bg);color:var(--text);border:1.5px solid var(--border)}}
.action-btn.secondary:hover{{border-color:var(--brand);background:var(--brand-light)}}
.action-btn.instagram-btn{{background:linear-gradient(135deg,#833AB4,#FD1D1D);color:#fff}}
.action-btn.email-btn{{background:#1A73E8;color:#fff}}
.action-btn.email-btn:hover{{background:#1558B0}}

/* ── Footer ── */
footer{{background:#2C1810;color:rgba(255,255,255,.55);text-align:center;padding:36px 20px;margin-top:60px}}
footer .footer-logo{{font-family:'Playfair Display',Georgia,serif;color:#fff;font-size:1.4rem;font-weight:700;margin-bottom:10px;letter-spacing:.02em}}
footer p{{font-size:.85rem;line-height:1.7}}
footer a{{color:var(--brand-light);text-decoration:underline}}

/* ── Collapsible filter — all mobile & tablet ── */
@media(max-width:768px){{
  .filter-toggle-row{{display:flex}}
  .filter-content{{display:none;border-top:1px solid var(--border)}}
  .filter-bar.open .filter-content{{display:block}}
}}

/* ── Responsive ── */
@media(max-width:900px){{
  .event-grid{{grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px}}
}}
@media(max-width:600px){{
  .hero{{padding:44px 16px 38px}}
  .hero-stats{{gap:24px}}
  .header-search{{display:none}}
  /* Hero search bar — removed from mobile for now, uncomment to restore */
  .hero-search{{display:none}}
  /* Cities count stat — removed from mobile for now, uncomment to restore */
  .hero-stat-cities{{display:none}}
  .filter-toggle-row{{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;gap:10px}}
  .filter-toggle-btn{{display:flex;align-items:center;gap:7px;padding:8px 16px;background:var(--bg);border:1.5px solid var(--border);border-radius:20px;font-size:.88rem;font-weight:700;color:var(--text);transition:all .2s}}
  .filter-toggle-btn:hover,.filter-bar.open .filter-toggle-btn{{background:var(--brand-light);border-color:var(--brand);color:var(--brand-dark)}}
  .filter-badge{{background:var(--brand);color:#fff;border-radius:10px;padding:1px 7px;font-size:.72rem;font-weight:800}}
  .filter-summary{{font-size:.78rem;color:var(--text2);font-weight:500;text-align:right;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .detail-grid{{grid-template-columns:1fr}}
  .filter-bar{{position:static}}
  .event-grid{{grid-template-columns:1fr;gap:12px}}
  .modal-actions{{flex-direction:column}}
  .modal-poster-wrap{{height:220px}}
}}
</style>
</head>
<body>

<!-- Header -->
<header>
  <div class="header-inner">
    <a href="{SITE_URL}/" class="logo">
      <img src="{SITE_URL}/images/logo.jpg" alt="{site_name}">
      {site_name}
    </a>
    <div class="header-search">
      <span class="search-icon">🔍</span>
      <input type="text" id="headerSearch" placeholder="Search events, venues..." oninput="onSearch(this.value)">
    </div>
  </div>
</header>

<!-- Hero -->
<section class="hero">
  <h1>Discover <span>Cultural Events</span><br>Near You</h1>
  <p>{tagline}</p>
  <div class="hero-search">
    <input type="text" id="heroSearch" placeholder="Search plays, concerts, workshops..." oninput="onSearch(this.value)">
    <button>Search</button>
  </div>
  <div class="hero-stats">
    <div class="hero-stat"><div class="num" id="statTotal">0</div><div class="lbl">Events</div></div>
    <div class="hero-stat"><div class="num" id="statFree">0</div><div class="lbl">Free</div></div>
    <div class="hero-stat hero-stat-cities"><div class="num" id="statCities">0</div><div class="lbl">Cities</div></div>
    <div class="hero-stat"><div class="num" id="statWeekend">0</div><div class="lbl">This Weekend</div></div>
  </div>
</section>

<!-- Filter Bar -->
<div class="filter-bar" id="filterBar">
  <!-- Mobile-only toggle row -->
  <div class="filter-toggle-row">
    <button class="filter-toggle-btn" onclick="toggleFilters()">
      🎛 Filters <span class="filter-badge" id="filterBadge" style="display:none">0</span>
    </button>
    <span class="filter-summary" id="filterSummary">All Cities · All Dates</span>
  </div>
  <!-- Filter content — collapsible on mobile -->
  <div class="filter-content" id="filterContent">
    <div class="filter-inner">
      <div class="city-tabs">
        <span>City</span>
        {city_tabs}
      </div>
      <div class="date-row">
        <span>Date</span>
        <button class="date-pill active" data-date="all"       onclick="setDate('all')">All Dates</button>
        <button class="date-pill"        data-date="weekend"   onclick="setDate('weekend')">This Weekend</button>
        <button class="date-pill"        data-date="thisweek"  onclick="setDate('thisweek')">This Week</button>
        <button class="date-pill"        data-date="nextweek"  onclick="setDate('nextweek')">Next Week</button>
        <button class="date-pill"        data-date="custom"    onclick="setDate('custom')">📅 Pick Dates</button>
      </div>
      <div class="custom-date-row" id="customDateRow" style="display:none">
        <label>From</label>
        <input type="date" id="filterDateFrom" onchange="applyCustomRange()">
        <label>to</label>
        <input type="date" id="filterDateTo" onchange="applyCustomRange()">
        <button class="date-range-clear" onclick="setDate('all')">✕ Clear</button>
      </div>
      <div class="genre-row">
        <span>Category</span>
        {genre_chips}
      </div>
    </div>
  </div>
</div>

<!-- Main -->
<main class="main">
  <div class="results-header">
    <div class="results-count" id="resultsCount">Loading...</div>
    <select class="sort-select" onchange="setSort(this.value)">
      <option value="date">Sort: By Date</option>
      <option value="price_asc">Price: Low to High</option>
      <option value="price_desc">Price: High to Low</option>
      <option value="title">Title A-Z</option>
    </select>
  </div>
  <div class="event-grid" id="eventGrid"></div>
  <div class="empty-state" id="emptyState" style="display:none">
    <div class="icon">🎭</div>
    <h3>No events found</h3>
    <p>Try changing your filters or search terms</p>
  </div>
</main>

<!-- Modal -->
<div class="modal-overlay" id="modalOverlay" onclick="closeModal(event)">
  <div class="modal" id="modal">
    <button class="modal-close" onclick="closeModal()">✕</button>
    <div id="modalPoster"></div>
    <div class="modal-body">
      <div class="modal-source-bar" id="modalBadges"></div>
      <div class="detail-grid" id="modalDetails"></div>
      <div id="modalCaption"></div>
      <div class="modal-actions" id="modalActions"></div>
    </div>
  </div>
</div>

<!-- Footer -->
<footer>
  <div class="footer-logo">{site_name}</div>
  <p>Curating plays, music &amp; cultural events across Indian cities.<br>
  Event details belong to their respective organizers.</p>
</footer>

<script>
{city_page_js}
{genre_page_js}
const EVENTS = {events_json};

// ── State ─────────────────────────────────────────────────────
let state = {{
  city:     'all',
  date:     'all',
  dateFrom: null,
  dateTo:   null,
  genres:   new Set(),
  search:   '',
  sort:     'date',
}};

// ── Date helpers ──────────────────────────────────────────────
function getDateRanges() {{
  const now   = new Date();
  const today = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  const dow   = today.getUTCDay(); // 0=Sun,6=Sat

  const daysToFri  = (5 - dow + 7) % 7 || 7;
  const weekendStart = new Date(today); weekendStart.setUTCDate(today.getUTCDate() + (dow >= 5 ? 0 : daysToFri - (dow === 0 ? 2 : dow >= 6 ? 1 : 0)));
  if (dow === 0) weekendStart.setUTCDate(today.getUTCDate());        // Sun → this weekend
  else if (dow === 6) weekendStart.setUTCDate(today.getUTCDate());   // Sat → this weekend
  else weekendStart.setUTCDate(today.getUTCDate() + (5 - dow));      // upcoming Fri

  const weekendEnd = new Date(weekendStart); weekendEnd.setUTCDate(weekendStart.getUTCDate() + 2);
  const thisWeekEnd = new Date(today); thisWeekEnd.setUTCDate(today.getUTCDate() + (7 - dow));
  const nextWeekStart = new Date(thisWeekEnd); nextWeekStart.setUTCDate(thisWeekEnd.getUTCDate() + 1);
  const nextWeekEnd   = new Date(nextWeekStart); nextWeekEnd.setUTCDate(nextWeekStart.getUTCDate() + 6);

  return {{ today, weekendStart, weekendEnd, thisWeekEnd, nextWeekStart, nextWeekEnd }};
}}

const MONTHS = {{jan:0,feb:1,mar:2,apr:3,may:4,jun:5,jul:6,aug:7,sep:8,oct:9,nov:10,dec:11,
  january:0,february:1,march:2,april:3,june:5,july:6,august:7,september:8,october:9,november:10,december:11}};

function parseDate(dateStr) {{
  if (!dateStr) return null;
  const s = dateStr.toLowerCase().trim();
  // "Sat, 28 Jun 2026" or "Fri, 3 Jul onwards"
  let m = s.match(/(\d{{1,2}})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\w*)(?:\s+(\d{{4}}))?/);
  if (m) {{
    const yr = m[3] ? +m[3] : 2026;
    return new Date(Date.UTC(yr, MONTHS[m[2]], +m[1]));
  }}
  // "June 28 2026"
  m = s.match(/(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{{1,2}})(?:\w*)?(?:\s+(\d{{4}}))?/);
  if (m) {{
    const mon = m[1].substring(0,3);
    const yr  = m[3] ? +m[3] : 2026;
    return new Date(Date.UTC(yr, MONTHS[mon], +m[2]));
  }}
  // DD/MM/YYYY
  m = s.match(/(\d{{1,2}})[\/\-](\d{{1,2}})(?:[\/\-](\d{{2,4}}))?/);
  if (m) {{
    const yr = m[3] ? (+m[3] < 100 ? +m[3]+2000 : +m[3]) : 2026;
    return new Date(Date.UTC(yr, +m[2]-1, +m[1]));
  }}
  return null;
}}

// ── Smart Book Now ─────────────────────────────────────────────
function getBookingAction(ev) {{
  const caption = ev.caption_full || '';
  const source  = ev._source || '';

  // 1. External registration link in caption
  const links = (caption.match(/https?:\/\/[^\s\)\]]+/g) || []);
  const extLink = links.find(l => !l.includes('instagram.com') && !l.includes('facebook.com') && l.length > 20);
  if (extLink) return {{ url: extLink.replace(/[.,;)\]]+$/, ''), label: 'Register Now', type: 'primary', icon: '📝' }};

  // 2. WhatsApp link in caption
  const waLink = caption.match(/wa\.me\/(\+?[\d]+)/);
  if (waLink) return {{ url: 'https://wa.me/' + waLink[1], label: 'WhatsApp', type: 'whatsapp', icon: '💬' }};

  // 3. Phone number → WhatsApp
  const phone = caption.match(/(?:call|contact|whatsapp|register|wa)[:\s]*[\+]?(91)?[\s\-]?([6-9]\d{{9}})/i);
  if (phone) return {{ url: 'https://wa.me/91' + phone[2], label: 'WhatsApp', type: 'whatsapp', icon: '💬' }};

  // 4. BMS link
  if (ev.link && ev.link.includes('bookmyshow')) return {{ url: ev.link, label: 'Book on BMS', type: 'primary', icon: '🎟️' }};

  // 5. WhatsApp direct link (admin-set phone number)
  if (ev.link && ev.link.includes('wa.me')) return {{ url: ev.link, label: 'Chat on WhatsApp', type: 'whatsapp', icon: '💬' }};

  // 6. District.in link
  if (ev.link && (ev.link.includes('district.in') || ev.link.includes('insider.in')))
    return {{ url: ev.link, label: 'Book on District', type: 'primary', icon: '🎟️' }};

  // 7. Instagram post
  if (ev.link && ev.link.includes('instagram')) return {{ url: ev.link, label: 'View on Instagram', type: 'instagram-btn', icon: '📸' }};

  // 8. Generic link
  if (ev.link) return {{ url: ev.link, label: 'View Details', type: 'secondary', icon: '🔗' }};

  return null;
}}

// ── Filter & render ────────────────────────────────────────────
function filterEvents() {{
  const ranges = getDateRanges();
  const q = state.search.toLowerCase().trim();

  return EVENTS.filter(ev => {{
    // Past events hidden
    const dt = parseDate(ev.date || '');
    if (dt && dt < ranges.today) return false;

    // City
    if (state.city !== 'all' && (ev._city || '') !== state.city) return false;

    // Search
    if (q) {{
      const hay = [ev.title,ev.venue,ev._city,...(ev._genres||[])].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }}

    // Date
    if (state.date !== 'all' && dt) {{
      if (state.date === 'weekend'  && (dt < ranges.weekendStart || dt > ranges.weekendEnd)) return false;
      if (state.date === 'thisweek' && dt > ranges.thisWeekEnd) return false;
      if (state.date === 'nextweek' && (dt < ranges.nextWeekStart || dt > ranges.nextWeekEnd)) return false;
      if (state.date === 'custom') {{
        const from = state.dateFrom ? new Date(state.dateFrom) : null;
        const to   = state.dateTo   ? new Date(state.dateTo)   : null;
        if (to) to.setUTCHours(23, 59, 59, 999);
        if (from && dt < from) return false;
        if (to   && dt > to  ) return false;
      }}
    }}

    // Genre
    if (state.genres.size > 0) {{
      const evGenres = (ev._genres || []).map(g => g.toLowerCase());
      if (![...state.genres].some(g => evGenres.includes(g.toLowerCase()))) return false;
    }}

    return true;
  }});
}}

function sortedEvents(evs) {{
  return [...evs].sort((a,b) => {{
    if (state.sort === 'date') {{
      const da = parseDate(a.date||''), db = parseDate(b.date||'');
      if (!da && !db) return 0; if (!da) return 1; if (!db) return -1;
      return da - db;
    }}
    if (state.sort === 'price_asc') {{
      const pa = a._price_num ?? 9999, pb = b._price_num ?? 9999;
      return pa - pb;
    }}
    if (state.sort === 'price_desc') {{
      const pa = a._price_num ?? -1, pb = b._price_num ?? -1;
      return pb - pa;
    }}
    if (state.sort === 'title') return (a.title||'').localeCompare(b.title||'');
    return 0;
  }});
}}

function render() {{
  const filtered = sortedEvents(filterEvents());
  const grid  = document.getElementById('eventGrid');
  const empty = document.getElementById('emptyState');
  document.getElementById('resultsCount').textContent = filtered.length + ' event' + (filtered.length !== 1 ? 's' : '') + ' found';

  if (!filtered.length) {{ grid.innerHTML = ''; empty.style.display = 'block'; return; }}
  empty.style.display = 'none';

  grid.innerHTML = filtered.map((ev,i) => {{
    const src    = ev._source || 'bms';
    const action = getBookingAction(ev);
    const price  = ev._price_display || '';
    const isFree = price.toLowerCase() === 'free' || ev._price_num === 0;
    const genres = (ev._genres||[]).slice(0,2).map(g => `<span class="genre-tag">${{g}}</span>`).join('');
    const gi = filteredIndexToGlobal(i, filtered);

    const imgEl = ev.image
      ? `<img class="card-img-bg" src="${{ev.image}}" alt="" aria-hidden="true" loading="lazy">`
        + `<img class="card-poster" src="${{ev.image}}" alt="${{escHtml(ev.title||'')}}" loading="lazy"
               onerror="this.previousElementSibling.style.display='none';this.style.display='none';this.nextElementSibling.style.display='flex'">`
        + `<div class="card-poster-placeholder ${{src}}" style="display:none">${{src==='bms'?'🎭':'📸'}}</div>`
      : `<div class="card-poster-placeholder ${{src}}">${{src==='bms'?'🎭':'📸'}}</div>`;

    let scrimBtns = '';
    if (ev.form_link) {{
      scrimBtns += `<a href="${{ev.form_link}}" target="_blank" class="register" onclick="event.stopPropagation()">📝 Register Now</a>`;
      if (ev.email) {{
        scrimBtns += `<a href="mailto:${{ev.email}}" class="email-btn" onclick="event.stopPropagation()">✉️ Email Us</a>`;
      }}
    }} else if (ev.email) {{
      scrimBtns += `<a href="mailto:${{ev.email}}" class="email-btn" onclick="event.stopPropagation()">✉️ Email Us</a>`;
      if (action) scrimBtns += `<a href="${{action.url}}" target="_blank" class="${{action.type}}" onclick="event.stopPropagation()">${{action.icon}} ${{action.label}}</a>`;
    }} else if (action) {{
      scrimBtns = `<a href="${{action.url}}" target="_blank" class="${{action.type}}" onclick="event.stopPropagation()">${{action.icon}} ${{action.label}}</a>`;
    }}
    const scrim = scrimBtns ? `<div class="card-hover-scrim">${{scrimBtns}}</div>` : '';

    return `<div class="event-card" onclick="openModal(${{i}},${{gi}})">
      <div class="card-img-wrap">
        ${{imgEl}}
        <div class="card-top">
          ${{isFree
            ? '<span class="badge badge-free">Free</span>'
            : price ? `<span class="badge badge-price">${{price}}</span>` : ''}}
        </div>
        ${{scrim}}
      </div>
      <div class="card-body">
        <div class="card-source-row">
          <span class="card-city-label">${{ev._city||''}}</span>
          ${{genres ? `<div class="genre-tags">${{genres}}</div>` : ''}}
        </div>
        <div class="card-title">${{escHtml(ev.title||'Untitled')}}</div>
        <div class="card-meta">
          ${{ev.date  ? `<div class="meta-row">📅 ${{escHtml(ev.date)}}</div>` : ''}}
          ${{ev.time  ? `<div class="meta-row">🕐 ${{escHtml(ev.time)}}</div>` : ''}}
          ${{ev.venue ? `<div class="meta-row">📍 ${{escHtml(ev.venue)}}</div>` : ''}}
        </div>
      </div>
    </div>`;
  }}).join('');

  updateStats();
}}

function filteredIndexToGlobal(i, filtered) {{
  const ev = filtered[i];
  return EVENTS.indexOf(ev);
}}

// ── Stats ──────────────────────────────────────────────────────
function updateStats() {{
  const ranges = getDateRanges();
  const all = EVENTS.filter(ev => {{
    const dt = parseDate(ev.date||'');
    return !dt || dt >= ranges.today;
  }});
  document.getElementById('statTotal').textContent   = all.length;
  document.getElementById('statFree').textContent    = all.filter(e => e._price_num === 0 || (e._price_display||'').toLowerCase() === 'free').length;
  document.getElementById('statCities').textContent  = new Set(all.map(e=>e._city).filter(Boolean)).size;
  document.getElementById('statWeekend').textContent = all.filter(e => {{
    const dt = parseDate(e.date||'');
    return dt && dt >= ranges.weekendStart && dt <= ranges.weekendEnd;
  }}).length;
}}

// ── Modal ──────────────────────────────────────────────────────
function openModal(filtI, globalI) {{
  const ev = EVENTS[globalI];
  if (!ev) return;

  // Poster with title overlay
  const posterEl = document.getElementById('modalPoster');
  if (ev.image) {{
    posterEl.innerHTML = `<div class="modal-poster-wrap">
      <img src="${{ev.image}}" alt="${{escHtml(ev.title||'')}}" onerror="this.parentElement.style.display='none'">
      <div class="modal-poster-gradient"></div>
      <div class="modal-poster-title">${{escHtml(ev.title||'')}}</div>
    </div>`;
  }} else {{
    posterEl.innerHTML = `<div style="padding:20px 20px 0"><h2 style="font-size:1.2rem;font-weight:800;line-height:1.3">${{escHtml(ev.title||'Event Details')}}</h2></div>`;
  }}

  // Badges
  const src = ev._source || '';
  document.getElementById('modalBadges').innerHTML =
    `<span class="badge badge-city">${{ev._city||''}}</span>
     ${{(ev._genres||[]).map(g=>`<span class="badge" style="background:#EEF2FF;color:#3B4CCA">${{g}}</span>`).join('')}}`;

  // Details grid
  const fields = [
    ['📅 Date',     ev.date],
    ['🕐 Time',     ev.time],
    ['⏱ Duration',  ev.duration],
    ['🌐 Language',  ev.language],
    ['📍 Venue',    ev.venue],
    ['💰 Price',    ev._price_display],
  ];
  document.getElementById('modalDetails').innerHTML = fields
    .filter(([,v]) => v)
    .map(([l,v]) => `<div class="detail-item"><div class="detail-label">${{l}}</div><div class="detail-value">${{escHtml(v)}}</div></div>`)
    .join('');

  // Caption (Instagram only)
  const captEl = document.getElementById('modalCaption');
  const caption = ev.caption_full || '';
  if (caption && src === 'instagram') {{
    captEl.innerHTML = `<div class="caption-box"><div class="caption-label">📝 Post Caption</div><div class="caption-text">${{escHtml(caption)}}</div></div>`;
  }} else {{
    captEl.innerHTML = '';
  }}

  // Actions
  const action  = getBookingAction(ev);
  const actions = [];
  const stopProp = `onclick="event.stopPropagation()"`;
  if (ev.form_link) {{
    actions.push(`<a href="${{ev.form_link}}" target="_blank" ${{stopProp}} class="action-btn primary">📝 Register Now</a>`);
    if (ev.email) {{
      actions.push(`<a href="mailto:${{ev.email}}" ${{stopProp}} class="action-btn email-btn">✉️ Email Us</a>`);
    }}
    if (ev.link && !ev.link.includes('wa.me')) {{
      const igLabel = ev.link.includes('instagram') ? '📸 View on Instagram' : '🔗 View Post';
      actions.push(`<a href="${{ev.link}}" target="_blank" ${{stopProp}} class="action-btn secondary">${{igLabel}}</a>`);
    }}
  }} else {{
    if (ev.email) {{
      actions.push(`<a href="mailto:${{ev.email}}" ${{stopProp}} class="action-btn email-btn">✉️ Email Us</a>`);
    }}
    if (action) actions.push(`<a href="${{action.url}}" target="_blank" ${{stopProp}} class="action-btn ${{action.type}}">${{action.icon}} ${{action.label}}</a>`);
    if (ev.link && action && action.url !== ev.link) {{
      const linkLabel = src === 'bms' ? '🔗 View on BMS' : '📸 View Post';
      actions.push(`<a href="${{ev.link}}" target="_blank" ${{stopProp}} class="action-btn secondary">${{linkLabel}}</a>`);
    }}
  }}
  document.getElementById('modalActions').innerHTML = actions.join('');

  document.getElementById('modalOverlay').classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function closeModal(e) {{
  if (e && e.target !== document.getElementById('modalOverlay')) return;
  document.getElementById('modalOverlay').classList.remove('open');
  document.body.style.overflow = '';
}}

document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});

// ── URL hash routing ───────────────────────────────────────────
function pushHash() {{
  const p = new URLSearchParams();
  if (!CITY_PAGE && state.city !== 'all') p.set('city',   state.city);
  if (state.date !== 'all')               p.set('date',   state.date);
  if (state.date === 'custom') {{
    if (state.dateFrom) p.set('dateFrom', state.dateFrom);
    if (state.dateTo)   p.set('dateTo',   state.dateTo);
  }}
  if (state.genres.size)   p.set('genres', [...state.genres].join(','));
  if (state.search.trim()) p.set('q',      state.search.trim());
  const hash = p.toString();
  history.replaceState(null, '', hash ? '#' + hash : location.pathname + location.search);
}}

function applyHash() {{
  const p        = new URLSearchParams(location.hash.slice(1));
  const date     = p.get('date')     || 'all';
  const dateFrom = p.get('dateFrom') || null;
  const dateTo   = p.get('dateTo')   || null;
  const genres   = new Set((p.get('genres') || '').split(',').filter(Boolean));
  const q        = p.get('q') || '';

  if (!CITY_PAGE) {{
    const city = p.get('city') || 'all';
    state.city = city;
    document.querySelectorAll('.city-tab').forEach(b => b.classList.toggle('active', b.dataset.city === city));
  }}

  state.date     = date;
  state.dateFrom = dateFrom;
  state.dateTo   = dateTo;
  state.genres   = genres;
  state.search   = q;

  document.querySelectorAll('.date-pill').forEach(b => b.classList.toggle('active', b.dataset.date === date));

  const customRow = document.getElementById('customDateRow');
  if (date === 'custom') {{
    customRow.style.display = 'flex';
    if (dateFrom) document.getElementById('filterDateFrom').value = dateFrom;
    if (dateTo)   document.getElementById('filterDateTo').value   = dateTo;
  }} else {{
    customRow.style.display = 'none';
  }}

  document.querySelectorAll('.genre-chip').forEach(b => b.classList.toggle('active', genres.has(b.dataset.genre)));
  document.getElementById('headerSearch').value = q;
  document.getElementById('heroSearch').value   = q;
}}

// ── Control handlers ───────────────────────────────────────────
function setCity(city) {{
  if (CITY_PAGE) {{
    window.location.href = city === 'all' ? 'index.html' : city.toLowerCase().replace(/ /g, '-') + '.html';
    return;
  }}
  state.city = city;
  document.querySelectorAll('.city-tab').forEach(b => b.classList.toggle('active', b.dataset.city === city));
  pushHash(); render(); updateFilterBadge();
}}

function setDate(d) {{
  state.date = d;
  document.querySelectorAll('.date-pill').forEach(b => b.classList.toggle('active', b.dataset.date === d));
  const row = document.getElementById('customDateRow');
  if (d === 'custom') {{
    row.style.display = 'flex';
  }} else {{
    row.style.display = 'none';
    state.dateFrom = null; state.dateTo = null;
    document.getElementById('filterDateFrom').value = '';
    document.getElementById('filterDateTo').value   = '';
  }}
  pushHash(); render(); updateFilterBadge();
}}

function applyCustomRange() {{
  state.dateFrom = document.getElementById('filterDateFrom').value || null;
  state.dateTo   = document.getElementById('filterDateTo').value   || null;
  pushHash(); render(); updateFilterBadge();
}}

function toggleGenre(g) {{
  const btn = document.querySelector(`.genre-chip[data-genre="${{g}}"]`);
  if (state.genres.has(g)) {{ state.genres.delete(g); btn.classList.remove('active'); }}
  else                      {{ state.genres.add(g);    btn.classList.add('active');    }}
  pushHash(); render(); updateFilterBadge();
}}

function setSort(v) {{ state.sort = v; render(); }}

function toggleFilters() {{
  document.getElementById('filterBar').classList.toggle('open');
}}

function updateFilterBadge() {{
  let count = 0;
  const parts = [];
  if (state.city && state.city !== 'all') {{ count++; parts.push(state.city); }} else {{ parts.push('All Cities'); }}
  if (state.date && state.date !== 'all') {{ count++; parts.push(state.date === 'weekend' ? 'Weekend' : state.date === 'thisweek' ? 'This Week' : state.date === 'nextweek' ? 'Next Week' : 'Custom Date'); }} else {{ parts.push('All Dates'); }}
  if (state.genres.size) {{ count += state.genres.size; parts.push([...state.genres].join(', ')); }}
  const badge = document.getElementById('filterBadge');
  const summary = document.getElementById('filterSummary');
  if (badge) {{ badge.textContent = count; badge.style.display = count ? 'inline' : 'none'; }}
  if (summary) summary.textContent = parts.slice(0,2).join(' · ');
}}

function onSearch(v) {{
  state.search = v;
  document.getElementById('headerSearch').value = v;
  document.getElementById('heroSearch').value   = v;
  pushHash(); render();
}}

function escHtml(s) {{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

// ── Init ───────────────────────────────────────────────────────
window.addEventListener('hashchange', () => {{ applyHash(); render(); }});
applyHash();
// On genre pages: pre-select the genre if the hash doesn't already specify one
if (GENRE_PAGE && !state.genres.size) {{
  state.genres.add(GENRE_PAGE);
  const chip = document.querySelector(`.genre-chip[data-genre="${{GENRE_PAGE}}"]`);
  if (chip) chip.classList.add('active');
}}
render();
</script>
{jsonld_tag}
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out",   default=OUTPUT_FILE,  help="Output HTML file")
    parser.add_argument("--title", default=SITE_NAME,    help="Site name")
    parser.add_argument("--tag",   default=SITE_TAGLINE, help="Tagline")
    args = parser.parse_args()

    print("Building website...\n")
    base = Path(__file__).parent

    # Auto-check BMS events for sold-out status before loading
    import subprocess as _sp
    _sp.run(['python', 'check_sold_out.py'], cwd=str(base))
    print()

    events = load_all_events()

    all_cities = sorted(set(e.get("_city","") for e in events if e.get("_city")))

    # index.html — all cities, JS-filtered
    html = build_html(events, args.title, args.tag, all_cities=all_cities)
    (base / args.out).write_text(html, encoding="utf-8")
    print(f"  index.html -> {len(html)//1024} KB  ({len(events)} events)")

    # Per-city pages — SEO-friendly separate files
    for city in all_cities:
        fname      = city_filename(city)
        html       = build_html(events, args.title, args.tag,
                                city_filter=city, all_cities=all_cities, output_filename=fname)
        city_count = sum(1 for e in events if (e.get("_city") or "").lower() == city.lower())
        (base / fname).write_text(html, encoding="utf-8")
        print(f"  {fname} -> {len(html)//1024} KB  ({city_count} events)")

    # Per-city+genre pages — only generated when event count meets threshold
    genre_page_files = []
    for city in all_cities:
        city_events = [e for e in events if (e.get("_city") or "").lower() == city.lower()]
        city_genres = sorted(set(g for e in city_events for g in e.get("_genres", [])))
        for genre in city_genres:
            genre_count = sum(1 for e in city_events if genre in e.get("_genres", []))
            if genre_count < GENRE_PAGE_MIN:
                continue
            fname = genre_filename(city, genre)
            html  = build_html(events, args.title, args.tag,
                               city_filter=city, genre_filter=genre,
                               all_cities=all_cities, output_filename=fname)
            (base / fname).write_text(html, encoding="utf-8")
            print(f"  {fname} -> {len(html)//1024} KB  ({genre_count} events)")
            genre_page_files.append(fname)

    # sitemap.xml
    today_iso = date.today().isoformat()
    sitemap_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f'  <url><loc>{SITE_URL}/</loc><lastmod>{today_iso}</lastmod><changefreq>weekly</changefreq><priority>1.0</priority></url>',
    ] + [
        f'  <url><loc>{SITE_URL}/{city_filename(c)}</loc><lastmod>{today_iso}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>'
        for c in all_cities
    ] + [
        f'  <url><loc>{SITE_URL}/{f}</loc><lastmod>{today_iso}</lastmod><changefreq>weekly</changefreq><priority>0.6</priority></url>'
        for f in genre_page_files
    ] + ['</urlset>']
    total_urls = 1 + len(all_cities) + len(genre_page_files)
    (base / "sitemap.xml").write_text("\n".join(sitemap_lines) + "\n", encoding="utf-8")
    print(f"  sitemap.xml -> {total_urls} URLs")
    print(f"\nDone. {len(events)} events, {len(all_cities)} city pages, {len(genre_page_files)} genre pages.")


if __name__ == "__main__":
    main()
