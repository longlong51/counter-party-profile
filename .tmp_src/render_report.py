import sys, glob
sys.path.insert(0,".")
import pymupdf
f=sorted(glob.glob("data/reports/*.pdf"))[-1]
doc=pymupdf.open(f)
print(f, doc.page_count, "pages")
for i,p in enumerate(doc):
    pix=p.get_pixmap(dpi=110)
    pix.save(f".tmp_src/report_p{i+1}.png")
print("rendered", doc.page_count)
