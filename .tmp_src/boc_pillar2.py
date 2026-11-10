import re, httpx
from urllib.parse import urljoin
h = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
base = "https://www.bank-of-china.com/investor/ir2/"
with httpx.Client(headers=h, timeout=30, follow_redirects=True) as c:
    r = c.get(base)
    cand = dict.fromkeys(urljoin(base, u.strip()) for u in re.findall(r'href="([^"]*t2026\d{4}_\d+\.html)"', r.text))
    print("detail pages:", len(cand))
    for full in cand:
        try:
            p = c.get(full)
            txt = p.text
            t = re.search(r'<title>(.*?)</title>', txt, re.S)
            title = t.group(1).strip() if t else ""
            if "第三支柱" in title or "Pillar" in title:
                pdfs = sorted(set(re.findall(r'https?://[^"\'\s<>]+?\.pdf', txt, re.I)))
                print("NOTE", title[:70], "|", full)
                for x in pdfs: print("   ", x)
        except Exception as e:
            print("ERR", full, type(e).__name__)
