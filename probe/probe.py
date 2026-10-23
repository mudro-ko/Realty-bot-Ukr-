import json, os, sys, tempfile, logging, time
sys.path.insert(0, os.getcwd())
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
from monitor.__main__ import run_search
from monitor.archive import Archive
from monitor.telegram import Telegram
os.makedirs("probe_out", exist_ok=True)
s = {"id": "t1", "name": "Одеса · Київський, Приморський · 1,2к · продаж · $50–85k", "city": "odesa", "deal": "sale",
     "districts": ["kyivskyi", "prymorskyi"], "rooms": [1, 2], "price_usd": {"min": 50000, "max": 85000},
     "condition": "renovated", "seller": "any", "keywords": ["море", "ремонт"], "sources": ["lun", "dimria"]}
variants = {"як_у_тебе": s,
            "без_ключових_слів": dict(s, id="t2", keywords=[]),
            "без_слів_і_стану": dict(s, id="t3", keywords=[], condition="any")}
arch = Archive(os.path.join(tempfile.mkdtemp(), "l.db"))
out = {}
for name, cfg in variants.items():
    st, ob, rep = {}, [], {}
    t0 = time.time()
    run_search(json.loads(json.dumps(cfg)), st, Telegram("", "", ob), {"usd_uah": 41.5}, rep, arch)
    r = rep[cfg["id"]]
    out[name] = {"sec": round(time.time() - t0), "sources": r["sources"], "archived": arch.count(cfg["id"]),
                 "msgs": [m.get("text", "")[:300] for m in ob if m["type"] == "text"],
                 "learned_micro": st.get("learned_micro"), "ria_learned": st.get("ria_learned")}
    # другий прогін — DIM.RIA вже з вивченими районами
    rep2 = {}
    run_search(json.loads(json.dumps(cfg)), st, Telegram("", "", []), {"usd_uah": 41.5}, rep2, arch)
    out[name]["second_run"] = rep2[cfg["id"]]["sources"]
    print(name, json.dumps(out[name], ensure_ascii=False)[:1500])
json.dump(out, open("probe_out/live.json", "w"), ensure_ascii=False, indent=1)
