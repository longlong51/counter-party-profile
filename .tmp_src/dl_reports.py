import httpx, os, fitz
raw = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\raw"
os.makedirs(raw, exist_ok=True)
h = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
targets = {
 "boc_ar2025": "https://pic.bankofchina.com/bocappd/report/202603/P020260330667255171149.pdf",
 "boc_h1_2026": "https://pic.bankofchina.com/bocappd/report/202608/P020260828605977184329.pdf",
 "boc_q3_2026": "https://pic.bankofchina.com/bocappd/report/202610/P020261030610901541524.pdf",
 "boc_mkt_global_2026": "https://pic.bankofchina.com/bocappd/rareport/202601/P020260105361224423478.pdf",
 "boc_mkt_china_2026": "https://pic.bankofchina.com/bocappd/rareport/202601/P020260105361224523714.pdf",
 "boc_mkt_banking_2026": "https://pic.bankofchina.com/bocappd/rareport/202601/P020260105361224622632.pdf",
 "boc_pillar3_q3_2026": "https://pic.bankofchina.com/bocappd/report/202610/P020261030610925345157.pdf",
 "boc_pillar3_h1_2026": "https://pic.bankofchina.com/bocappd/report/202608/P020260828605953907036.pdf",
 "zhengxin_rating_2026": "http://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/BOND/2026/2026-7/2026-07-22/24907728.PDF",
 "dagong_rating_2026": "https://www.chinamoney.org.cn/dqs/cm-s-notice-query/fileDownLoad.do?contentId=3343937&mode=save&priority=0",
}
with httpx.Client(headers=h, timeout=120, follow_redirects=True, verify=False) as c:
    for name, u in targets.items():
        p = os.path.join(raw, name + ".pdf")
        if os.path.exists(p) and os.path.getsize(p) > 1000:
            print("skip", name); continue
        try:
            r = c.get(u)
            if r.content[:4] != b"%PDF":
                print("NOTPDF", name, r.status_code, len(r.content)); continue
            open(p, "wb").write(r.content)
            d = fitz.open(p)
            print("OK", name, len(r.content), "pages=", d.page_count, "|", d[0].get_text().strip().replace("\n"," ")[:60])
        except Exception as e:
            print("ERR", name, type(e).__name__, e)
