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
    ("a_rep_sort", "https://lun.ua/sale/mykolaiv/flats-lisky?room_count=3&room_count=4&sort=insert_time", lun_info),
    ("b_sort_comma", "https://lun.ua/sale/mykolaiv/flats-lisky?sort=insert_time&room_count=3,4", lun_info),
    ("c_sort_rep", "https://lun.ua/sale/mykolaiv/flats-lisky?sort=insert_time&room_count=3&room_count=4", lun_info),
    ("d_comma_only", "https://lun.ua/sale/mykolaiv/flats-lisky?room_count=3,4", lun_info),
    ("e_sort_only", "https://lun.ua/sale/mykolaiv/flats-lisky?sort=insert_time", lun_info),
    ("f_ods_rep_sort", "https://lun.ua/sale/odesa/flats?room_count=1&room_count=2&sort=insert_time", lun_info),
    ("g_ods_comma_sort", "https://lun.ua/sale/odesa/flats?room_count=1,2&sort=insert_time", lun_info),
    ("h_ods_single_sort", "https://lun.ua/sale/odesa/flats?room_count=2&sort=insert_time", lun_info),
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

