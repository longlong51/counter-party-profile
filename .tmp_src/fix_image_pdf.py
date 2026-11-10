import pymupdf, os
raw = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\raw"
out_pdf = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\pdf"
out_img = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\images"
IMG_JOBS = [
 ("财务报告_03_中国银行2026年第三季度报告_主要财务数据与经营情况", "boc_q3_2026.pdf", [2,3,7,8]),
 ("交易报告_01_2026年第071期同业存单发行公告", "cd071_notice.pdf", [1,2]),
 ("交易报告_02_2026年第072期同业存单发行情况公告", "cd072_notice.pdf", [1]),
 ("信用报告_02_大公国际2026年度跟踪评级报告_等级与同业交易对手", "dagong_rating_2026.pdf", [3,8,11]),
 ("风险报告_01_2026年第三季度第三支柱信息披露_KMV与OV1", "boc_pillar3_q3_2026.pdf", [4,5,7]),
]
for name, src, pages in IMG_JOBS:
    src_doc = pymupdf.open(os.path.join(raw, src))
    dst = pymupdf.open()
    for pno in pages:
        pix = src_doc[pno-1].get_pixmap(dpi=150)
        jpg = os.path.join(out_img, f"{name}_p{pno}.jpg")
        pix.save(jpg, jpg_quality=82)
        w,h = pix.width, pix.height
        p = dst.new_page(width=w, height=h)
        p.insert_image(pymupdf.Rect(0,0,w,h), filename=jpg)
    op = os.path.join(out_pdf, name + ".pdf")
    dst.save(op, deflate=True, garbage=4); dst.close()
    d = pymupdf.open(op)
    print(f"pages={d.page_count} text={sum(len(pg.get_text().strip()) for pg in d)} size={os.path.getsize(op)/1e6:.1f}MB | {name}")
    d.close(); src_doc.close()
# 清理旧 png（已被 jpg 取代），仅删除本次生成的 png
for f in os.listdir(out_img):
    if f.endswith(".png"):
        os.remove(os.path.join(out_img, f))
print("images dir:", [(f, round(os.path.getsize(os.path.join(out_img,f))/1e6,2)) for f in sorted(os.listdir(out_img))])
