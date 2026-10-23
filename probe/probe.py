import os, re, json, sys, time, gzip
import requests
sys.stdout.reconfigure(line_buffering=True)
os.makedirs("probe_out", exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept-Language": "uk-UA,uk;q=0.9"})
def save(k, r):
    with gzip.open(f"probe_out/{k}.gz", "wt", encoding="utf-8") as f: f.write(r.text)
T = [
 ("ar_home", "https://auto.ria.com/uk/"),
 ("ar_search_html", "https://auto.ria.com/uk/search/?indexName=auto&categories.main.id=1&brand.id[0]=79&model.id[0]=2104&year[0].gte=2016&price.currency=1&size=20&page=0"),
 ("ar_legacy_search", "https://auto.ria.com/uk/legkovie/toyota/camry/"),
 ("ar_bu_search", "https://auto.ria.com/bu/searchPage/v2/view/auto/38000000/?lang_id=4"),
 ("ar_api_search", "https://auto.ria.com/api/search/auto?indexName=auto&categories.main.id=1&brand.id[0]=79&model.id[0]=2104&size=5"),
 ("ar_search_json", "https://auto.ria.com/search/?indexName=auto&categories.main.id=1&brand.id[0]=79&size=5&json=1"),
 ("ar_demo_search", "https://auto.ria.com/demo/bu/searchPage/search/auto?categories.main.id=1&brand.id[0]=79"),
 ("ar_marks", "https://auto.ria.com/api/categories/1/marks"),
 ("ar_marks2", "https://auto.ria.com/demo/api/categories/1/marks/_active"),
 ("dev_api", "https://developers.ria.com/auto/categories/1/marks"),
 ("tg_s", "https://t.me/s/autoria_news"),
]
for k, u in T:
    try:
        r = S.get(u, timeout=30); save(k, r)
        t = r.text
        print(f"{k}: {r.status_code} {len(t)}B ct={r.headers.get('content-type','')[:30]} final={r.url[:120]} :: {t[:200].replace(chr(10),' ') if 'json' in r.headers.get('content-type','') else ''} markers={[m for m in ['__INITIAL_STATE__','window.__','__NEXT_DATA__','searchResults','ticket','auto_id','tgme_widget_message'] if m in t]}")
    except Exception as e:
        print(k, "ERR", e)
    time.sleep(1.5)
