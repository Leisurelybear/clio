import sqlite3, json

con = sqlite3.connect("task-center.sqlite3")
con.row_factory = sqlite3.Row
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("tables:", [r[0] for r in cur.fetchall()])
