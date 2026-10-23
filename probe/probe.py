"""Фінал: забудовник для кожного ЖК (сторінка ЛУН) + ID ЖК на DIM.RIA."""
import os, re, json, sys, time, html as H
import requests
sys.stdout.reconfigure(line_buffering=True)
os.makedirs("probe_out", exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
                  "Accept-Language": "uk-UA,uk;q=0.9"})
raw = json.load(open(os.path.join(os.path.dirname(__file__), "zhk_raw.json")))
zhk = raw["zhk"]
for n, z in enumerate(zhk):
    slug = (z.get("link") or "").rsplit("/", 1)[-1]
    z["slug"] = slug
    if not slug:
        continue
    try:
        r = S.get(f"https://lun.ua/new/odesa/{slug}", timeout=30)
        m = re.search(r"Новобудови від забудовника\s+([^<\"]{2,80})", r.text)
        z["developer"] = H.unescape(m.group(1)).strip() if (r.ok and m) else None
    except Exception:
        z["developer"] = None
    if n % 20 == 0:
        print(n, z["name"], z.get("developer"))
    time.sleep(1.1)

ria = {}
for p in range(1, 8):
    r = S.get("https://dom.ria.com/uk/prodazha-kvartir/odessa-zhks/" + (f"?page={p}" if p > 1 else ""), timeout=30)
    found = re.findall(r'href="/uk/prodazha-kvartir/odessa-(?:zhk|zhr|kg|km|kb|zhm)-([a-z0-9\-]+)-(\d+)/"[^>]*>\s*([^<]+?)\s*<', r.text)
    before = len(ria)
    for slug, rid, name in found:
        ria[int(rid)] = {"ria_id": int(rid), "ria_slug": slug, "name": H.unescape(name)}
    print("ria page", p, len(found), "new", len(ria) - before)
    if len(ria) == before:
        break
    time.sleep(1.3)

json.dump({"zhk": zhk, "ria": list(ria.values()), "catalog": raw["catalog"]},
          open("probe_out/zhk_final.json", "w"), ensure_ascii=False, indent=1)
print("done", sum(1 for z in zhk if z.get("developer")), "with developer of", len(zhk), "; ria", len(ria))
