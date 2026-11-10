import json, sys
sys.path.insert(0,".")
from app.core.config import settings
from app.services.rag import retrieve
rows=[json.loads(l) for l in open(settings.kb_jsonl,encoding="utf-8")]
p=[r for r in rows if "风险基金的来源" in r["title"]][0]
print(p["id"], p["title"]); print("CONTENT:", p["content"][:400])
r=retrieve("风险基金的来源与交纳标准情形下，违约如何处置？")
for c in r["chunks"][:3]:
    print("---", c["metadata"].get("knowledge_id"), round(c["rrf"],4))
    print(c["document"][:300])
