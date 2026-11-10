import sys, glob, os, pymupdf
d = r"D:\PycharmProjects\counter-party-profile\.tmp_src"
for p in sorted(glob.glob(os.path.join(d, "*.pdf"))):
    doc = pymupdf.open(p)
    txt = "\n".join(page.get_text() for page in doc)
    out = p + ".txt"
    open(out, "w", encoding="utf-8").write(txt)
    print(os.path.basename(out), len(txt), doc.page_count)
