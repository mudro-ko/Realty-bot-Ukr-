import os, re, json, sys, time
import requests
OUT = "probe_out"; os.makedirs(OUT, exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
     "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7",
     "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}
URLS = {
 "olx_sale_odesa": "https://www.olx.ua/uk/nedvizhimost/kvartiry/prodazha-kvartir/odesa/",
 "olx_rent_odesa": "https://www.olx.ua/uk/nedvizhimost/kvartiry/dolgosrochnaya-arenda-kvartir/odesa/",
 "olx_sale_mykolaiv": "https://www.olx.ua/uk/nedvizhimost/kvartiry/prodazha-kvartir/nikolaev/",
 "olx_api_offers": "https://www.olx.ua/api/v1/offers/?offset=0&limit=3&category_id=1602&region_id=15",
 "dimria_sale_odesa": "https://dom.ria.com/uk/prodazha-kvartir/odessa/",
 "dimria_rent_odesa": "https://dom.ria.com/uk/arenda-kvartir/odessa/",
 "dimria_node_search": "https://dom.ria.com/node/searchEngine/v2/?category=1&realty_type=2&operation=1&state_id=12&city_id=12&limit=5&page=0",
 "dimria_dev_api": "https://developers.dom.ria.com/api/realty/search?category=1&operation_type=1&state_id=12&count=5",
 "lun_sale_odesa": "https://lun.ua/продажа-квартир-одесса",
 "lun_rent_odesa": "https://lun.ua/аренда-квартир-одесса",
 "lun_sale_mykolaiv": "https://lun.ua/продажа-квартир-николаев",
}
MARK = ["__PRERENDERED_STATE__","__NEXT_DATA__","__NUXT__","__INITIAL_STATE__","application/ld+json","window.__","data-cy=\"l-card\"","realty_id","searchEngine"]
for k,u in URLS.items():
    try:
        r = requests.get(u, headers=H, timeout=25)
        body = r.text
        ext = "json" if "json" in r.headers.get("content-type","") else "html"
        open(f"{OUT}/{k}.{ext}","w",encoding="utf-8").write(body)
        found = [m for m in MARK if m in body]
        print(f"{k}: {r.status_code} {len(body)}B ct={r.headers.get('content-type','')[:30]} final={r.url[:90]} markers={found}")
    except Exception as e:
        print(f"{k}: ERR {e}")
    time.sleep(1.5)
