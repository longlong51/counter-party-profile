import ssl, httpx, os, fitz
ctx = ssl.create_default_context()
ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
raw = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\raw"
h = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
u = "https://www.chinamoney.org.cn/dqs/cm-s-notice-query/fileDownLoad.do?contentId=3343937&mode=save&priority=0"
with httpx.Client(headers=h, timeout=120, follow_redirects=True, verify=ctx) as c:
    r = c.get(u)
    print(r.status_code, len(r.content), r.content[:5])
    if r.content[:4]==b"%PDF":
        p=os.path.join(raw,"dagong_rating_2026.pdf")
        open(p,"wb").write(r.content)
        d=fitz.open(p); print("pages",d.page_count,"|",d[0].get_text()[:80].replace("\n"," "))
