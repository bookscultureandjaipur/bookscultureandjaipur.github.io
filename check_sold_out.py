"""
check_sold_out.py
=================
Visits all BMS event pages, detects sold-out / housefull status, and
auto-updates excluded_links.json. A cache (sold_out_cache.json) tracks
when each URL was last checked — URLs verified within CACHE_DAYS are
skipped so subsequent builds only check new or stale events.
"""

import asyncio, json, re, sys
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

BASE        = Path(__file__).parent
CACHE_FILE  = BASE / "sold_out_cache.json"
EXCL_FILE   = BASE / "excluded_links.json"
CACHE_DAYS  = 7
CONCURRENCY = 4

BMS_SOURCES = [
    "bms_events.json",
    "bms_mumbai_events.json",
    "bms_bengaluru_events.json",
    "bms_jaipur_events.json",
]

SOLD_OUT_PAT = re.compile(r'sold\s*out|housefull|house\s*full', re.I)


def _load(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def _save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


async def _check_url(browser, url):
    ctx  = await browser.new_context(viewport={'width': 1280, 'height': 900})
    page = await ctx.new_page()
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=20000)
        await page.wait_for_timeout(2000)
        text = await page.evaluate('() => document.body.innerText')
        return bool(SOLD_OUT_PAT.search(text))
    except Exception:
        return False   # network error → don't exclude
    finally:
        await ctx.close()


async def run():
    from playwright.async_api import async_playwright

    # Collect all BMS links from JSON sources
    all_links = set()
    for fname in BMS_SOURCES:
        fp = BASE / fname
        if not fp.exists():
            continue
        for ev in json.loads(fp.read_text(encoding='utf-8')):
            link = ev.get('link', '').strip().rstrip('/')
            if link and 'bookmyshow' in link:
                all_links.add(link)

    cache    = _load(CACHE_FILE, {})
    excluded = set(_load(EXCL_FILE, []))

    today   = str(date.today())
    cutoff  = str(date.today() - timedelta(days=CACHE_DAYS))

    to_check = [
        url for url in sorted(all_links)
        if url not in excluded
        and cache.get(url, {}).get('checked', '') < cutoff
    ]

    print(f"  BMS events total : {len(all_links)}")
    print(f"  Already excluded : {len(all_links & excluded)}")
    print(f"  Need checking    : {len(to_check)}")

    if not to_check:
        print("  All events recently verified — nothing to do.")
        return

    newly_excluded = []
    sem = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled']
        )

        async def check_one(url, idx):
            async with sem:
                sold_out = await _check_url(browser, url)
                cache[url] = {'checked': today, 'sold_out': sold_out}
                status = 'SOLD OUT' if sold_out else 'ok'
                print(f"  [{idx:>3}/{len(to_check)}] {status:9}  {url.split('/')[-1]}")
                if sold_out:
                    newly_excluded.append(url)

        await asyncio.gather(*[check_one(url, i+1) for i, url in enumerate(to_check)])
        await browser.close()

    if newly_excluded:
        excluded.update(newly_excluded)
        _save(EXCL_FILE, sorted(excluded))
        print(f"\n  Added {len(newly_excluded)} newly sold-out event(s) to excluded_links.json")
    else:
        print("\n  No newly sold-out events found.")

    _save(CACHE_FILE, cache)


if __name__ == '__main__':
    print("\nChecking BMS events for sold-out status...\n")
    asyncio.run(run())
    print()
