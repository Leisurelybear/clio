import sqlite3, json

con = sqlite3.connect("task-center.sqlite3")
con.row_factory = sqlite3.Row
cur = con.cursor()
cur.execute("PRAGMA table_info(tasks)")
print("tasks cols:", [r[1] for r in cur.fetchall()])
cur.execute("SELECT * FROM tasks ORDER BY rowid DESC LIMIT 8")
for r in cur.fetchall():
    d = dict(r)
    print("----")
    for k in ("id", "kind", "status", "created_at", "updated_at", "error", "message", "detail", "project_dir"):
        if k in d:
            v = d[k]
            if isinstance(v, str) and len(v) > 400:
                v = v[:400] + "..."
            print(f"  {k}: {v}")
