"""Каталог ЖК (зараз — Одеса): пошук за районом, забудовником, назвою."""
import json
import os
import re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE = {}


def catalog(city: str) -> list:
    if city not in _CACHE:
        path = os.path.join(HERE, f"zhk_{city}.json")
        _CACHE[city] = json.load(open(path, encoding="utf-8"))["complexes"] if os.path.exists(path) else []
    return _CACHE[city]


def has_catalog(city: str) -> bool:
    return bool(catalog(city))


def by_key(city: str, key: str):
    return next((z for z in catalog(city) if z["key"] == key), None)


def index_of(city: str, key: str) -> int:
    for i, z in enumerate(catalog(city)):
        if z["key"] == key:
            return i
    return -1


def in_district(city: str, district: str) -> list:
    return [z for z in catalog(city) if z.get("district") == district]


def developers(city: str) -> list:
    """[(забудовник, к-сть ЖК)] — найбільші першими."""
    c = Counter(z["developer"] for z in catalog(city) if z.get("developer"))
    return c.most_common()


def of_developer(city: str, dev: str) -> list:
    return [z for z in catalog(city) if z.get("developer") == dev]


def norm(s: str) -> str:
    s = (s or "").lower().replace("’", "'").replace("ʼ", "'").replace("ё", "е")
    s = re.sub(r"^(жк|ж/к|кб|кг|км|жр|клубний будинок|клубный дом|апарт-комплекс|апарт комплекс|"
               r"житловий квартал|житловий комплекс|жилой комплекс)\s+", "", s)
    s = re.sub(r"[«»\"'.,()]", " ", s)
    return " ".join(s.split())


# числівники в назвах типу «Двадцять четверта Перлина» ↔ «24 Перлина»
_NUM = {"перша": 1, "друга": 2, "третя": 3, "четверта": 4, "п'ята": 5, "шоста": 6, "сьома": 7, "восьма": 8,
        "дев'ята": 9, "десята": 10, "одинадцята": 11, "дванадцята": 12, "тринадцята": 13, "чотирнадцята": 14,
        "п'ятнадцята": 15, "шістнадцята": 16, "сімнадцята": 17, "вісімнадцята": 18, "дев'ятнадцята": 19,
        "двадцята": 20, "тридцята": 30, "сорокова": 40, "п'ятдесята": 50, "шістдесята": 60, "сімдесята": 70}
_TENS = {"двадцять": 20, "тридцять": 30, "сорок": 40, "п'ятдесят": 50, "шістдесят": 60, "сімдесят": 70}


def canon(s: str) -> str:
    """Нормалізована назва з числівниками-цифрами: «двадцять четверта перлина» → «24 перлина»."""
    words = norm(s).replace("перлина", "перлина").split()
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w in _TENS and i + 1 < len(words) and words[i + 1] in _NUM and _NUM[words[i + 1]] < 10:
            out.append(str(_TENS[w] + _NUM[words[i + 1]]))
            i += 2
            continue
        if w in _NUM:
            out.append(str(_NUM[w]))
        else:
            out.append({"жемчужина": "перлина"}.get(w, w))
        i += 1
    return " ".join(out)


def search(city: str, text: str, limit: int = 20) -> list:
    q = canon(text)
    if not q:
        return []
    toks = q.split()
    hits = []
    for z in catalog(city):
        n = canon(z["name"])
        if all(t in n for t in toks):
            hits.append(z)
    return hits[:limit]


def same_complex(a: str, b: str) -> bool:
    """Чи це той самий ЖК (назви з різних сайтів)."""
    ca, cb = canon(a), canon(b)
    if not ca or not cb:
        return False
    return ca == cb or (len(ca) >= 5 and len(cb) >= 5 and (ca in cb or cb in ca))
