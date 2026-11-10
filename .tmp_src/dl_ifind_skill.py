import httpx, os, zipfile
url = "https://s.thsi.cn/cd/ifind-java-ds-bff-web-container/ifind-mcp-web/skills/ifind-finance-data-1.5.1.zip"
tmp = r"D:\PycharmProjects\counter-party-profile\.tmp_src\ifind-skill.zip"
ext = r"D:\PycharmProjects\counter-party-profile\.tmp_src\ifind_skill"
h = {"User-Agent":"Mozilla/5.0"}
with httpx.Client(headers=h, timeout=120, follow_redirects=True) as c:
    r = c.get(url)
    print(r.status_code, len(r.content))
    open(tmp,"wb").write(r.content)
os.makedirs(ext, exist_ok=True)
with zipfile.ZipFile(tmp) as z:
    z.extractall(ext)
for root, dirs, files in os.walk(ext):
    for f in files:
        print(os.path.join(root,f).replace(ext,""), os.path.getsize(os.path.join(root,f)))
