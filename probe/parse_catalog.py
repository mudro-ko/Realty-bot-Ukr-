import re, html as H
def parse_catalog(page: str):
    out = []
    # кожна картка закінчується блоком Card-favorite з data-favorites="buildings-ID"
    for m in re.finditer(r'data-favorites="buildings-(\d+)"', page):
        bid = int(m.group(1))
        seg = page[max(0, m.start() - 9000):m.start()]
        a = seg.rfind("<a ")
        card = seg[a:] if a >= 0 else seg
        href = re.search(r'href="([^"]+)"', card)
        title = re.search(r'<h5[^>]*>(.*?)</h5>', card, re.S)
        dev = re.findall(r'CardDeveloper-text">(.*?)</span>', card, re.S)
        loc = re.search(r'Card-location[^"]*">(.*?)</div>', card, re.S)
        usd = re.search(r'data-currency="usd">(.*?)</span>', card, re.S)
        if not title:
            continue
        out.append({"lun_bid": bid, "name": H.unescape(title.group(1).strip()),
                    "developer": ", ".join(H.unescape(d.strip()) for d in dev) or None,
                    "district": H.unescape(loc.group(1).strip()) if loc else None,
                    "price_from": H.unescape(usd.group(1).strip()) if usd else None,
                    "url": H.unescape(href.group(1)) if href else None})
    seen, res = set(), []
    for x in out:
        if x["lun_bid"] not in seen:
            seen.add(x["lun_bid"]); res.append(x)
    return res
if __name__ == "__main__":
    import gzip, json, sys
    for f in sys.argv[1:]:
        r = parse_catalog(gzip.open(f, "rt", encoding="utf-8").read())
        print(f.split("/")[-1], len(r))
        for x in r[:5]: print("  ", json.dumps(x, ensure_ascii=False))
