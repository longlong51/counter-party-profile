import httpx, json, glob
base="http://127.0.0.1:8000"
# 1) 建会话
s=httpx.post(f"{base}/api/chat/sessions", json={"title":"SSE联调"}).json()
# 2) SSE 流式
with httpx.stream("POST", f"{base}/api/chat/messages/stream",
                  json={"session_id":s["session_id"],"content":"什么是中央对手方？"}, timeout=60) as r:
    events=[]
    for line in r.iter_lines():
        if line.startswith("event:"):
            events.append(line.split(":",1)[1].strip())
print("stream status", r.status_code, "events", events[:3], "total", len(events))
# 3) 上传 PDF 解析
pdf = sorted(glob.glob("data/reference_reports/pdf/*.pdf"))[0]
with open(pdf,"rb") as f:
    resp = httpx.post(f"{base}/api/documents/parse", files={"file":(pdf.split("\\")[-1], f, "application/pdf")},
                      data={"session_id": s["session_id"]}, timeout=120)
print("upload", resp.status_code, json.dumps({k:v for k,v in resp.json().items() if k!="markdown"}, ensure_ascii=False)[:300])
# 4) 上传图片（图片型 PDF 的渲染页）
imgs = sorted(glob.glob("data/reference_reports/images/*.jpg"))
if imgs:
    with open(imgs[0],"rb") as f:
        r2 = httpx.post(f"{base}/api/documents/parse", files={"file":(imgs[0].split("\\")[-1], f, "image/jpeg")}, timeout=120)
    print("image", r2.status_code, r2.json()["parser"], r2.json()["text_chars"])
# 5) 报告列表
print("reports", len(httpx.get(f"{base}/api/reports").json()["items"]))
