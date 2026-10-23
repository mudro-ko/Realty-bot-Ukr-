"""Довідники: марки/моделі (з AUTO.RIA, кешуються), регіони, пальне, КПП, кузов, привід."""
import json
import os
import re
import time

from .common import get, log

API = "https://auto.ria.com/api"
CACHE_DIR = os.environ.get("AUTOMON_CACHE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache"))

POPULAR_BRANDS = ["Toyota", "Volkswagen", "BMW", "Mercedes-Benz", "Audi", "Skoda", "Renault", "Hyundai", "Kia",
                  "Nissan", "Ford", "Mazda", "Honda", "Mitsubishi", "Lexus", "Tesla", "Chevrolet", "Peugeot",
                  "Opel", "Volvo", "Subaru", "Land Rover", "Porsche", "BYD"]

# популярні моделі (назви як на AUTO.RIA) — ID знаходяться автоматично
POPULAR_MODELS = {
    "Toyota": ["Camry", "RAV4", "Corolla", "Land Cruiser", "Land Cruiser Prado", "Highlander", "Prius", "C-HR", "Auris", "Avensis"],
    "Volkswagen": ["Passat", "Golf", "Tiguan", "Touareg", "Jetta", "Polo", "Touran", "Arteon", "ID.4", "Caddy"],
    "BMW": ["X5", "3 Series", "5 Series", "X3", "X6", "X1", "7 Series", "4 Series", "i3", "X7"],
    "Mercedes-Benz": ["E-Class", "C-Class", "GLE-Class", "S-Class", "GLC-Class", "ML-Class", "GL-Class", "Sprinter", "Vito", "A-Class"],
    "Audi": ["A6", "Q7", "A4", "Q5", "A8", "Q8", "A5", "Q3", "A3", "e-tron"],
    "Skoda": ["Octavia", "Superb", "Kodiaq", "Fabia", "Karoq", "Rapid", "Kamiq", "Scala", "Yeti", "Enyaq"],
    "Renault": ["Megane", "Duster", "Logan", "Kangoo", "Sandero", "Arkana", "Captur", "Koleos", "Trafic", "Zoe"],
    "Hyundai": ["Tucson", "Santa FE", "Elantra", "Sonata", "Accent", "i30", "Kona", "Creta", "Ioniq 5", "ix35"],
    "Kia": ["Sportage", "Sorento", "Ceed", "Rio", "Optima", "K5", "Niro", "Soul", "EV6", "Stinger"],
    "Nissan": ["Qashqai", "X-Trail", "Leaf", "Rogue", "Juke", "Note", "Murano", "Pathfinder", "Sentra", "Altima"],
    "Ford": ["Focus", "Fusion", "Mondeo", "Escape", "Kuga", "Edge", "Explorer", "Mustang", "Transit", "Fiesta"],
    "Mazda": ["CX-5", "6", "3", "CX-30", "CX-9", "CX-3", "CX-60", "MX-5"],
    "Honda": ["CR-V", "Accord", "Civic", "HR-V", "Pilot", "Jazz", "e:NS1", "e:NP1"],
    "Mitsubishi": ["Outlander", "Lancer", "Pajero Sport", "ASX", "Eclipse Cross", "L 200", "Pajero Wagon"],
    "Lexus": ["RX", "NX", "ES", "GX", "LX", "IS", "UX", "LS"],
    "Tesla": ["Model 3", "Model Y", "Model S", "Model X"],
    "Chevrolet": ["Volt", "Bolt EV", "Cruze", "Malibu", "Equinox", "Aveo", "Lacetti", "Tahoe", "Camaro"],
    "Peugeot": ["308", "3008", "508", "2008", "5008", "Partner", "Expert", "208"],
    "Opel": ["Astra", "Insignia", "Vectra", "Zafira", "Mokka", "Combo", "Corsa", "Vivaro"],
    "Volvo": ["XC90", "XC60", "XC40", "S60", "S90", "V60", "V90", "XC70"],
    "Subaru": ["Forester", "Outback", "XV", "Legacy", "Impreza", "Crosstrek"],
    "Land Rover": ["Range Rover", "Range Rover Sport", "Range Rover Velar", "Range Rover Evoque", "Discovery", "Discovery Sport", "Defender"],
    "Porsche": ["Cayenne", "Macan", "Panamera", "Taycan", "911"],
    "BYD": ["Song Plus", "Han", "Seagull", "Yuan Plus", "Tang", "Seal", "Qin Plus", "Dolphin"],
}

