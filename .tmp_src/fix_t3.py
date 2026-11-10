import pymupdf, os
raw = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\raw"
out = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\pdf"
src = pymupdf.open(os.path.join(raw, "boc_h1_2026.pdf"))
dst = pymupdf.open()
for pno in (29, 33):
    dst.insert_pdf(src, from_page=pno-1, to_page=pno-1)
p = os.path.join(out, "交易报告_03_2026年半年度报告_金融市场与交易业务节选.pdf")
old = os.path.join(out, "交易报告_03_2026年半年度报告_金融市场业务节选.pdf")
dst.save(p); dst.close(); src.close()
if os.path.exists(old): os.remove(old)
d = pymupdf.open(p)
print("pages", d.page_count, "|", d[0].get_text().replace("\n"," ")[:120])
print("...", d[1].get_text().replace("\n"," ")[:120])
d.close()
