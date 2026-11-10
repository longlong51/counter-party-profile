import glob, os
from pypdf import PdfReader
p = max(glob.glob("data/reports/*.pdf"), key=os.path.getmtime)
print("pdf:", p, os.path.getsize(p))
r = PdfReader(p); print("pages:", len(r.pages))
t = "\n".join((pg.extract_text() or "") for pg in r.pages)
print("2943:", "2943" in t, "1983:", "1983" in t, "示例值:", "示例值" in t)
print(t[:600].replace("\n", " | "))
