"""
Image Enrichment Script
========================
Adds 'image' field to existing event JSON files.

- BMS files      : constructs image URL directly from event ID (no browser needed)
- Instagram files : uses Edge with persistent profile to fetch og:image

Usage:
    python add_images.py                          # enriches all known JSON files
    python add_images.py --file bms_events.json   # enriches one file
"""

import asyncio
import argparse
import json
import re
import subprocess
import time
import sys
from pathlib import Path
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8')

EDGE_EXE     = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EDGE_PROFILE = Path(r"C:\Users\Vaibhav Choudhary\AppData\Local\Microsoft\Edge\User Data")

BMS_FILES = [
    "bms_events.json",
    "bms_mumbai_events.json",
    "bms_bengaluru_events.json",
    "bms_jaipur_events.json",
]
IG_FILES = [
    "ig_events.json",
    "ig_mumbai_events.json",
]

IG_OG_JS = r"""
() => {
    // Scan ALL og:image tags — BMS/Instagram sometimes has duplicates; pick first with valid URL
    for (const og of document.querySelectorAll('meta[property="og:image"]')) {
        const c = og.getAttribute('content') || '';
        if (c.startsWith('http')) return c;
    }
    const tw = document.querySelector('meta[name="twitter:image"]');
    if (tw) { const c = tw.getAttribute('content') || ''; if (c.startsWith('http')) return c; }
    const img = document.querySelector('meta[name="image"]');
    if (img) { const c = img.getAttribute('content') || ''; if (c.startsWith('http')) return c; }
    return '';
}
"""


# ── BMS: construct image URL directly from event ID ───────────────────────────

def bms_image_url(link: str) -> str:
    """
    BMS event URLs contain the event ID (ET followed by digits).
    Poster image is reliably at: https://in.bmscdn.com/events/moviecard/{ETID}.jpg
    """
    m = re.search(r'(ET\d+)', link, re.IGNORECASE)
    if not m:
        return ''
    et_id = m.group(1).upper()
    return f"https://in.bmscdn.com/events/moviecard/{et_id}.jpg"


def enrich_bms_files(files):
    for fp in files:
        data = json.loads(fp.read_text(encoding='utf-8'))
        updated = 0
        for ev in data:
            if ev.get('image') or not ev.get('link'):
                continue
            img = bms_image_url(ev['link'])
            if img:
                ev['image'] = img
                updated += 1
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"  {fp.name}: {updated} BMS images added (total {len(data)} events)")


# ── Instagram: fetch og:image via Edge ────────────────────────────────────────

async def fetch_ig_image(context, url: str) -> str:
    page = None
    try:
        page = await context.new_page()
        await page.goto(url, wait_until='domcontentloaded', timeout=25_000)
        await page.wait_for_timeout(3000)
        img = await page.evaluate(IG_OG_JS)
        return img or ''
    except Exception as e:
        print(f"      Warning: {e}")
        return ''
    finally:
        if page:
            await page.close()


async def enrich_ig_files(pw, files):
    if not files:
        return

    print("\nKilling any existing Edge processes ...")
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
    time.sleep(2)

    print("Opening Edge with your profile for Instagram ...")
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(EDGE_PROFILE),
        executable_path=EDGE_EXE,
        headless=False,
        args=["--no-first-run", "--no-default-browser-check",
              "--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )

    for fp in files:
        data = json.loads(fp.read_text(encoding='utf-8'))
        to_enrich = [(i, ev) for i, ev in enumerate(data) if not ev.get('image') and ev.get('link')]
        if not to_enrich:
            print(f"  {fp.name}: already complete, skipping.")
            continue
        print(f"\n  {fp.name}: fetching images for {len(to_enrich)} events ...")
        updated = 0
        for idx_in_list, (idx, ev) in enumerate(to_enrich, 1):
            print(f"    [{idx_in_list}/{len(to_enrich)}] {ev.get('title','')[:55]}")
            img_url = await fetch_ig_image(context, ev['link'])
            if img_url:
                data[idx]['image'] = img_url
                updated += 1
                print(f"           -> {img_url[:80]}")
            else:
                print(f"           -> (not found)")
            await asyncio.sleep(1.5)
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"  Saved {fp.name} ({updated} images added)")

    await context.close()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=None, help="Enrich a specific JSON file only")
    args = parser.parse_args()

    base = Path(__file__).parent

    if args.file:
        fp = base / args.file
        bms_files = [fp] if fp.name in BMS_FILES else []
        ig_files  = [fp] if fp.name in IG_FILES  else []
    else:
        bms_files = [base / f for f in BMS_FILES if (base / f).exists()]
        ig_files  = [base / f for f in IG_FILES  if (base / f).exists()]

    print(f"BMS files : {[f.name for f in bms_files]}")
    print(f"IG files  : {[f.name for f in ig_files]}\n")

    # BMS — no browser needed, just construct URLs from event IDs
    print("Adding BMS poster images (from event ID, no browser needed) ...")
    enrich_bms_files(bms_files)

    # Instagram — needs Edge
    async with async_playwright() as pw:
        await enrich_ig_files(pw, ig_files)

    print("\nAll done. Run `python build_website.py` to rebuild the website.")


if __name__ == "__main__":
    asyncio.run(main())
