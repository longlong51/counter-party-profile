import re, httpx, os
pages = {
 "boc_ar2025": "https://www.boc.cn/investor/ir3/202603/t20260330_25657526.html",
 "boc_hy2026": "https://www.boc.cn/investor/ir3/202608/t20260828_25688316.html",
 "boc_q3_2026": "https://www.boc.cn/investor/ir3/202610/t20261030_25701540.html",
}
h = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
with httpx.Client(headers=h, timeout=30, follow_redirects=True) as c:
    for k,u in pages.items():
        r = c.get(u)
        links = set(re.findall(r'https?://[^"\'\s<>]+?\.pdf', r.text, re.I))
        print(k, r.status_code, len(links))
        for l in sorted(links):
            print("   ", l)
