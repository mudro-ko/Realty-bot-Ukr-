import os, re, json, sys, time, requests
sys.stdout.reconfigure(line_buffering=True); os.makedirs("probe_out", exist_ok=True)
S = requests.Session(); S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept-Language": "uk-UA,uk;q=0.9"})
FR = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
def info(t):
    try:
        fl = "".join(json.loads('"' + m + '"') for m in FR.findall(t)); i = fl.find('{"realties":{"cards":')
        o, _ = json.JSONDecoder().raw_decode(fl, i); cs = o["realties"]["cards"]
        zk = {g["name"] for c in cs for g in (c.get("geoEntities") or []) if g.get("type") == "residential_complex"}
        return f"total={o['realties']['totalRealtiesCount']} n={len(cs)} rooms={sorted(set(c.get('roomCount') or 0 for c in cs))} prices={[c.get('price') for c in cs[:5]]} zhk={list(zk)[:3]}"
    except Exception as e:
        return f"parse-err {e}"
for k, u in [("sale", "https://lun.ua/sale/odesa/korfu"), ("sale_f", "https://lun.ua/sale/odesa/korfu?room_count=1&room_count=2&currency=USD&price_min=50000&price_max=150000&area_min=40&area_max=80&sort=insert_time"),
             ("rent", "https://lun.ua/rent/odesa/korfu"), ("rent_f", "https://lun.ua/rent/odesa/korfu?room_count=1&sort=insert_time"),
             ("perl", "https://lun.ua/sale/odesa/dvadtsiat-chetverta-perlyna?sort=insert_time&page=2")]:
    r = S.get(u, timeout=30); print(k, r.status_code, r.url[:80], info(r.text)); time.sleep(1.5)
R = "https://dom.ria.com/node/searchEngine/v2/?category=1&realty_type=2&operation=1&state_id=12&city_id=12&wo_dupl=1&limit=3&page=0"
for k, q in [("ria_nb_2", "&newbuild_id%5B0%5D=5391&newbuild_id%5B1%5D=5365"), ("ria_nb_rent", "&newbuild_id%5B0%5D=5391").replace("operation=1","operation=3")]:
    print(k, S.get((R if k != "ria_nb_rent" else R.replace("operation=1", "operation=3")) + ("&newbuild_id%5B0%5D=5391&newbuild_id%5B1%5D=5365" if k == "ria_nb_2" else "&newbuild_id%5B0%5D=5391"), timeout=30).text[:110]); time.sleep(1)
