import pymupdf, os
d0 = r"D:\PycharmProjects\counter-party-profile\data\reference_reports\pdf"
for f in sorted(os.listdir(d0)):
    d = pymupdf.open(os.path.join(d0, f))
    t = " ".join(p.get_text().replace("\n"," ").strip() for p in d)
    print("-"*100)
    print(f, "| pages", d.page_count, "| size", round(os.path.getsize(os.path.join(d0,f))/1e6,2), "MB")
    print("  HEAD:", t[:150])
    print("  TAIL:", t[-90:] if t else "(无文本层：图片型PDF)")
    d.close()
