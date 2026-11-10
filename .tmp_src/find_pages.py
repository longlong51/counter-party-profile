import pymupdf, os
raw = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\raw"
def find(fn, kws, maxpages=12):
    d = pymupdf.open(os.path.join(raw, fn))
    print("="*90); print(fn, d.page_count)
    for i,p in enumerate(d):
        t = p.get_text()
        for kw in kws:
            if kw in t:
                print(f"  p{i+1} [{kw}] " + t.replace("\n"," ")[:110])
                break
find("boc_pillar3_h1_2026.pdf", ["交易对手","CCR","信用估值调整"])
find("boc_h1_2026.pdf", ["金融市场业务"])
find("boc_ar2025.pdf", ["风险管理体系","全面风险管理","风险偏好"])
