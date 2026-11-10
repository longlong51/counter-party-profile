import json, sys
sys.path.insert(0,".")
from app.core.config import settings
rows=[json.loads(l) for l in open(settings.kb_jsonl,encoding="utf-8")]
for r in rows:
    if "多边净额结算" in r["title"] or "分级结算" in r["title"]:
        print(r["id"], r["title"])
        print("KEY:", r.get("key_points"))
        print("CONTENT:", r["content"][:260])
        print("-"*60)
