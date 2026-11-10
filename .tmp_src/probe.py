import sys, json
sys.path.insert(0, ".")
from app.services.rag import retrieve
qs = ["预测一下中国银行下周的股价涨跌。","帮我推荐三只今年收益最高的公募基金。","茅台 2025 年的营业收入是多少？",
      "多边净额结算的定义的核心要求有哪些？","证券资金结算实行分级结算原则的核心要求有哪些？","什么是货银对付（DVP）？"]
for q in qs:
    r = retrieve(q)
    print(f"{q[:28]:30s} sim={r['best_similarity']:.3f} bm25={r['best_bm25']:.2f} grounded={r['grounded']}")
