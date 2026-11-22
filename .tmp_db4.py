import sqlite3, io, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
con = sqlite3.connect("task-center.sqlite3")
con.row_factory = sqlite3.Row
cur = con.cursor()
cur.execute("PRAGMA table_info(task_events)")
print("event cols:", [r[1] for r in cur.fetchall()])
cur.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY rowid", ("85b5e085cb594c02885a2141b226a8be",))
for r in cur.fetchall():
    d = dict(r)
    print("|", d.get("created_at"), d.get("level"), d.get("kind"), str(d.get("message"))[:300])
