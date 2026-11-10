import sys
sys.path.insert(0,".")
from app.agents.counterparty_graph import generate_report
from app.services.report_pdf import _md_sections
r = generate_report("中国银行", max_retry=0)
secs = _md_sections(r["markdown"])
for k,v in secs.items():
    print(f"[{k}] len={len(v)}")
    print(v[:200])
    print("-"*40)
