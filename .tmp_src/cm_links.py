import re, httpx
from urllib.parse import urljoin
h = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
pages = [
 "https://www.chinamoney.com.cn/chinese/zxpjbg/20260703/3369843.html",
 "https://www.chinamoney.com.cn/chinese/zqfxgg/20261022/3436216.html",
 "https://www.chinamoney.com.cn/chinese/zqfxgg/20261027/3438552.html",
]
with httpx.Client(headers=h, timeout=30, follow_redirects=True, verify=False) as c:
    for u in pages:
        try:
            r = c.get(u)
            links = re.findall(r'(?:href|src)="([^"]+)"', r.text)
            cands = [urljoin(u,x) for x in links if ("fileDownLoad" in x or ".pdf" in x.lower() or "PDF" in x)]
            print("PAGE", u, r.status_code)
            for x in list(dict.fromkeys(cands))[:8]: print("   ", x)
        except Exception as e:
            print("ERR", u, type(e).__name__, e)
