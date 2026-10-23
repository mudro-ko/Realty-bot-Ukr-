import os, re, json, sys, time, gzip
import requests
sys.stdout.reconfigure(line_buffering=True)
os.makedirs("probe_out", exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept-Language": "uk-UA,uk;q=0.9"})
URLS = {
 "lun_nb": "https://lun.ua/uk/новобудови-одеси",
 "lun_nb_p2": "https://lun.ua/uk/новобудови-одеси?page=2",
 "ria_nb": "https://dom.ria.com/uk/novostroyki/odessa/",
 "ria_nb_list": "https://dom.ria.com/uk/novostroyki/odessa/?page=2",
}
for k, u in URLS.items():
    try:
        r = S.get(u, timeout=30)
        with gzip.open(f"probe_out/{k}.html.gz", "wt", encoding="utf-8") as f: f.write(r.text)
        print(k, r.status_code, len(r.text), r.url[:100], "next_f" in r.text, "__INITIAL_STATE__" in r.text)
    except Exception as e:
        print(k, "ERR", e)
    time.sleep(1.5)
# LUN listing card: geoEntities / houseData for a new-build listing
r = S.get("https://lun.ua/sale/odesa/flats-prymorskyi-district?currency=USD&price_min=150000&sort=insert_time", timeout=30)
with gzip.open("probe_out/lun_prim_exp.html.gz", "wt", encoding="utf-8") as f: f.write(r.text)
print("lun_prim_exp", r.status_code, len(r.text))
