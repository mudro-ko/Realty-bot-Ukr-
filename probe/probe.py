import os, re, json, sys, time, gzip
import requests
sys.stdout.reconfigure(line_buffering=True)
OUT = "probe_out"; os.makedirs(OUT, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "uk-UA,uk;q=0.9"})


def save(k, r):
    ext = "json" if "json" in r.headers.get("content-type", "") else "html"
    with gzip.open(f"{OUT}/{k}.{ext}.gz", "wt", encoding="utf-8") as f:
        f.write(r.text)


def lun_info(text):
    try:
        flight = "".join(json.loads('"' + m + '"') for m in re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', text))
        i = flight.find('{"realties":{"cards":')
        obj, _ = json.JSONDecoder().raw_decode(flight, i)
        cs = obj["realties"]["cards"]
        return (f"total={obj['realties']['totalRealtiesCount']} init={obj.get('initialValuesStr')} n={len(cs)} "
                f"rooms={sorted(set(c.get('roomCount') or 0 for c in cs))} prices={[c.get('price') for c in cs[:6]]} "
                f"ins={[c.get('insertTime','')[:16] for c in cs[:4]]}")
    except Exception as e:
        return f"parse-err {e}"


def ria_info(text):
    try:
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*", text)
        o, _ = json.JSONDecoder().raw_decode(text, m.end())
        c = o["catalog"]
        its = [x for x in c["realtyForCatalog"] if x.get("realty_id")]
        return (f"pp={json.dumps(c['parsedParamsCatalog']['parsedParams'], ensure_ascii=False)} n={len(its)} "
                f"rooms={sorted(set(x.get('rooms_count') or 0 for x in its))} pub={[x.get('publishing_date','')[:16] for x in its[:4]]} "
                f"distr={sorted(set(str(x.get('district_name_uk')) for x in its))[:8]}")
    except Exception as e:
        return f"parse-err {e}"


TESTS = [
    ('r_wodupl', 'https://dom.ria.com/node/searchEngine/v2/?category=1&realty_type=2&operation=1&state_id=12&city_id=12&limit=5&page=0&sort=created_at&wo_dupl=1', None),
    ('r_wodupl_f', 'https://dom.ria.com/node/searchEngine/v2/?category=1&realty_type=2&operation=1&state_id=12&city_id=12&limit=5&page=0&sort=created_at&wo_dupl=1&characteristic%5B209%5D%5Bfrom%5D=1&characteristic%5B209%5D%5Bto%5D=2&characteristic%5B242%5D=239&characteristic%5B234%5D%5Bfrom%5D=50000&characteristic%5B234%5D%5Bto%5D=85000', None),
    ('r_f', 'https://dom.ria.com/node/searchEngine/v2/?category=1&realty_type=2&operation=1&state_id=12&city_id=12&limit=5&page=0&sort=created_at&characteristic%5B209%5D%5Bfrom%5D=1&characteristic%5B209%5D%5Bto%5D=2&characteristic%5B242%5D=239&characteristic%5B234%5D%5Bfrom%5D=50000&characteristic%5B234%5D%5Bto%5D=85000', None),
    ('l_rent_uah', 'https://lun.ua/rent/odesa/flats-kyivskyi-district?room_count=1&sort=insert_time&price_max=12000&currency=UAH', lun_info),
    ('l_rent_none', 'https://lun.ua/rent/odesa/flats-kyivskyi-district?room_count=1&sort=insert_time', lun_info),
]

VERIFY = []
for k, u, fn in TESTS:
    try:
        r = S.get(u, timeout=25)
        save(k, r)
        info = fn(r.text) if fn else r.text[:400].replace("\n", " ")
        print(f"{k}: {r.status_code} final={r.url[:110]} :: {info}")
        if k.startswith("r_") and r.ok:
            for rid in r.json().get("items", [])[:4]:
                d = S.get(f"https://dom.ria.com/realty/data/{rid}?lang_id=4", timeout=25).json()
                print(f"   {k} id={rid} rooms={d.get('rooms_count')} price$={(d.get('priceArr') or {}).get('1')} distr={d.get('district_name_uk')}")
                time.sleep(0.8)
    except Exception as e:
        print(f"{k}: ERR {e}")
    time.sleep(1.5)

