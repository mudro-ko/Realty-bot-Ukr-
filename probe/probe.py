import os, re, json, sys, time, gzip
import requests
sys.stdout.reconfigure(line_buffering=True)
os.makedirs("probe_out", exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept-Language": "uk-UA,uk;q=0.9"})
CH = ["autobazarkyiv1", "KievavtoSale", "kievautotrade", "PerekupiKievLive", "autotraderkiev", "bazaravtoukr", "imperiya_auto_1",
      "prodatu_avto", "ua_autobazar", "autohub_24_autonext", "tachkavplus", "autohub_odesa", "avtorynok_ukraina", "vidkrytyi_cars", "kievavto2"]
for ch in CH:
    r = S.get(f"https://t.me/s/{ch}", timeout=30)
    n = r.text.count('class="tgme_widget_message_text')
    with gzip.open(f"probe_out/tg_{ch}.gz", "wt", encoding="utf-8") as f: f.write(r.text)
    print("tg", ch, r.status_code, r.url[:50], "posts:", n); time.sleep(1.2)
for k, u in [("fuel", "https://auto.ria.com/api/fuels"), ("fuel2", "https://auto.ria.com/api/categories/1/fuels"), ("drive", "https://auto.ria.com/api/categories/1/driverTypes"),
             ("cities10", "https://auto.ria.com/api/states/10/cities"), ("cities12", "https://auto.ria.com/api/states/12/cities")]:
    r = S.get(u, timeout=30); print("cat", k, r.status_code, r.text[:300].replace("\n"," ")); time.sleep(1)
A = "https://auto.ria.com/api/search/auto?indexName=auto&categories.main.id=1&size=10&page=0&marka_id[0]=79"
for k, q in [("fuel1", "&type[0]=1"), ("fuel2", "&type[0]=2"), ("fuel4", "&type[0]=4"), ("fuel5", "&type[0]=5"), ("fuel6", "&type[0]=6"), ("drive1", "&drive_id[0]=1"), ("body5", "&body_id[0]=5"),
             ("city", "&state[0]=12&city[0]=12"), ("eng", "&engine.gte=2&engine.lte=3"), ("eng2", "&engineVolumeFrom=2&engineVolumeTo=3"), ("race2", "&raceInt[0]=0&raceInt[1]=50"), ("race3", "&race.gte=0&race.lte=50"),
             ("damage_no", "&damage=0"), ("damage_ex", "&damage=2"), ("custom_ok", "&custom=1"), ("sort7p1", "&order_by=7&page=1")]:
    r = S.get(A + q, timeout=30); print("search", k, r.json()["result"]["search_result"].get("count")); time.sleep(1)
