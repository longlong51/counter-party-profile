import sys, glob, os
sys.path.insert(0,".")
from app.agents.counterparty_graph import generate_report
r = generate_report("中国银行", session_id="", max_retry=1)
print("status", r["status"], "pdf", r.get("pdf_url"))
