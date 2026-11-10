from pypdf import PdfReader
p = "data/reports/cp_6d5bd1af387643d8_20261109_202654.pdf"
r = PdfReader(p)
print(len(r.pages))
t = "\n".join((pg.extract_text() or "") for pg in r.pages)
for kw in ["2943", "1983", "商业银行", "九、", "八、"]:
    print(kw, kw in t)
