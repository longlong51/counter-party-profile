import sqlite3, glob, os
con = sqlite3.connect("data/app.db")
con.row_factory = sqlite3.Row
rows=[dict(r) for r in con.execute("select * from CP_COUNTERPARTY")]
con.execute("delete from CP_COUNTERPARTY where FULL_NAME=?", ("测试银行",)); con.commit()
print("rows:", [(r["CP_ID"], r["FULL_NAME"], r["REG_CAPITAL"], r["FOUND_DATE"]) for r in rows])
print("latest pdf:", max(glob.glob("data/reports/*.pdf"), key=os.path.getmtime))
