import pymupdf, os, shutil
raw = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\raw"
out_pdf = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\pdf"
out_img = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\images"
os.makedirs(out_pdf, exist_ok=True); os.makedirs(out_img, exist_ok=True)

# (输出名, 源文件, 页码列表(1-based), 模式)
JOBS = [
 ("财务报告_01_中国银行2025年年度报告_财务摘要与主要指标", "boc_ar2025.pdf", [7,8,9], "text"),
 ("财务报告_02_中国银行2026年半年度报告_财务摘要", "boc_h1_2026.pdf", [7,8,9], "text"),
 ("财务报告_03_中国银行2026年第三季度报告_主要财务数据与经营情况", "boc_q3_2026.pdf", [2,3,7,8], "image"),
 ("市场报告_01_全球经济金融展望报告2026_增长与通胀图表", "boc_mkt_global_2026.pdf", [5,7,8], "text"),
 ("市场报告_02_中国经济金融展望报告2026_消费投资图表", "boc_mkt_china_2026.pdf", [9,11,14], "text"),
 ("市场报告_03_全球银行业展望报告2026_监管政策与经营趋势", "boc_mkt_banking_2026.pdf", [6,10,12], "text"),
 ("交易报告_01_2026年第071期同业存单发行公告", "cd071_notice.pdf", [1,2], "image"),
 ("交易报告_02_2026年第072期同业存单发行情况公告", "cd072_notice.pdf", [1], "image"),
 ("交易报告_03_2026年半年度报告_金融市场业务节选", "boc_h1_2026.pdf", [28,29,30], "text"),
 ("信用报告_01_中诚信国际2026年度跟踪评级报告_评级结论与财务概况", "zhengxin_rating_2026.pdf", [3,4,9], "text"),
 ("信用报告_02_大公国际2026年度跟踪评级报告_等级与同业交易对手", "dagong_rating_2026.pdf", [3,8,11], "image"),
 ("信用报告_03_联合资信2026年跟踪评级报告_评级结果与债项概况", "lianhe_rating_2026.pdf", [4,6,7], "text"),
 ("风险报告_01_2026年第三季度第三支柱信息披露_KMV与OV1", "boc_pillar3_q3_2026.pdf", [4,5,7], "image"),
 ("风险报告_02_2026年半年度第三支柱信息披露_信用风险与交易对手信用风险", "boc_pillar3_h1_2026.pdf", [19,20,21], "text"),
 ("风险报告_03_中国银行2025年年度报告_风险管理章节", "boc_ar2025.pdf", [56,57,58], "text"),
]

for name, src, pages, mode in JOBS:
    sp = os.path.join(raw, src)
    src_doc = pymupdf.open(sp)
    dst = pymupdf.open()
    if mode == "text":
        for pno in pages:
            dst.insert_pdf(src_doc, from_page=pno-1, to_page=pno-1)
        op = os.path.join(out_pdf, name + ".pdf")
        dst.save(op); dst.close()
    else:
        for pno in pages:
            page = src_doc[pno-1]
            pix = page.get_pixmap(dpi=200)
            png = os.path.join(out_img, f"{name}_p{pno}.png")
            pix.save(png)
            w, h = pix.width, pix.height
            newp = dst.new_page(width=w, height=h)
            newp.insert_image(pymupdf.Rect(0, 0, w, h), filename=png)
        op = os.path.join(out_pdf, name + ".pdf")
        dst.save(op); dst.close()
    # 校验
    d = pymupdf.open(op)
    txt = sum(len(p.get_text().strip()) for p in d)
    imgs = sum(len(p.get_images(full=True)) for p in d)
    print(f"{mode:5} pages={d.page_count} text={txt:>5} img={imgs:>2} size={os.path.getsize(op):>8} | {name}")
    d.close()

src_doc.close()
