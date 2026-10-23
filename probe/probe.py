import os, re, json, sys, time, gzip
import requests
sys.stdout.reconfigure(line_buffering=True)
os.makedirs("probe_out", exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept-Language": "uk-UA,uk;q=0.9"})
FR = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
def info(t):
    try:
        fl = "".join(json.loads('"' + m + '"') for m in FR.findall(t)); i = fl.find('{"realties":{"cards":')
        o, _ = json.JSONDecoder().raw_decode(fl, i); cs = o["realties"]["cards"]
        return f"total={o['realties']['totalRealtiesCount']} init={o.get('initialValuesStr')} areas={[c.get('areaTotal') for c in cs[:8]]}"
    except Exception as e:
        return f"parse-err {e}"
L = "https://lun.ua/sale/odesa/flats-prymorskyi-district?room_count=1&room_count=2&currency=USD&price_min=70000&price_max=80000&sort=insert_time"
for k, q in [("base", ""), ("area_total", "&area_total_min=40&area_total_max=50"), ("area", "&area_min=40&area_max=50"),
             ("total_area", "&total_area_min=40&total_area_max=50"), ("areaTotal", "&areaTotalMin=40&areaTotalMax=50"),
             ("square", "&square_min=40&square_max=50")]:
    r = S.get(L + q, timeout=25); print(k, r.status_code, info(r.text)); time.sleep(1.5)
# ключі в JS-бандлах, схожі на площу
h = S.get(L, timeout=25).text
found = {}
for cu in sorted(set(re.findall(r'https://lun\.ua/_next/static/chunks/[A-Za-z0-9_\-]+\.js', h))):
    js = S.get(cu, timeout=25).text
    for w in re.findall(r'"([a-z_]*(?:area|square)[a-z_]*)"', js):
        found[w] = found.get(w, 0) + 1
    time.sleep(0.3)
print("AREA_KEYS", json.dumps(found))
R = "https://dom.ria.com/node/searchEngine/v2/?category=1&realty_type=2&operation=1&state_id=12&city_id=12&wo_dupl=1&limit=3&page=0"
for k, q in [("ria_base", ""), ("ria_area", "&characteristic%5B214%5D%5Bfrom%5D=40&characteristic%5B214%5D%5Bto%5D=50")]:
    print(k, S.get(R + q, timeout=25).text[:120]); time.sleep(1)
