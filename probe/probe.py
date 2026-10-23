import os, re, json, sys, time, gzip
import requests
sys.stdout.reconfigure(line_buffering=True)
os.makedirs("probe_out", exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept-Language": "uk-UA,uk;q=0.9"})
def save(k, t):
    with gzip.open(f"probe_out/{k}.gz", "wt", encoding="utf-8") as f: f.write(t)
A = "https://auto.ria.com/api/search/auto?indexName=auto&categories.main.id=1&size=10&page=0"
for k, q in [("base", ""), ("marka", "&marka_id[0]=79&model_id[0]=2104"), ("marka_year", "&marka_id[0]=79&model_id[0]=2104&s_yers[0]=2016&po_yers[0]=2020"),
             ("price", "&marka_id[0]=79&price_ot=10000&price_do=20000&currency=1"), ("sort_new", "&marka_id[0]=79&order_by=7"),
             ("state", "&marka_id[0]=79&state[0]=12"), ("fuel", "&marka_id[0]=79&type[0]=6"), ("gear", "&marka_id[0]=79&gearbox[0]=2"),
             ("mileage", "&marka_id[0]=79&raceInt[0]=0&raceInt[1]=100"), ("custom", "&marka_id[0]=79&custom=1&damage=1")]:
    r = S.get(A + q, timeout=30); d = r.json()
    sr = d["result"]["search_result"]
    print("search", k, r.status_code, sr.get("count"), sr.get("ids", [])[:4]); save("s_" + k, r.text); time.sleep(1.2)
ids = S.get(A + "&marka_id[0]=79&order_by=7", timeout=30).json()["result"]["search_result"]["ids"][:2]
for rid in ids:
    for k, u in [("v2view", f"https://auto.ria.com/demo/bu/searchPage/v2/view/auto/{rid}/?lang_id=4"),
                 ("bu_v2", f"https://auto.ria.com/bu/searchPage/v2/view/auto/{rid}/?lang_id=4"),
                 ("api_info", f"https://auto.ria.com/api/auto/{rid}"),
                 ("demo_info", f"https://auto.ria.com/demo/bu/finalPage/auto/{rid}"),
                 ("html", f"https://auto.ria.com/uk/auto___{rid}.html")]:
        r = S.get(u, timeout=30); save(f"d_{k}_{rid}", r.text)
        print("detail", k, rid, r.status_code, len(r.text), r.headers.get("content-type","")[:25], r.url[:100], r.text[:160].replace("\n"," ") if "json" in r.headers.get("content-type","") else "")
        time.sleep(1.2)
for k, u in [("models", "https://auto.ria.com/api/categories/1/marks/79/models"), ("models2", "https://auto.ria.com/demo/api/categories/1/marks/79/models/_active"),
             ("states", "https://auto.ria.com/api/states"), ("fuels", "https://auto.ria.com/api/type"), ("gear", "https://auto.ria.com/api/categories/1/gearboxes"),
             ("bodies", "https://auto.ria.com/api/categories/1/bodystyles")]:
    r = S.get(u, timeout=30); save("c_" + k, r.text); print("cat", k, r.status_code, r.text[:200].replace("\n", " ")); time.sleep(1)
for ch in ["avtobazar_ua", "auto_ukraine_sale", "avtorynok_ua", "autoria_ua", "prodazha_avto_ua", "avto_ua_prodazh", "carsua", "kiev_auto_sale", "odessa_auto", "avtorinok_odessa"]:
    r = S.get(f"https://t.me/s/{ch}", timeout=30)
    n = r.text.count("tgme_widget_message_text")
    print("tg", ch, r.status_code, r.url[:60], "posts:", n); save("tg_" + ch, r.text); time.sleep(1)
