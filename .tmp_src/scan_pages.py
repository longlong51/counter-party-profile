import pymupdf, os, json
raw = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\raw"
def scan(path, topn=18):
    d = pymupdf.open(path)
    rows = []
    for i, p in enumerate(d):
        t = p.get_text().strip()
        ndraw = len(p.get_drawings())
        nimg = len(p.get_images(full=True))
        score = ndraw*2 + nimg*40 + min(len(t), 1200)
        rows.append((score, i+1, len(t), ndraw, nimg, t.replace("\n", " ")[:70]))
    rows.sort(reverse=True)
    print("="*100)
    print(os.path.basename(path), "pages", d.page_count)
    for r in rows[:topn]:
        print(f"  p{r[1]:>3} score={r[0]:>6} text={r[2]:>5} draw={r[3]:>4} img={r[4]:>2} | {r[5]}")
for f in ["boc_ar2025","boc_h1_2026","boc_q3_2026","boc_mkt_global_2026","boc_mkt_china_2026","boc_mkt_banking_2026","boc_pillar3_q3_2026","boc_pillar3_h1_2026","zhengxin_rating_2026","dagong_rating_2026","lianhe_rating_2026","cd071_notice","cd072_notice"]:
    scan(os.path.join(raw, f + ".pdf"))
