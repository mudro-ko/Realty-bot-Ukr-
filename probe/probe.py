"""Збір каталогу ЖК Одеси: каталог новобудов ЛУН + ЖК з оголошень вторинного ринку."""
import os, re, json, sys, time, statistics, collections
import requests
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
sys.stdout.reconfigure(line_buffering=True)
from parse_catalog import parse_catalog
from monitor.sources import lun
os.makedirs("probe_out", exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept-Language": "uk-UA,uk;q=0.9"})
def get(u):
    r = S.get(u, timeout=30); time.sleep(1.3); return r

catalog = []
for p in range(1, 10):
    r = get("https://lun.ua/uk/новобудови-одеси" + (f"?page={p}" if p > 1 else ""))
    items = parse_catalog(r.text)
    print("catalog page", p, len(items))
    if not items: break
    catalog += items
print("catalog total", len(catalog))

zhk = {}
def add(card):
    ents = card.get("geoEntities") or []
    rc = [g for g in ents if g.get("type") == "residential_complex"]
    if not rc: return
    g = rc[0]
    dist = next((e["name"] for e in ents if e.get("type") == "district"), None)
    micro = next((e["name"] for e in ents if e.get("type") == "microdistrict"), None)
    z = zhk.setdefault(g["geoId"], {"lun_geo_id": g["geoId"], "name": g["name"], "link": (g.get("realtyGeoUrl") or {}).get("link"),
                                   "address": g.get("address"), "districts": collections.Counter(), "micro": collections.Counter(), "ppm": [], "n": 0})
    z["n"] += 1
    if dist: z["districts"][dist] += 1
    if micro: z["micro"][micro] += 1
    if card.get("currency") == "usd" and card.get("price") and card.get("areaTotal"):
        z["ppm"].append(card["price"] / card["areaTotal"])

for slug in ["prymorskyi-district", "kyivskyi-district", "khadzhybeiskyi-district", "peresypskyi-district"]:
    for sort in ["", "&sort=insert_time"]:
        for p in range(1, 16):
            r = get(f"https://lun.ua/sale/odesa/flats-{slug}?page={p}{sort}")
            cards, total = lun.parse_page(r.text)
            for c in cards: add(c)
            if not cards: break
        print(slug, sort, "zhk so far", len(zhk))

# DIM.RIA: чи працює фільтр newbuild_id (ЖК Чотири сезони = 5391)
R = "https://dom.ria.com/node/searchEngine/v2/?category=1&realty_type=2&operation=1&state_id=12&city_id=12&wo_dupl=1&limit=3&page=0"
for k, q in [("base", ""), ("nb", "&newbuild_id%5B0%5D=5391"), ("nb2", "&newbuild_id=5391"), ("nb3", "&user_newbuild_id=5391")]:
    print("ria", k, get(R + q).text[:120])

out = []
for z in zhk.values():
    out.append({"lun_geo_id": z["lun_geo_id"], "name": z["name"], "link": z["link"], "address": z["address"],
                "district": z["districts"].most_common(1)[0][0] if z["districts"] else None,
                "micro": z["micro"].most_common(1)[0][0] if z["micro"] else None,
                "listings_seen": z["n"], "median_ppm": round(statistics.median(z["ppm"])) if z["ppm"] else None})
json.dump({"catalog": catalog, "zhk": out}, open("probe_out/zhk.json", "w"), ensure_ascii=False, indent=1)
print("ZHK", len(out))
