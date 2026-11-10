import re, httpx
h = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
with httpx.Client(headers=h, timeout=30, follow_redirects=True) as c:
    r = c.get("https://www.bank-of-china.com/investor/ir2/")
    html = r.text
    # 找第三支柱相关详情页
    cand = re.findall(r'href="([^"]*t2026\d{4}_\d+\.html)"', html)
    print("detail pages:", len(cand))
    # 抓页面文本判断
    for u in dict.fromkeys(cand):
        full = u if u.startswith("http") else "https://www.bank-of-china.com"+u
        try:
            p = c.get(full)
            txt = p.text
            if "第三支柱" in txt or "Pillar" in txt:
                pdfs = sorted(set(re.findall(r'https?://[^"\'\s<>]+?\.pdf', txt, re.I)))
                title = re.search(r'<title>(.*?)</title>', txt, re.S)
                print("NOTE", full, "|", (title.group(1).strip()[:60] if title else ""))
                for x in pdfs: print("   ", x)
        except Exception as e:
            print("ERR", full, e)
