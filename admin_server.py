"""
Events Admin Server
===================
Local admin UI for the events website.
Run:  python admin_server.py
Open: http://localhost:5000

Features:
  - Paste a BMS or Instagram URL → auto-fetches event details + image
  - Manually fill / edit any event fields
  - Save to custom_events.json
  - Delete saved events
  - Rebuild index.html with one click
"""

import asyncio, json, re, subprocess, time, hashlib, sys, uuid
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string, send_from_directory

sys.stdout.reconfigure(encoding='utf-8')

BASE         = Path(__file__).parent
CUSTOM_FILE  = BASE / "custom_events.json"
IMG_DIR      = BASE / "images"
IMG_DIR.mkdir(exist_ok=True)

EDGE_EXE     = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EDGE_PROFILE = Path(r"C:\Users\Vaibhav Choudhary\AppData\Local\Microsoft\Edge\User Data")
USER_AGENT   = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

app = Flask(__name__)

# ── Data helpers ──────────────────────────────────────────────────────────────

def load_events():
    if CUSTOM_FILE.exists():
        return json.loads(CUSTOM_FILE.read_text(encoding='utf-8'))
    return []

def save_events(evs):
    CUSTOM_FILE.write_text(json.dumps(evs, ensure_ascii=False, indent=2), encoding='utf-8')

def bms_image_url(link):
    m = re.search(r'(ET\d+)', link, re.IGNORECASE)
    return f"https://in.bmscdn.com/events/moviecard/{m.group(1).upper()}.jpg" if m else ''

# ── BMS fetch ─────────────────────────────────────────────────────────────────

BMS_JS = r"""
() => {
    const body = document.body.innerText;
    const lines = body.split('\n').map(l => l.trim()).filter(Boolean);

    let title = '';
    for (const sel of ['h1','[class*="__title"]','[class*="EventTitle"]','[class*="Title"]']) {
        const el = document.querySelector(sel);
        if (el) { title = el.innerText.trim(); break; }
    }

    const timeM   = body.match(/\b(\d{1,2}:\d{2}\s*(?:AM|PM))/i);
    const durM    = body.match(/(\d+\s*(?:hour|hr|min)[^\n.]{0,30})/i);
    const langM   = body.match(/(?:Language|Lang)[:\s]+([A-Za-z ,|\/]+)/i);
    const genreM  = body.match(/(?:Genre|Category|Type)[:\s]+([A-Za-z ,|\/]+)/i);
    const priceM  = body.match(/(?:₹|Rs\.?)\s*([\d,]+)/);
    const dateM   = body.match(/\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:\s+\d{4})?)\b/i);
    const soldOut = /sold\s*out|housefull|house\s*full/i.test(body);

    let venue = '';
    for (const sel of ['[class*="venue"]','[class*="Venue"]','[data-test*="venue"]']) {
        const el = document.querySelector(sel);
        if (el) { venue = el.innerText.trim(); break; }
    }
    if (!venue) {
        const pi = lines.findIndex(l => /^₹\s*[\d,]+/.test(l));
        if (pi > 0) {
            const c = lines[pi - 1];
            if (!/^\d|Age Limit|yrs|mins|hour|Comedy|Drama|Musical|Hindi|English|Marathi|Tamil|Telugu|Punjabi|Urdu|Onwards/i.test(c)
                && c.length > 3 && c.length < 120) venue = c;
        }
    }

    return {
        title:    title,
        date:     dateM  ? dateM[1]  : '',
        time:     timeM  ? timeM[1]  : '',
        duration: durM   ? durM[1].trim()  : '',
        language: langM  ? langM[1].replace(/\|/g,',').trim().substring(0,60) : '',
        genre:    genreM ? genreM[1].replace(/\|/g,',').trim().substring(0,80) : '',
        price:     priceM ? priceM[1] : '',
        venue:     venue,
        sold_out:  soldOut,
    };
}
"""

IG_JS = r"""
() => {
    const imgs = Array.from(document.querySelectorAll('img[src]'))
        .map(i => ({ src: i.src, w: i.naturalWidth || 0, h: i.naturalHeight || 0 }))
        .filter(i => (i.src.includes('cdninstagram') || i.src.includes('fbcdn')) && i.w > 100);
    imgs.sort((a, b) => (b.w * b.h) - (a.w * a.h));

    const og = s => { const el = document.querySelector(`meta[property="${s}"]`); return el ? el.getAttribute('content') || '' : ''; };
    return {
        imgUrl:  imgs.length ? imgs[0].src : '',
        caption: og('og:description'),
        title:   og('og:title').replace(/\s*on Instagram$/,'').trim(),
    };
}
"""

# ── Playwright helpers ────────────────────────────────────────────────────────

async def _bms_fetch(url):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        ctx = await browser.new_context(
            user_agent=USER_AGENT, viewport={"width": 1280, "height": 900},
            locale="en-IN", timezone_id="Asia/Kolkata",
        )
        page = await ctx.new_page()
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(3000)
        data = await page.evaluate(BMS_JS)
        await browser.close()

    data['link']    = url
    data['image']   = bms_image_url(url)
    data['_source'] = 'bms'
    return data


