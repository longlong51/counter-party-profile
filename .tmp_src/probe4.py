import sys
sys.path.insert(0,".")
from app.services.rag import retrieve
for q in ["什么是中央对手方？","中央对手方是什么？"]:
    r=retrieve(q)
    print(q, "sim",r["best_similarity"],"bm25",r["best_bm25"],"grounded",r["grounded"])
