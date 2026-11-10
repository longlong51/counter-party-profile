import httpx, os
raw = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\raw"
os.makedirs(raw, exist_ok=True)
h = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ids = {"lianhe_rating_2026":3369843, "cd071_notice":3436216, "cd072_notice":3438552}
with httpx.Client(headers=h, timeout=60, follow_redirects=True, verify=False) as c:
    for name, cid in ids.items():
        u = f"https://www.chinamoney.com.cn/dqs/cm-s-notice-query/fileDownLoad.do?contentId={cid}&mode=save&priority=0"
        try:
            r = c.get(u)
            print(name, r.status_code, r.headers.get("content-type"), len(r.content), r.content[:5])
            if r.content[:4] == b"%PDF":
                p = os.path.join(raw, name + ".pdf")
                open(p, "wb").write(r.content)
                print("   saved", p, len(r.content))
        except Exception as e:
            print("ERR", name, type(e).__name__, e)
