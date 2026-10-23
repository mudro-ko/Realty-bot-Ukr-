"""Ринкові орієнтири: медіана $/м² (продаж) або ціни (оренда) серед схожих оголошень.

Вибірка — усі оголошення, які бот бачить на сайтах (до фільтрів користувача), плюс раз на добу
окрема «ринкова» вибірка району/ЖК без фільтрів ціни. Порівнюємо з найточнішою групою, де досить даних:
ЖК+кімнати → ЖК → мікрорайон+кімнати → мікрорайон → район+кімнати → місто+кімнати.
"""
import os
import re
import sqlite3
import statistics
import threading
from datetime import datetime, timedelta, timezone

KYIV = timezone(timedelta(hours=3))
WINDOW_DAYS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS market (
    key TEXT PRIMARY KEY, city TEXT, deal TEXT,
    complex TEXT, micro TEXT, district TEXT, rooms INTEGER,
    value REAL, seen TEXT, kind TEXT
);
CREATE INDEX IF NOT EXISTS m_cx ON market(city, deal, complex);
CREATE INDEX IF NOT EXISTS m_mi ON market(city, deal, micro);
CREATE INDEX IF NOT EXISTS m_di ON market(city, deal, district);
"""

# (поле групи, з кімнатами?, той самий тип (стан+новобудова/вторинка)?, мінімум оголошень, підпис)
LEVELS = [
    ("complex", True, True, 5, "ЖК {g}, {r}к{k}"),
    ("complex", False, True, 6, "ЖК {g}{k}"),
    ("complex", True, False, 6, "ЖК {g}, {r}к"),
    ("complex", False, False, 8, "ЖК {g}"),
    ("micro", True, True, 8, "{g}, {r}к{k}"),
    ("micro", False, True, 10, "{g}{k}"),
    ("district", True, True, 10, "{g} р-н, {r}к{k}"),
    ("district", False, True, 15, "{g} р-н{k}"),
    ("city", True, True, 15, "місто, {r}к{k}"),
    ("micro", True, False, 12, "{g}, {r}к"),
    ("district", True, False, 15, "{g} р-н, {r}к"),
]
KIND_LABEL = {"work:nb": "під ремонт, новобуд.", "ok:nb": "новобуд.", "work:old": "під ремонт, вторинка",
              "ok:old": "вторинка"}


def kind_of(l):
    """Стан (під ремонт / нормальний) + новобудова в ЖК чи вторинка — порівнюємо однакове з однаковим."""
    from .filters import condition_of
    c = condition_of(l)
    cond = "work" if c in ("needs_repair", "cosmetic") else "ok"
    seg = "nb" if (l.complex or "").strip() else "old"
    return f"{cond}:{seg}"


def _n(s):
    s = (s or "").lower().replace("ё", "е").replace("’", "'")
    s = re.sub(r"^(жк|ж/к|кб|км|клубний будинок|апарт-комплекс)\s+", "", s)
    return " ".join(re.sub(r"[«»\"'.,()]", " ", s).split())


def rooms_bucket(r):
    if not r:
        return None
    return min(int(r), 4)


def value_of(l, deal, usd_uah):
    """Продаж: $/м²; оренда: ціна в ₴ на місяць."""
    if deal == "sale":
        if l.price_usd and l.area and l.area >= 12:
            v = l.price_usd / l.area
            return v if 150 <= v <= 15000 else None
        return None
    if deal == "rent":
        v = l.price_uah or (l.price_usd * usd_uah if l.price_usd else None)
        return v if v and 1500 <= v <= 400000 else None
    return None


class Market:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self._c() as c:
            c.executescript(SCHEMA)
            if "kind" not in {r[1] for r in c.execute("PRAGMA table_info(market)")}:
                c.execute("ALTER TABLE market ADD COLUMN kind TEXT")

    def _c(self):
        return sqlite3.connect(self.path, timeout=30)

    def add(self, listings, city, deal, usd_uah=41.5):
        now = datetime.now(KYIV).strftime("%Y-%m-%d")
        rows = []
        for l in listings:
            v = value_of(l, deal, usd_uah)
            if v is None:
                continue
            rows.append((l.key, city, deal, _n(l.complex) or None, _n(l.district_name) or None,
                         _n(l.admin_district) or None, rooms_bucket(l.rooms), v, now, kind_of(l)))
        if not rows:
            return 0
        with self.lock, self._c() as c:
            c.executemany("""INSERT INTO market (key, city, deal, complex, micro, district, rooms, value, seen, kind)
                             VALUES (?,?,?,?,?,?,?,?,?,?)
                             ON CONFLICT(key) DO UPDATE SET value=excluded.value, seen=excluded.seen, kind=excluded.kind,
                             complex=COALESCE(excluded.complex, complex), micro=COALESCE(excluded.micro, micro),
                             district=COALESCE(excluded.district, district)""", rows)
            cutoff = (datetime.now(KYIV) - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
            c.execute("DELETE FROM market WHERE seen < ?", (cutoff,))
        return len(rows)

    def count(self, city, deal):
        with self._c() as c:
            return c.execute("SELECT COUNT(*) FROM market WHERE city=? AND deal=?", (city, deal)).fetchone()[0]

    def _values(self, c, city, deal, field, group, rooms, kind=None):
        q = f"SELECT value FROM market WHERE city=? AND deal=?"
        args = [city, deal]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if field != "city":
            q += f" AND {field}=?"
            args.append(group)
        if rooms is not None:
            q += " AND rooms=?"
            args.append(rooms)
        return [r[0] for r in c.execute(q, args)]

    def reference(self, l, city, deal):
        """→ (медіана, кількість, підпис) або None."""
        groups = {"complex": _n(l.complex), "micro": _n(l.district_name), "district": _n(l.admin_district), "city": city}
        rb = rooms_bucket(l.rooms)
        kd = kind_of(l)
        with self._c() as c:
            for field, with_rooms, same_kind, need, label in LEVELS:
                g = groups.get(field)
                if not g or (with_rooms and rb is None):
                    continue
                if not same_kind and kd.startswith("work"):
                    continue          # «під ремонт» порівнюємо лише з такими ж — інакше фальшива «знижка»
                vals = self._values(c, city, deal, field, g, rb if with_rooms else None, kd if same_kind else None)
                if len(vals) >= need:
                    shown = (l.complex if field == "complex" else l.district_name if field == "micro"
                             else l.admin_district if field == "district" else "")
                    shown = re.sub(r"^ЖК\s+", "", shown or "")
                    k = f", {KIND_LABEL[kd]}" if same_kind and field != "complex" else (
                        ", під ремонт" if same_kind and kd.startswith("work") else "")
                    return statistics.median(vals), len(vals), label.format(g=shown, r=f"{rb}+" if rb == 4 else rb, k=k)
        return None

    def evaluate(self, l, city, deal, usd_uah=41.5):
        """Записує в оголошення відхилення від ринку (у %, мінус = дешевше)."""
        v = value_of(l, deal, usd_uah)
        ref = self.reference(l, city, deal) if v is not None else None
        if not ref:
            l.market_pct = None
            return None
        med, n, label = ref
        l.market_pct = round((v / med - 1) * 100)
        l.market_ref, l.market_n, l.market_label = med, n, label
        return l.market_pct