async def _ig_fetch(url):
    from playwright.async_api import async_playwright
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
    time.sleep(2)

    img_local = ''
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(EDGE_PROFILE), executable_path=EDGE_EXE,
            headless=False,
            args=["--no-first-run", "--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        page = await ctx.new_page()
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(7000)
        for sel in ['button:has-text("Not Now")', 'button:has-text("Not now")', '[aria-label="Close"]']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(800)
            except Exception:
                pass
        await page.wait_for_timeout(2000)
        raw = await page.evaluate(IG_JS)

        if raw.get('imgUrl'):
            try:
                h   = hashlib.md5(url.encode()).hexdigest()[:8]
                fn  = f"custom_{h}.jpg"
                p2  = await ctx.new_page()
                resp = await p2.goto(raw['imgUrl'], timeout=20000)
                if resp and resp.status == 200:
                    (IMG_DIR / fn).write_bytes(await resp.body())
                    img_local = f"images/{fn}"
                await p2.close()
            except Exception as e:
                print(f"Image download warning: {e}")

        await ctx.close()

    caption = raw.get('caption', '')
    dm  = re.search(r'\b(\d{1,2}[\s/\-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:[\s,]+\d{4})?)', caption, re.I)
    vm  = re.search(r'(?:📍|venue|location|at\s+the|at\s+)[:\s]+([^\n|,📅🕐💰]{3,80})', caption, re.I)
    tm  = re.search(r'\b(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))', caption)
    pm  = re.search(r'(?:₹|Rs\.?)\s*([\d,]+)', caption)

    return {
        'title':        raw.get('title', ''),
        'date':         dm.group(1).strip() if dm else '',
        'time':         tm.group(1)         if tm else '',
        'venue':        vm.group(1).strip() if vm else '',
        'price':        pm.group(1)         if pm else '',
        'link':         url,
        'image':        img_local,
        'caption_full': caption,
        '_source':      'instagram',
    }

# ── API routes ────────────────────────────────────────────────────────────────

@app.route('/api/fetch', methods=['POST'])
def api_fetch():
    url = (request.json or {}).get('url', '').strip()
    if not url:
        return jsonify({'error': 'No URL provided'}), 400
    try:
        if 'bookmyshow' in url:
            data = asyncio.run(_bms_fetch(url))
        elif 'instagram.com' in url:
            data = asyncio.run(_ig_fetch(url))
        else:
            return jsonify({'error': 'Paste a BookMyShow or Instagram URL'}), 400
        return jsonify({'ok': True, 'event': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/events', methods=['GET'])
def api_list():
    return jsonify(load_events())


@app.route('/api/events', methods=['POST'])
def api_add():
    ev = dict(request.json or {})
    ev.setdefault('_id',     str(uuid.uuid4())[:8])
    ev.setdefault('_source', 'custom')
    ev['_city'] = ev.get('city') or ev.get('_city') or ''
    events = load_events()
    # Replace if same _id, else append
    idx = next((i for i, e in enumerate(events) if e.get('_id') == ev['_id']), None)
    if idx is not None:
        events[idx] = ev
    else:
        events.append(ev)
    save_events(events)
    return jsonify({'ok': True, 'event': ev})


@app.route('/api/events/<eid>', methods=['DELETE'])
def api_delete(eid):
    save_events([e for e in load_events() if e.get('_id') != eid])
    return jsonify({'ok': True})


@app.route('/api/rebuild', methods=['POST'])
def api_rebuild():
    r = subprocess.run(['python', 'build_website.py'], capture_output=True,
                       text=True, cwd=str(BASE), encoding='utf-8')
    out = (r.stdout + r.stderr).strip()
    lines = [l for l in out.split('\n') if l.strip() and not l.startswith('C:\\')]
    return jsonify({'ok': r.returncode == 0, 'output': '\n'.join(lines)})


@app.route('/')
def admin_page():
    return render_template_string(ADMIN_HTML)


@app.route('/site/')
@app.route('/site/<path:filename>')
def serve_site(filename='index.html'):
    return send_from_directory(BASE, filename)


# ── Admin UI ──────────────────────────────────────────────────────────────────

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Events Admin</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --red:#E84C3D;--green:#27AE60;--purple:#833AB4;
  --bg:#F4F6F8;--surface:#fff;--border:#E0E6ED;
  --text:#1A1A2E;--text2:#5A6A7A;--radius:12px;
  --shadow:0 2px 12px rgba(0,0,0,.08);
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{text-decoration:none;color:inherit}

/* Header */
header{background:#1A1A2E;color:#fff;padding:0 28px;height:60px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(0,0,0,.2)}
.logo{font-size:1.1rem;font-weight:800;color:#fff;display:flex;align-items:center;gap:8px}
.logo span{background:var(--red);color:#fff;padding:2px 9px;border-radius:6px;font-size:.8rem}
.header-right{display:flex;gap:10px;align-items:center}
.btn{padding:8px 20px;border-radius:8px;font-size:.85rem;font-weight:700;cursor:pointer;border:none;transition:all .18s;font-family:inherit}
.btn-rebuild{background:var(--green);color:#fff}
.btn-rebuild:hover{background:#219a52}
.btn-rebuild:disabled{background:#aaa;cursor:not-allowed}
.btn-primary{background:var(--red);color:#fff}
.btn-primary:hover{background:#c0392b}
.btn-primary:disabled{background:#aaa;cursor:not-allowed}
.btn-secondary{background:var(--bg);color:var(--text);border:1.5px solid var(--border)}
.btn-secondary:hover{border-color:var(--text)}
.btn-ghost{background:none;border:1.5px solid rgba(255,255,255,.3);color:#fff;padding:7px 16px;font-size:.8rem}
.btn-ghost:hover{background:rgba(255,255,255,.1)}

/* Layout */
.container{max-width:860px;margin:0 auto;padding:28px 20px;display:flex;flex-direction:column;gap:24px}

/* Card */
.card{background:var(--surface);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden}
.card-header{padding:16px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.card-header h2{font-size:1rem;font-weight:700;color:var(--text)}
.card-header .subtitle{font-size:.82rem;color:var(--text2);margin-left:auto}
.card-body{padding:20px}

/* Fetch bar */
.fetch-bar{display:flex;gap:10px}
.fetch-bar input{flex:1;padding:11px 16px;border:1.5px solid var(--border);border-radius:8px;font-size:.9rem;outline:none;font-family:inherit;transition:border .18s}
.fetch-bar input:focus{border-color:var(--red)}
.fetch-bar input::placeholder{color:#aaa}
.fetch-status{margin-top:10px;font-size:.83rem;padding:8px 14px;border-radius:8px;display:none}
.fetch-status.loading{background:#FFF8E1;color:#B8860B;display:block}
.fetch-status.error{background:#FFEBEE;color:#C62828;display:block}
.fetch-status.ok{background:#E8F5E9;color:#1B5E20;display:block}

/* Form grid */
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.form-grid .full{grid-column:1/-1}
.field{display:flex;flex-direction:column;gap:5px}
.field label{font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.4px;color:var(--text2)}
.field input,.field select,.field textarea{
  padding:10px 13px;border:1.5px solid var(--border);border-radius:8px;
  font-size:.88rem;outline:none;font-family:inherit;color:var(--text);
  transition:border .18s;background:#fff;
}
.field input:focus,.field select:focus,.field textarea:focus{border-color:var(--red)}
.field textarea{resize:vertical;min-height:70px}

/* Date picker row */
.date-range-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.date-range-row input[type="date"]{flex:1;min-width:130px;padding:10px 11px;border:1.5px solid var(--border);border-radius:8px;font-size:.88rem;outline:none;font-family:inherit;color:var(--text);transition:border .18s;background:#fff}
.date-range-row input[type="date"]:focus{border-color:var(--red)}
.date-sep{color:var(--text2);font-size:.82rem;font-weight:600}
.ongoing-label{display:flex;align-items:center;gap:5px;font-size:.82rem;color:var(--text2);cursor:pointer;white-space:nowrap;padding:6px 10px;border:1.5px solid var(--border);border-radius:8px;transition:border-color .18s}
.ongoing-label:hover{border-color:#aaa}
.ongoing-label input[type="checkbox"]{width:14px;height:14px;cursor:pointer;accent-color:var(--red)}
.date-preview{margin-top:5px;font-size:.82rem;font-weight:700;color:var(--red);min-height:18px;letter-spacing:.2px}

/* Image preview */
.img-preview-wrap{margin-top:6px;width:100%;height:160px;border-radius:8px;border:1.5px dashed var(--border);display:flex;align-items:center;justify-content:center;overflow:hidden;background:#f9f9f9;position:relative}
.img-preview-wrap img{width:100%;height:100%;object-fit:contain}
.img-placeholder{color:var(--text2);font-size:.82rem;text-align:center}

/* Form actions */
.form-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:6px}

/* Saved events list */
.events-list{display:flex;flex-direction:column;gap:10px}
.event-item{border:1.5px solid var(--border);border-radius:10px;padding:14px 16px;display:flex;align-items:flex-start;gap:14px;transition:border-color .18s}
.event-item:hover{border-color:#bbb}
.event-thumb{width:52px;height:52px;border-radius:7px;object-fit:cover;flex-shrink:0;background:#eee}
.event-thumb-placeholder{width:52px;height:52px;border-radius:7px;background:linear-gradient(135deg,#E84C3D22,#E84C3D44);display:flex;align-items:center;justify-content:center;font-size:1.5rem;flex-shrink:0}
.event-info{flex:1;min-width:0}
.event-title{font-weight:700;font-size:.92rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.event-meta{font-size:.78rem;color:var(--text2);margin-top:3px;display:flex;gap:10px;flex-wrap:wrap}
.source-badge{padding:2px 8px;border-radius:6px;font-size:.65rem;font-weight:700;text-transform:uppercase}
.source-bms{background:#FEE;color:var(--red)}
.source-instagram{background:#F3EFF9;color:var(--purple)}
.source-custom{background:#EFF9F3;color:var(--green)}
.event-actions{display:flex;gap:6px;flex-shrink:0}
.btn-sm{padding:5px 12px;font-size:.75rem;border-radius:6px;cursor:pointer;border:none;font-family:inherit;font-weight:600;transition:all .15s}
.btn-delete{background:#FFEBEE;color:#C62828}
.btn-delete:hover{background:#FFCDD2}
.empty-list{text-align:center;padding:40px 20px;color:var(--text2);font-size:.9rem}

/* Card preview */
.card-preview-wrap{margin-top:4px}
.card-preview-label{font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.4px;color:var(--text2);margin-bottom:14px}
.pc-outer{display:flex;justify-content:center}
.pc-card{background:#fff;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.13);overflow:hidden;width:230px;display:flex;flex-direction:column}
.pc-img-wrap{position:relative;width:100%;aspect-ratio:1/1;overflow:hidden;background:#111;flex-shrink:0}
.pc-img-bg{position:absolute;inset:-10px;width:calc(100% + 20px);height:calc(100% + 20px);object-fit:cover;filter:blur(14px) brightness(.55) saturate(1.2);transform:scale(1.05)}
.pc-img-main{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;z-index:1}
.pc-placeholder{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:3rem;z-index:1;background:linear-gradient(160deg,#1a0828,#833AB444)}
.pc-top{position:absolute;top:9px;left:9px;right:9px;display:flex;justify-content:flex-end;z-index:2}
.pc-badge{padding:3px 9px;border-radius:10px;font-size:.68rem;font-weight:700;text-transform:uppercase;backdrop-filter:blur(4px)}
.pc-badge-free{background:rgba(39,174,96,.92);color:#fff}
.pc-badge-price{background:rgba(0,0,0,.62);color:#fff}
.pc-body{padding:12px 14px 14px;display:flex;flex-direction:column;gap:5px}
.pc-top-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.pc-city{font-size:.72rem;font-weight:600;color:#5A6A7A;text-transform:uppercase;letter-spacing:.4px}
.pc-title{font-size:.93rem;font-weight:700;color:#1A1A2E;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.pc-meta{display:flex;flex-direction:column;gap:3px}
.pc-meta-row{font-size:.77rem;color:#5A6A7A;line-height:1.4}
.pc-genres{display:flex;gap:4px;flex-wrap:wrap}
.pc-genre-tag{background:#F0F4FF;color:#3A5FCD;padding:2px 8px;border-radius:8px;font-size:.67rem;font-weight:600}
.pc-empty{color:#5A6A7A;font-size:.82rem;font-style:italic;text-align:center;padding:30px 16px;min-width:200px}
.pc-badge-wa{background:rgba(37,211,102,.92);color:#fff}
.wa-hint{margin-top:5px;font-size:.78rem;color:#25D366;font-weight:600;min-height:16px}

/* Rebuild output */
.rebuild-output{margin-top:12px;background:#1A1A2E;color:#a8ff78;padding:14px;border-radius:8px;font-family:monospace;font-size:.8rem;white-space:pre-wrap;display:none;max-height:160px;overflow-y:auto}

/* Toast */
.toast{position:fixed;bottom:28px;right:28px;background:#1A1A2E;color:#fff;padding:12px 20px;border-radius:10px;font-size:.88rem;font-weight:600;box-shadow:0 4px 20px rgba(0,0,0,.25);transform:translateY(80px);opacity:0;transition:all .3s;z-index:999}
.toast.show{transform:translateY(0);opacity:1}
.toast.success{border-left:4px solid var(--green)}
.toast.error{border-left:4px solid var(--red)}
</style>
</head>
<body>

<header>
  <div class="logo">🎭 Events Admin <span>Local</span></div>
  <div class="header-right">
    <a href="/site/" target="_blank" class="btn btn-ghost">View Website ↗</a>
    <button class="btn btn-rebuild" id="rebuildBtn" onclick="rebuild()">⟳ Rebuild Website</button>
  </div>
</header>

<div class="container">

  <!-- Fetch from URL -->
  <div class="card">
    <div class="card-header">
      <h2>🔗 Add Event from URL</h2>
      <span class="subtitle">Paste a BookMyShow or Instagram post link</span>
    </div>
    <div class="card-body">
      <div class="fetch-bar">
        <input type="url" id="fetchUrl" placeholder="https://in.bookmyshow.com/plays/... or https://www.instagram.com/p/..." onkeydown="if(event.key==='Enter')fetchFromUrl()">
        <button class="btn btn-primary" id="fetchBtn" onclick="fetchFromUrl()">Fetch Details</button>
      </div>
      <div class="fetch-status" id="fetchStatus"></div>
    </div>
  </div>

  <!-- Event form -->
  <div class="card">
    <div class="card-header">
      <h2 id="formTitle">✏️ Event Details</h2>
      <span class="subtitle" id="formSubtitle">Fill manually or use Fetch above</span>
    </div>
    <div class="card-body">
      <input type="hidden" id="fId">
      <input type="hidden" id="fSource" value="custom">

      <div class="form-grid">
        <div class="field full">
          <label>Event Title *</label>
          <input type="text" id="fTitle" placeholder="Name of the event" oninput="updateCardPreview()">
        </div>

        <div class="field">
          <label>City *</label>
          <select id="fCity" onchange="updateCardPreview()">
            <option value="">Select city</option>
            <option value="Delhi">Delhi</option>
            <option value="Mumbai">Mumbai</option>
            <option value="Bengaluru">Bengaluru</option>
            <option value="Jaipur">Jaipur</option>
            <option value="Other">Other</option>
          </select>
        </div>

        <div class="field">
          <label>Price (₹ or "Free")</label>
          <input type="text" id="fPrice" placeholder="e.g. 299 or Free" oninput="updateCardPreview()">
        </div>

        <div class="field full">
          <label>Date</label>
          <div class="date-range-row">
            <input type="date" id="fDateStart" onchange="updateDatePreview()">
            <span class="date-sep" id="dateSep">to</span>
            <input type="date" id="fDateEnd" onchange="updateDatePreview()">
            <label class="ongoing-label" title="No fixed end date">
              <input type="checkbox" id="fOngoing" onchange="toggleOngoing()"> Ongoing
            </label>
          </div>
          <div class="date-preview" id="datePreview"></div>
        </div>

        <div class="field">
          <label>Time</label>
          <input type="text" id="fTime" placeholder="e.g. 7:00 PM" oninput="updateCardPreview()">
        </div>

        <div class="field full">
          <label>Venue</label>
          <input type="text" id="fVenue" placeholder="Venue name and city" oninput="updateCardPreview()">
        </div>

        <div class="field">
          <label>Duration</label>
          <input type="text" id="fDuration" placeholder="e.g. 2 hours">
        </div>

        <div class="field">
          <label>Language</label>
          <input type="text" id="fLanguage" placeholder="e.g. Hindi, English">
        </div>

        <div class="field">
          <label>Booking / Event Link</label>
          <input type="url" id="fLink" placeholder="https://...">
        </div>

        <div class="field">
          <label>WhatsApp Number</label>
          <input type="text" id="fPhone" placeholder="e.g. 9876543210" oninput="onPhoneInput()" maxlength="15">
          <div class="wa-hint" id="waHint"></div>
        </div>

        <div class="field full">
          <label>Image URL (or local path)</label>
          <input type="text" id="fImage" placeholder="https://... or images/..." oninput="previewImage(this.value);updateCardPreview()">
          <div class="img-preview-wrap" id="imgPreview">
            <div class="img-placeholder">🖼️<br>Image preview</div>
          </div>
        </div>

        <div class="field full card-preview-wrap">
          <div class="card-preview-label">Card Preview</div>
          <div class="pc-outer"><div id="cardPreview"><div class="pc-empty">Fill in the form to see preview</div></div></div>
        </div>

        <div class="field full">
          <label>Caption / Description</label>
          <textarea id="fCaption" placeholder="Event description or Instagram caption..." oninput="updateCardPreview()"></textarea>
        </div>
      </div>

      <div class="form-actions">
        <button class="btn btn-secondary" onclick="clearForm()">Clear</button>
        <button class="btn btn-primary" onclick="saveEvent()">💾 Save Event</button>
      </div>
    </div>
  </div>

  <!-- Rebuild output -->
  <div class="card" id="rebuildCard" style="display:none">
    <div class="card-header"><h2>⟳ Build Output</h2></div>
    <div class="card-body" style="padding-bottom:0">
      <pre class="rebuild-output" id="rebuildOutput" style="display:block;margin-bottom:20px"></pre>
    </div>
  </div>

  <!-- Saved events -->
  <div class="card">
    <div class="card-header">
      <h2>📋 Saved Custom Events</h2>
      <span class="subtitle" id="eventCount">0 events</span>
    </div>
    <div class="card-body" id="eventsList">
      <div class="empty-list">No custom events yet. Fetch one from a URL or fill the form above.</div>
    </div>
  </div>

</div>

<div class="toast" id="toast"></div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
let events = [];

// ── Init ───────────────────────────────────────────────────────────────────
loadEvents();

async function loadEvents() {
  const r = await fetch('/api/events');
  events = await r.json();
  renderEvents();
}

// ── Fetch from URL ─────────────────────────────────────────────────────────
async function fetchFromUrl() {
  const url = document.getElementById('fetchUrl').value.trim();
  if (!url) return;
  const btn    = document.getElementById('fetchBtn');
  const status = document.getElementById('fetchStatus');

  btn.disabled = true;
  btn.textContent = 'Fetching...';
  status.className = 'fetch-status loading';
  status.textContent = url.includes('instagram')
    ? '⏳ Opening Edge and fetching Instagram post... (this takes ~15 seconds, Edge will open)'
    : '⏳ Opening browser to fetch BMS page...';

  try {
    const r = await fetch('/api/fetch', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ url }),
    });
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || 'Fetch failed');

    fillForm(data.event);
    status.className = 'fetch-status ok';
    status.textContent = '✅ Event details fetched — review and save below.';
    toast('Details fetched! Review and save.', 'success');
  } catch(e) {
    status.className = 'fetch-status error';
    status.textContent = '❌ ' + e.message;
    toast(e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Fetch Details';
  }
}

// ── Form ───────────────────────────────────────────────────────────────────
function fillForm(ev) {
  document.getElementById('fId').value       = ev._id      || '';
  document.getElementById('fSource').value   = ev._source  || 'custom';
  document.getElementById('fTitle').value    = ev.title    || '';
  document.getElementById('fCity').value     = ev._city || ev.city || '';
  document.getElementById('fTime').value     = ev.time     || '';
  document.getElementById('fVenue').value    = ev.venue    || '';
  document.getElementById('fPrice').value    = ev.price    || '';
  document.getElementById('fDuration').value = ev.duration || '';
  document.getElementById('fLanguage').value = ev.language || '';
  document.getElementById('fLink').value     = ev.link     || '';
  document.getElementById('fPhone').value    = ev.phone    || '';
  refreshWaHint();
  document.getElementById('fImage').value    = ev.image    || '';
  document.getElementById('fCaption').value  = ev.caption_full || '';
  setDateFromString(ev.date || '');
  previewImage(ev.image || '');
  document.getElementById('formTitle').textContent    = '✏️ Review & Save';
  document.getElementById('formSubtitle').textContent = 'Edit any field, then save';
  updateCardPreview();
  document.getElementById('cardPreview').scrollIntoView({behavior:'smooth', block:'center'});
}

function clearForm() {
  ['fId','fSource','fTitle','fTime','fVenue','fPrice',
   'fDuration','fLanguage','fLink','fPhone','fImage','fCaption'].forEach(id => {
    document.getElementById(id).value = '';
  });
  document.getElementById('fCity').value = '';
  document.getElementById('fSource').value = 'custom';
  document.getElementById('fDateStart').value = '';
  document.getElementById('fDateEnd').value   = '';
  document.getElementById('fOngoing').checked = false;
  document.getElementById('fDateEnd').style.display  = '';
  document.getElementById('dateSep').style.display   = '';
  document.getElementById('datePreview').textContent = '';
  document.getElementById('waHint').textContent = '';
  previewImage('');
  document.getElementById('formTitle').textContent    = '✏️ Event Details';
  document.getElementById('formSubtitle').textContent = 'Fill manually or use Fetch above';
  updateCardPreview();
}

function previewImage(src) {
  const wrap = document.getElementById('imgPreview');
  if (src) {
    wrap.innerHTML = `<img src="${src}" onerror="this.parentElement.innerHTML='<div class=img-placeholder>⚠️ Image not found</div>'">`;
  } else {
    wrap.innerHTML = '<div class="img-placeholder">🖼️<br>Image preview</div>';
  }
}

async function saveEvent() {
  const title = document.getElementById('fTitle').value.trim();
  const city  = document.getElementById('fCity').value;
  if (!title) { toast('Please enter an event title', 'error'); return; }
  if (!city)  { toast('Please select a city', 'error'); return; }

  const phone = document.getElementById('fPhone').value.trim();
  const waLink = phone ? buildWaLink(phone) : '';
  const ev = {
    _id:          document.getElementById('fId').value      || undefined,
    _source:      document.getElementById('fSource').value  || 'custom',
    title,
    city,
    date:         buildDateValue(),
    time:         document.getElementById('fTime').value.trim(),
    venue:        document.getElementById('fVenue').value.trim(),
    price:        document.getElementById('fPrice').value.trim(),
    duration:     document.getElementById('fDuration').value.trim(),
    language:     document.getElementById('fLanguage').value.trim(),
    link:         waLink || document.getElementById('fLink').value.trim(),
    phone:        phone,
    image:        document.getElementById('fImage').value.trim(),
    caption_full: document.getElementById('fCaption').value.trim(),
  };

  const r    = await fetch('/api/events', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(ev) });
  const data = await r.json();
  if (data.ok) {
    toast('Event saved!', 'success');
    clearForm();
    loadEvents();
  } else {
    toast('Save failed', 'error');
  }
}

// ── Events list ────────────────────────────────────────────────────────────
function renderEvents() {
  const el    = document.getElementById('eventsList');
  const count = document.getElementById('eventCount');
  count.textContent = events.length + ' event' + (events.length !== 1 ? 's' : '');

  if (!events.length) {
    el.innerHTML = '<div class="empty-list">No custom events yet. Fetch one from a URL or fill the form above.</div>';
    return;
  }

  el.innerHTML = `<div class="events-list">${events.map(ev => {
    const src   = ev._source || 'custom';
    const thumb = ev.image
      ? `<img class="event-thumb" src="${ev.image}" onerror="this.style.display='none'">`
      : `<div class="event-thumb-placeholder">${src==='bms'?'🎭':src==='instagram'?'📸':'✨'}</div>`;
    const meta  = [ev.city||ev._city, ev.date, ev.venue].filter(Boolean).join(' · ');
    return `<div class="event-item">
      ${thumb}
      <div class="event-info">
        <div class="event-title">${esc(ev.title||'Untitled')}</div>
        <div class="event-meta">
          <span class="source-badge source-${src}">${src.toUpperCase()}</span>
          ${meta}
        </div>
      </div>
      <div class="event-actions">
        <button class="btn-sm btn-secondary" onclick="fillForm(${JSON.stringify(ev).replace(/"/g,'&quot;')})">Edit</button>
        <button class="btn-sm btn-delete" onclick="deleteEvent('${ev._id}')">Delete</button>
      </div>
    </div>`;
  }).join('')}</div>`;
}

async function deleteEvent(id) {
  if (!confirm('Delete this event?')) return;
  await fetch(`/api/events/${id}`, { method:'DELETE' });
  toast('Event deleted', 'success');
  loadEvents();
}

// ── Rebuild ────────────────────────────────────────────────────────────────
async function rebuild() {
  const btn = document.getElementById('rebuildBtn');
  btn.disabled = true;
  btn.textContent = '⟳ Building...';
  document.getElementById('rebuildCard').style.display = 'block';
  const out = document.getElementById('rebuildOutput');
  out.textContent = 'Running build_website.py...';

  const r    = await fetch('/api/rebuild', { method:'POST' });
  const data = await r.json();
  out.textContent = data.output || '(no output)';
  btn.disabled    = false;
  btn.textContent = '⟳ Rebuild Website';
  toast(data.ok ? 'Website rebuilt!' : 'Build failed — check output', data.ok ? 'success' : 'error');
}

// ── Date helpers ───────────────────────────────────────────────────────────
const _DAYS  = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
const _MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const _MONTHS_IDX = {jan:0,feb:1,mar:2,apr:3,may:4,jun:5,jul:6,aug:7,sep:8,oct:9,nov:10,dec:11};

function formatDateISO(iso) {
  // "2026-07-05" → "Sat, 5 Jul 2026"
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  const dt  = new Date(y, m - 1, d);
  return `${_DAYS[dt.getDay()]}, ${d} ${_MONTHS[m - 1]} ${y}`;
}

function parseDateToISO(str) {
  // "Sat, 5 Jul 2026" or "5 Jul" → "2026-07-05"
  if (!str) return '';
  const m = str.toLowerCase().match(/(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\w*)?(?:\s+(\d{4}))?/);
  if (!m) return '';
  const yr = m[3] ? +m[3] : 2026;
  const mo = String(_MONTHS_IDX[m[2]] + 1).padStart(2, '0');
  const dy = String(+m[1]).padStart(2, '0');
  return `${yr}-${mo}-${dy}`;
}

function buildDateValue() {
  const start   = document.getElementById('fDateStart').value;
  const end     = document.getElementById('fDateEnd').value;
  const ongoing = document.getElementById('fOngoing').checked;
  if (!start) return '';
  const startFmt = formatDateISO(start);
  if (ongoing)           return startFmt + ' onwards';
  if (end && end !== start) return startFmt + ' - ' + formatDateISO(end);
  return startFmt;
}

function updateDatePreview() {
  const val = buildDateValue();
  document.getElementById('datePreview').textContent = val || '';
  updateCardPreview();
}

function toggleOngoing() {
  const on = document.getElementById('fOngoing').checked;
  document.getElementById('fDateEnd').style.display = on ? 'none' : '';
  document.getElementById('dateSep').style.display  = on ? 'none' : '';
  if (on) document.getElementById('fDateEnd').value = '';
  updateDatePreview();
}

function setDateFromString(dateStr) {
  const ongoing = /onwards/i.test(dateStr);
  const clean   = dateStr.replace(/\s*onwards\s*/i, '').trim();
  const parts   = clean.split(/\s*-\s(?=[A-Z])/);   // split on " - Mon" pattern
  document.getElementById('fDateStart').value    = parseDateToISO(parts[0] || '');
  document.getElementById('fDateEnd').value      = parts[1] ? parseDateToISO(parts[1]) : '';
  document.getElementById('fOngoing').checked    = ongoing;
  document.getElementById('fDateEnd').style.display = ongoing ? 'none' : '';
  document.getElementById('dateSep').style.display  = ongoing ? 'none' : '';
  updateDatePreview();
}

// ── WhatsApp helpers ───────────────────────────────────────────────────────
function buildWaLink(raw) {
  const digits = raw.replace(/\D/g, '');
  if (!digits) return '';
  const num = digits.length === 10 ? '91' + digits : digits;
  return 'https://wa.me/' + num;
}

function refreshWaHint() {
  const raw  = document.getElementById('fPhone').value.trim();
  const hint = document.getElementById('waHint');
  if (raw) {
    hint.textContent = '→ ' + buildWaLink(raw);
  } else {
    hint.textContent = '';
  }
  updateCardPreview();
}

function onPhoneInput() {
  const raw  = document.getElementById('fPhone').value.trim();
  if (raw) document.getElementById('fLink').value = buildWaLink(raw);
  else if (document.getElementById('fLink').value.startsWith('https://wa.me/'))
    document.getElementById('fLink').value = '';
  refreshWaHint();
}

// ── Card Preview ───────────────────────────────────────────────────────────
const KW_MAP = {
  Comedy:    ['comedy','standup','stand-up','improv','laughter','funny'],
  Drama:     ['drama','play','theatre','theater','natak'],
  Musical:   ['musical','music show','concert','live music','ghazal','qawwali','sufi'],
  Heritage:  ['heritage','walk','museum','fort','history','archaeological','monument'],
  Workshop:  ['workshop','masterclass','training','acting class','craft class'],
  Poetry:    ['poetry','poem','shayari','open mic','storytelling','kavishala'],
  Dance:     ['dance','bhangra','kathak','bharatanatyam','nritya'],
  Art:       ['art','painting','exhibition','gallery','sculpture'],
  Classical: ['classical','hindustani','carnatic','tabla','sitar','flute','santoor'],
  Film:      ['film','cinema','screening','movie'],
};

function detectGenres() {
  const title   = (document.getElementById('fTitle').value || '').toLowerCase();
  const caption = (document.getElementById('fCaption').value || '').toLowerCase().substring(0, 200);
  const titleFound = [], otherFound = [];
  for (const [label, kws] of Object.entries(KW_MAP)) {
    if (kws.some(k => title.includes(k)))        titleFound.push(label);
    else if (kws.some(k => caption.includes(k))) otherFound.push(label);
  }
  const combined = [...titleFound, ...otherFound].slice(0, 3);
  return combined.length ? combined : ['Event'];
}

function updateCardPreview() {
  const title  = document.getElementById('fTitle').value.trim();
  const city   = document.getElementById('fCity').value;
  const price  = document.getElementById('fPrice').value.trim();
  const time   = document.getElementById('fTime').value.trim();
  const venue  = document.getElementById('fVenue').value.trim();
  const image  = document.getElementById('fImage').value.trim();
  const phone  = document.getElementById('fPhone').value.trim();
  const date   = buildDateValue();
  const wrap   = document.getElementById('cardPreview');

  if (!title && !city) {
    wrap.innerHTML = '<div class="pc-empty">Fill in the form to see preview</div>';
    return;
  }

  const genres = detectGenres();

  // Image
  let imgHtml = '';
  if (image) {
    imgHtml = `<img class="pc-img-bg" src="${esc(image)}" alt=""><img class="pc-img-main" src="${esc(image)}" alt="${esc(title)}">`;
  } else {
    const emojis = {Comedy:'😂',Drama:'🎭',Musical:'🎵',Dance:'💃',Classical:'🎶',Film:'🎬',Art:'🎨',Poetry:'📜',Workshop:'🛠️',Heritage:'🏛️',Event:'🎪'};
    imgHtml = `<div class="pc-placeholder">${emojis[genres[0]] || '🎪'}</div>`;
  }

  // Price / WhatsApp badge
  let badgeHtml = '';
  if (phone) {
    badgeHtml = `<span class="pc-badge pc-badge-wa">💬 WhatsApp</span>`;
  } else if (price) {
    if (/^free$/i.test(price)) {
      badgeHtml = `<span class="pc-badge pc-badge-free">FREE</span>`;
    } else {
      const num = price.replace(/[^\d]/g, '');
      if (num) badgeHtml = `<span class="pc-badge pc-badge-price">₹${num}</span>`;
    }
  }

  // Meta rows
  const rows = [];
  if (date)  rows.push(`<div class="pc-meta-row">📅 ${esc(date)}</div>`);
  if (time)  rows.push(`<div class="pc-meta-row">🕐 ${esc(time)}</div>`);
  if (venue) rows.push(`<div class="pc-meta-row">📍 ${esc(venue)}</div>`);

  const genreTags = genres.map(g => `<span class="pc-genre-tag">${esc(g)}</span>`).join('');

  wrap.innerHTML = `<div class="pc-card">
    <div class="pc-img-wrap">
      ${imgHtml}
      <div class="pc-top">${badgeHtml}</div>
    </div>
    <div class="pc-body">
      ${city ? `<div class="pc-top-row"><span class="pc-city">${esc(city)}</span></div>` : ''}
      <div class="pc-title">${esc(title || 'Event Title')}</div>
      ${rows.length ? `<div class="pc-meta">${rows.join('')}</div>` : ''}
      <div class="pc-genres">${genreTags}</div>
    </div>
  </div>`;
}

// ── Utilities ──────────────────────────────────────────────────────────────
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function toast(msg, type='success') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className   = `toast ${type} show`;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 3000);
}
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("\n🎭  Events Admin Server")
    print(f"   Admin UI : http://localhost:5000")
    print(f"   Data dir : {BASE}")
    print(f"   Press Ctrl+C to stop\n")
    app.run(debug=False, port=5000, use_reloader=False)
