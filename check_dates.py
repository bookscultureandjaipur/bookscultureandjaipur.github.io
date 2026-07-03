import json, re
from pathlib import Path
from datetime import date

MONTHS = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
          'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}

def end_date(date_str):
    if not date_str: return None
    if re.search(r'onwards', date_str, re.I): return None
    matches = list(re.finditer(r'(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*(\d{4})?', date_str, re.I))
    if not matches: return None
    year_m = re.search(r'\b(202\d)\b', date_str)
    year = int(year_m.group(1)) if year_m else 2026
    last = matches[-1]
    try:
        return date(int(last.group(3)) if last.group(3) else year, MONTHS[last.group(2).lower()[:3]], int(last.group(1)))
    except ValueError:
        return None

today = date.today()
files = ['bms_events.json','bms_mumbai_events.json','bms_jaipur_events.json','bms_bengaluru_events.json','custom_events.json','ig_events.json','ig_mumbai_events.json']
print(f"Today: {today}\n")
for f in files:
    fp = Path(f)
    if not fp.exists(): continue
    events = json.loads(fp.read_text(encoding='utf-8'))
    past = [e for e in events if (end_date(e.get('date','')) or date.max) < today]
    if past:
        print(f"=== PAST EVENTS in {f} ===")
        for e in past:
            print(f"  end={end_date(e.get('date',''))}  date={e.get('date','')!r:45}  {e.get('title','')[:40]}")
    else:
        print(f"=== {f}: no past events ===")
