import sys, json
sys.path.insert(0,".")
from app.agents.counterparty_graph import generate_report
r = generate_report("中国银行", max_retry=0)
md = r["markdown"]
for line in md.splitlines():
    if line.startswith("#") or line.startswith("**"):
        print(line[:80])
print("SECTIONS_KEYS:", list(r.keys()))
