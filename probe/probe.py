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
    ("lun_myk_lisky", "https://lun.ua/sale/mykolaiv/flats-lisky", lun_info),
    ("lun_myk_rooms_a", "https://lun.ua/sale/mykolaiv/flats-lisky?roomCount=3&roomCount=4", lun_info),
    ("lun_myk_rooms_b", "https://lun.ua/sale/mykolaiv/flats-lisky?room_count=3&room_count=4", lun_info),
    ("lun_ods_sort_a", "https://lun.ua/sale/odesa/flats?sort=insert_time", lun_info),
    ("lun_ods_sort_b", "https://lun.ua/sale/odesa/flats?sortBy=insertTime", lun_info),
    ("lun_search_raw", "https://lun.ua/search?geo_id=10016589&section_id=1&language=uk&sort=insert_time", lun_info),
    ("lun_ods_page2", "https://lun.ua/sale/odesa/flats?page=2", lun_info),
    ("lun_ods_price", "https://lun.ua/sale/odesa/flats?price_max=50000&currency=usd", lun_info),
    ("lun_rent_ods", "https://lun.ua/rent/odesa/flats", lun_info),
    ("lun_daily_ods", "https://lun.ua/daily/odesa/flats", lun_info),
    ("ria_myk", "https://dom.ria.com/uk/prodazha-kvartir/nikolaev/", ria_info),
    ("ria_ods_sort", "https://dom.ria.com/uk/prodazha-kvartir/odessa/?sort=created_at", ria_info),
    ("ria_ods_bezrem", "https://dom.ria.com/uk/prodazha-kvartir/odessa-bez-remonta/", ria_info),
    ("ria_ods_3k", "https://dom.ria.com/uk/prodazha-kvartir/odessa-3k/", ria_info),
    ("ria_ods_daily", "https://dom.ria.com/uk/posutochno-kvartir/odessa/", ria_info),
    ("ria_se_sort", "https://dom.ria.com/node/searchEngine/v2/?category=1&realty_type=2&operation=1&state_id=12&city_id=12&limit=5&page=0&sort=created_at", None),
    ("ria_se_rooms", "https://dom.ria.com/node/searchEngine/v2/?category=1&realty_type=2&operation=1&state_id=12&city_id=12&limit=5&page=0&characteristic%5B209%5D%5Bfrom%5D=3&characteristic%5B209%5D%5Bto%5D=4", None),
    ("ria_se_price", "https://dom.ria.com/node/searchEngine/v2/?category=1&realty_type=2&operation=1&state_id=12&city_id=12&limit=5&page=0&characteristic%5B234%5D%5Bto%5D=30000&characteristic%5B242%5D=239", None),
    ("ria_data", "https://dom.ria.com/realty/data/34962517?lang_id=4", None),
]

for k, u, fn in TESTS:
    try:
        r = S.get(u, timeout=25)
        save(k, r)
        info = fn(r.text) if fn else r.text[:400].replace("\n", " ")
        print(f"{k}: {r.status_code} final={r.url[:110]} :: {info}")
    except Exception as e:
        print(f"{k}: ERR {e}")
    time.sleep(1.5)

# LUN front-end bundles: which query-param names the filters use
try:
    h = S.get("https://lun.ua/sale/odesa/flats", timeout=25).text
    chunks = sorted(set(re.findall(r'https://lun\.ua/_next/static/chunks/[A-Za-z0-9_\-]+\.js', h)))
    found = {}
    for cu in chunks:
        js = S.get(cu, timeout=25).text
        for w in re.findall(r'"((?:room_count|roomCount|price_max|priceMax|price_min|sort|sort_by|repair|insert_time|is_owner|currency|area_total_min|area_total_max)[a-z_]*)"', js):
            found[w] = found.get(w, 0) + 1
        for m in re.finditer(r'.{0,120}insert_time.{0,160}', js):
            print("JSCTX", cu[-20:], m.group(0)[:280])
        time.sleep(0.3)
    print("LUN_JS_KEYS", json.dumps(found))
except Exception as e:
    print("lun js ERR", e)
