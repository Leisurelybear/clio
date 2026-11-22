import sqlite3, json, io, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
con = sqlite3.connect("task-center.sqlite3")
con.row_factory = sqlite3.Row
cur = con.cursor()
for tid in ("85b5e085cb594c02885a2141b226a8be", "59f1718020f24795ad7aabfd67bd2fbb", "303c0abb47194f5683103f00d5f6145a"):
    cur.execute("SELECT * FROM tasks WHERE id=?", (tid,))
    r = dict(cur.fetchone())
    print("=" * 80)
    print("id", r["id"], "status", r["status"], "phase", r.get("phase"))
    print("created", r["created_at"], "finished", r.get("finished_at"))
    print("error_code", r.get("error_code"))
    print("error_message", r.get("error_message"))
    print("message", r.get("message"))
    print("input_summary", r.get("input_summary_json"))
    print("result_summary", r.get("result_summary_json"))
    ip = r.get("input_data_json")
    if ip and len(ip) < 3000:
        print("input_data", ip)
    else:
        print("input_data (len)", len(ip or ""))
