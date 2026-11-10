import sqlite3
con = sqlite3.connect("data/app.db")
con.row_factory = sqlite3.Row
for r in con.execute("select CP_ID, FULL_NAME, INST_TYPE, REG_CAPITAL, FOUND_DATE, UPDATE_TIME from CP_COUNTERPARTY"):
    print(dict(r))
