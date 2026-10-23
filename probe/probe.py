import re, json, requests, gzip, os
H={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36","Accept-Language":"uk"}
os.makedirs("probe_out",exist_ok=True)
def g(u,**k):
    r=requests.get(u,headers=H,timeout=30,**k); print(r.status_code,u[:110],len(r.content)); return r
r=g("https://auto.ria.com/api/search/auto",params=[("indexName","auto"),("categories.main.id",1),("marka_id[0]",9),("model_id[0]",3597),("s_yers[0]",2018),("po_yers[0]",2021),("order_by",7)])
ids=r.json()["result"]["search_result"]["ids"][:10]; print(ids)
for i in ids[:6]:
    d=g(f"https://auto.ria.com/demo/bu/searchPage/v2/view/auto/{i}/",params={"lang_id":4})
    if d.status_code!=200: continue
    j=d.json(); t=json.dumps(j,ensure_ascii=False)
    print(" ",i,"optionStyles",json.dumps(j.get("optionStyles"),ensure_ascii=False)[:300],"| шкір у JSON:",[t[max(0,m.start()-60):m.end()+60] for m in re.finditer(r"(?i)шкір",t)][:3])
    h=g("https://auto.ria.com/uk"+j["linkToView"])
    if h.status_code==200:
        x=h.text
        hits=[x[max(0,m.start()-120):m.end()+160] for m in re.finditer(r"(?i)комплектац|шкіряний салон|Салон[^<\"]{0,30}шкір|leather",x)][:5]
        print("  HTML hits:",len(hits)); [print("    ",repr(s)) for s in hits]
        if i==ids[0]: gzip.open("probe_out/page.gz","wt").write(x)
    continue
    for u in (f"https://auto.ria.com/api/auto/{i}/options", f"https://auto.ria.com/demo/api/autos/info?auto_id={i}", f"https://auto.ria.com/bu/blocks/json/auto/{i}/options"):
        try: g(u)
        except Exception as e: print("ERR",u,e)
