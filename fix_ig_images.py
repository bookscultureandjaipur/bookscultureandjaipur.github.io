"""
Visits each Instagram post URL individually and downloads the actual post image.
Uses Edge persistent context (needs Instagram login).
"""
import asyncio, json, hashlib, subprocess, time, sys
from pathlib import Path
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8')

EDGE_EXE     = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EDGE_PROFILE = Path(r"C:\Users\Vaibhav Choudhary\AppData\Local\Microsoft\Edge\User Data")

IG_FILES = ["ig_events.json", "ig_mumbai_events.json"]
IMG_DIR  = Path("images")
IMG_DIR.mkdir(exist_ok=True)

# Known bad file: the duplicate placeholder (778568 bytes)
BAD_SIZE = 778568

def image_is_square_crop(path: Path) -> bool:
    """Return True if image is 640x640 — these were og:image square crops, need re-fetch."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size == (640, 640)
    except Exception:
        return False

FIND_IMAGE_JS = r"""
() => {
    // Use the largest rendered <img> on the page (the actual post image at full resolution).
    // This avoids og:image which is always a square crop.
    const imgs = Array.from(document.querySelectorAll('img[src]'))
        .map(i => ({ src: i.src, w: i.naturalWidth || 0, h: i.naturalHeight || 0 }))
        .filter(i => (i.src.includes('cdninstagram') || i.src.includes('fbcdn')) && i.w > 100);
    imgs.sort((a, b) => (b.w * b.h) - (a.w * a.h));
    if (imgs.length > 0) return imgs[0].src;
    // Fallback: og:image
    for (const og of document.querySelectorAll('meta[property="og:image"]')) {
        const c = og.getAttribute('content') || '';
        if (c.startsWith('http') && !c.includes('instagram.com/static')) return c;
    }
    return '';
}
"""


def is_bad_image(path: Path) -> bool:
    return path.exists() and path.stat().st_size == BAD_SIZE


def make_filename(url: str, prefix: str, idx: int) -> str:
    h = hashlib.md5(url.encode()).hexdigest()[:8]
    return f"{prefix}_{idx:04d}_{h}.jpg"


async def fetch_post_image(context, post_url: str) -> str:
    page = None
    try:
        page = await context.new_page()
        await page.goto(post_url, wait_until='domcontentloaded', timeout=30_000)
        await page.wait_for_timeout(6000)
        # Dismiss login/cookie dialogs
        for sel in ['button:has-text("Not Now")', 'button:has-text("Not now")',
                    '[aria-label="Close"]', 'button:has-text("Close")']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(800)
            except Exception:
                pass
        await page.wait_for_timeout(2000)
        img_url = await page.evaluate(FIND_IMAGE_JS)
        return img_url or ''
    except Exception as e:
        print(f"      Warning: {e}")
        return ''
    finally:
        if page:
            await page.close()


async def download_bytes(context, url: str) -> bytes | None:
    page = None
    try:
        page = await context.new_page()
        resp = await page.goto(url, timeout=20_000)
        if resp and resp.status == 200:
            return await resp.body()
        return None
    except Exception as e:
        print(f"      Download warning: {e}")
        return None
    finally:
        if page:
            await page.close()


async def main():
    base = Path(__file__).parent

    # Collect events with bad/missing images
    to_fix = []  # (fp, ev_idx, post_link, prefix)
    for fname in IG_FILES:
        fp = base / fname
        if not fp.exists():
            continue
        data = json.loads(fp.read_text(encoding='utf-8'))
        prefix = 'ig' if 'mumbai' not in fname else 'igm'
        for i, ev in enumerate(data):
            local = ev.get('image', '')
            link  = ev.get('link', '')
            if not link or not link.startswith('https://www.instagram.com/p/'):
                continue
            if local:
                local_path = base / local
                if local_path.exists():
                    # Skip only if it's a good image (not placeholder, not a square crop)
                    if not is_bad_image(local_path) and not image_is_square_crop(local_path):
                        continue
            to_fix.append((fp, i, link, prefix))

    if not to_fix:
        print("All Instagram images look good already.")
        return

    print(f"Need to fix {len(to_fix)} Instagram post images ...")
    print("\nKilling any existing Edge processes ...")
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
    time.sleep(2)

    async with async_playwright() as pw:
        print("Opening Edge with your profile ...")
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(EDGE_PROFILE),
            executable_path=EDGE_EXE,
            headless=False,
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )

        file_data: dict[Path, list] = {}
        for fp in set(t[0] for t in to_fix):
            file_data[fp] = json.loads(fp.read_text(encoding='utf-8'))

        for count, (fp, ev_idx, post_link, prefix) in enumerate(to_fix, 1):
            ev = file_data[fp][ev_idx]
            title = ev.get('title', '')[:50]
            print(f"  [{count}/{len(to_fix)}] {title}")

            img_url = await fetch_post_image(context, post_link)
            if not img_url:
                print(f"           -> no image found")
                await asyncio.sleep(1.5)
                continue

            fn = make_filename(post_link, prefix, count)
            body = await download_bytes(context, img_url)
            if body and len(body) != BAD_SIZE:
                (IMG_DIR / fn).write_bytes(body)
                file_data[fp][ev_idx]['image'] = f"images/{fn}"
                print(f"           -> saved ({len(body)//1024}KB)")
            else:
                print(f"           -> bad image, skipping")

            await asyncio.sleep(1.5)

        await context.close()

        for fp, data in file_data.items():
            fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"  Updated {fp.name}")

    print("\nDone. Run: python build_website.py")


if __name__ == "__main__":
    asyncio.run(main())
