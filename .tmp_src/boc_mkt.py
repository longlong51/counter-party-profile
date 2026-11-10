import re, httpx
pages = {
 "boc_mkt_2026q4": "https://www.boc.cn/fimarkets/summarize/202610/t20261009_25695483.html",
 "boc_mkt_2026q3": "https://www.boc.cn/fimarkets/summarize/202607/t20260703_25676324.html",
 "boc_mkt_2026annual": "https://www.boc.cn/fimarkets/summarize/202601/t20260105_25638427.html",
}
h = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
with httpx.Client(headers=h, timeout=30, follow_redirects=True) as c:
    for k,u in pages.items():
        r = c.get(u)
        links = sorted(set(re.findall(r'https?://[^"\'\s<>]+?\.pdf', r.text, re.I)))
        print(k, r.status_code, len(links))
        for l in links: print("   ", l)
