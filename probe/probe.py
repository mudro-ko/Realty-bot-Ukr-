import json, os, sys, tempfile, logging, time, collections
sys.path.insert(0, os.getcwd())
logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stdout)
from monitor.__main__ import run_search
from monitor.archive import Archive
from monitor.market import Market
from monitor.telegram import Telegram
os.makedirs("probe_out", exist_ok=True)
T = tempfile.mkdtemp()
arch, mk = Archive(os.path.join(T, "a.db")), Market(os.path.join(T, "m.db"))
S = {
 "districts": {"id": "d", "name": "Київський+Приморський 1-2к $50-85k", "city": "odesa", "deal": "sale",
               "districts": ["kyivskyi", "prymorskyi"], "rooms": [1, 2], "price_usd": {"min": 50000, "max": 85000},
               "area": {"min": 30, "max": 55}, "condition": "any", "sources": ["lun", "dimria"], "below_market": 10},
 "zhk": {"id": "z", "name": "ЖК Перлини+Корфу+Родос", "city": "odesa", "deal": "sale",
         "zhk": ["dvadtsiat-chetverta-perlyna", "korfu", "rodos", "atmosfera", "chotyry-sezony"], "rooms": [1, 2],
         "price_usd": {"min": 40000, "max": 120000}, "sources": ["lun", "dimria"], "below_market": 0},
 "rent": {"id": "r", "name": "Оренда Київський 1к до 12k", "city": "odesa", "deal": "rent", "districts": ["kyivskyi"],
          "rooms": [1], "price_uah": {"max": 12000}, "sources": ["lun"], "below_market": 10},
}
out = {}
for k, cfg in S.items():
    st, ob, rep = {}, [], {}
    t0 = time.time()
    run_search(json.loads(json.dumps(cfg)), st, Telegram("", "", ob), {"usd_uah": 41.5}, rep, arch, mk)
    r = rep[cfg["id"]]
    rows = arch.rows(cfg["id"])
    labels = collections.Counter((x.get("market_label") or "").split(",")[0] for x in rows)
    out[k] = {"sec": round(time.time() - t0), "market_scan": r.get("market_scan"), "market_filter": r.get("market_filter"),
              "sources": r["sources"], "archived": len(rows),
              "pcts": sorted(x.get("market_pct") for x in rows if x.get("market_pct") is not None)[:15],
              "labels": labels.most_common(8),
              "sample": [m["caption"][:500] for m in ob if m["type"] == "listing"][:2]}
    print(k, json.dumps(out[k], ensure_ascii=False)[:2500]); print()
print("market rows:", mk.count("odesa", "sale"), mk.count("odesa", "rent"))
json.dump(out, open("probe_out/market.json", "w"), ensure_ascii=False, indent=1)
