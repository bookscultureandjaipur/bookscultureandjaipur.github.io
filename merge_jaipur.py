import json, sys
sys.stdout.reconfigure(encoding='utf-8')

plays = json.load(open('bms_jaipur_plays_raw.json', encoding='utf-8'))
music = json.load(open('bms_jaipur_music_raw.json', encoding='utf-8'))

combined = plays + music

def is_jaipur(ev):
    v = (ev.get('venue') or '').lower()
    if not v or 'multiple venues' in v or 'to be announced' in v:
        return True
    return 'jaipur' in v or 'rajasthan' in v

filtered = [ev for ev in combined if is_jaipur(ev)]

seen = set()
deduped = []
for ev in filtered:
    key = ev.get('link') or ev.get('title')
    if key not in seen:
        seen.add(key)
        deduped.append(ev)

print(f'Plays raw: {len(plays)} | Music raw: {len(music)} | After Jaipur filter + dedup: {len(deduped)}')
for ev in deduped:
    date = (ev.get('date') or '')[:15]
    title = (ev.get('title') or '')[:50]
    venue = (ev.get('venue') or '')[:40]
    print(f'  {date:15}  {title:50}  {venue}')

json.dump(deduped, open('bms_jaipur_events.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('\nSaved bms_jaipur_events.json')