# як марки пишуть у Telegram (кирилиця, скорочення)
BRAND_ALIASES = {
    "Mercedes-Benz": ["mercedes", "мерседес", "мерс", "benz", "mb "],
    "Volkswagen": ["vw ", "фольксваген", "volks"],
    "BMW": ["бмв"], "Toyota": ["тойота"], "Audi": ["ауди", "ауді"], "Skoda": ["шкода", "škoda"],
    "Renault": ["рено"], "Hyundai": ["хюндай", "хендай", "хундай", "hundai"], "Kia": ["киа", "кіа"],
    "Nissan": ["ниссан", "ніссан"], "Ford": ["форд"], "Mazda": ["мазда"], "Honda": ["хонда"],
    "Mitsubishi": ["мицубиси", "міцубісі", "mitsu"], "Lexus": ["лексус"], "Tesla": ["тесла"],
    "Chevrolet": ["шевроле", "chevy"], "Peugeot": ["пежо"], "Opel": ["опель"], "Volvo": ["вольво"],
    "Subaru": ["субару"], "Land Rover": ["range rover", "рендж", "ленд ровер", "landrover"], "Porsche": ["порше"],
    "BYD": ["бід", "біуайді"], "Jeep": ["джип"], "Lada": ["лада", "ваз"], "Infiniti": ["инфинити", "інфініті"],
}

REGIONS = {  # id AUTO.RIA → назва
    10: "Київ і область", 12: "Одеська", 11: "Дніпропетровська", 5: "Львівська", 7: "Харківська", 19: "Миколаївська",
    14: "Запорізька", 1: "Вінницька", 18: "Волинська", 2: "Житомирська", 22: "Закарпатська", 15: "Івано-Франківська",
    16: "Кіровоградська", 20: "Полтавська", 9: "Рівненська", 8: "Сумська", 3: "Тернопільська", 23: "Херсонська",
    4: "Хмельницька", 24: "Черкаська", 6: "Чернігівська", 25: "Чернівецька", 13: "Донецька",
}
REGION_ORDER = [10, 12, 11, 5, 7, 19, 14, 1, 18, 2, 22, 15, 16, 20, 9, 8, 3, 23, 4, 24, 6, 25, 13]
# як місто/область пишуть у постах → id області
CITY_REGION = {
    "київ": 10, "киев": 10, "kyiv": 10, "kiev": 10, "бровари": 10, "бориспіль": 10, "ірпінь": 10, "буча": 10, "біла церква": 10,
    "одес": 12, "odesa": 12, "odessa": 12, "дніпр": 11, "днепр": 11, "кривий ріг": 11, "кривой рог": 11, "львів": 5, "львов": 5, "lviv": 5,
    "харків": 7, "харьков": 7, "миколаїв": 19, "николаев": 19, "запоріж": 14, "запорож": 14, "вінниц": 1, "винниц": 1,
    "луцьк": 18, "луцк": 18, "житомир": 2, "ужгород": 22, "мукачев": 22, "івано-франків": 15, "ивано-франков": 15, "франківськ": 15,
    "кропивницьк": 16, "полтав": 20, "кременчу": 20, "рівне": 9, "ровно": 9, "суми": 8, "сумы": 8, "тернопіл": 3, "тернопол": 3,
    "херсон": 23, "хмельниц": 4, "черкас": 24, "чернігів": 6, "чернигов": 6, "чернівц": 25, "черновц": 25,
}

