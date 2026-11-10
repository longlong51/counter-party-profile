import sqlite3
con=sqlite3.connect("data/app.db"); con.row_factory=sqlite3.Row
cols=[r[1] for r in con.execute("PRAGMA table_info(CP_RATING)")]
print("cols:", cols)
for r in con.execute("select * from CP_RATING"):
    print(dict(r))
