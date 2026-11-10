import re, httpx
h = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
u = "https://www.boc.cn/fimarkets/summarize/202610/t20261009_25695483.html"
with httpx.Client(headers=h, timeout=30, follow_redirects=True) as c:
    r = c.get(u)
    r.encoding = r.charset_encoding or "utf-8"
    html = r.text
print(len(html))
for m in re.finditer(r'<a[^>]+>', html):
    tag = m.group(0)
    if any(k in tag.lower() for k in ("pdf","rareport","download","附件")):
        print(tag[:200])