FUELS = {"petrol": ("⛽ Бензин", [1]), "diesel": ("🛢 Дизель", [2]), "lpg": ("🔥 Газ/бензин", [3, 4, 8]),
         "hybrid": ("🔋 Гібрид", [5, 11]), "phev": ("🔌 Плагін-гібрид", [10]), "electric": ("⚡ Електро", [6])}
FUEL_FROM_ID = {1: "petrol", 2: "diesel", 3: "lpg", 4: "lpg", 8: "lpg", 5: "hybrid", 11: "hybrid", 10: "phev", 6: "electric"}
GEARBOXES = {"auto": ("Автомат", [2, 3, 4, 5]), "manual": ("Механіка", [1])}
DRIVES = {"awd": ("Повний", [1]), "fwd": ("Передній", [2]), "rwd": ("Задній", [3])}
BODIES = {"sedan": ("Седан", [3]), "suv": ("Кросовер/позашляховик", [5]), "hatch": ("Хетчбек", [4]),
          "wagon": ("Універсал", [2]), "minivan": ("Мінівен", [8, 449]), "liftback": ("Ліфтбек", [307]),
          "coupe": ("Купе/кабріолет", [6, 7]), "pickup": ("Пікап", [9])}
BODY_FROM_NAME = {"седан": "sedan", "позашляховик": "suv", "кросовер": "suv", "хетчбек": "hatch", "універсал": "wagon",
                  "мінівен": "minivan", "мікровен": "minivan", "ліфтбек": "liftback", "купе": "coupe", "кабріолет": "coupe",
                  "пікап": "pickup", "фастбек": "liftback"}


def _cache(name, ttl, fetch):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    try:
        data = fetch()
        json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        return data
    except Exception as e:
        log.warning("Довідник %s: %s", name, e)
        if os.path.exists(path):
            return json.load(open(path, encoding="utf-8"))
        return []


def brands():
    """[{'name','value'}] — усі марки легкових."""
    return _cache("marks.json", 14 * 86400, lambda: get(f"{API}/categories/1/marks").json())


def brand_by_name(name):
    n = norm(name)
    for b in brands():
        if norm(b["name"]) == n:
            return b
    return None


def brand_by_id(bid):
    return next((b for b in brands() if b["value"] == bid), None)


def models(brand_id):
    return _cache(f"models_{brand_id}.json", 14 * 86400,
                  lambda: get(f"{API}/categories/1/marks/{brand_id}/models").json())


def model_by_id(brand_id, mid):
    return next((m for m in models(brand_id) if m["value"] == mid), None)


def popular_models(brand_name, brand_id):
    ms = models(brand_id)
    out = []
    for want in POPULAR_MODELS.get(brand_name, []):
        m = next((x for x in ms if norm(x["name"]) == norm(want)), None) or \
            next((x for x in ms if norm(x["name"]).startswith(norm(want))), None)
        if m and m not in out:
            out.append(m)
    return out


def search_models(brand_id, text, limit=24):
    q = norm(text)
    ms = models(brand_id)
    exact = [m for m in ms if norm(m["name"]) == q]
    starts = [m for m in ms if norm(m["name"]).startswith(q) and m not in exact]
    contains = [m for m in ms if q in norm(m["name"]) and m not in exact and m not in starts]
    return (exact + starts + contains)[:limit]


def search_brands(text, limit=24):
    q = norm(text)
    out = []
    for b in brands():
        n = norm(b["name"])
        if n.startswith(q) or q in n:
            out.append(b)
    for canon, al in BRAND_ALIASES.items():
        if any(q in norm(a) or norm(a).strip() in q for a in al):
            b = brand_by_name(canon)
            if b and b not in out:
                out.insert(0, b)
    return out[:limit]


def norm(s):
    s = (s or "").lower().replace("ё", "е").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip()
