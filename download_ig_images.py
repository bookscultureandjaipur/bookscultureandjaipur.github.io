"""
Downloads Instagram CDN images to local images/ folder and updates JSON files.
Must be run while logged into Instagram in Edge (uses Edge cookies via browser_cookie3).
Falls back to requests if cookies unavailable.
"""
import json, re, time, sys, hashlib, subprocess
from pathlib import Path
from playwright.async_api import async_playwright
import asyncio

sys.stdout.reconfigure(encoding='utf-8')

EDGE_EXE     = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EDGE_PROFILE = Path(r"C:\Users\Vaibhav Choudhary\AppData\Local\Microsoft\Edge\User Data")

IG_FILES = [
    "ig_events.json",
    "ig_mumbai_events.json",
]

IMG_DIR = Path("images")
IMG_DIR.mkdir(exist_ok=True)


async def download_image(context, url: str, filename: str) -> bool:
    """Download an image URL using the browser context (has Instagram cookies)."""
    page = None
    try:
        page = await context.new_page()
        resp = await page.goto(url, timeout=20_000)
        if resp and resp.status == 200:
            body = await resp.body()
            with open(IMG_DIR / filename, 'wb') as f:
                f.write(body)
            return True
        return False
    except Exception as e:
        print(f"      Warning: {e}")
        return False
    finally:
        if page:
            await page.close()


def image_filename(url: str, idx: int) -> str:
    h = hashlib.md5(url.encode()).hexdigest()[:8]
    return f"ig_{idx:04d}_{h}.jpg"


async def main():
    base = Path(__file__).parent

    # Collect all events needing image download
    to_download = []  # (file_path, event_idx, url, filename)
    for fname in IG_FILES:
        fp = base / fname
        if not fp.exists():
            continue
        data = json.loads(fp.read_text(encoding='utf-8'))
        for i, ev in enumerate(data):
            url = ev.get('image', '')
            if not url or url.startswith('images/'):
                continue  # already local or missing
            fn = image_filename(url, len(to_download))
            local_path = IMG_DIR / fn
            if local_path.exists():
                # Already downloaded, just update reference
                ev['image'] = f"images/{fn}"
            else:
                to_download.append((fp, i, url, fn))
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    if not to_download:
        print("All Instagram images already local. Run build_website.py to rebuild.")
        return

    print(f"Need to download {len(to_download)} Instagram images...")
    print("\nKilling any existing Edge processes ...")
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
    time.sleep(2)

    async with async_playwright() as pw:
        print("Opening Edge with your profile (for Instagram CDN cookies) ...")
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(EDGE_PROFILE),
            executable_path=EDGE_EXE,
            headless=False,
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )

        # Group by file for efficient JSON updates
        file_data_cache = {}

        for idx, (fp, ev_idx, url, fn) in enumerate(to_download, 1):
            print(f"  [{idx}/{len(to_download)}] Downloading {fn}")
            ok = await download_image(context, url, fn)
            if ok:
                # Load + update JSON
                if fp not in file_data_cache:
                    file_data_cache[fp] = json.loads(fp.read_text(encoding='utf-8'))
                file_data_cache[fp][ev_idx]['image'] = f"images/{fn}"
                print(f"           -> saved")
            else:
                print(f"           -> failed")
            await asyncio.sleep(0.5)

        await context.close()

        # Write updated JSONs
        for fp, data in file_data_cache.items():
            fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"  Updated {fp.name}")

    print("\nDone. Now run: python build_website.py")


if __name__ == "__main__":
    asyncio.run(main())
