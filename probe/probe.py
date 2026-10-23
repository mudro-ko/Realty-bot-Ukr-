import os, sys, time, gzip, requests
os.makedirs("probe_out", exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept-Language": "uk-UA,uk;q=0.9"})
for k, u in [("korfu", "https://lun.ua/new/odesa/korfu"), ("rodos", "https://lun.ua/new/odesa/rodos"),
             ("perlyna24", "https://lun.ua/new/odesa/dvadtsiat-chetverta-perlyna"), ("chotyry", "https://lun.ua/new/odesa/chotyry-sezony"),
             ("ria_zhks", "https://dom.ria.com/uk/prodazha-kvartir/odessa-zhks/"), ("ria_zhks2", "https://dom.ria.com/uk/prodazha-kvartir/odessa-zhks/?page=2")]:
    r = S.get(u, timeout=30)
    with gzip.open(f"probe_out/{k}.html.gz", "wt", encoding="utf-8") as f: f.write(r.text)
    print(k, r.status_code, len(r.text), r.url[:90]); time.sleep(1.5)
