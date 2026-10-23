"""Ринкова ціна авто: медіана серед тієї ж марки/моделі, року (±1–2), пального і КПП.

Вибірка — усі авто, які бот бачить (до фільтрів), плюс раз на добу окремий збір по обраних моделях
без фільтра ціни. Поруч — медіанний пробіг групи, щоб видно було «дешево, бо пробіг великий».
"""
import os
import sqlite3
import statistics
import threading
from datetime import datetime, timedelta, timezone

KYIV = timezone(timedelta(hours=3))
WINDOW_DAYS = 60
SCHEMA = """
CREATE TABLE IF NOT EXISTS market (
    key TEXT PRIMARY KEY, brand TEXT, model TEXT, year INTEGER, fuel TEXT, gearbox TEXT,
    price REAL, mileage REAL, seen TEXT
);
CREATE INDEX IF NOT EXISTS m_bm ON market(brand, model, year);
"""
# (± років, той самий тип пального й КПП?, мінімум)
LEVELS = [(0, True, 6), (1, True, 6), (0, False, 6), (1, False, 6), (2, False, 8)]


def _n(s):
    return " ".join((s or "").lower().split())


class Market:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self._c() as c:
            c.executescript(SCHEMA)

    def _c(self):
        return sqlite3.connect(self.path, timeout=30)

    def add(self, cars):
        now = datetime.now(KYIV).strftime("%Y-%m-%d")
        rows = [(c.vin.upper() if c.vin and len(c.vin) >= 11 else c.key, _n(c.brand), _n(c.model), c.year,
                 c.fuel or None, c.gearbox or None, c.price_usd, c.mileage, now)
                for c in cars if c.brand and c.model and c.year and c.price_usd and 300 <= c.price_usd <= 2_000_000]
        if not rows:
            return 0
        with self.lock, self._c() as c:
            c.executemany("""INSERT INTO market VALUES (?,?,?,?,?,?,?,?,?)
                             ON CONFLICT(key) DO UPDATE SET price=excluded.price, mileage=COALESCE(excluded.mileage, mileage),
                             seen=excluded.seen, fuel=COALESCE(excluded.fuel, fuel), gearbox=COALESCE(excluded.gearbox, gearbox)""", rows)
            cutoff = (datetime.now(KYIV) - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
            c.execute("DELETE FROM market WHERE seen < ?", (cutoff,))
        return len(rows)

    def reference(self, car):
        if not (car.brand and car.model and car.year):
            return None
        with self._c() as c:
            for dy, same, need in LEVELS:
                q = "SELECT price, mileage FROM market WHERE brand=? AND model=? AND year BETWEEN ? AND ?"
                a = [_n(car.brand), _n(car.model), car.year - dy, car.year + dy]
                if same:
                    if not (car.fuel and car.gearbox):
                        continue
                    q += " AND fuel=? AND gearbox=?"
                    a += [car.fuel, car.gearbox]
                rows = c.execute(q, a).fetchall()
                if len(rows) >= need:
                    prices = [r[0] for r in rows]
                    mil = [r[1] for r in rows if r[1] is not None]
                    yr = f"{car.year}" if dy == 0 else f"{car.year - dy}–{car.year + dy}"
                    extra = ""
                    if same:
                        extra = ", " + {"petrol": "бензин", "diesel": "дизель", "lpg": "газ/бензин", "hybrid": "гібрид",
                                        "phev": "плагін-гібрид", "electric": "електро"}.get(car.fuel, car.fuel) + \
                                ", " + {"auto": "автомат", "manual": "механіка"}.get(car.gearbox, car.gearbox)
                    label = f"{car.brand} {car.model} {yr}{extra}"
                    return statistics.median(prices), len(rows), label, (statistics.median(mil) if mil else None)
        return None

    def evaluate(self, car):
        ref = self.reference(car) if car.price_usd else None
        if not ref:
            car.market_pct = None
            return None
        med, n, label, mil = ref
        car.market_pct = round((car.price_usd / med - 1) * 100)
        car.market_ref, car.market_n, car.market_label, car.market_mileage = med, n, label, mil
        return car.market_pct
