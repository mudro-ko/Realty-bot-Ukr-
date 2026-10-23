import os, json, sys, time, requests
sys.stdout.reconfigure(line_buffering=True)
os.makedirs("probe_out", exist_ok=True)
S = requests.Session(); S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"})
marks = S.get("https://auto.ria.com/api/categories/1/marks", timeout=30).json()
want = ["Toyota","Volkswagen","BMW","Mercedes-Benz","Audi","Skoda","Renault","Hyundai","Kia","Nissan","Ford","Mazda","Honda","Mitsubishi","Lexus","Tesla","Chevrolet","Peugeot","Opel","Volvo","Subaru","Land Rover","Porsche","BYD","Jeep","Infiniti","Citroen","Suzuki","Dacia","Fiat","SEAT","Geely","Chery","Daewoo","ВАЗ / Lada","ЗАЗ","Jaguar","MINI","Smart","Cupra","Genesis","Zeekr","Dodge","Chrysler","Cadillac","Lincoln","GMC","Acura","Alfa Romeo","Maserati","Bentley","Ram"]
by = {m["name"]: m["value"] for m in marks}
models = {}
for n in want:
    bid = by.get(n)
    if not bid:
        print("no brand", n); continue
    models[str(bid)] = S.get(f"https://auto.ria.com/api/categories/1/marks/{bid}/models", timeout=30).json()
    print(n, bid, len(models[str(bid)])); time.sleep(0.6)
json.dump({"marks": marks, "models": models}, open("probe_out/catalog_seed.json", "w"), ensure_ascii=False)
